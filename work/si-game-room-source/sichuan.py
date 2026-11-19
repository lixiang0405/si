"""Four-player Sichuan mahjong with blood-battle rules and game-only points."""

from collections import Counter
from functools import lru_cache
from random import SystemRandom


MIN_PLAYERS = MAX_PLAYERS = 4
SUITS = {"m": "万", "p": "筒", "s": "条"}
TILES = tuple(f"{suit}{rank}" for suit in SUITS for rank in range(1, 10))
NUMERALS = "〇一二三四五六七八九"
PHASE_NAMES = {
    "exchange": "换三张",
    "choose_missing": "定缺",
    "discard": "摸打轮次",
    "react": "响应出牌",
    "finished": "本局结算",
}
RULES = (
    "四人血战到底。换三张须同花色，随后定缺。"
    "先打缺门，支持碰、明杠、暗杠、补杠、一炮多响和抢补杠胡。"
    "三家胡牌或牌墙打尽结束。平胡一分、七对两分，"
    "自摸由其他未胡玩家付分，点炮由出牌者付分。"
    "直杠由出牌者付两分，暗杠每家两分，补杠每家一分。"
    "加番、自摸加底、查大叫和查花猪留给进阶规则。"
)


def tile_label(tile):
    return NUMERALS[int(tile[1])] + SUITS[tile[0]]


def create(players):
    if len(players) != 4 or len(set(players)) != 4 or any(not isinstance(player, str) or not player for player in players):
        raise ValueError("川麻需要四名不同的玩家")
    random = SystemRandom()
    wall = list(TILES) * 4
    random.shuffle(wall)
    seats = {}
    for player in players:
        hand = [wall.pop() for _ in range(13)]
        seats[player] = {"hand": hand, "melds": [], "discards": [], "points": 0, "missing": None, "won": False}
    return {
        "order": list(players),
        "seats": seats,
        "dealer": players[0],
        "wall": wall,
        "exchange_direction": random.choice((-1, 1, 2)),
        "drafts": {player: [] for player in players},
        "submitted": [],
        "phase": "exchange",
        "turn": None,
        "drawn_tile": None,
        "pending": None,
        "last_tile": None,
        "last_event": "请从同一花色选出三张牌。",
        "winners": [],
        "finished": False,
        "result": "",
    }


@lru_cache(maxsize=8192)
def _is_set_partition(counts):
    first = next((index for index, count in enumerate(counts) if count), None)
    if first is None:
        return True
    remainder = list(counts)
    if remainder[first] >= 3:
        remainder[first] -= 3
        if _is_set_partition(tuple(remainder)):
            return True
        remainder[first] += 3
    if first % 9 <= 6 and remainder[first + 1] and remainder[first + 2]:
        for index in (first, first + 1, first + 2):
            remainder[index] -= 1
        return _is_set_partition(tuple(remainder))
    return False


def _win_points(seat, extra=None):
    hand = seat["hand"] + ([extra] if extra else [])
    missing = seat["missing"]
    melds = seat["melds"]
    if (missing is None or len(hand) != 14 - 3 * len(melds)
            or any(tile[0] == missing for tile in hand)
            or any(meld["tile"][0] == missing for meld in melds)):
        return 0
    tiles = Counter(hand)
    if not melds and all(count % 2 == 0 for count in tiles.values()):
        return 2
    counts = [tiles[tile] for tile in TILES]
    for index, count in enumerate(counts):
        if count >= 2:
            counts[index] -= 2
            if _is_set_partition(tuple(counts)):
                return 1
            counts[index] += 2
    return 0


def _action(name, label, value=None):
    choice = {"id": name, "label": label}
    if value is not None:
        choice["value"] = value
    return choice


def _actions(game, player):
    seat = game["seats"].get(player)
    if game["finished"] or seat is None or seat["won"]:
        return []
    phase = game["phase"]
    if phase == "exchange" and player not in game["submitted"]:
        draft = game["drafts"][player]
        selections = [_action("exchange_remove", f"收回 {tile_label(tile)}", tile) for tile in sorted(set(draft))]
        if len(draft) == 3:
            return [_action("exchange_submit", "确认换牌"), *selections]
        available = Counter(seat["hand"]) - Counter(draft)
        suit_counts = Counter(tile[0] for tile in seat["hand"])
        return [
            *(_action("exchange_add", f"选 {tile_label(tile)}", tile) for tile in sorted(available)
              if suit_counts[tile[0]] >= 3 and (not draft or tile[0] == draft[0][0])),
            *selections,
        ]
    if phase == "choose_missing" and seat["missing"] is None:
        return [_action("choose_missing", f"缺{name}", suit) for suit, name in SUITS.items()]
    if phase == "react":
        pending = game["pending"]
        if player in pending["responses"] or player not in pending["options"]:
            return []
        choices = pending["options"][player]
        labels = {"win": "胡", "pong": "碰", "kong": "直杠", "pass": "过"}
        return [_action(choice, labels[choice]) for choice in choices]
    if phase != "discard" or game["turn"] != player:
        return []
    counts = Counter(seat["hand"])
    missing_tiles = [tile for tile in counts if tile[0] == seat["missing"]]
    if missing_tiles:
        return [_action("discard", f"打 {tile_label(tile)}", tile) for tile in sorted(missing_tiles)]
    choices = []
    if game["drawn_tile"] and _win_points(seat):
        choices.append(_action("self_win", "自摸胡牌"))
    if game["wall"]:
        choices.extend(
            _action("concealed_kong", f"暗杠 {tile_label(tile)}", tile)
            for tile, count in sorted(counts.items()) if count == 4
        )
        choices.extend(
            _action("added_kong", f"补杠 {tile_label(meld['tile'])}", meld["tile"])
            for meld in seat["melds"] if meld["kind"] == "pong" and counts[meld["tile"]]
        )
    return [*choices, *(_action("discard", f"打 {tile_label(tile)}", tile) for tile in sorted(counts))]


def _clockwise(game, player):
    order = game["order"]
    start = order.index(player) + 1
    return order[start:] + order[:start - 1]


def _transfer(game, payer, recipient, amount):
    game["seats"][payer]["points"] -= amount
    game["seats"][recipient]["points"] += amount


def _finish(game, reason):
    game["phase"] = "finished"
    game["finished"] = True
    game["turn"] = None
    game["pending"] = None
    game["drawn_tile"] = None
    game["last_event"] = reason
    # The lobby maps seat IDs to nicknames when rendering the final result.
    game["result"] = f"川麻本局结束，{reason}，{len(game['winners'])} 人胡牌。"


def _draw(game, player):
    if len(game["winners"]) == 3:
        _finish(game, "三家已胡牌")
    elif not game["wall"]:
        _finish(game, "牌墙已打尽")
    else:
        tile = game["wall"].pop()
        game["seats"][player]["hand"].append(tile)
        game["turn"] = player
        game["drawn_tile"] = tile
        game["phase"] = "discard"


def _advance(game, player):
    if len(game["winners"]) == 3:
        _finish(game, "三家已胡牌")
        return
    following = next(p for p in _clockwise(game, player) if not game["seats"][p]["won"])
    _draw(game, following)


def _settle_win(game, winner, source, tile, kind):
    seat = game["seats"][winner]
    amount = _win_points(seat, tile if source else None)
    payers = [source] if source else [p for p in _clockwise(game, winner) if not game["seats"][p]["won"]]
    for payer in payers:
        _transfer(game, payer, winner, amount)
    seat["won"] = True
    game["winners"].append({
        "id": winner, "from": source, "tile": tile, "kind": kind,
        "pattern": "七对" if amount == 2 else "平胡", "points": amount * len(payers),
    })


def _apply_kong(game, player, tile, kind):
    seat = game["seats"][player]
    if kind == "added_kong":
        seat["hand"].remove(tile)
        meld = next(meld for meld in seat["melds"] if meld["kind"] == "pong" and meld["tile"] == tile)
        meld["kind"] = "added_kong"
    else:
        for _ in range(4):
            seat["hand"].remove(tile)
        seat["melds"].append({"tile": tile, "kind": kind})
    for payer in _clockwise(game, player):
        if not game["seats"][payer]["won"]:
            _transfer(game, payer, player, 2 if kind == "concealed_kong" else 1)
    game["last_event"] = "暗杠后补牌。" if kind == "concealed_kong" else f"补杠 {tile_label(tile)}，杠后补牌。"
    _draw(game, player)


def _start_reactions(game, source, tile, kind):
    options = {}
    for player in _clockwise(game, source):
        seat = game["seats"][player]
        if seat["won"] or tile[0] == seat["missing"]:
            continue
        choices = ["win"] if _win_points(seat, tile) else []
        if kind == "discard" and game["wall"]:
            count = seat["hand"].count(tile)
            if count >= 3:
                choices.append("kong")
            if count >= 2:
                choices.append("pong")
        if choices:
            options[player] = [*choices, "pass"]
    if options:
        game["pending"] = {"source": source, "tile": tile, "kind": kind, "options": options, "responses": {}}
        game["phase"] = "react"
        game["turn"] = None
    elif kind == "added_kong":
        _apply_kong(game, source, tile, kind)
    else:
        _advance(game, source)


def _settle_reactions(game, pending):
    source, tile, kind = pending["source"], pending["tile"], pending["kind"]
    responses = pending["responses"]
    game["pending"] = None
    winners = [p for p in _clockwise(game, source) if responses.get(p) == "win"]
    if winners:
        if kind == "added_kong":
            game["seats"][source]["hand"].remove(tile)
            game["seats"][source]["discards"].append(tile)
        for player in winners:
            _settle_win(game, player, source, tile, "rob_kong" if kind == "added_kong" else "discard")
        game["last_event"] = f"{len(winners)} 人胡 {tile_label(tile)}。"
        _advance(game, source)
        return
    if kind == "added_kong":
        _apply_kong(game, source, tile, kind)
        return
    claim = next((p for p in _clockwise(game, source) if responses.get(p) in ("pong", "kong")), None)
    if claim is None:
        _advance(game, source)
        return
    game["seats"][source]["discards"].pop()
    seat = game["seats"][claim]
    kong = responses[claim] == "kong"
    for _ in range(3 if kong else 2):
        seat["hand"].remove(tile)
    seat["melds"].append({"tile": tile, "kind": "exposed_kong" if kong else "pong"})
    if kong:
        _transfer(game, source, claim, 2)
        game["last_event"] = f"直杠 {tile_label(tile)}，杠后补牌。"
        _draw(game, claim)
    else:
        game["last_event"] = f"碰 {tile_label(tile)}。"
        game["phase"] = "discard"
        game["turn"] = claim
        game["drawn_tile"] = None


def play(game, player, action, value=None):
    if not any(choice["id"] == action and choice.get("value") == value for choice in _actions(game, player)):
        raise ValueError("这个操作当前不可用")
    phase = game["phase"]
    if phase == "exchange":
        draft = game["drafts"][player]
        if action == "exchange_add":
            draft.append(value)
        elif action == "exchange_remove":
            draft.remove(value)
        else:
            game["submitted"].append(player)
            if len(game["submitted"]) == 4:
                order = game["order"]
                for sender in order:
                    for tile in game["drafts"][sender]:
                        game["seats"][sender]["hand"].remove(tile)
                for index, sender in enumerate(order):
                    receiver = order[(index + game["exchange_direction"]) % 4]
                    game["seats"][receiver]["hand"].extend(game["drafts"][sender])
                game["phase"] = "choose_missing"
                game["last_event"] = "换牌完成，请选择一门花色作为缺门。"
        return
    if phase == "choose_missing":
        game["seats"][player]["missing"] = value
        if all(seat["missing"] for seat in game["seats"].values()):
            game["last_event"] = "定缺完成，庄家摸牌。"
            _draw(game, game["dealer"])
        return
    if phase == "react":
        pending = game["pending"]
        pending["responses"][player] = action
        if len(pending["responses"]) == len(pending["options"]):
            _settle_reactions(game, pending)
        return
    if action == "self_win":
        _settle_win(game, player, None, game["drawn_tile"], "self")
        game["last_event"] = "一人自摸胡牌。"
        _advance(game, player)
    elif action == "concealed_kong":
        _apply_kong(game, player, value, action)
    elif action == "added_kong":
        game["drawn_tile"] = None
        game["last_tile"] = {"tile": value, "from": player, "kind": "added_kong"}
        game["last_event"] = f"申请补杠 {tile_label(value)}。"
        _start_reactions(game, player, value, action)
    else:
        seat = game["seats"][player]
        seat["hand"].remove(value)
        seat["discards"].append(value)
        game["drawn_tile"] = None
        game["last_tile"] = {"tile": value, "from": player, "kind": "discard"}
        game["last_event"] = f"打出 {tile_label(value)}。"
        _start_reactions(game, player, value, "discard")


def view(game, player=None):
    phase = game["phase"]
    public_missing = phase not in ("exchange", "choose_missing")
    own_seat = game["seats"].get(player)
    seats = []
    for key in game["order"]:
        seat = game["seats"][key]
        seats.append({
            "id": key,
            "points": seat["points"],
            "hand_count": len(seat["hand"]),
            "won": seat["won"],
            "missing": seat["missing"] if public_missing or key == player else None,
            "ready": key in game["submitted"] if phase == "exchange" else seat["missing"] is not None,
            "melds": [
                {"kind": meld["kind"], "tile": meld["tile"] if key == player or meld["kind"] != "concealed_kong" else None}
                for meld in seat["melds"]
            ],
            "discards": list(seat["discards"]),
        })
    pending = game["pending"]
    return {
        "game": "sichuan",
        "phase": phase,
        "phase_label": PHASE_NAMES[phase],
        "turn": game["turn"],
        "dealer": game["dealer"],
        "seats": seats,
        "wall_count": len(game["wall"]),
        "my_hand": sorted(own_seat["hand"]) if own_seat else [],
        "drawn_tile": game["drawn_tile"] if game["turn"] == player else None,
        "exchange_draft": list(game["drafts"].get(player, [])) if phase == "exchange" else [],
        "exchange_direction": None if phase == "exchange" else game["exchange_direction"],
        "waiting": len(pending["options"]) - len(pending["responses"]) if pending else 0,
        "last_tile": dict(game["last_tile"]) if game["last_tile"] else None,
        "last_event": game["last_event"],
        "winners": [dict(winner) for winner in game["winners"]],
        "actions": _actions(game, player),
        "finished": game["finished"],
        "result": game["result"],
        "rules": RULES,
    }
