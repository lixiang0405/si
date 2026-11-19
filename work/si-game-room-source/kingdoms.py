"""Browser Three Kingdoms tables, with one rules engine for six modes."""

from collections import Counter
from itertools import combinations
from random import SystemRandom
import sys

from card_catalog import (
    BASIC, CARDS, CARD_DETAILS as MANIFEST, DECK, DELAYED, NATURES, SLOTS, SLASHES, SUITS, TRICKS, WEAPONS,
    kind as _kind, name_with_rank as _physical_label, rank as _rank, suit as _suit,
)
import general_expansions as expansion
import divine_expansion_rules as divine
import forest_mountain_rules as forest
import tournament
import wind_fire_rules as wind_fire

ENGINE = sys.modules[__name__]


TURN_LIMIT = 120
LORD, LOYALIST, REBEL, SPY = "主公", "忠臣", "反贼", "内奸"
WARM, COLD = "暖方", "冷方"
LANDLORD, FARMER = "地主", "农民"
FACTIONS = ("魏", "蜀", "吴", "群")

MODES = {
    "identity": {"name": "身份场", "minimum": 4, "capacity": 8, "description": "经典 5／8 人身份分配，也支持 4、6、7 人。"},
    "duel": {"name": "单挑 1v1", "minimum": 2, "capacity": 2, "exact": 2, "description": "两位武将正面对决。"},
    "pair": {"name": "双人团战 2v2", "minimum": 4, "capacity": 4, "exact": 4, "description": "一四联手对阵二三。"},
    "team": {"name": "主帅团战 3v3", "minimum": 6, "capacity": 6, "exact": 6, "description": "每方一名主帅，两名前锋。"},
    "factions": {"name": "国战轻量", "minimum": 4, "capacity": 8, "description": "同势力双将，按时机明置并结盟。"},
    "landlord": {"name": "斗地主", "minimum": 3, "capacity": 3, "exact": 3, "description": "三人竞价选地主，两名农民联手。"},
    **tournament.MODES,
}

TILTED = DELAYED
CHENG_CARD_ACTIONS = {"attack", "dodge", "respond_strike", "borrow_strike", "follow_strike", "wine", "heal",
                      "insight", "trick", "rotate_general", "counterspell", "rescue", "rescue_wine", "equip_card", "equip"}

SKILLS = {
    "strategy": ("谋略", "每回合首次使用即时锦囊时多摸一张。"),
    "draw": ("筹算", "每次摸牌阶段多摸一张。"),
    "fury": ("追击", "每回合可以多使用一张杀。"),
    "switch": ("换锋", "杀可以当闪使用，闪可以当杀使用。"),
    "recover": ("坚忍", "受到伤害后摸一张。"),
    "limit": ("蓄势", "弃牌阶段的手牌上限加一。"),
    "cycle": ("调度", "出牌阶段可弃一张手牌换摸两张，每回合一次。"),
    "aid": ("援护", "出牌阶段可随机弃一张手牌，给一位受伤角色回复一点体力，每回合一次。"),
    **expansion.SKILLS,
}
# Historical names and original, deliberately compact skills.
GENERALS = {
    "cao_cao": ("曹操", "魏", 4, "recover"), "cao_ren": ("曹仁", "魏", 4, "limit"),
    "xun_yu": ("荀彧", "魏", 3, "draw"), "guo_jia": ("郭嘉", "魏", 3, "cycle"),
    "xiahou_dun": ("夏侯惇", "魏", 4, "fury"), "zhen_ji": ("甄宓", "魏", 3, "strategy"),
    "liu_bei": ("刘备", "蜀", 4, "aid"), "zhao_yun": ("赵云", "蜀", 4, "switch"),
    "zhang_fei": ("张飞", "蜀", 4, "fury"), "ma_chao": ("马超", "蜀", 4, "limit"),
    "zhuge_liang": ("诸葛亮", "蜀", 3, "strategy"), "huang_yueying": ("黄月英", "蜀", 3, "draw"),
    "sun_quan": ("孙权", "吴", 4, "cycle"), "zhou_yu": ("周瑜", "吴", 3, "draw"),
    "sun_shangxiang": ("孙尚香", "吴", 3, "aid"), "lu_xun": ("陆逊", "吴", 3, "strategy"),
    "huang_gai": ("黄盖", "吴", 4, "recover"), "taishi_ci": ("太史慈", "吴", 4, "fury"),
    "yuan_shao": ("袁绍", "群", 4, "limit"), "hua_tuo": ("华佗", "群", 3, "aid"),
    "lu_bu": ("吕布", "群", 4, "fury"), "diao_chan": ("貂蝉", "群", 3, "cycle"),
    "zhang_jiao": ("张角", "群", 3, "strategy"), "gong_sunzan": ("公孙瓒", "群", 4, "switch"),
}
BASE_GENERALS = frozenset(GENERALS)
for key, detail in expansion.GENERAL_CATALOG.items():
    GENERALS[key] = (detail["name"], detail["faction"], detail["hp"], detail["skills"][0] if detail["skills"] else None)
GENERALS.update(tournament.units.GENERALS)

RULES = (
    "依次判定、摸牌、出牌和弃牌。每回合通常摸两张牌，可使用一张杀。",
    "锦囊可以被无懈抵消，决斗、南蛮和万箭分别要求出杀或出闪。",
    "体力降到零以下时，从濒死者开始依次救援。手牌上限通常等于当前体力。",
    "标准 104 张、EX 4 张与军争 52 张同桌。判定翻开真实牌堆顶牌。",
)
MODE_RULES = {
    "identity": ("主公公开，其余身份暗置。主忠消灭反贼和内奸获胜，反贼击败主公获胜，内奸要独存。",),
    "duel": ("两名玩家各自单独参战，存活角色获胜。",),
    "pair": ("座次一与四同队，二与三同队。消灭另一队获胜。",),
    "team": ("每方一名主帅和两名前锋，按座次轮转。击败敌方主帅即可取胜。",),
    "factions": ("每人两名同势力武将，自己的回合可明置。先明置者摸两张牌；亮出第二将后摸一张。",
                 "同势力角色结盟；超过半数后继续加入者成为野心家。最后存活的势力或野心家获胜。"),
    "landlord": ("依座次竞价选地主，全员放弃时首位成为地主。地主多一点体力、每回合多摸一张且可多出一张杀。",
                 "地主和农民互相击败对方；一名农民出局时另一名摸两张。"),
    **tournament.RULES,
}


def _mode(game):
    return game.get("mode", "identity")


def _seat(game, player):
    return next((seat for seat in game["seats"] if seat["id"] == player), None)


def _live(game):
    return [seat["id"] for seat in game["seats"] if seat["alive"]]


def _rotate(game, anchor):
    seats = [seat["id"] for seat in game["seats"]]
    index = seats.index(anchor)
    return [player for player in seats[index:] + seats[:index] if _seat(game, player)["alive"]]


def _after(game, anchor):
    seats = [seat["id"] for seat in game["seats"]]
    return _rotate(game, seats[(seats.index(anchor) + 1) % len(seats)])


def _general_info(key):
    if key in tournament.units.BY_KEY:
        return tournament.units.info(key)
    name, faction, _, skill = GENERALS[key]
    detail = expansion.GENERAL_CATALOG.get(key, {})
    skills = [SKILLS[ability] for ability in detail.get("skills", (skill,)) if ability in SKILLS]
    return {"id": key, "name": name, "faction": faction, "skill": " · ".join(title for title, _ in skills),
            "description": " ".join(f"{title}｜{text}" for title, text in skills),
            "pack": detail.get("pack", "standard"), "omitted": list(detail.get("omitted", ()))}


def _skills(game, seat):
    if seat.get("skill_disabled"):
        return set()
    avatars = expansion.avatar_skills(seat) | forest.derived_skills(seat)
    if _mode(game) == tournament.CHENG:
        return set(tournament.earned_skills(game, seat)) | avatars
    keys = seat.get("generals", []) if _mode(game) != "factions" else seat.get("revealed", [])
    return avatars | {skill for key in keys
                      for skill in expansion.GENERAL_CATALOG.get(key, {}).get("skills", (GENERALS[key][3],))}


def _pool(packs):
    return {key: detail for key, detail in GENERALS.items()
            if key in BASE_GENERALS or expansion.GENERAL_CATALOG.get(key, {}).get("pack") in packs}


def _general_faction(seat):
    key = next(iter(seat.get("generals", ())), None)
    return GENERALS[key][1] if key else None


def _general_assignments(mode, size, random, available):
    roster = list(available)
    if mode != "factions":
        by_name = {}
        random.shuffle(roster)
        for key in roster:
            by_name.setdefault(available[key][0], key)
        return [[key] for key in random.sample(list(by_name.values()), size)]
    pairs = []
    for faction in FACTIONS:
        same = [key for key in roster if GENERALS[key][1] == faction]
        random.shuffle(same)
        pairs.extend([same[index:index + 2] for index in range(0, len(same) - 1, 2)])
    return random.sample(pairs, size)


def _roles(mode, size, random):
    if mode == "identity":
        loyalists = 2 if size >= 7 else 1
        roles = [LORD, SPY] + [LOYALIST] * loyalists + [REBEL] * (size - loyalists - 2)
        random.shuffle(roles)
        return roles, [None] * size, roles.index(LORD)
    if mode == "pair":
        teams = [WARM, COLD, COLD, WARM]
        return teams, teams, random.randrange(size)
    if mode == "team":
        return ["前锋", "主帅", "前锋"] * 2, [WARM] * 3 + [COLD] * 3, random.choice((1, 4))
    if mode == "duel":
        return ["甲方", "乙方"], ["甲方", "乙方"], random.randrange(size)
    return ["待定"] * size, [None] * size, random.randrange(size)


def create(players: list[str], mode: str = "identity", packs=None) -> dict:
    size = len(players)
    meta = MODES.get(mode) if isinstance(mode, str) else None
    if (not meta or not meta["minimum"] <= size <= meta["capacity"]
            or any(not isinstance(player, str) or not player for player in players)
            or len(set(players)) != size):
        raise ValueError("人数与所选玩法不符，所有玩家须为不同座位。")
    random = SystemRandom()
    packs = list(expansion.PACKS) if packs is None else list(packs)
    if any(pack not in expansion.PACKS for pack in packs):
        raise ValueError("武将包不存在。")
    available = _pool(packs)
    roles, teams, first = _roles(mode, size, random)
    assignments = _general_assignments(mode, size, random, available)
    deck = list(tournament.DECK if mode == tournament.CHENG else DECK)
    random.shuffle(deck)
    seats = []
    for player, role, team, generals in zip(players, roles, teams, assignments):
        hp = sum(GENERALS[key][2] for key in generals) // len(generals)
        if role == LORD and size >= 5:
            hp += 1
        if mode == "factions":
            role = GENERALS[generals[0]][1]
        seats.append({
            "id": player, "role": role, "team": team, "hp": hp, "max_hp": hp, "alive": True,
            "hand": [deck.pop() for _ in range(4)], "generals": generals,
            "revealed": generals.copy() if mode != "factions" else [], "judgement": [],
            **dict.fromkeys(SLOTS.values()),
        })
    game = {
        "kind": "kingdoms", "mode": mode, "seats": seats, "deck": deck, "discard": [],
        "active": players[first], "phase": "play", "pending": None, "queue": [],
        "turn_number": 0, "turn_limit": TURN_LIMIT, "strikes": 0, "used_skills": [], "wine": False,
        "log": [], "finished": False, "result": "", "winners": [],
        "enabled_packs": packs, "general_state": {},
    }
    if tournament.initialize(game, mode, random, available, official=True):
        return game
    for seat in seats:
        expansion.initialize_seat(game, seat, ENGINE)
    if mode == "landlord":
        game["phase"] = "bid"
        game["auction"] = {"order": _rotate(game, game["active"]), "index": 0, "high": 0, "leader": None}
    else:
        _begin_turn(game)
    return game


def _turn(game):
    if game["finished"]:
        return None
    if player := tournament.turn(game) or expansion.current_player(game):
        return player
    phase, pending = game["phase"], game["pending"]
    if phase == "bid":
        auction = game["auction"]
        return auction["order"][auction["index"]]
    if phase in {"dodge", "strike_response", "choice", "fire_reveal", "borrow", "double_sword"}:
        return pending["target"]
    if phase in {"fire_pay", "weapon", "follow_up", "axe_cost", "asset"}:
        return pending["actor"]
    if phase == "counter":
        return pending["current"]
    if phase == "rescue":
        return pending["order"][pending["index"]]
    return game["active"]


def _add_log(game, text, actor=None, target=None):
    game["log"].append({"text": text, "actor": actor, "target": target})
    del game["log"][:-24]


def _take(game):
    if not game["deck"]:
        game["deck"], game["discard"] = game["discard"], []
        SystemRandom().shuffle(game["deck"])
    return game["deck"].pop() if game["deck"] else None


def _draw(game, seat, amount):
    cards = [card for _ in range(amount) if (card := _take(game)) is not None]
    if cards:
        seat["hand"].extend(cards)
        _add_log(game, f"摸了 {len(cards)} 张牌", seat["id"])


def _judge_card(game, seat):
    card = _take(game)
    if card is None:
        return None, None
    game["discard"].append(card)
    suit = expansion.suit(game, seat, card, ENGINE) or SystemRandom().choice(tuple(SUITS.values()))
    rank = _rank(card) or SystemRandom().randint(1, 13)
    _add_log(game, f"判定为{suit}{rank} · {_card_name(card)}", seat["id"])
    return suit, rank


def _judge(game, seat):
    return _judge_card(game, seat)[0]


def _begin_turn(game):
    if divine.start_setup(game, ENGINE) or forest.start_setup(game, ENGINE):
        return
    game.update(turn_number=game["turn_number"] + 1, strikes=0, used_skills=[], wine=False, phase="play",
                played_during_turn=False)
    if _mode(game) == tournament.CHENG:
        game["tournament"]["card_users"] = []
    current = _seat(game, game["active"])
    tournament.begin_turn(game, current)
    _add_log(game, "开始回合", current["id"])
    game["turn_start"] = {"skip_play": False, "skip_draw": False}
    if expansion.start_turn(game, current, ENGINE):
        game.pop("turn_start", None)
        _advance(game, skip_end=True)
        return
    if game["phase"] == "general":
        return
    _next_judgement(game, current)


def _next_judgement(game, current):
    changes = game["turn_start"]
    if wind_fire.before_judgement(game, current, ENGINE) or forest.before_phase(game, current, "judgement", ENGINE):
        return
    unresolved = [card for card in current.get("judgement", ()) if card not in changes.get("judged_chengtian", ())]
    if unresolved and not changes.get("skip_judgement"):
        card = unresolved[-1]
        current["judgement"].remove(card)
        effect = {"actor": current["id"], "target": current["id"], "card": card,
                  "virtual_kind": game.get("virtual_delays", {}).pop(card, None),
                  "judging": True, "negated": False, "passed": []}
        _offer_counter(game, effect)
        return
    if not changes.get("drawn"):
        if forest.before_phase(game, current, "draw", ENGINE):
            return
        changes["drawn"] = True
        if changes["skip_draw"]:
            _add_log(game, "跳过摸牌阶段", current["id"])
        elif divine.before_draw(game, current, ENGINE):
            return
        elif not expansion.replace_draw(game, current, ENGINE):
            amount = 2 + int("draw" in _skills(game, current)) + int(current["role"] == LANDLORD)
            _draw(game, current, expansion.draw_count(game, current, amount, ENGINE))
    _finish_draw(game, current)


def _finish_draw(game, current):
    changes = game["turn_start"]
    if not changes["skip_draw"] and divine.after_draw(game, current, ENGINE):
        return
    if not changes["skip_play"] and forest.before_phase(game, current, "play", ENGINE):
        return
    game.pop("turn_start")
    game["phase"] = "play"
    if changes["skip_play"]:
        _add_log(game, "跳过出牌阶段", current["id"])
        _end_play(game, current)


def _resolve_judgement(game, effect):
    if effect['negated']:
        _continue_judgement(game, {"kind": "delayed", "effect": effect}, None, None, None)
    else:
        wind_fire.judge(game, _seat(game, effect["target"]), {"kind": "delayed", "effect": effect}, ENGINE)


def _continue_judgement(game, context, card, suit, rank):
    kind = context["kind"]
    if kind == "delayed":
        _apply_judgement(game, context["effect"], suit, rank)
    elif kind == "armor":
        pending = context["effect"]
        game.update(phase="dodge", pending=pending)
        if suit in {"红桃", "方片"}:
            _add_log(game, "八卦阵判定成功", pending["target"])
            _after_dodge(game, pending)
    elif kind.startswith("forest_"):
        forest.judgement_result(game, context, card, suit, rank, ENGINE)
    elif kind.startswith("divine_"):
        divine.judgement_result(game, context, card, suit, rank, ENGINE)


def _apply_judgement(game, effect, suit, rank):
    target = _seat(game, effect["target"])
    card = effect["card"]
    kind = effect.get("virtual_kind") or _kind(card)
    hit = suit == "黑桃" and rank is not None and 2 <= rank <= 9
    if kind == "indulgence" and _mode(game) == tournament.CHENG:
        if not effect["negated"] and suit != "红桃":
            target["judgement"].append(card)
            game["turn_start"].setdefault("judged_chengtian", []).append(card)
        else:
            game["discard"].append(card)
            tournament.clear_unit_lock(game, card)
        _complete(game)
        return
    if kind == "lightning" and not hit:
        other = next((_seat(game, player) for player in _after(game, target["id"])
                      if player != target["id"] and not any(_kind(item) == kind for item in
                                                           _seat(game, player).get("judgement", []))), None)
        _place_delayed(game, card, other["id"] if other else target["id"])
        # A lone player may keep it for a future turn. Never re-judge it now.
        if not other:
            retained = target["judgement"].pop()
            _complete(game)
            target["judgement"].append(retained)
            return
    else:
        game["discard"].append(card)
    if not effect["negated"] and suit is not None:
        if kind == "indulgence" and suit != "红桃":
            game["turn_start"]["skip_play"] = True
        if kind == "supply" and suit != "梅花":
            game["turn_start"]["skip_draw"] = True
    if hit and kind == "lightning":
        _damage(game, {"actor": target["id"], "target": target["id"], "card": "lightning",
                       "damage": 3, "nature": "thunder", "no_source": True})
    else:
        _complete(game)


def _card_name(card):
    return CARDS.get(_kind(card), ("武将技能",))[0]


def _printed(card):
    return _physical_label(card)


def _matching(seat, kinds, game=None):
    return [card for card in seat["hand"] if (divine.hand_kind(game, seat, card, ENGINE) if game else _kind(card)) in kinds]


def _has(seat, kind):
    return any(_kind(card) == kind for card in seat["hand"])


def _remove(seat, card):
    actual = card if card in seat["hand"] else next((item for item in seat["hand"] if _kind(item) == card), None)
    if actual is None:
        raise ValueError("这张手牌已不可用。")
    seat["hand"].remove(actual)
    return actual


def _spend(game, seat, card):
    actual = _remove(seat, card)
    game["discard"].append(actual)
    expansion.after_spend(game, seat, ENGINE)
    return actual


def _harvest_cards(game):
    # Saved games from before per-target resolution embedded the selection pool.
    return game["pending"].get("cards", game.get("harvest_cards", []))


def _distance(game, origin, target):
    if _mode(game) == tournament.CHENG:
        return 1
    live = _live(game)
    offset = abs(live.index(origin["id"]) - live.index(target["id"]))
    base = min(offset, len(live) - offset)
    return max(1, base + bool(target.get("guard")) - bool(origin.get("steed"))
               + expansion.distance_delta(game, origin, target, ENGINE))


def _capacity(game, seat):
    limit = seat["hp"] + int("limit" in _skills(game, seat))
    if _mode(game) == tournament.CHENG:
        limit += bool(seat.get("guard")) + bool(seat.get("steed"))
    return max(0, expansion.hand_limit(game, seat, limit, ENGINE))


def _attack_cards(game, seat):
    skills = _skills(game, seat)
    return _matching(seat, SLASHES | ({"dodge"} if "switch" in skills else set()), game)


def _dodge_cards(game, seat):
    return _matching(seat, {"dodge"} | (SLASHES if "switch" in _skills(game, seat) else set()), game)


def _option(action, label, value=None, target=None, card=None):
    option = {"id": action, "label": label}
    if value is not None:
        option["value"] = value
    if target is not None:
        option["target"] = target
    if card is not None:
        option["card"] = card
    return option


def _options_for_cards(cards, action, label, target=None):
    if not cards:
        return []
    return [
        _option(action, label, card if card in MANIFEST else None, target, card if card in MANIFEST else None)
        for card in dict.fromkeys(cards)
    ]


def _has_assets(seat, *, include_judgement=True):
    return bool(seat["hand"] or (include_judgement and seat.get("judgement"))
                or any(seat.get(slot) for slot in SLOTS.values()))


def _trick_targets(game, seat, card):
    for target in game["seats"]:
        own = target["id"] == seat["id"]
        if not target["alive"] or (own and card not in {"fire_attack", "lightning"}) or (not own and card == "lightning"):
            continue
        if card in {"snatch", "supply"} and _distance(game, seat, target) > 1:
            continue
        if card in {"snatch", "dismantle"} and not _has_assets(target):
            continue
        if card == "fire_attack" and len(target["hand"]) <= int(own):
            continue
        if card in TILTED and _has_delayed(game, target, card):
            continue
        yield target["id"]


def _has_delayed(game, seat, kind):
    return any(game.get("virtual_delays", {}).get(card, _kind(card)) == kind for card in seat.get("judgement", ()))


def _in_range(game, seat):
    weapon = _kind(seat.get("weapon"))
    return [
        other["id"] for other in game["seats"]
        if other["alive"] and other["id"] != seat["id"] and expansion.attack_reaches(game, seat, other, WEAPONS.get(weapon, 1), ENGINE)
    ]


def _spear_pairs(seat):
    return combinations(dict.fromkeys(seat["hand"]), 2) if _kind(seat.get("weapon")) == "spear" else ()


def _attack_options(game, seat):
    targets = _in_range(game, seat)
    cards = _attack_cards(game, seat)
    weapon = _kind(seat.get("weapon"))
    maximum = min(len(targets), wind_fire.slash_target_limit(game, seat, 3 if weapon == "halberd" and len(seat["hand"]) == 1 else 1))
    for card in dict.fromkeys(cards):
        physical = card in MANIFEST
        kind = divine.hand_kind(game, seat, card, ENGINE)
        for target in targets:
            value = {"card": card, "target": target} if physical else target
            yield _option("attack", f"使用{_card_name(kind)}", value, target, card if physical else None)
            if weapon == "fan" and kind == "strike":
                value = {"card": card, "target": target, "fire": True}
                yield _option("attack", "发动朱雀羽扇 · 火杀", value, target, card)
        for size in range(2, maximum + 1):
            for selected in combinations(targets, size):
                names = "、".join(str(_live(game).index(player) + 1) for player in selected)
                yield _option("attack", f"杀 · 攻击 {names} 号位", {"card": card, "targets": list(selected)}, card=card)
                if weapon == "fan" and kind == "strike":
                    yield _option("attack", f"朱雀羽扇 · 火杀 {names} 号位",
                                  {"card": card, "targets": list(selected), "fire": True}, card=card)
    for first, second in _spear_pairs(seat):
        for target in targets:
            yield _option("attack", f"丈八 · {_printed(first)}＋{_printed(second)}",
                          {"materials": [first, second], "target": target}, target)
        for size in range(2, maximum + 1):
            for selected in combinations(targets, size):
                yield _option("attack", f"丈八 · {_printed(first)}＋{_printed(second)} · 攻击 {size} 人",
                              {"materials": [first, second], "targets": list(selected)})


def _trick_options(game, seat, card):
    kind = _kind(card)
    if _mode(game) == tournament.CHENG and kind in {"rotate_general", "indulgence"}:
        for owner, unit in tournament.unit_targets(game, seat["id"], opponents=kind == "indulgence"):
            if kind == "indulgence" and _has_delayed(game, _seat(game, owner), kind):
                continue
            name = GENERALS[unit["general"]][0]
            action = "rotate_general" if kind == "rotate_general" else "trick"
            yield _option(action, f"{_card_name(card)} · {name}", {"card": card, "target": owner, "unit": unit["id"]}, owner, card)
    elif kind == "rotate_general":
        return
    elif kind in {"invasion", "arrows", "feast", "harvest"}:
        yield _option("trick", f"使用{_card_name(card)}", {"card": card}, card=card)
    elif kind == "chain":
        yield _option("recast", "重铸铁索连环", card, card=card)
        live = _live(game)
        for size in (1, 2):
            for selected in combinations(live, size):
                names = "、".join(str(live.index(player) + 1) for player in selected)
                yield _option("trick", f"铁索 · 切换 {names} 号位", {"card": card, "targets": list(selected)}, card=card)
    elif kind == "collateral":
        for target in game["seats"]:
            if target["alive"] and target["id"] != seat["id"] and target.get("weapon"):
                for victim in _in_range(game, target):
                    index = _live(game).index(victim) + 1
                    yield _option("trick", f"借刀杀人 · 指向 {index} 号位",
                                  {"card": card, "target": target["id"], "victim": victim}, target["id"], card)
    else:
        for target in _trick_targets(game, seat, kind):
            if not expansion.prevent_trick(game, seat, _seat(game, target), card, ENGINE):
                yield _option("trick", f"使用{_card_name(card)}", {"card": card, "target": target}, target, card)


def _base_actions(game, seat):
    if seat is None or not seat["alive"] or _turn(game) != seat["id"]:
        return []
    hand, phase, pending = seat["hand"], game["phase"], game["pending"]
    if phase == "bid":
        return [_option("bid", "不叫", 0)] + [
            _option("bid", f"叫 {score} 分", score) for score in range(game["auction"]["high"] + 1, 4)
        ]
    if phase == "dodge":
        choices = [_option("take_damage", "承受伤害")]
        choices[:0] = _options_for_cards(_dodge_cards(game, seat), "dodge", "打出闪")
        if expansion.armor_available(game, seat, ENGINE) and not pending.get("armor_tried") and not pending.get("ignore_armor"):
            choices.insert(0, _option("armor", "发动八卦阵"))
        return choices
    if phase == "strike_response":
        choices = [_option("take_damage", "承受伤害")]
        choices[:0] = _options_for_cards(_attack_cards(game, seat), "respond_strike", "打出杀")
        choices[:0] = [_option("respond_strike", f"丈八 · {_printed(first)}＋{_printed(second)}",
                               {"materials": [first, second]}) for first, second in _spear_pairs(seat)]
        return choices
    if phase == "rescue":
        choices = [_option("pass", "放弃救援")]
        if expansion.peach_allowed(game, seat, pending["target"], ENGINE) and divine.allow_peach(game, seat, pending["target"], ENGINE):
            choices[:0] = _options_for_cards(_matching(seat, {"peach"}, game), "rescue", "用桃救援", pending["target"])
        if pending["target"] == seat["id"]:
            choices[:0] = _options_for_cards(_matching(seat, {"wine"}, game), "rescue_wine", "用酒自救")
        return choices
    if phase == "counter":
        return [*_options_for_cards(_matching(seat, {"counterspell"}, game), "counterspell", "使用无懈可击"),
                _option("pass", "跳过无懈")]
    if phase == "choice":
        return [_option("take_harvest", f"获得{_printed(card)}", card) for card in dict.fromkeys(_harvest_cards(game))]
    if phase == "asset":
        return _asset_options(game, pending)
    if phase == "discard":
        return [_option("discard", f"弃置{_printed(card)}", card, card=card) for card in dict.fromkeys(hand)]
    if phase == "fire_reveal":
        return [_option("fire_reveal", f"展示{_printed(card)}", card, card=card) for card in dict.fromkeys(hand)]
    if phase == "fire_pay":
        return [
            *(_option("fire_pay", f"弃置{_printed(card)}造成火伤", card, card=card) for card in dict.fromkeys(hand)
              if pending["suit"] and expansion.suit(game, seat, card, ENGINE) == pending["suit"]),
            _option("skip_fire", "放弃火攻"),
        ]
    if phase == "borrow":
        return [
            *(_option("borrow_strike", f"打出{_card_name(divine.hand_kind(game, seat, card, ENGINE))} · {_printed(card)}",
                      card, pending["victim"], card)
              for card in dict.fromkeys(_attack_cards(game, seat))),
            _option("give_weapon", "交出武器"),
        ]
    if phase == "double_sword":
        return [
            *(_option("double_discard", f"弃置{_printed(card)}", card, card=card) for card in dict.fromkeys(hand)),
            _option("double_draw", "让用杀者摸牌"),
        ]
    if phase == "weapon":
        options = [_option("weapon_damage", "照常结算伤害")]
        target = _seat(game, pending["target"])
        if pending["weapon_choice"] == "ice_sword":
            options.insert(0, _option("ice_sword", "寒冰剑 · 改为弃两张牌"))
        else:
            options[:0] = [_option("bow_horse", f"麒麟弓 · 弃置{_card_name(target[slot])}", slot)
                           for slot in ("steed", "guard") if target.get(slot)]
        return options
    if phase == "follow_up":
        if pending["weapon_choice"] == "blade":
            return [*(_option("follow_strike", f"追加{_printed(card)}", card, pending["target"], card)
                      for card in dict.fromkeys(_attack_cards(game, seat))), _option("stop_follow", "结束追击")]
        return [*(_option("axe_choose", f"贯石斧 · 弃置{_printed(card)}", card, card=card)
                  for card in dict.fromkeys(_axe_cards(seat))), _option("stop_follow", "放弃强制命中")]
    if phase == "axe_cost":
        cards = _axe_cards(seat).copy()
        cards.remove(pending["axe_first"])
        return [
            *(_option("axe_finish", f"贯石斧 · 再弃{_printed(card)}", card, card=card) for card in dict.fromkeys(cards)),
            _option("stop_follow", "放弃强制命中"),
        ]
    actions = []
    if _mode(game) == "factions":
        actions.extend(_option("reveal", f"明置{GENERALS[key][0]}", key)
                       for key in seat["generals"] if key not in seat["revealed"])
    skills = _skills(game, seat)
    weapon = _kind(seat.get("weapon"))
    limit = 1 + int("fury" in skills) + int(seat["role"] == LANDLORD)
    limit = expansion.attack_limit(game, seat, limit, ENGINE)
    if limit and (weapon == "crossbow" or game["strikes"] < limit):
        actions.extend(_attack_options(game, seat))
    if seat["hp"] < seat["max_hp"]:
        actions.extend(_options_for_cards(_matching(seat, {"peach"}, game), "heal", "用桃回血"))
    if not game.get("wine") and "wine" not in game.get("used_skills", []):
        actions.extend(_options_for_cards(_matching(seat, {"wine"}, game), "wine", "饮酒蓄力"))
    actions.extend(_options_for_cards(_matching(seat, {"insight"}, game), "insight", "使用无中生有"))
    if "blade" in hand:
        actions.append(_option("equip", "装备长刃"))
    for card in dict.fromkeys(hand):
        kind = _kind(card)
        if divine.hand_kind(game, seat, card, ENGINE) != kind:
            continue
        if kind in SLOTS and card != "blade":
            actions.append(_option("equip_card", f"装备{_card_name(card)}", card, card=card))
        elif kind in TRICKS and kind != "insight":
            actions.extend(_trick_options(game, seat, card))
    if "cycle" in skills and "cycle" not in game.get("used_skills", []):
        actions.extend(_option("cycle", f"调度 · 弃{_printed(card)}", card, card=card) for card in dict.fromkeys(hand))
    if hand and "aid" in skills and "aid" not in game.get("used_skills", []):
        actions.extend(_option("aid", "援护 · 随机弃牌", target["id"], target["id"])
                       for target in game["seats"] if target["alive"] and target["hp"] < target["max_hp"])
    return [*actions, _option("end", "结束出牌")]


def _actions(game, seat):
    if seat is None or _turn(game) != seat["id"]:
        return []
    scripted = tournament.actions(game, seat, GENERALS, ENGINE)
    if scripted is not None:
        return scripted
    if game["phase"] == "general":
        return expansion.response_actions(game, seat, ENGINE)
    options = _base_actions(game, seat)
    if game["phase"] == "play":
        return [*options, *expansion.active_actions(game, seat, ENGINE), *tournament.extra_actions(game, seat, GENERALS, ENGINE)]
    return [*options, *expansion.response_actions(game, seat, ENGINE), *tournament.extra_actions(game, seat, GENERALS, ENGINE)]


def view(game: dict, player: str | None) -> dict:
    mode = _mode(game)
    own = _seat(game, player)
    seats = []
    for seat in game["seats"]:
        revealed = seat.get("revealed", [])
        exposed = seat["id"] == player or not seat["alive"] or game["finished"]
        visible = seat.get("generals", []) if exposed else revealed
        role_visible = mode not in {"identity", "factions"} or exposed or seat["role"] == LORD or bool(revealed and mode == "factions")
        generals = [_general_info(key) for key in visible]
        equipment = [
            {"id": seat[slot], "name": _card_name(seat[slot]), "slot": slot, **MANIFEST.get(seat[slot], {})}
            for slot in dict.fromkeys(SLOTS.values()) if seat.get(slot)
        ]
        seats.append({
            "id": seat["id"], "hp": seat["hp"], "max_hp": seat["max_hp"],
            "alive": seat["alive"], "hand_count": len(seat["hand"]),
            "weapon": _card_name(seat["weapon"]) if seat.get("weapon") else None,
            "role": seat["role"] if role_visible else None, "team": seat.get("team") if role_visible else None,
            "general": generals[0] if generals else None, "generals": generals, "equipment": equipment,
            "judgement": [_card_name(game.get("virtual_delays", {}).get(card, card)) for card in seat.get("judgement", [])],
            "chained": bool(seat.get("chained")),
        })
    hand = []
    if own:
        for card, count in Counter(own["hand"]).items():
            kind = divine.hand_kind(game, own, card, ENGINE)
            title, description, _ = CARDS[kind]
            if kind != _kind(card):
                description = f"武神 · {_card_name(card)}在手牌区视为杀。{description}"
            hand.append({**MANIFEST.get(card, {}), "id": card, "kind": kind, "name": title, "description": description,
                         "count": count, "type": "basic" if kind in BASIC else "equipment" if kind in SLOTS else "trick"})
    private_generals = [_general_info(key) for key in own.get("generals", [])] if own else []
    pending = game["pending"]
    event = None
    if pending:
        event = {"kind": game["phase"], "actor": pending["actor"], "target": pending["target"]}
        if card := pending.get("card"):
            event["card"] = _card_name(card)
        if game["phase"] == "choice":
            event["cards"] = [_printed(card) for card in _harvest_cards(game)]
        if game["phase"] == "fire_pay":
            event["revealed"] = pending["revealed"]
    return {
        "kind": "kingdoms", "mode": mode, "mode_name": MODES[mode]["name"],
        "phase": game["phase"], "turn": _turn(game), "active": game["active"], "turn_number": game["turn_number"],
        "seats": seats, "your_role": own["role"] if own else None, "your_team": own.get("team") if own else None,
        "your_general": private_generals[0] if private_generals else None, "your_generals": private_generals,
        "hand": hand, "actions": _actions(game, own), "event": event, "log": game["log"][-12:],
        "deck_count": len(game["deck"]), "finished": game["finished"], "result": game["result"], "winners": game["winners"],
        "tournament": tournament.public(game, player, GENERALS),
        "rules": [*MODE_RULES[mode], *(RULES if mode != tournament.CHENG else ()),
                  f"{game.get('turn_limit', 90)} 回合后按和局结算。"],
    }


def _finish(game, text, winners=()):
    pending = game["pending"] or {}
    game["discard"].extend(pending.get("cards", []))
    game["discard"].extend(game.pop("harvest_cards", []))
    if invasion := game.pop("invasion_card", None):
        game["discard"].append(invasion["card"])
    wind_fire.finish_bluffs(game)
    game["discard"].extend(wind_fire.judgment_held_cards(game))
    game["discard"].extend(_held_divine_counters(game))
    game.get("divine_state", {}).pop("unpaid_trick", None)
    game.pop("general_judgement", None)
    game.update(finished=True, phase="finished", pending=None, queue=[], winners=list(winners))
    game["result"] = f"三国杀{MODES[_mode(game)]['name']} · {text}"
    _add_log(game, text)


def _team_wins(game, team, label=None):
    winners = [seat["id"] for seat in game["seats"] if seat["team"] == team]
    _finish(game, f"{label or team}获胜", winners)


def _check_winner(game):
    live = [seat for seat in game["seats"] if seat["alive"]]
    mode = _mode(game)
    if mode == "identity":
        roles = [seat["role"] for seat in live]
        text, winning_roles = "", ()
        if LORD not in roles:
            if roles == [SPY]:
                text, winning_roles = "内奸独存，内奸获胜", (SPY,)
            else:
                text, winning_roles = "主公阵亡，反贼获胜", (REBEL,)
        elif REBEL not in roles and SPY not in roles:
            text, winning_roles = "反贼和内奸均已出局，主公与忠臣获胜", (LORD, LOYALIST)
        if winning_roles:
            _finish(game, text, [seat["id"] for seat in game["seats"] if seat["role"] in winning_roles])
    elif mode == "team":
        captains = [seat["team"] for seat in live if seat["role"] == "主帅"]
        if len(captains) == 1:
            _team_wins(game, captains[0])
    elif mode == "factions":
        if len(live) == 1 and not live[0]["revealed"]:
            _finish(game, "最后一位武将获胜", [live[0]["id"]])
        elif live and all(seat["revealed"] for seat in live) and len({seat["team"] for seat in live}) == 1:
            team = live[0]["team"]
            _team_wins(game, team, "野心家" if live[0]["role"] == "野心家" else team)
    elif live and len({seat["team"] for seat in live}) == 1:
        _team_wins(game, live[0]["team"])


def _discard_all(game, seat):
    game["discard"].extend(seat["hand"])
    seat["hand"].clear()
    for slot in dict.fromkeys(SLOTS.values()):
        if seat.get(slot):
            game["discard"].append(_unequip(game, seat, slot))


def _unequip(game, seat, slot):
    old = seat.get(slot)
    seat[slot] = None
    if _kind(old) == "lion" and seat["alive"] and seat["hp"] < seat["max_hp"] and not divine.armor_ignored(game, seat):
        seat["hp"] += 1
        _add_log(game, "离开白银狮子，回复一点体力", seat["id"])
    return old


def _axe_cards(seat):
    return [*seat["hand"], *(seat[slot] for slot in ("armor", "steed", "guard") if seat.get(slot))]


def _discard_owned(game, seat, card):
    if card in seat["hand"]:
        _spend(game, seat, card)
    else:
        slot = next(name for name in dict.fromkeys(SLOTS.values()) if seat.get(name) == card)
        game["discard"].append(_unequip(game, seat, slot))
        expansion.after_spend(game, seat, ENGINE)


FEMALE = frozenset(("zhen_ji", "huang_yueying", "sun_shangxiang", "diao_chan", "xiao_qiao",
                    "cai_wenji", "sun_shangxiang_sp", "da_qiao", "bu_lian_shi", "wind_xiao_qiao", "forest_zhu_rong",
                    "mountain_cai_wenji", "TY01-007", "TY01-112", "TY01-114", "TY01-137", "TY01-199"))


def _is_female(seat):
    if avatar := forest.avatar_key(seat):
        return avatar in FEMALE
    return any(key in FEMALE for key in seat.get("generals", ()))


def _ask_dodge(game, effect):
    seat, target = _seat(game, effect["actor"]), _seat(game, effect["target"])
    if not target["alive"]:
        _complete(game)
        return
    weapon = _kind(seat.get("weapon"))
    effect["ignore_armor"] = weapon == "qinggang" or divine.armor_ignored(game, target)
    armor = None if effect["ignore_armor"] else _kind(target.get("armor"))
    if (armor == "rattan" and not effect.get("nature")
            or armor == "renwang" and effect.get("color") == "black"):
        _add_log(game, "防具令这张杀无效", target["id"])
        _complete(game)
        return
    game.update(phase="dodge", pending=effect)
    effect.setdefault("required_dodges", forest.require_dodges(game, seat, target, ENGINE))
    if not effect.get("ct_red_checked"):
        effect["ct_red_checked"] = True
        if tournament.units.try_stage(game, "slash_red", seat, ENGINE, pending=effect):
            return
    if not effect.get("ct_target_checked"):
        effect["ct_target_checked"] = True
        if tournament.units.try_stage(game, "slash_targeted", target, ENGINE, pending=effect):
            return
    if not effect.get("offered_xiangle"):
        effect["offered_xiangle"] = True
        expansion.card_used(game, seat, target, effect.get("physical", effect["card"]), ENGINE)
        if expansion.before_attack(game, seat, target, effect, ENGINE):
            return
    if weapon == "double_sword" and not effect.get("offered_gender") and _is_female(seat) != _is_female(target):
        effect["offered_gender"] = True
        game.update(phase="double_sword", pending=effect)
    else:
        game.update(phase="dodge", pending=effect)


def _launch_slash(game, seat, card, victims, *, fire=False, materials=(), count=True, virtual_kind=None):
    actual = None
    if materials:
        for item in materials:
            _spend(game, seat, item)
        color = _suit(materials[0])
        color = color if color == _suit(materials[1]) else None
        printed = "strike"
    else:
        printed = virtual_kind or divine.hand_kind(game, seat, card, ENGINE)
        actual = _spend(game, seat, card)
        color = expansion.suit(game, seat, actual, ENGINE)
    nature = "fire" if fire else NATURES.get(printed)
    if count:
        game["strikes"] += 1
    active = game["active"] == seat["id"]
    damage = 1 + int(active and game.get("wine", False))
    if active:
        game["wine"] = False
    color = "black" if color in {"黑桃", "梅花"} else "red" if color in {"红桃", "方片"} else None
    printed = printed if printed in SLASHES else "strike"
    effects = [{"actor": seat["id"], "target": target, "card": printed, "physical": actual, "damage": damage, "nature": nature,
                "slash": True, "color": color, "settlement": "hit"} for target in victims]
    game.setdefault("queue", [])[:0] = effects
    for target in victims:
        _add_log(game, f"使用了{CARDS[printed][0] if printed in SLASHES else '杀'}", seat["id"], target)
    _complete(game)


def _after_dodge(game, pending):
    pending.setdefault("card", "strike")
    if pending.get("required_dodges", 1) > 1:
        pending["required_dodges"] -= 1
        pending["armor_tried"] = False
        _add_log(game, "肉林，还需打出一张闪", pending["target"])
        return
    if divine.extra_response(game, pending, ENGINE):
        pending["armor_tried"] = False
        return
    if not pending.get("ct_dodged_checked"):
        pending["ct_dodged_checked"] = True
        if tournament.units.try_stage(game, "dodged_slash", _seat(game, pending["actor"]), ENGINE, pending=pending):
            return
    if expansion.after_dodge(game, _seat(game, pending["target"]), pending, ENGINE):
        return
    actor = _seat(game, pending["actor"])
    weapon = _kind(actor.get("weapon"))
    if pending.get("slash") and actor["alive"] and (
        (weapon == "blade" and _attack_cards(game, actor)) or (weapon == "axe" and len(_axe_cards(actor)) >= 2)
    ):
        pending["weapon_choice"] = weapon
        game["phase"] = "follow_up"
    else:
        _complete(game)


def _hit(game, pending):
    actor, target = _seat(game, pending["actor"]), _seat(game, pending["target"])
    weapon = _kind(actor.get("weapon"))
    if pending.get("slash") and actor["alive"] and (
        (weapon == "ice_sword" and _has_assets(target, include_judgement=False))
        or (weapon == "bow" and (target.get("steed") or target.get("guard")))
    ):
        pending["weapon_choice"] = weapon
        game["phase"] = "weapon"
    else:
        _damage(game, pending)


def _end_play(game, seat):
    if tournament.end_play(game, seat, GENERALS, _add_log):
        return
    _finish_end_play(game, seat)


def _finish_end_play(game, seat):
    if forest.before_phase(game, seat, "discard", ENGINE):
        return
    if not forest.skip_discard(game, seat) and len(seat["hand"]) > _capacity(game, seat):
        game["phase"] = "discard"
    else:
        _advance(game)


def _advance(game, *, skip_end=False):
    active = _seat(game, game["active"])
    if not skip_end and active["alive"] and forest.after_discard(game, active, ENGINE):
        return
    if not skip_end and active["alive"] and expansion.end_turn(game, active, ENGINE):
        return
    if (active["alive"] and forest.end_turn(game, active, ENGINE)) or divine.end_turn(game, active, ENGINE):
        return
    limit = game.get("turn_limit", 90)
    if game["turn_number"] >= limit:
        _finish(game, f"已打满 {limit} 回合，和局")
        return
    regular = forest.next_player(game, active, ENGINE) or _after(game, game["active"])[0]
    game["active"] = divine.next_player(game, regular, ENGINE) or regular
    _begin_turn(game)


def _asset_options(game, effect):
    target = _seat(game, effect["target"])
    label = "取得" if _kind(effect["card"]) == "snatch" else "弃置"
    options = [_option("select_asset", f"{label}一张暗手牌", "hand")] if target["hand"] else []
    if _kind(effect["card"]) != "ice_sword":
        for index, card in enumerate(target.get("judgement", [])):
            kind = game.get("virtual_delays", {}).get(card, card)
            options.append(_option("select_asset", f"{label}判定区的{_card_name(kind)}", {"judgement": index}))
    options.extend(_option("select_asset", f"{label}{_printed(target[slot])}", slot)
                   for slot in dict.fromkeys(SLOTS.values()) if target.get(slot))
    return options


def _select_asset(game, effect, selected):
    target = _seat(game, effect["target"])
    actor = _seat(game, effect["actor"])
    if selected == "hand":
        card = target["hand"].pop(SystemRandom().randrange(len(target["hand"])))
    elif isinstance(selected, dict):
        card = target["judgement"].pop(selected["judgement"])
        game.get("virtual_delays", {}).pop(card, None)
        tournament.clear_unit_lock(game, card)
    else:
        card = _unequip(game, target, selected)
    if _kind(effect["card"]) == "snatch":
        actor["hand"].append(card)
        _add_log(game, "取得一张牌", actor["id"], target["id"])
    else:
        game["discard"].append(card)
        _add_log(game, "拆去一张牌", actor["id"], target["id"])
    if not isinstance(selected, dict):
        expansion.after_spend(game, target, ENGINE)
    effect["remaining_assets"] -= 1
    if not effect["remaining_assets"] or not _has_assets(target, include_judgement=_kind(effect["card"]) != "ice_sword"):
        _complete(game)


def _complete(game):
    if game["finished"]:
        return
    pending = game["pending"]
    if pending and (amount := pending.pop("damage_applied", 0)):
        if _mode(game) == tournament.CHENG and _chengtian_damage_skills(game, pending):
            pending["damage_applied"] = amount
            return
        if divine.after_survival(game, pending, ENGINE):
            pending["damage_applied"] = amount
            return
        if forest.after_damage(game, pending, amount, ENGINE):
            return
    wind_fire.finish_damage(game, pending, ENGINE)
    game["pending"] = None
    if divine.continue_effects(game, ENGINE):
        return
    queue = game.setdefault("queue", [])
    while queue:
        effect = queue.pop(0)
        target = _seat(game, effect["target"])
        if target["alive"]:
            grouped_attack = _kind(effect["card"]) in {"arrows", "invasion"}
            if (grouped_attack and _kind(target.get("armor")) == "rattan" and not divine.armor_ignored(game, target)
                    or effect.get("settlement") in {None, "trick"}
                    and expansion.prevent_trick(game, _seat(game, effect["actor"]), target, effect["card"], ENGINE)):
                _add_log(game, "技能或防具令群体锦囊无效", target["id"])
                continue
            if effect.get("settlement") == "trick":
                if _kind(effect["card"]) == "harvest" and not game.get("harvest_cards"):
                    continue
                _offer_counter(game, effect)
                return
            if effect.get("settlement") == "damage":
                _damage(game, effect)
                return
            if effect.get("settlement") == "hit":
                _ask_dodge(game, effect)
                return
            game["pending"] = effect
            game["phase"] = "dodge" if _kind(effect["card"]) == "arrows" else "strike_response"
            return
    game["discard"].extend(game.pop("harvest_cards", []))
    if invasion := game.pop("invasion_card", None):
        game["discard"].append(invasion["card"])
        forest.on_invasion_finished(game, invasion["actor"], invasion["card"], ENGINE)
    if expansion.continue_effects(game, ENGINE) or forest.continue_effects(game, ENGINE):
        return
    game["phase"] = "play"
    actor = _seat(game, game["active"])
    if game.pop("tournament_turn_ended", False) or not actor["alive"]:
        game.pop("turn_start", None)
        _advance(game)
    elif "turn_start" in game:
        _next_judgement(game, actor)


def _chengtian_damage_skills(game, pending):
    actor, target = (_seat(game, pending[key]) for key in ("actor", "target"))
    timings = [("damaged", target), ("damaged_slash", target), ("hit_slash", actor)]
    if _kind(pending["card"]) in TRICKS:
        timings.append(("hit_trick", actor))
    checked = pending.setdefault("ct_damage_checked", [])
    for event, seat in timings:
        if event not in checked:
            checked.append(event)
            if tournament.units.try_stage(game, event, seat, ENGINE, pending=pending):
                return True
    return False


def _reveal(game, seat, key, *, reward=True):
    if not seat["revealed"]:
        faction = GENERALS[key][1]
        others = sum(other["team"] == faction for other in game["seats"])
        if others >= len(game["seats"]) // 2:
            seat["role"] = "野心家"
            seat["team"] = seat["id"]
        else:
            seat["team"] = faction
        if reward and not game.get("first_reveal"):
            game["first_reveal"] = True
            _draw(game, seat, 2)
    elif len(seat["revealed"]) == 1 and reward:
        _draw(game, seat, 1)
    seat["revealed"].append(key)
    _add_log(game, f"明置{GENERALS[key][0]} · {seat['role']}", seat["id"])


def _eliminate(game, pending):
    target = _seat(game, pending["target"])
    actor = _seat(game, pending["actor"])
    if divine.before_eliminate(game, pending, ENGINE):
        return
    pending.pop("damage_applied", None)
    if _mode(game) == "factions":
        for key in target["generals"]:
            if key not in target["revealed"]:
                _reveal(game, target, key, reward=False)
    target["alive"] = False
    expansion.on_death(game, target, None if pending.get("no_source") else actor, ENGINE)
    divine.on_death(game, target, None if pending.get("no_source") else actor, ENGINE)
    _discard_all(game, target)
    game["discard"].extend(target.get("judgement", []))
    for card in target.get("judgement", []):
        game.get("virtual_delays", {}).pop(card, None)
    target.get("judgement", []).clear()
    _add_log(game, f"出局，身份为{target['role']}", target["id"])
    if tournament.on_defeat(game, target, None if pending.get("no_source") else actor, GENERALS,
                           draw=_draw, log=_add_log, complete=_complete, finish=_finish):
        return
    _check_winner(game)
    if game["finished"]:
        return
    if _mode(game) == "identity" and actor["alive"] and not pending.get("no_source"):
        if target["role"] == REBEL:
            _add_log(game, "击败反贼，获得摸牌奖励", actor["id"])
            _draw(game, actor, 3)
        elif actor["role"] == LORD and target["role"] == LOYALIST:
            _discard_all(game, actor)
            _add_log(game, "误伤忠臣，弃掉所有手牌和装备", actor["id"])
    if _mode(game) == "landlord" and target["role"] == FARMER:
        for seat in game["seats"]:
            if seat["alive"] and seat["role"] == FARMER:
                _draw(game, seat, 2)
    _complete(game)


def _damage(game, pending):
    game["pending"] = pending
    target = _seat(game, pending["target"])
    actor = _seat(game, pending["actor"])
    amount = pending.get("damage", 1)
    nature = pending.get("nature")
    armor = None if pending.get("ignore_armor") or divine.armor_ignored(game, target) else _kind(target.get("armor"))
    if _mode(game) == tournament.CHENG and (pending.get("slash") or _kind(pending["card"]) == "duel"):
        state = game["tournament"]
        if state["entries"][actor["id"]].get("pressure") == game["turn_number"] and target["id"] not in state["card_users"]:
            amount += 1
    if pending.get("slash") and not pending.get("skip_source_modifier") and _kind(actor.get("weapon")) == "guding" and not target["hand"]:
        amount += 1
    if nature == "fire" and armor == "rattan":
        amount += 1
    if armor == "lion":
        amount = min(amount, 1)
    amount = expansion.alter_damage(game, pending, amount, ENGINE)
    if amount <= 0:
        _complete(game)
        return
    if not wind_fire.before_damage(game, pending, amount, ENGINE):
        _apply_damage(game, pending, amount)


def _apply_damage(game, pending, amount):
    game["pending"] = pending
    target = _seat(game, pending["target"])
    nature = pending.get("nature")
    pending["damage_applied"] = amount
    divine.after_damage(game, pending, amount, ENGINE)
    target["hp"] -= amount
    _add_log(game, f"失去 {amount} 点体力", target["id"])
    if target["hp"] <= 0 and _mode(game) == tournament.CHENG and game["tournament"].get("official"):
        _eliminate(game, pending)
        return
    if nature and target.get("chained"):
        target["chained"] = False
        if not pending.get("propagated"):
            transferred = [
                {"actor": pending["actor"], "target": player, "card": pending["card"],
                 "damage": amount, "nature": nature, "propagated": True,
                 "settlement": "damage", "no_source": pending.get("no_source", False)}
                for player in _after(game, target["id"])
                if player != target["id"] and _seat(game, player).get("chained")
            ]
            game.setdefault("queue", [])[:0] = transferred
    if "recover" in _skills(game, target):
        _draw(game, target, 1)
    if expansion.after_damage(game, pending, amount, ENGINE):
        return
    if target["hp"] > 0:
        _complete(game)
        return
    pending["order"] = _rotate(game, target["id"])
    pending["index"] = 0
    game["phase"] = "rescue"
    _add_log(game, "进入濒死，等待救援", target["id"])


def _save(game, seat, card, effect):
    _spend(game, seat, card)
    target = _seat(game, effect["target"])
    target["hp"] += 1
    _add_log(game, "为濒死角色回复一点体力", seat["id"], target["id"])
    if target["hp"] > 0:
        _complete(game)


def _resolve_trick(game, effect):
    if effect.get("judging"):
        _resolve_judgement(game, effect)
        return
    if effect["negated"]:
        if _kind(effect["card"]) in TILTED:
            _place_delayed(game, effect["card"], None)
        _add_log(game, "锦囊被无懈抵消", effect["actor"], effect["target"])
        _complete(game)
        return
    card, actor_id, target_id = _kind(effect["card"]), effect["actor"], effect["target"]
    actor, target = _seat(game, actor_id), _seat(game, target_id)
    if effect.get("settlement") == "trick":
        if card in {"arrows", "invasion", "harvest"}:
            game.update(phase={"arrows": "dodge", "invasion": "strike_response", "harvest": "choice"}[card], pending=effect)
            return
        if card == "feast":
            target["hp"] = min(target["max_hp"], target["hp"] + 1)
            _complete(game)
            return
    if card == "insight" and not effect.get("no_source"):
        _draw(game, actor, 2)
    elif card == "feast":
        for player in _rotate(game, actor_id):
            seat = _seat(game, player)
            seat["hp"] = min(seat["max_hp"], seat["hp"] + 1)
    elif card == "harvest":
        order = _rotate(game, actor_id)
        cards = [card for _ in order if (card := _take(game)) is not None]
        if cards:
            game["phase"] = "choice"
            game["pending"] = {"actor": actor_id, "target": order[0], "card": "harvest", "order": order, "cards": cards}
            return
    elif card in {"arrows", "invasion"}:
        game["queue"] = [{"actor": actor_id, "target": p, "card": card} for p in _after(game, actor_id) if p != actor_id]
    elif card == "duel":
        game["phase"] = "strike_response"
        game["pending"] = {"actor": actor_id, "target": target_id, "card": card,
                           "no_source": effect.get("no_source", False)}
        return
    elif card in TILTED:
        _place_delayed(game, effect["card"], target_id)
    elif card == "chain":
        for player in effect.get("targets", [target_id]):
            receiver = _seat(game, player)
            receiver["chained"] = not receiver.get("chained")
            _add_log(game, "横置" if receiver["chained"] else "重置", actor_id, player)
    elif card == "rotate_general":
        tournament.swap_general(game, actor, target, effect["unit"], GENERALS, _draw, _add_log)
    elif card == "fire_attack" and not effect.get("no_source"):
        if target["hand"]:
            game.update(phase="fire_reveal", pending=effect)
            return
    elif card == "collateral":
        if target.get("weapon"):
            game.update(phase="borrow", pending=effect)
            return
    elif card in {"snatch", "dismantle"} and not effect.get("no_source") and _has_assets(target):
        game.update(phase="asset", pending=effect | {"remaining_assets": 1})
        return
    _complete(game)


def _counter_window(game, effect, order):
    for player in order:
        if player not in effect["passed"]:
            effect["current"] = player
            game["pending"] = effect
            game["phase"] = "counter"
            return
    _resolve_trick(game, effect)


def _use_trick(game, seat, card, target, *, targets=None, victim=None, unit=None, virtual_kind=None, materials=()):
    # Hold delayed tricks out of the discard pile until their window resolves.
    # Immediate tricks are discarded before draw effects and may be reshuffled.
    kind = virtual_kind or _kind(card)
    if materials and kind in TILTED:
        card = _remove(seat, materials[0])
        game.setdefault("virtual_delays", {})[card] = kind
    elif materials:
        for material in materials:
            _spend(game, seat, material)
        card = kind
    elif kind in TILTED or kind == "invasion" and card in MANIFEST:
        card = _remove(seat, card)
        if kind == "invasion":
            game["invasion_card"] = {"actor": seat["id"], "card": card}
    else:
        card = _spend(game, seat, card)
    _add_log(game, f"使用{CARDS[kind][0]}", seat["id"], target)
    target = target or seat["id"]
    effect = {"actor": seat["id"], "target": target, "card": card, "negated": False, "passed": []}
    if victim:
        effect["victim"] = victim
    if unit:
        effect["unit"] = unit
    if not divine.before_trick(game, seat, effect, kind, targets, ENGINE):
        _dispatch_trick(game, seat, effect, kind, targets)


def _dispatch_trick(game, seat, effect, kind, targets):
    card, target = effect["card"], effect["target"]
    if seat["alive"] and not effect.get("no_source"):
        expansion.card_used(game, seat, _seat(game, target), card, ENGINE)
        used = game.setdefault("used_skills", [])
        if kind not in TILTED and "strategy" in _skills(game, seat) and "strategy" not in used:
            used.append("strategy")
            _draw(game, seat, 1)
    if kind in {"chain", "arrows", "invasion", "feast", "harvest"}:
        order = _rotate(game, seat["id"])
        if kind == "chain":
            order = [player for player in order if player in (targets or ())]
        elif kind in {"arrows", "invasion"}:
            order = [player for player in order if player != seat["id"]]
        elif kind == "harvest":
            game["harvest_cards"] = [drawn for _ in order if (drawn := _take(game)) is not None]
        game.setdefault("queue", [])[:0] = [
            {**effect, "target": player, "passed": [], "settlement": "trick"}
            for player in order
        ]
        _complete(game)
        return
    if kind in TILTED and (card in MANIFEST or card in game.get("virtual_delays", {})):
        if kind == "indulgence" and effect.get("unit") and _mode(game) == tournament.CHENG:
            tournament.lock_unit(game, card, effect["unit"])
        _place_delayed(game, card, target if _seat(game, target)["alive"] else None)
        return
    if not _seat(game, target)["alive"]:
        _complete(game)
        return
    _offer_counter(game, effect)


def _offer_counter(game, effect):
    target = effect["target"]
    order = _rotate(game, target)
    # When a response is possible, take passes in seat order. Skipping only
    # the players without nullifications would identify who has one.
    if any(_matching(_seat(game, player), {"counterspell"}, game) or expansion.has_counter(game, _seat(game, player), ENGINE)
           for player in order):
        _counter_window(game, effect, order)
    else:
        _resolve_trick(game, effect)


def _held_divine_counters(game):
    unpaid = game.get("divine_state", {}).get("unpaid_trick") or {}
    held = unpaid.get("counter") or {}
    if game["phase"] != "counter" and held.get("judging"):
        yield held["card"]
    if unpaid.get("kind") in TILTED:
        yield unpaid["effect"]["card"]


def _place_delayed(game, card, target):
    if target:
        _seat(game, target).setdefault("judgement", []).append(card)
    else:
        game["discard"].append(card)


def _add_virtual_judgement(game, seat, material, target, kind):
    _use_trick(game, seat, material, target, virtual_kind=kind, materials=(material,))


def _bid(game, player, points):
    auction = game["auction"]
    if points > auction["high"]:
        auction["high"], auction["leader"] = points, player
    _add_log(game, f"叫 {points} 分" if points else "放弃叫分", player)
    auction["index"] += 1
    if auction["index"] < len(auction["order"]) and points != 3:
        return
    player = auction["leader"] or auction["order"][0]
    for seat in game["seats"]:
        seat["role"] = LANDLORD if seat["id"] == player else FARMER
        seat["team"] = seat["role"]
    boss = _seat(game, player)
    boss["hp"] += 1
    boss["max_hp"] += 1
    _draw(game, boss, 2)
    game["active"] = player
    _add_log(game, "成为地主", player)
    _begin_turn(game)


def _accept_strike_response(game, pending):
    if _kind(pending["card"]) != "duel" or pending.get("no_source") or not _seat(game, pending["actor"])["alive"]:
        _complete(game)
    elif not divine.extra_response(game, pending, ENGINE, duel=True):
        pending["actor"], pending["target"] = pending["target"], pending["actor"]


def play(game: dict, player: str, action: str, value=None) -> None:
    seat = _seat(game, player)
    if not any(action == option["id"] and value == option.get("value") and type(value) is type(option.get("value"))
               for option in _actions(game, seat)):
        raise ValueError("现在无法执行这个操作。")
    if game["phase"] == "play":
        game["played_during_turn"] = True
    if _mode(game) == tournament.CHENG and action in CHENG_CARD_ACTIONS:
        game["tournament"].setdefault("card_users", []).append(player)
    if tournament.play(game, seat, action, value, GENERALS, begin_turn=_begin_turn, draw=_draw, log=_add_log,
                       complete=_complete, finish_end_play=_finish_end_play,
                       prepare_seat=lambda state, chosen: expansion.initialize_seat(state, chosen, ENGINE), engine=ENGINE):
        return
    if expansion.play_extra(game, seat, action, value, ENGINE):
        return
    pending = game["pending"]
    if action == "bid":
        _bid(game, player, value)
    elif action == "reveal":
        _reveal(game, seat, value)
        _check_winner(game)
    elif action == "attack":
        if isinstance(value, str):
            _launch_slash(game, seat, _attack_cards(game, seat)[0], [value])
        else:
            _launch_slash(game, seat, value.get("card"), value.get("targets") or [value["target"]],
                          fire=value.get("fire", False), materials=value.get("materials", ()))
    elif action == "dodge":
        _spend(game, seat, value or _dodge_cards(game, seat)[0])
        _add_log(game, "打出闪，抵消攻击", player)
        _after_dodge(game, pending)
    elif action == "armor":
        pending["armor_tried"] = True
        wind_fire.judge(game, seat, {"kind": "armor", "effect": pending}, ENGINE)
    elif action == "respond_strike":
        if isinstance(value, dict):
            for material in value["materials"]:
                _spend(game, seat, material)
        else:
            _spend(game, seat, value or _attack_cards(game, seat)[0])
        _add_log(game, "响应打出杀", player)
        _accept_strike_response(game, pending)
    elif action == "take_damage":
        _hit(game, pending)
    elif action in {"rescue", "rescue_wine"}:
        _save(game, seat, value or ("peach" if action == "rescue" else "wine"), pending)
    elif action in {"counterspell", "pass"} and game["phase"] == "counter":
        if action == "counterspell":
            if divine.before_counter(game, seat, pending, ENGINE, value):
                return
            _spend(game, seat, value or "counterspell")
            pending["negated"] = not pending["negated"]
            pending["passed"] = [player]
            _add_log(game, "使用无懈可击", player, pending["target"])
        else:
            pending["passed"].append(player)
        _counter_window(game, pending, _after(game, player))
    elif action == "pass":
        pending["index"] += 1
        if pending["index"] >= len(pending["order"]):
            _eliminate(game, pending)
    elif action == "take_harvest":
        cards = _harvest_cards(game)
        cards.remove(value)
        seat["hand"].append(value)
        _add_log(game, f"取得{_printed(value)}", player)
        if pending.get("settlement") == "trick":
            _complete(game)
        else:
            pending["order"].pop(0)
            if cards and pending["order"]:
                pending["target"] = pending["order"][0]
            else:
                game["discard"].extend(cards)
                _complete(game)
    elif action == "heal":
        _spend(game, seat, value or "peach")
        seat["hp"] += 1
        _add_log(game, "用桃回复一点体力", player)
    elif action == "wine":
        _spend(game, seat, value or "wine")
        game["wine"] = True
        game.setdefault("used_skills", []).append("wine")
        _add_log(game, "饮酒蓄力", player)
    elif action in {"insight", "trick", "rotate_general"}:
        if action == "insight":
            _use_trick(game, seat, value or "insight", None)
        else:
            _use_trick(game, seat, value["card"], value.get("target"), targets=value.get("targets"),
                       victim=value.get("victim"), unit=value.get("unit"))
    elif action == "recast":
        _spend(game, seat, value)
        _draw(game, seat, 1)
        _add_log(game, "重铸铁索连环", player)
    elif action == "fire_reveal":
        pending["suit"] = expansion.suit(game, seat, value, ENGINE)
        pending["revealed"] = _printed(value)
        game["phase"] = "fire_pay"
        _add_log(game, f"展示{pending['revealed']}", player)
    elif action == "fire_pay":
        _spend(game, seat, value)
        pending["nature"] = "fire"
        _damage(game, pending)
    elif action in {"skip_fire", "stop_follow"}:
        _complete(game)
    elif action == "borrow_strike":
        _launch_slash(game, seat, value, [pending["victim"]], count=False)
    elif action == "give_weapon":
        if not pending.get("no_source"):
            _seat(game, pending["actor"])["hand"].append(_unequip(game, seat, "weapon"))
            expansion.after_spend(game, seat, ENGINE)
        _complete(game)
    elif action in {"double_discard", "double_draw"}:
        if action == "double_discard":
            _spend(game, seat, value)
        else:
            _draw(game, _seat(game, pending["actor"]), 1)
        _ask_dodge(game, pending)
    elif action == "ice_sword":
        game.update(phase="asset", pending=pending | {"card": "ice_sword", "remaining_assets": 2})
    elif action == "select_asset":
        _select_asset(game, pending, value)
    elif action in {"weapon_damage", "bow_horse"}:
        if action == "bow_horse":
            target = _seat(game, pending["target"])
            game["discard"].append(_unequip(game, target, value))
            expansion.after_spend(game, target, ENGINE)
        _damage(game, pending)
    elif action == "follow_strike":
        _launch_slash(game, seat, value, [pending["target"]], count=False)
    elif action == "axe_choose":
        pending["axe_first"] = value
        game["phase"] = "axe_cost"
    elif action == "axe_finish":
        _discard_owned(game, seat, pending["axe_first"])
        _discard_owned(game, seat, value)
        _hit(game, pending)
    elif action in {"equip", "equip_card"}:
        card = "blade" if action == "equip" else value
        slot = SLOTS[_kind(card)]
        if _mode(game) == tournament.CHENG and slot == "guard":
            slot = "steed"
        seat["hand"].remove(card)
        if seat.get(slot):
            game["discard"].append(_unequip(game, seat, slot))
        seat[slot] = card
        _add_log(game, f"装备{_printed(card)}", player)
    elif action in {"cycle", "aid"}:
        game.setdefault("used_skills", []).append(action)
        if action == "cycle":
            _spend(game, seat, value)
            _draw(game, seat, 2)
        else:
            _spend(game, seat, SystemRandom().choice(seat["hand"]))
            _seat(game, value)["hp"] += 1
            _add_log(game, "弃一张牌，援护回复一点体力", player, value)
    elif action == "discard":
        _spend(game, seat, value)
        expansion.on_discard(game, seat, ENGINE)
        forest.on_phase_discard(game, seat, value, ENGINE)
        _add_log(game, f"弃置{_printed(value)}", player)
        if len(seat["hand"]) <= _capacity(game, seat):
            _advance(game)
    elif action == "end":
        _end_play(game, seat)
