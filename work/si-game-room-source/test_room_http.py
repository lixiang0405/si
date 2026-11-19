"""Local integration tests for the browser and bot HTTP interfaces."""

import asyncio
import io
import json
import random
import socket
import ssl
import tempfile
import unittest
from collections import Counter, defaultdict
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout, CookieJar, TCPConnector
from aiohttp.test_utils import TestServer, get_port_socket

import server
import tournament
from card_catalog import DELAYED, MANIFEST, WEAPONS, kind as card_kind
from certificates import local_certificate


class PublicOriginTest(unittest.TestCase):
    def test_https_origins_match_browser_serialization(self):
        for value, expected in (
            ("HTTPS://Play.Example.COM:443", "https://play.example.com"),
            ("https://play.example.com:8443", "https://play.example.com:8443"),
            ("https://192.0.2.1:443", "https://192.0.2.1"),
            ("https://[2001:0DB8:0000::1]:443", "https://[2001:db8::1]"),
        ):
            with self.subTest(value=value):
                self.assertEqual(server.public_origin(value), expected)

        for value in (
            "http://play.example.com", "https://user:pass@play.example.com",
            "https://play.example.com/", "https://play.example.com/room",
            "https://play.example.com?", "https://play.example.com#",
            "https://play.example.com:0", "https://play.example.com:65536",
            "https://play.example.com:", "https://play.example.com\n",
            "https://play.example.com\\other", "https://play..example.com",
            "https://127.1", "https://0177.0.0.1", "https://[gggg::1]",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                server.public_origin(value)

    def test_proxy_mode_requires_origin_and_loopback(self):
        for args, reason in (
            (["--behind-proxy"], "需要 --public-origin"),
            (["--behind-proxy", "--public-origin", "https://play.example.com", "--host", "192.168.0.1"],
             "只能绑定 IPv4 回环地址"),
        ):
            with self.subTest(args=args), patch("sys.argv", ["server.py", *args]):
                with redirect_stderr(io.StringIO()) as stderr, self.assertRaises(SystemExit) as error:
                    server.main()
                self.assertEqual(error.exception.code, 2)
                self.assertIn(reason, stderr.getvalue())


class PublicProxyHttpTest(unittest.IsolatedAsyncioTestCase):
    async def test_proxy_backend_uses_public_origin_and_keeps_bridge_private(self):
        with tempfile.TemporaryDirectory(prefix=".test-proxy-", dir=Path(__file__).parent) as folder:
            root = Path(folder)
            with get_port_socket("127.0.0.1", 0, socket.AF_INET) as public_socket, \
                    get_port_socket("127.0.0.1", 0, socket.AF_INET) as bridge_socket:
                port, bridge_port = public_socket.getsockname()[1], bridge_socket.getsockname()[1]
            origin = server.public_origin("https://Play.Example.COM:443")
            args = SimpleNamespace(data=root, host="127.0.0.1", port=port, bridge_port=bridge_port, behind_proxy=True)
            manifest_file = root / "bridge.json"
            with redirect_stdout(io.StringIO()):
                service = asyncio.create_task(server.serve(args, server.GameRooms(root / "rooms.sqlite3"), origin))
                try:
                    async with asyncio.timeout(5):
                        while not manifest_file.is_file():
                            if service.done():
                                await service
                            await asyncio.sleep(0.01)
                    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                    self.assertEqual(manifest["origin"], origin)
                    self.assertEqual(manifest["url"], f"http://127.0.0.1:{bridge_port}")
                    self.assertFalse((root / "server.pem").exists())
                    upstream = f"http://127.0.0.1:{port}"
                    headers = {"Host": "play.example.com"}
                    async with ClientSession(timeout=ClientTimeout(total=5)) as client:
                        async with client.post(
                            f'{manifest["url"]}/open', json=RoomHttpTest.QQ,
                            headers={"Authorization": f'Bearer {manifest["secret"]}'},
                        ) as response:
                            self.assertEqual(response.status, 200)
                            link = (await response.json())["link"]
                        self.assertTrue(link.startswith(f"{origin}/i/"))
                        async with client.post(f"{upstream}/open", json=RoomHttpTest.QQ, headers=headers) as response:
                            self.assertEqual(response.status, 404)
                        async with client.get(f"{upstream}{urlsplit(link).path}", headers=headers,
                                              allow_redirects=False) as response:
                            self.assertEqual(response.status, 303)
                            room = response.headers["Location"]
                            key, cookie = next(iter(response.cookies.items()))
                            self.assertTrue(cookie["secure"])
                        for browser_origin, status in ((upstream, 403), ("https://elsewhere.example", 403), (origin, 200)):
                            async with client.post(
                                f"{upstream}{room}action",
                                headers=headers | {"Origin": browser_origin, "Cookie": f"{key}={cookie.value}"},
                                json={"action": "join", "value": "Player", "revision": 0},
                            ) as response:
                                self.assertEqual(response.status, status, await response.text())
                finally:
                    service.cancel()
                    await asyncio.gather(service, return_exceptions=True)


class RoomHttpTest(unittest.IsolatedAsyncioTestCase):
    SECRET = "only-the-local-test-bot-can-open-a-game"
    QQ = {"kind": "holdem", "platform_type": "aiocqhttp", "platform_id": "qq", "bot": "10001", "group": "20001"}
    WECHAT = {
        "kind": "holdem", "platform_type": "wechat_native", "platform_id": "wechat",
        "bot": "wxid_bot", "group": "20001@chatroom",
    }

    async def asyncSetUp(self):
        workspace = tempfile.TemporaryDirectory(prefix=".test-http-", dir=Path(__file__).parent)
        self.addCleanup(workspace.cleanup)
        root = Path(workspace.name)
        self.db = root / "rooms.sqlite3"
        ca, certificate, key = local_certificate(root, "127.0.0.1")
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(certificate, key)
        self.trust = ssl.create_default_context(cafile=ca)

        # A socket reserved before app creation makes the expected browser Origin
        # identical to the actual, dynamically assigned HTTPS address.
        listener = get_port_socket("127.0.0.1", 0, socket.AF_INET)
        self.addCleanup(listener.close)
        self.origin = server.origin_for(*listener.getsockname())
        self.port = listener.getsockname()[1]
        self.tls = tls
        self.root = root
        site = root / "web"
        (site / "assets").mkdir(parents=True)
        (site / "invite.html").write_text("<title>Invite required</title>", encoding="utf-8")
        (site / "index.html").write_text("<title>Game room</title>", encoding="utf-8")
        self.public = None
        await self.restart_public(listener)

        self.bot = ClientSession(timeout=ClientTimeout(total=8))
        self.addAsyncCleanup(self.bot.close)
        self.bridge = None
        await self.restart_bridge()

    async def restart_public(self, listener=None):
        if self.public is not None:
            await self.public.close()
        self.store = server.GameRooms(self.db)
        with patch.object(server, "ROOT", self.root):
            app = server.public_app(self.store, self.origin)
        options = {"socket_factory": lambda *_: listener} if listener is not None else {}
        self.public = TestServer(app, port=self.port, **options)
        self.addAsyncCleanup(self.public.close)
        await self.public.start_server(ssl=self.tls)

    async def restart_bridge(self):
        if self.bridge is not None:
            await self.bridge.close()
        app = server.bridge_app(server.GameRooms(self.db), self.origin, self.SECRET)
        self.bridge = TestServer(app)
        self.addAsyncCleanup(self.bridge.close)
        await self.bridge.start_server()

    def browser(self):
        client = ClientSession(
            cookie_jar=CookieJar(unsafe=True),
            connector=TCPConnector(ssl=self.trust),
            timeout=ClientTimeout(total=8),
        )
        self.addAsyncCleanup(client.close)
        return client

    def bot_headers(self):
        return {"Authorization": f"Bearer {self.SECRET}"}

    async def open(self, settings=None):
        async with self.bot.post(
            self.bridge.make_url("/open"), headers=self.bot_headers(),
            json=self.QQ if settings is None else settings,
        ) as response:
            self.assertEqual(response.status, 200, await response.text())
            return (await response.json())["link"]

    async def invite(self, link, client=None):
        client = client or self.browser()
        async with client.get(link, allow_redirects=False) as response:
            self.assertEqual(response.status, 303, await response.text())
            path = response.headers["Location"]
            cookie = next(iter(response.cookies.values()))
            self.assertTrue(cookie["secure"])
            self.assertTrue(cookie["httponly"])
            self.assertEqual(cookie["samesite"], "Lax")
            self.assertEqual(cookie["path"], "/")
            self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
            return client, path, cookie.value

    async def state(self, client, room):
        async with client.get(f"{self.origin}{room}state") as response:
            self.assertEqual(response.status, 200, await response.text())
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            return await response.json()

    async def submit(self, client, room, action, value=None, *, revision=None, origin=None):
        if revision is None:
            revision = (await self.state(client, room))["revision"]
        async with client.post(
            f"{self.origin}{room}action",
            json={"action": action, "value": value, "revision": revision},
            headers={"Origin": self.origin if origin is None else origin},
        ) as response:
            return response.status, await response.json()

    async def action(self, client, room, action, value=None, *, revision=None, status=200, origin=None):
        actual, payload = await self.submit(client, room, action, value, revision=revision, origin=origin)
        self.assertEqual(actual, status, payload)
        return payload

    async def pending(self):
        async with self.bot.get(self.bridge.make_url("/settlements"), headers=self.bot_headers()) as response:
            self.assertEqual(response.status, 200, await response.text())
            return (await response.json())["items"]

    async def mark(self, room_id, operation):
        async with self.bot.post(
            self.bridge.make_url(f"/settlements/{room_id}"),
            json={"operation": operation},
            headers=self.bot_headers(),
        ) as response:
            self.assertEqual(response.status, 200, await response.text())
            return (await response.json())["ok"]

    async def test_invitation_required_bearer_and_origin(self):
        client = self.browser()
        async with client.get(self.origin) as response:
            self.assertEqual(response.status, 403)
        async with client.get(f"{self.origin}/i/{'Z' * 43}") as response:
            self.assertEqual(response.status, 404)
        async with client.post(f"{self.origin}/open", json=self.QQ, headers=self.bot_headers()) as response:
            self.assertEqual(response.status, 404)
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            async with self.bot.post(self.bridge.make_url("/open"), json=self.QQ, headers=headers) as response:
                self.assertEqual(response.status, 403)
            async with self.bot.get(self.bridge.make_url("/settlements"), headers=headers) as response:
                self.assertEqual(response.status, 403)

        link = await self.open()
        self.assertEqual(urlsplit(link).netloc, urlsplit(self.origin).netloc)
        seated_browser, room, token = await self.invite(link)
        async with client.get(f"{self.origin}{room}") as response:
            self.assertEqual(response.status, 403)
        async with client.get(f"{self.origin}{room}state") as response:
            self.assertEqual(response.status, 403)
        async with seated_browser.get(f"{self.origin}{room}") as response:
            self.assertEqual(response.status, 200)
        _, same_room, same_token = await self.invite(link, seated_browser)
        self.assertEqual((same_room, same_token), (room, token))
        await self.action(seated_browser, room, "join", "跨站", origin="https://cross-site.invalid", status=403)
        await self.action(seated_browser, room, "join", "无来源", origin="", status=403)
        self.assertEqual((await self.state(seated_browser, room))["revision"], 0)

    async def test_groups_isolated_and_duplicate_suppressed(self):
        qq_link = await self.open()
        self.assertIsNone(await self.open(self.QQ | {"kind": "sichuan"}))
        wechat_link = await self.open(self.WECHAT)
        self.assertIsNone(await self.open(self.WECHAT))
        another_qq_group = await self.open(self.QQ | {"group": "20002"})
        another_qq_bot = await self.open(self.QQ | {"bot": "10002"})
        self.assertEqual(len({qq_link, wechat_link, another_qq_group, another_qq_bot}), 4)
        client, qq_room, qq_token = await self.invite(qq_link)
        visitor, wx_room, _ = await self.invite(wechat_link)
        await self.action(client, qq_room, "join", "QQ桌主")
        self.assertEqual((await self.state(visitor, wx_room))["players"], [])
        # Even an explicitly relabelled QQ session cannot be reused across rooms.
        cookie_name = f"seat_{wx_room.split('/')[2]}"
        async with self.browser().get(
            f"{self.origin}{wx_room}state", headers={"Cookie": f"{cookie_name}={qq_token}"}
        ) as response:
            self.assertEqual(response.status, 403)

    async def test_seat_cap_approval_and_private_cards(self):
        link = await self.open()
        host, room, host_token = await self.invite(link)
        earlier = await self.action(host, room, "join", "桌主")
        self.assertTrue(earlier["host"])
        guests = []
        for index in range(8):
            guest, guest_room, guest_token = await self.invite(link)
            self.assertEqual(room, guest_room)
            self.assertNotEqual(host_token, guest_token)
            pending = await self.action(guest, room, "join", f"玩家{index + 1}")
            self.assertEqual(pending["seat"], "pending")
            guests.append((guest, pending["mine"]))

        first, first_id = guests[0]
        await self.action(first, room, "approve", first_id, status=403)
        await self.action(first, room, "start", status=403)
        await self.action(host, room, "approve", first_id, revision=earlier["revision"], status=409)
        for _, player in guests[:7]:
            await self.action(host, room, "approve", player)
        watcher, extra = guests[-1]
        await self.action(host, room, "approve", extra, status=400)
        started = await self.action(host, room, "start")
        host_id = started["mine"]
        self.assertEqual(started["phase"], "playing")
        self.assertEqual(len(started["game"]["players"]), 8)
        guests_view = await self.state(first, room)
        watcher_view = await self.state(watcher, room)
        for snapshot, viewer in ((started, host_id), (guests_view, first_id), (watcher_view, None)):
            cards = {seat["id"]: seat["cards"] for seat in snapshot["game"]["players"]}
            for player, hand in cards.items():
                if player == viewer:
                    self.assertEqual(len(hand), 2)
                    self.assertTrue(all(isinstance(card, str) for card in hand))
                else:
                    self.assertEqual(hand, [None, None])
            self.assertNotIn("deck", snapshot["game"])
        await self.action(watcher, room, "fold", status=403)

    async def test_settlement_claim_persists_after_restart(self):
        link = await self.open(self.WECHAT)
        host, room, _ = await self.invite(link)
        first = await self.action(host, room, "join", "桌主")
        player, _, _ = await self.invite(link)
        second = await self.action(player, room, "join", "玩家")
        await self.action(host, room, "approve", second["mine"])
        current = await self.action(host, room, "start")
        browsers = {first["mine"]: host, second["mine"]: player}
        while True:
            turn = current["game"]["turn"]
            current = await self.action(browsers[turn], room, "fold")
            if current["phase"] == "finished":
                break
            self.assertEqual(await self.pending(), [])
            current = await self.action(host, room, "next")

        await self.restart_bridge()
        items = await self.pending()
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["group_id"], self.WECHAT["group"])
        for key in ("platform_type", "platform_id", "bot"):
            self.assertEqual(item[key], self.WECHAT[key])
        self.assertIn("德州", item["text"])
        self.assertIn("5 手", item["text"])
        self.assertNotIn("cards", item["text"])
        self.assertEqual(item["room_id"], room.split("/")[2])
        self.assertTrue(await self.mark(item["room_id"], "claim"))
        await self.restart_bridge()
        self.assertEqual(await self.pending(), [])
        self.assertFalse(await self.mark(item["room_id"], "claim"))
        self.assertTrue(await self.mark(item["room_id"], "sent"))
        self.assertFalse(await self.mark(item["room_id"], "sent"))
        self.assertEqual(await self.pending(), [])
        self.assertNotEqual(await self.open(self.WECHAT), link)

    async def test_host_can_close_a_stalled_game_without_settlement(self):
        link = await self.open(self.WECHAT)
        host, room, _ = await self.invite(link)
        await self.action(host, room, "join", "桌主")
        guest, _, _ = await self.invite(link)
        joined = await self.action(guest, room, "join", "玩家")
        await self.action(host, room, "approve", joined["mine"])
        started = await self.action(host, room, "start")

        await self.action(guest, room, "close", status=400)
        self.assertEqual((await self.state(host, room))["phase"], "playing")
        self.assertIsNone(await self.open(self.WECHAT))
        closed = await self.action(host, room, "close")
        self.assertEqual(closed["phase"], "closed")
        self.assertIsNone(closed["game"])
        self.assertEqual(closed["revision"], started["revision"] + 1)
        self.assertIsNone((await self.state(guest, room))["game"])
        self.assertEqual(await self.pending(), [])

        replacement_link = await self.open(self.WECHAT)
        self.assertTrue(replacement_link)
        self.assertNotEqual(replacement_link, link)
        _, replacement_room, _ = await self.invite(replacement_link, host)
        self.assertNotEqual(replacement_room, room)
        self.assertEqual((await self.state(host, replacement_room))["phase"], "lobby")

    async def test_simulated_tables_through_https(self):
        cases = [("holdem", None, 8), ("sichuan", None, 4), ("kingdoms", None, 4)]
        self.assertEqual(await self.pending(), [])
        for offset, (kind, mode, size) in enumerate(cases):
            with self.subTest(kind=kind, mode=mode):
                platform = self.QQ if offset % 2 == 0 else self.WECHAT
                link = await self.open(platform | {"kind": kind})
                host, room, token = await self.invite(link)
                initial = await self.action(host, room, "join", "桌主")
                browsers = {initial["mine"]: host}
                if kind == "kingdoms" and mode is None and initial.get("modes"):
                    self.assertLessEqual({"blood_battle", "chengtian"}, {option["id"] for option in initial["modes"]})
                    cases.extend(
                        ("kingdoms", option["id"], option["capacity"])
                        for option in initial["modes"] if option["id"] != initial["mode"]
                    )
                    size = initial["settings"]["capacity"]
                if mode is not None:
                    selected = await self.action(host, room, "mode", mode)
                    self.assertEqual(selected["mode"], mode)
                await self.action(host, room, "start", status=400)
                candidates = [await self.invite(link) for _ in range(size - 1)]
                # Two sessions arrived simultaneously. The stale write is rejected
                # while keeping the successful request available to the host.
                for guest_offset in range(0, len(candidates), 2):
                    pair = candidates[guest_offset:guest_offset + 2]
                    rev = (await self.state(host, room))["revision"]
                    results = await asyncio.gather(*(
                        self.submit(guest, room, "join", f"好友{guest_offset + index + 1}", revision=rev)
                        for index, (guest, _, _) in enumerate(pair)
                    ))
                    self.assertEqual(sorted(code for code, _ in results), [200] if len(pair) == 1 else [200, 409])
                    for index, ((guest, _, _), (code, state)) in enumerate(zip(pair, results)):
                        if code == 409:
                            state = await self.action(guest, room, "join", f"好友{guest_offset + index + 1}")
                        browsers[state["mine"]] = guest
                        await self.action(host, room, "approve", state["mine"])
                if size > 2 and any(option["id"] == "duel" for option in initial.get("modes", [])):
                    await self.action(host, room, "mode", "duel", status=400)
                started = await self.action(host, room, "start")
                self.assertEqual(len(started["players"]), size)
                watcher, _, _ = await self.invite(link)
                final, moves = await self.finish_simulation(kind, link, room, host, token, watcher, browsers, offset)
                self.assertTrue(final["game"]["result"])
                entries = await self.pending()
                self.assertEqual(len(entries), 1)
                result = entries[0]
                self.assertEqual((result["group_id"], result["room_id"]), (platform["group"], final["room"]))
                self.assertNotRegex(result["text"], r"p[a-f0-9]{16}")
                claims = await asyncio.gather(*(self.mark(final["room"], "claim") for _ in range(2)))
                self.assertEqual(sorted(claims), [False, True])
                self.assertTrue(await self.mark(final["room"], "sent"))
                self.assertEqual(await self.pending(), [])
                print(f"HTTPS simulation: {kind}/{mode or 'default'}, {size} seats, {moves} legal moves")

    @staticmethod
    def choose_tournament_option(rng, game, progress):
        choices = game["actions"]
        by_id = defaultdict(list)
        for choice in choices:
            by_id[choice["id"]].append(choice)
        if "select_active" in by_id:
            return min(by_id["select_active"], key=lambda choice: choice["general"]["hp"])
        if "select_alliance" in by_id:
            single = not progress["select_alliance"]
            return next(choice for choice in by_id["select_alliance"] if (choice["value"] == "single") == single)
        if game["phase"] == "ct_compose":
            if "ct_fill" in by_id and sum(card["count"] for card in game["tournament"]["your_roster"]) < 39:
                return by_id["ct_fill"][0]
            if not progress["remove_general"] and "remove_general" in by_id:
                return rng.choice(by_id["remove_general"])
            return rng.choice(by_id["confirm_roster"] or by_id["add_general"])
        if game["phase"] != "play":
            # Choose failures in response windows to reach actual defeats and
            # reserve replacements, not a turn-limit draw.
            for action in ("take_damage", "pass", "fire_pay", "skip_fire"):
                if action in by_id:
                    return rng.choice(by_id[action])
            return rng.choice(choices)
        if game["mode"] == "chengtian":
            if not progress["ct_use_skill"] and "ct_use_skill" in by_id:
                return rng.choice(by_id["ct_use_skill"])
            if not progress["trim_general"]:
                return by_id["end"][0]
            if "rotate_general" in by_id and not progress["rotate_general"]:
                return rng.choice(by_id["rotate_general"])
        by_seat = {seat["id"]: seat for seat in game["seats"]}
        enemies = {seat["id"] for seat in game["seats"] if seat["team"] != game["your_team"]}
        attacks = [choice for choice in choices if choice.get("target") in enemies
                   and (choice["id"] == "attack" or (choice["id"] == "trick"
                        and card_kind(choice["value"]["card"]) in {"fire_attack", "duel"}))]
        if attacks:
            if "wine" in by_id and any(choice["id"] == "attack" for choice in attacks):
                return by_id["wine"][0]
            return min(attacks, key=lambda choice: by_seat[choice["target"]]["hp"])
        for action in ("insight", "equip"):
            if action in by_id:
                return rng.choice(by_id[action])
        weapons = [choice for choice in by_id.get("equip_card", []) if card_kind(choice["value"]) in WEAPONS]
        return rng.choice(weapons or by_id["end"])

    def assert_tournament_result(self, game, progress):
        if game.get("mode") == "blood_battle":
            projected = game["tournament"]
            self.assertEqual(progress["select_active"], 6)
            self.assertEqual(progress["nominate_general"], 12)
            self.assertEqual(progress["select_reserve"], 6)
            self.assertGreaterEqual(progress["revive_general"], 3)
            self.assertEqual(max(projected["score"].values()), 4)
            self.assertEqual(len(game["winners"]), 3)
        elif game.get("mode") == "chengtian":
            self.assertEqual(progress["select_commander"], 2)
            self.assertEqual(progress["select_alliance"], 2)
            self.assertEqual(progress["confirm_roster"], 2)
            self.assertEqual(progress["remove_general"], 1)
            self.assertEqual(progress["add_general"], 1)
            self.assertGreaterEqual(progress["trim_general"], 1)
            self.assertEqual(len(game["winners"]), 1)

    def assert_kingdoms_inventory(self, room_id, mode):
        with self.store.connect() as db:
            game = json.loads(db.execute("SELECT game FROM rooms WHERE id=?", (room_id,)).fetchone()["game"])
        cards = Counter(game["deck"] + game["discard"])
        if invasion := game.get("invasion_card"):
            cards[invasion["card"]] += 1
        for seat in game["seats"]:
            cards.update(seat["hand"])
            cards.update(seat.get("judgement", []))
            cards.update(seat[slot] for slot in ("weapon", "armor", "guard", "steed") if seat.get(slot))
            for pile in seat.get("general_cards", {}).values():
                cards.update(pile)
        pending = game["pending"] or {}
        cards.update(pending.get("cards", []))
        cards.update(game.get("harvest_cards", []))
        card = pending.get("card")
        if card and game["phase"] == "counter" and (pending.get("virtual_kind") or card_kind(card)) in DELAYED:
            cards[card] += 1
        from wind_fire_rules import bluff_suspended_judgements, judgment_held_cards
        cards.update(bluff_suspended_judgements(game))
        cards.update(judgment_held_cards(game))
        from kingdoms import _held_divine_counters
        cards.update(_held_divine_counters(game))
        expected = Counter(tournament.DECK if mode == "chengtian" else dict.fromkeys(MANIFEST, 1))
        self.assertEqual(cards, expected, (mode, game["phase"], game["turn_number"]))
        if mode == "chengtian":
            for entry in game["tournament"]["entries"].values():
                if "army" in entry:
                    self.assertEqual(sum(len(entry[zone]) for zone in ("army", "field", "retired")), 40)

    @staticmethod
    def choose_option(rng, kind, actor):
        choices = actor["game"]["actions"]
        desired = []
        if kind == "holdem" and rng.random() < .76:
            desired = [choice for choice in choices if choice["id"] in ("check", "call", "next")]
        elif kind == "sichuan":
            if actor["game"]["phase"] == "choose_missing":
                hand = actor["game"]["my_hand"]
                return min(choices, key=lambda choice: sum(tile.startswith(choice["value"]) for tile in hand))
            desired = [choice for choice in choices if choice["id"] in ("exchange_submit", "self_win", "win")]
            choices = [choice for choice in choices if choice["id"] != "exchange_remove"]
        option = rng.choice(desired or choices)
        if option["id"] == "raise":
            option = option | {"value": rng.randint(option["min"], option["max"])}
        return option

    async def finish_simulation(self, kind, link, room, host, token, watcher, browsers, offset):
        rng = random.Random(72 + offset)
        moves = 0
        progress = Counter()
        while moves < 2000:
            view = await self.state(watcher, room)
            game = view["game"]
            self.assertEqual(game["actions"], [])
            if kind == "kingdoms" and (moves in (0, 1) or moves % 19 == 0 or view["phase"] == "finished"):
                self.assert_kingdoms_inventory(view["room"], game["mode"])
            if view["phase"] == "finished":
                self.assert_tournament_result(game, progress)
                return view, moves
            turn = game["turn"]
            if moves == 1:
                await self.restart_public()
                await self.restart_bridge()
                _, same_room, same_token = await self.invite(link, host)
                self.assertEqual((same_room, same_token), (room, token))
                self.assertEqual(await self.state(watcher, room), view)
            if moves < 3 or moves % 19 == 0:
                self.assertEqual(await self.pending(), [])
            if kind == "holdem":
                self.assertNotIn("deck", game)
                self.assertEqual(game["pot"] + sum(seat["stack"] for seat in game["players"]), 1000 * len(browsers))
                self.assertTrue(all(card is None for seat in game["players"] for card in seat["cards"]))
            elif kind == "sichuan":
                self.assertEqual(game["my_hand"], [])
                self.assertEqual(game["exchange_draft"], [])
                self.assertEqual(sum(seat["points"] for seat in game["seats"]), 0)
                if game["phase"] == "choose_missing":
                    actors = await asyncio.gather(*(self.state(client, room) for client in browsers.values()))
                    requests = [
                        (browsers[actor["mine"]], self.choose_option(rng, kind, actor)["value"])
                        for actor in actors
                    ]
                    # Independent selections can share a revision, but a
                    # duplicated selection for the same seat must be rejected.
                    responses = await asyncio.gather(*(
                        self.submit(client, room, "choose_missing", suit, revision=view["revision"])
                        for client, suit in [*requests, requests[0]]
                    ))
                    self.assertEqual(sorted(code for code, _ in responses), [200] * len(requests) + [409])
                    moves += len(requests)
                    continue
            elif kind == "kingdoms":
                self.assertEqual(game["hand"], [])
                self.assertTrue(all("hand" not in seat for seat in game["seats"]))
                if game.get("mode") == "blood_battle":
                    self.assertEqual(game["tournament"]["your_reserves"], [])
                elif game.get("mode") == "chengtian":
                    self.assertEqual(game["tournament"]["your_roster"], [])
                    limit = 7 if game["phase"] == "ct_trim" else 5
                    self.assertTrue(all(len(army["field"]) <= limit for army in game["tournament"]["armies"]))
            if turn in browsers:
                actor = await self.state(browsers[turn], room)
            else:
                states = await asyncio.gather(*(self.state(client, room) for client in browsers.values()))
                playable = [state for state in states if state["game"]["actions"]]
                self.assertTrue(playable, f"{kind} stalled at {game['phase']}")
                actor = rng.choice(playable)
            self.assertTrue(actor["game"]["actions"], f"{kind} stalled at {game['phase']} for {turn}")
            if game.get("mode") in {"blood_battle", "chengtian"}:
                options = actor["game"]
                option = self.choose_tournament_option(rng, options, progress)
                self.assertIn(option, options["actions"])
                if game["phase"] in {"draft_reserve", "reinforce_reserve"}:
                    self.assertEqual(options["your_role"], "主帅")
                    self.assertEqual(len(options["tournament"]["your_reserves"]),
                                     game["tournament"]["reserve_counts"][options["your_team"]])
                if game["phase"] == "ct_compose":
                    self.assertEqual(sum(card["count"] for card in options["tournament"]["your_roster"]),
                                     {"ct_fill": 1, "add_general": 39}.get(option["id"], 40))
                progress[option["id"]] += 1
            else:
                option = self.choose_option(rng, kind, actor)
            client = browsers[actor["mine"]]
            if moves == 0:
                responses = await asyncio.gather(*(
                    self.submit(client, room, option["id"], option.get("value"), revision=actor["revision"])
                    for _ in range(2)
                ))
                self.assertEqual(sorted(code for code, _ in responses), [200, 409])
            else:
                await self.action(client, room, option["id"], option.get("value"), revision=actor["revision"])
            moves += 1
        self.fail(f"{kind} exceeded 2000 moves")


if __name__ == "__main__":
    unittest.main()
