"""Invite-only local game rooms with a separate AstrBot bridge."""

import argparse
import asyncio
import base64
import hashlib
import hmac
import importlib
import ipaddress
import json
import re
import secrets
import socket
import sqlite3
import ssl
import time
from contextlib import closing, contextmanager
from pathlib import Path

from aiohttp import web

from certificates import local_certificate


ROOT = Path(__file__).resolve().parent
GAMES = {
    "holdem": {"name": "德州", "minimum": 2, "capacity": 8},
    "sichuan": {"name": "四川麻将", "minimum": 4, "capacity": 4},
    "kingdoms": {"name": "三国杀", "minimum": 4, "capacity": 8},
}
INVITATION_LIFE = 8 * 3600
PRESENCE_GRACE = 90
PLATFORMS = {"aiocqhttp", "wechat_native"}
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
ROOM_ID = re.compile(r"[a-f0-9]{16}")
CONTENT_SECURITY_POLICY = "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"


def hash_token(value):
    return hashlib.sha256(value.encode()).hexdigest()


class RoomError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class GameRooms:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS rooms (
                    id TEXT PRIMARY KEY, invite_hash TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
                    platform_type TEXT NOT NULL, platform_id TEXT NOT NULL, bot TEXT NOT NULL,
                    group_id TEXT NOT NULL, opened REAL NOT NULL, expires REAL NOT NULL,
                    demo INTEGER NOT NULL, phase TEXT NOT NULL, host TEXT,
                    revision INTEGER NOT NULL DEFAULT 0, game TEXT, mode TEXT NOT NULL DEFAULT 'identity',
                    general_packs TEXT
                );
                CREATE TABLE IF NOT EXISTS visitors (
                    room_id TEXT NOT NULL, token TEXT NOT NULL,
                    player_id TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
                    seat TEXT NOT NULL DEFAULT 'visitor',
                    PRIMARY KEY (room_id, token)
                );
                CREATE TABLE IF NOT EXISTS settlements (
                    room_id TEXT PRIMARY KEY, text TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending', claimed REAL
                );
                CREATE INDEX IF NOT EXISTS rooms_by_group
                    ON rooms(platform_id, bot, group_id, phase, expires);
            """)
            db.execute("BEGIN IMMEDIATE")
            columns = {column["name"] for column in db.execute("PRAGMA table_info(rooms)")}
            if "mode" not in columns:
                db.execute("ALTER TABLE rooms ADD COLUMN mode TEXT NOT NULL DEFAULT 'identity'")
            if "general_packs" not in columns:
                db.execute("ALTER TABLE rooms ADD COLUMN general_packs TEXT")
            visitor_columns = {column["name"] for column in db.execute("PRAGMA table_info(visitors)")}
            if "last_seen" not in visitor_columns:
                db.execute("ALTER TABLE visitors ADD COLUMN last_seen REAL NOT NULL DEFAULT 0")
                # Give existing browsers a full grace period after upgrading.
                db.execute("UPDATE visitors SET last_seen=?", (time.time(),))

    @contextmanager
    def connect(self, write=False):
        with closing(sqlite3.connect(self.path, timeout=3, isolation_level=None)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield db
            except BaseException:
                db.rollback()
                raise
            else:
                db.commit()

    def open(self, kind, platform_type, platform_id, bot, group, *, demo=False):
        if not isinstance(kind, str) or kind not in GAMES:
            raise RoomError("没有这个游戏。")
        if not demo:
            if platform_type not in PLATFORMS or not all(
                isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_@.-]{1,140}", value)
                for value in (platform_id, bot, group)
            ):
                raise RoomError("群聊信息无效。")
            if platform_type == "aiocqhttp" and not (bot.isascii() and bot.isdecimal() and group.isascii() and group.isdecimal()):
                raise RoomError("QQ 群信息无效。")
            if platform_type == "wechat_native" and not group.endswith("@chatroom"):
                raise RoomError("微信群信息无效。")
        now = time.time()
        token = secrets.token_urlsafe(32)
        room = secrets.token_hex(8)
        with self.connect(write=True) as db:
            if not demo:
                # An abandoned lobby must not block the group's next invitation
                # for the entire eight-hour invitation lifetime.
                db.execute(
                    """UPDATE rooms SET phase='closed',revision=revision+1
                       WHERE platform_type=? AND platform_id=? AND bot=? AND group_id=?
                       AND phase='lobby' AND opened<? AND NOT EXISTS (
                           SELECT 1 FROM visitors WHERE room_id=rooms.id AND last_seen>=?)""",
                    (platform_type, platform_id, bot, group, now - PRESENCE_GRACE, now - PRESENCE_GRACE),
                )
            if not demo and db.execute(
                """SELECT 1 FROM rooms WHERE platform_type=? AND platform_id=? AND bot=? AND group_id=?
                   AND phase IN ('lobby','playing') AND expires>?""",
                (platform_type, platform_id, bot, group, now),
            ).fetchone():
                return None
            db.execute(
                """INSERT INTO rooms (id,invite_hash,kind,platform_type,platform_id,bot,group_id,
                   opened,expires,demo,phase) VALUES (?,?,?,?,?,?,?,?,?,?,'lobby')""",
                (room, hash_token(token), kind, platform_type, platform_id, bot, group, now,
                 now + INVITATION_LIFE, int(demo)),
            )
        return token

    @staticmethod
    def valid_room(room):
        if room is None or room["expires"] < time.time():
            raise RoomError("这条邀请已失效，请通过群里的新链接进入。", 404)
        return room

    def admit_invite(self, invitation, cookies):
        with self.connect(write=True) as db:
            room = self.valid_room(db.execute("SELECT * FROM rooms WHERE invite_hash=?", (hash_token(invitation),)).fetchone())
            previous = cookies.get(f"seat_{room['id']}", "")
            if TOKEN.fullmatch(previous) and db.execute(
                "SELECT 1 FROM visitors WHERE room_id=? AND token=?", (room["id"], hash_token(previous)),
            ).fetchone():
                return room["id"], previous, int(room["expires"] - time.time())
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO visitors (room_id,token,player_id,last_seen) VALUES (?,?,?,?)",
                       (room["id"], hash_token(token), "p" + secrets.token_hex(8), time.time()))
            return room["id"], token, int(room["expires"] - time.time())

    def session(self, db, room_id, token):
        if not ROOM_ID.fullmatch(room_id) or not TOKEN.fullmatch(token):
            raise RoomError("请从祀发在群里的邀请链接进入。", 403)
        visitor = db.execute("SELECT * FROM visitors WHERE room_id=? AND token=?", (room_id, hash_token(token))).fetchone()
        if visitor is None:
            raise RoomError("请从祀发在群里的邀请链接进入。", 403)
        room = self.valid_room(db.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone())
        return room, visitor

    @staticmethod
    def roster(db, room):
        return db.execute(
            "SELECT player_id,name,seat FROM visitors WHERE room_id=? AND seat IN ('pending','seated') ORDER BY rowid",
            (room["id"],),
        ).fetchall()

    @staticmethod
    def maintain_host(db, room):
        if room["phase"] not in {"lobby", "playing"}:
            return room
        cutoff = time.time() - PRESENCE_GRACE
        changed = False
        if room["phase"] == "lobby":
            changed = db.execute(
                """UPDATE visitors SET seat='visitor',name='' WHERE room_id=?
                   AND seat IN ('pending','seated') AND last_seen<?""",
                (room["id"], cutoff),
            ).rowcount > 0
        current = db.execute(
            """SELECT 1 FROM visitors WHERE room_id=? AND player_id=?
               AND seat='seated' AND last_seen>=?""",
            (room["id"], room["host"], cutoff),
        ).fetchone()
        host = room["host"]
        if current is None:
            # Approved seats take precedence. Only a lobby may promote an
            # applicant; game seats and private hands remain unchanged in play.
            candidate = db.execute(
                """SELECT player_id,seat FROM visitors WHERE room_id=? AND last_seen>=?
                   AND (seat='seated' OR (seat='pending' AND ?='lobby'))
                   ORDER BY CASE seat WHEN 'seated' THEN 0 ELSE 1 END,rowid LIMIT 1""",
                (room["id"], cutoff, room["phase"]),
            ).fetchone()
            if candidate:
                host = candidate["player_id"]
                db.execute("UPDATE visitors SET seat='seated' WHERE room_id=? AND player_id=?",
                           (room["id"], host))
            elif room["phase"] == "lobby":
                host = None
        if changed or host != room["host"]:
            db.execute("UPDATE rooms SET host=?,revision=revision+1 WHERE id=?", (host, room["id"]))
            room = db.execute("SELECT * FROM rooms WHERE id=?", (room["id"],)).fetchone()
        return room

    def touch(self, db, room, visitor):
        db.execute("UPDATE visitors SET last_seen=? WHERE room_id=? AND token=?",
                   (time.time(), room["id"], visitor["token"]))
        room = self.maintain_host(db, room)
        visitor = db.execute("SELECT * FROM visitors WHERE room_id=? AND token=?",
                             (room["id"], visitor["token"])).fetchone()
        return room, visitor

    @staticmethod
    def modes(kind):
        if kind != "kingdoms":
            return {}
        return importlib.import_module(kind).MODES

    @staticmethod
    def settings(kind, mode, modes):
        if not modes:
            return GAMES[kind].copy()
        selected = modes[mode]
        return {
            "name": GAMES[kind]["name"],
            "minimum": selected["minimum"],
            "capacity": selected["capacity"],
        }

    @staticmethod
    def packs(room):
        if room["kind"] != "kingdoms":
            return {}, []
        catalog = importlib.import_module("general_expansions").PACKS
        selected = json.loads(room["general_packs"]) if room["general_packs"] is not None else list(catalog)
        return catalog, selected

    @staticmethod
    def describe(room, visitor, players):
        mine = visitor["player_id"]
        host = room["host"] == mine
        kind = room["kind"]
        mode = room["mode"]
        modes = GameRooms.modes(kind)
        packs, enabled_packs = GameRooms.packs(room)
        state = None
        if room["game"] and room["phase"] != "closed":
            state = importlib.import_module(kind).view(
                json.loads(room["game"]), mine if visitor["seat"] == "seated" else None,
            )
        return {
            "room": room["id"], "kind": kind, "settings": GameRooms.settings(kind, mode, modes),
            "mode": mode if modes else None,
            "modes": [dict(details, id=key) for key, details in modes.items()],
            "packs": [dict(details, id=key) for key, details in packs.items()],
            "enabled_packs": enabled_packs,
            "phase": room["phase"], "revision": room["revision"], "host": host,
            "presence_timeout": PRESENCE_GRACE,
            "players": [dict(row) | {"host": row["player_id"] == room["host"]} for row in players
                        if row["seat"] == "seated" or host or row["player_id"] == mine],
            "mine": mine, "seat": visitor["seat"], "game": state, "demo": bool(room["demo"]),
        }

    def view(self, room_id, token):
        with self.connect(write=True) as db:
            room, visitor = self.session(db, room_id, token)
            room, visitor = self.touch(db, room, visitor)
            return self.describe(room, visitor, self.roster(db, room))

    def play(self, room_id, token, data):
        action = data.get("action")
        value = data.get("value")
        if not isinstance(action, str) or len(action) > 36:
            raise RoomError("操作无效。")
        with self.connect(write=True) as db:
            room, visitor = self.session(db, room_id, token)
            revision = data.get("revision")
            if type(revision) is not int or revision != room["revision"]:
                # Each Mahjong seat chooses its missing suit only once. Choices
                # from other seats do not invalidate that same decision.
                parallel_choice = (
                    type(revision) is int and 0 <= revision < room["revision"]
                    and room["kind"] == "sichuan" and room["phase"] == "playing"
                    and visitor["seat"] == "seated" and action == "choose_missing"
                )
                if parallel_choice:
                    current = json.loads(room["game"])
                    parallel_choice = current["phase"] == "choose_missing" and current["seats"][visitor["player_id"]]["missing"] is None
                if not parallel_choice:
                    raise RoomError("牌桌刚刚更新，请再试一次。", 409)
            room, visitor = self.touch(db, room, visitor)
            players = self.roster(db, room)
            seats = [p["player_id"] for p in players if p["seat"] == "seated"]
            host = room["host"] == visitor["player_id"]
            kind = room["kind"]
            mode = room["mode"]
            modes = self.modes(kind)
            settings = self.settings(kind, mode, modes)
            packs, enabled_packs = self.packs(room)
            if action == "join" and room["phase"] == "lobby" and visitor["seat"] == "visitor":
                name = value.strip() if isinstance(value, str) else ""
                if not name or len(name) > 16 or not all(char.isprintable() for char in name):
                    raise RoomError("昵称需要 1 到 16 个可见字符。")
                if len(seats) >= settings["capacity"] or len(players) >= 24:
                    raise RoomError("牌桌已满。")
                if any(p["name"].casefold() == name.casefold() for p in players):
                    raise RoomError("这个昵称已经有人用了。")
                seat = "pending" if room["host"] else "seated"
                db.execute("UPDATE visitors SET name=?,seat=? WHERE room_id=? AND token=?",
                           (name, seat, room_id, visitor["token"]))
                if seat == "seated":
                    db.execute("UPDATE rooms SET host=? WHERE id=?", (visitor["player_id"], room_id))
            elif action == "leave" and room["phase"] == "lobby" and visitor["seat"] in {"pending", "seated"}:
                db.execute("UPDATE visitors SET seat='visitor',name='',last_seen=0 WHERE room_id=? AND token=?",
                           (room_id, visitor["token"]))
                self.maintain_host(db, room)
            elif action in {"approve", "decline"} and host and room["phase"] == "lobby":
                if not any(p["player_id"] == value and p["seat"] == "pending" for p in players):
                    raise RoomError("这个入座申请已处理。")
                if action == "approve" and len(seats) >= settings["capacity"]:
                    raise RoomError("牌桌已满。")
                db.execute("UPDATE visitors SET seat=?,name=CASE WHEN ?='visitor' THEN '' ELSE name END WHERE room_id=? AND player_id=?",
                           ("seated" if action == "approve" else "visitor",
                            "seated" if action == "approve" else "visitor", room_id, value))
            elif action == "mode" and host and room["phase"] == "lobby":
                if not isinstance(value, str) or value not in modes:
                    raise RoomError("这张牌桌没有该玩法。")
                if len(seats) > modes[value]["capacity"]:
                    raise RoomError("已入座人数超过这个玩法的上限。")
                db.execute("UPDATE rooms SET mode=? WHERE id=?", (value, room_id))
            elif action == "packs" and host and room["phase"] == "lobby":
                if not isinstance(value, list) or len(value) > len(packs) or any(
                    not isinstance(item, str) or item not in packs for item in value
                ):
                    raise RoomError("武将包不存在。")
                db.execute("UPDATE rooms SET general_packs=? WHERE id=?",
                           (json.dumps([key for key in packs if key in value]), room_id))
            elif action == "start" and host and room["phase"] == "lobby":
                if not settings["minimum"] <= len(seats) <= settings["capacity"]:
                    raise RoomError("入座人数还不够。")
                create = importlib.import_module(kind).create
                try:
                    game = create(seats, mode=mode, packs=enabled_packs) if modes else create(seats)
                except ValueError as error:
                    raise RoomError(str(error)) from error
                db.execute("UPDATE rooms SET phase='playing',game=? WHERE id=?", (json.dumps(game, ensure_ascii=False), room_id))
            elif action == "close" and host and room["phase"] in {"lobby", "playing"}:
                db.execute("UPDATE rooms SET phase='closed' WHERE id=?", (room_id,))
            elif room["phase"] == "playing" and visitor["seat"] == "seated":
                game = json.loads(room["game"])
                try:
                    importlib.import_module(kind).play(game, visitor["player_id"], action, value)
                except ValueError as error:
                    raise RoomError(str(error)) from error
                phase = "finished" if game["finished"] else "playing"
                db.execute("UPDATE rooms SET phase=?,game=? WHERE id=?",
                           (phase, json.dumps(game, ensure_ascii=False), room_id))
                if phase == "finished" and not room["demo"]:
                    names = {p["player_id"]: p["name"] for p in players}
                    result = re.sub(r"p[a-f0-9]{16}", lambda match: names.get(match[0], "玩家"), game["result"])
                    db.execute("INSERT INTO settlements (room_id,text) VALUES (?,?)",
                               (room_id, f"【{GAMES[kind]['name']}结算】{result}"[:480]))
            else:
                raise RoomError("当前无法执行这个操作。", 403)
            db.execute("UPDATE rooms SET revision=revision+1 WHERE id=?", (room_id,))
            room, visitor = self.session(db, room_id, token)
            return self.describe(room, visitor, self.roster(db, room))

    def pending(self):
        with self.connect(write=True) as db:
            db.execute("UPDATE settlements SET state='uncertain' WHERE state='delivering' AND claimed<?",
                       (time.time() - 120,))
            return [dict(row) for row in db.execute(
                """SELECT settlements.room_id,text,platform_type,platform_id,bot,group_id FROM settlements
                   JOIN rooms ON rooms.id=settlements.room_id WHERE state='pending' ORDER BY settlements.rowid LIMIT 20"""
            )]

    def settlement(self, room, operation):
        with self.connect(write=True) as db:
            if operation == "claim":
                return db.execute("UPDATE settlements SET state='delivering',claimed=? WHERE room_id=? AND state='pending'",
                                  (time.time(), room)).rowcount == 1
            if operation in {"pending", "sent", "uncertain"}:
                return db.execute("UPDATE settlements SET state=? WHERE room_id=? AND state='delivering'",
                                  (operation, room)).rowcount == 1
            return False


@web.middleware
async def errors(request, handler):
    headers = {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    }
    try:
        response = await handler(request)
    except RoomError as error:
        response = web.json_response({"error": str(error), "room_error": True}, status=error.status)
    except web.HTTPException as error:
        error.headers.update(headers)
        error.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        raise
    response.headers.update(headers)
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    return response


async def object_body(request):
    if request.content_type != "application/json":
        raise RoomError("请求格式无效。", 415)
    try:
        value = await request.json()
    except (ValueError, UnicodeError):
        raise RoomError("请求格式无效。") from None
    if not isinstance(value, dict):
        raise RoomError("请求格式无效。")
    return value


def public_app(store, origin):
    app = web.Application(middlewares=[errors], client_max_size=8192)
    site = ROOT / "web"
    index = (site / "index.html").read_text(encoding="utf-8")
    bootstrap = re.search(r'<script id="room-bootstrap">([\s\S]*?)</script>', index)
    policy = CONTENT_SECURITY_POLICY
    if bootstrap:
        digest = base64.b64encode(hashlib.sha256(bootstrap[1].encode()).digest()).decode("ascii")
        policy += f"; script-src 'self' 'sha256-{digest}'"

    async def ready(request):
        try:
            available = all((site / name).is_file() and (site / name).stat().st_size > 0
                            for name in ("index.html", "invite.html", "assets/app.js", "assets/site.css"))
        except OSError:
            available = False
        return web.json_response({"service": "si-game-room", "ready": available}, status=200 if available else 503)

    def session(request):
        room = request.match_info["room"]
        return room, request.cookies.get(f"seat_{room}", "")

    async def invite(request):
        token = request.match_info["token"]
        if not TOKEN.fullmatch(token):
            raise RoomError("请从祀发在群里的邀请链接进入。", 404)
        room, visitor, max_age = store.admit_invite(token, request.cookies)
        path = f"/r/{room}/"
        redirect = web.Response(status=303, headers={"Location": path})
        redirect.set_cookie(f"seat_{room}", visitor, path="/", secure=True, httponly=True,
                            samesite="Lax", max_age=max_age)
        return redirect

    async def room_page(request):
        store.view(*session(request))
        return web.Response(text=index, content_type="text/html", headers={"Content-Security-Policy": policy})

    async def view(request):
        return web.json_response(store.view(*session(request)))

    async def action(request):
        if request.headers.get("Origin") != origin:
            raise RoomError("请在牌桌页面操作。", 403)
        return web.json_response(store.play(*session(request), await object_body(request)))

    async def no_invite(request):
        return web.FileResponse(site / "invite.html", status=403)

    app.router.add_get("/", no_invite)
    app.router.add_get("/ready", ready)
    app.router.add_get("/i/{token}", invite)
    app.router.add_get("/r/{room}/", room_page)
    app.router.add_get("/r/{room}/state", view)
    app.router.add_post("/r/{room}/action", action)
    app.router.add_static("/assets", site / "assets", show_index=False)
    return app


def bridge_app(store, origin, secret):
    @web.middleware
    async def authenticate(request, handler):
        if not hmac.compare_digest(request.headers.get("Authorization", "").encode(), f"Bearer {secret}".encode()):
            raise RoomError("机器人凭据无效。", 403)
        return await handler(request)

    app = web.Application(middlewares=[errors, authenticate], client_max_size=8192)

    async def new(request):
        data = await object_body(request)
        token = store.open(data.get("kind"), data.get("platform_type"), data.get("platform_id"),
                           data.get("bot"), data.get("group"))
        return web.json_response({"link": f"{origin}/i/{token}" if token else None})

    async def pending(request):
        return web.json_response({"items": store.pending()})

    async def mark(request):
        data = await object_body(request)
        room = request.match_info["room"]
        if not ROOM_ID.fullmatch(room):
            raise RoomError("结算编号无效。")
        return web.json_response({"ok": store.settlement(room, data.get("operation"))})

    app.router.add_post("/open", new)
    app.router.add_get("/settlements", pending)
    app.router.add_post("/settlements/{room}", mark)
    return app


def local_address():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        route.connect(("192.0.2.1", 9))
        return route.getsockname()[0]


def origin_for(host, port):
    address = ipaddress.IPv4Address(host)
    if not address.is_private:
        raise ValueError("本地试玩请指定电脑的局域网 IPv4 地址。")
    return f"https://{address}:{port}"


def public_origin(value):
    message = "公网地址必须是 HTTPS 源：只有主机和可选端口，不能包含账号、路径、查询或片段。"
    match = re.fullmatch(r"https://(\[[0-9a-f:.]+\]|[a-z0-9.-]+)(?::([0-9]{1,5}))?",
                         value, re.ASCII | re.IGNORECASE)
    if not match:
        raise ValueError(message)
    host, port_text = match.groups()
    if host.startswith("["):
        try:
            host = f"[{ipaddress.IPv6Address(host[1:-1])}]"
        except ValueError:
            raise ValueError(message) from None
    else:
        host = host.lower()
        try:
            host = str(ipaddress.IPv4Address(host))
        except ValueError:
            labels = host.split(".")
            if (len(host) > 253
                or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
                or re.fullmatch(r"[0-9]+|0x[0-9a-f]*", labels[-1])):
                # Browsers resolve names ending in a number as IPv4, sometimes
                # accepting shorthand or octal forms. Require canonical IPv4.
                raise ValueError(message) from None
    port = int(port_text) if port_text is not None else 443
    if not 1 <= port <= 65535:
        raise ValueError(message)
    return f"https://{host}" if port == 443 else f"https://{host}:{port}"


async def serve(args, store, origin):
    # Load rule modules before accepting invitations, rather than on the first
    # browser request while the room appears to be stuck loading.
    for kind in GAMES:
        importlib.import_module(kind)
    tls = None
    if not args.behind_proxy:
        ca, certificate, key = local_certificate(args.data, args.host)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(certificate, key)
    manifest = args.data / "bridge.json"
    if manifest.is_file():
        secret = json.loads(manifest.read_text(encoding="utf-8"))["secret"]
    else:
        secret = secrets.token_urlsafe(32)
    public = web.AppRunner(public_app(store, origin), access_log=None)
    bridge = web.AppRunner(bridge_app(store, origin, secret), access_log=None)
    await public.setup()
    await bridge.setup()
    try:
        await web.TCPSite(public, args.host, args.port, ssl_context=tls).start()
        await web.TCPSite(bridge, "127.0.0.1", args.bridge_port).start()
        manifest.write_text(json.dumps({"url": f"http://127.0.0.1:{args.bridge_port}", "secret": secret, "origin": origin}),
                            encoding="utf-8")
        if args.behind_proxy:
            print(f"Public HTTPS origin {origin}\nLoopback backend http://{args.host}:{args.port}", flush=True)
        elif args.public_origin is not None:
            print(f"Public HTTPS origin {origin}\nLocal HTTPS ready https://{args.host}:{args.port}\nCA certificate {ca}", flush=True)
        else:
            print(f"LAN HTTPS ready {origin}\nCA certificate {ca}", flush=True)
        await asyncio.Event().wait()
    finally:
        await bridge.cleanup()
        await public.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="电脑局域网 IPv4 地址")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--public-origin", help="反向代理对外提供的 HTTPS 源，例如 https://play.example.com（不含末尾斜杠）")
    parser.add_argument("--behind-proxy", action="store_true", help="在 IPv4 回环地址使用 HTTP，由反向代理终止 HTTPS")
    parser.add_argument("--bridge-port", type=int, default=8766)
    parser.add_argument("--data", type=Path, default=ROOT / ".runtime")
    parser.add_argument("--demo", choices=GAMES, help="签发本地试玩邀请，不发群消息")
    args = parser.parse_args()
    if args.behind_proxy and args.public_origin is None:
        parser.error("--behind-proxy 需要 --public-origin。")
    args.host = args.host or ("127.0.0.1" if args.behind_proxy else local_address())
    try:
        if args.behind_proxy and not ipaddress.IPv4Address(args.host).is_loopback:
            raise ValueError("--behind-proxy 只能绑定 IPv4 回环地址。")
        local_origin = origin_for(args.host, args.port)
        origin = local_origin if args.public_origin is None else public_origin(args.public_origin)
    except ValueError as error:
        parser.error(str(error))
    args.data = args.data.resolve()
    args.data.mkdir(exist_ok=True, parents=True)
    store = GameRooms(args.data / "rooms.sqlite3")
    if args.demo:
        token = store.open(args.demo, "demo", "demo", "demo", "demo", demo=True)
        print(f"{origin}/i/{token}")
        return
    try:
        asyncio.run(serve(args, store, origin))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
