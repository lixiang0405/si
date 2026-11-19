"""State for the reserve battle and a locally playable Cheng Tian trial deck."""

from collections import Counter
from random import SystemRandom

from card_catalog import CHENGTIAN_DECK as DECK
import chengtian_units as units


BLOOD = "blood_battle"
CHENG = "chengtian"
MODES = {
    CHENG: {
        "name": "承天之弈 · 实体规则验收", "minimum": 2, "capacity": 2, "exact": 2,
        "description": "选主帅和势力，编排四十张武将牌，在对弈中轮转和休整。",
    },
    BLOOD: {
        "name": "3v3 血战到底", "minimum": 6, "capacity": 6, "exact": 6,
        "description": "六人选将荐将，主帅配置三名备将，率先完成四次敌方击破取胜。",
    },
}
RULES = {
    CHENG: (
        "选择主帅和构筑势力，同势力开局五体力，双势力开局四体力。",
        "军营共四十张武将牌，第二势力至多十张。调整预组后确认，同名武将首先上阵。",
        "出牌阶段结束时上阵两将，超过五将时选将休整。走马换将将旧将置于军营底并补一将，换自身武将另摸一牌。",
        "采用实体一百零四张公共牌。乐不思蜀锁指定武将的技能。距离取消，坐骑增加手牌上限。",
    ),
    BLOOD: (
        "每人从五张武将中选一人上场，另荐两人。两位主帅各从本队六名候选中私下选定三名共同备将。",
        "暖方主帅先手，每座起手四张，主帅多一点体力。阵亡后清空该座牌区和横置，主帅选一名备将立即接替，补四张手牌。",
        "敌方击破计一分，先得四分一方获胜。击破本方角色不计分，阵亡没有额外摸牌或弃牌奖惩。",
    ),
}

WARM, COLD = "暖方", "冷方"
COMMANDERS = {"魏": "cao_cao", "蜀": "liu_bei", "吴": "sun_quan", "群": "yuan_shao"}
SETUP = {"draft_active", "draft_nominate", "draft_reserve", "ct_commander", "ct_alliance", "ct_compose"}
FORCED = {*SETUP, "reinforce_reserve", "ct_trim", "ct_skill", "ct_skill_choice"}
SKILL_EFFECTS = {
    "strategy": ("谋略", "指定一名武将休整，然后摸两张手牌。"),
    "draw": ("筹算", "指定一名武将休整，然后摸两张手牌。"),
    "cycle": ("调度", "指定一名武将休整，然后摸两张手牌。"),
    "fury": ("追击", "指定一名武将休整，本回合可以多使用一张杀。"),
    "switch": ("换锋", "指定一名武将休整，本回合的杀与闪可以互换使用。"),
    "recover": ("坚忍", "指定一名武将休整，为自己的主帅回复一点体力。"),
    "aid": ("援护", "指定一名武将休整，为自己的主帅回复一点体力。"),
    "limit": ("蓄势", "指定一名武将休整，本回合手牌上限加一。"),
}
DEFAULT_SKILL = ("整军", "指定一名武将休整，然后摸一张手牌。")


def _option(action, label, value, **details):
    return {"id": action, "label": label, "value": value, **details}


def _seat(game, key):
    return next(seat for seat in game["seats"] if seat["id"] == key)


def _general(key, generals):
    if key in units.BY_KEY:
        return units.info(key)
    name, faction, hp, skill = generals[key]
    return {"id": key, "name": name, "faction": faction, "hp": hp, "skill": skill}


def _reveal(seat, key, generals, *, captain_bonus=False):
    hp = generals[key][2] + int(captain_bonus)
    for marker in ("face_down", "skill_disabled", "general_marks"):
        seat.pop(marker, None)
    seat.update(alive=True, generals=[key], revealed=[key], max_hp=hp, hp=hp)


def initialize(game, mode, rng, generals, *, official=False):
    """Start tournament-specific selection. Core handles other modes."""
    if mode not in MODES:
        return False
    for seat in game["seats"]:
        game["deck"].extend(seat["hand"])
        seat.update(hand=[], generals=[], revealed=[], max_hp=0, hp=0)
    rng.shuffle(game["deck"])
    if mode == BLOOD:
        # Drafting alternate printings of one named general would give both
        # teams the same on-table character, so take one printing per name.
        unique = {}
        for key in rng.sample(list(generals), len(generals)):
            unique.setdefault(generals[key][0], key)
        if len(unique) < 30:
            raise ValueError("血战到底需要至少三十名不同武将。")
        rng.shuffle(game["seats"])
        pool = rng.sample(list(unique.values()), 30)
        teams = {name: {"captain": None, "reserves": []} for name in (WARM, COLD)}
        entries = {}
        for index, seat in enumerate(game["seats"]):
            side = (WARM, COLD)[index % 2]
            role = "主帅" if index < 2 else "前锋"
            seat.update(role=role, team=side)
            if role == "主帅":
                teams[side]["captain"] = seat["id"]
            entries[seat["id"]] = {"offers": pool[index * 5:index * 5 + 5], "active": None, "nominees": []}
        game["active"] = teams[WARM]["captain"]
        game["phase"] = "draft_active"
        game["tournament"] = {"entries": entries, "teams": teams, "score": dict.fromkeys(teams, 0)}
    else:
        entries = {}
        for index, seat in enumerate(game["seats"]):
            team = ("甲方", "乙方")[index]
            seat.update(role="主帅", team=team)
            entries[seat["id"]] = {"commander": None, "alliance": None, "ready": False}
        game["phase"] = "ct_commander"
        game["tournament"] = {"entries": entries, "unit_serial": 0, "official": official}
    return True


def turn(game):
    phase = game["phase"]
    if phase not in FORCED:
        return None
    state = game["tournament"]
    entries = state["entries"]
    if phase in {"ct_skill", "ct_skill_choice"}:
        return state["skill_window" if phase == "ct_skill" else "decision"]["player"]
    if phase == "reinforce_reserve":
        return state["teams"][state["reinforce_team"]]["captain"]
    if phase == "draft_reserve":
        return next((team["captain"] for team in state["teams"].values() if len(team["reserves"]) < 3), None)
    if phase == "ct_trim":
        return game["active"]
    need_choice = {
        "draft_active": lambda entry: entry["active"] is None,
        "draft_nominate": lambda entry: len(entry["nominees"]) < 2,
        "ct_commander": lambda entry: entry["commander"] is None,
        "ct_alliance": lambda entry: entry["alliance"] is None,
        "ct_compose": lambda entry: not entry["ready"],
    }[phase]
    return next((seat["id"] for seat in game["seats"] if need_choice(entries[seat["id"]])), None)


def _nominees(game, team):
    entries = game["tournament"]["entries"]
    return [key for seat in game["seats"] if seat["team"] == team for key in entries[seat["id"]]["nominees"]]


def _choices(action, verb, keys, generals):
    return [_option(action, f"{verb} {generals[key][0]}", key, general=_general(key, generals)) for key in keys]


def actions(game, seat, generals, engine=None):
    phase = game["phase"]
    if phase not in FORCED:
        return None
    if seat is None or turn(game) != seat["id"]:
        return []
    state = game["tournament"]
    own = state["entries"][seat["id"]]
    if phase == "ct_skill":
        window = state["skill_window"]
        return [*units.choices(game, seat, window["event"], engine, pending=game.get("pending")),
                engine._option("ct_pass_skill", "跳过武将技能")]
    if phase == "ct_skill_choice":
        return units.decision_actions(game, seat, engine)
    if phase == "draft_active":
        return _choices("select_active", "上场", own["offers"], generals)
    if phase == "draft_nominate":
        return _choices("nominate_general", "推荐", (key for key in own["offers"]
                        if key != own["active"] and key not in own["nominees"]), generals)
    if phase in {"draft_reserve", "reinforce_reserve"}:
        reserves = state["teams"][seat["team"]]["reserves"]
        if phase == "draft_reserve":
            return _choices("select_reserve", "选为备将", (key for key in _nominees(game, seat["team"])
                            if key not in reserves), generals)
        return _choices("revive_general", "替补上场", reserves, generals)
    if phase == "ct_commander":
        if state.get("official"):
            return [_option("select_commander", f"选择主帅 {generals[units.key(start)][0]}", units.key(start),
                            general=units.info(units.key(start)) | {"code": units.key(commander), "skill": "同名武将开局上阵",
                                                                    "description": "以一张同名武将为起始武将。"})
                    for commander, start in units.COMMANDERS.values()]
        return _choices("select_commander", "选择主帅", COMMANDERS.values(), generals)
    if phase == "ct_alliance":
        faction = generals[own["commander"]][1]
        return [_option("select_alliance", "单势力 · 5 体力", "single"),
                *(_option("select_alliance", f"联用{key}势力 · 4 体力", key)
                  for key in COMMANDERS if key != faction)]
    if phase == "ct_trim":
        return [_option("trim_general", f"遣下 {generals[unit['general']][0]}", unit["id"],
                        general=_general(unit["general"], generals)) for unit in own["field"]]
    cards = own["roster"]
    faction = generals[own["commander"]][1]
    secondary = own["alliance"]
    pool = units.GENERALS if state.get("official") else generals
    available = [key for key, info in pool.items() if info[1] == faction
                 or (info[1] == secondary and sum(generals[card][1] == secondary for card in cards) < 10)]
    choices = []
    if len(cards) == 40 and own["commander"] in cards:
        choices.append(_option("confirm_roster", "确认军营 · 40 张", True))
    if len(cards) < 40:
        choices.extend(_choices("add_general", "收录", available, generals))
        if state.get("official"):
            choices.append(_option("ct_fill", "从已核对的牌补满军营", True))
    choices.extend(_choices("remove_general", "移出",
                   (key for key in dict.fromkeys(cards) if key != own["commander"] or cards.count(key) > 1), generals))
    return choices


def _append_unit(game, entry, key):
    state = game["tournament"]
    state["unit_serial"] += 1
    entry["field"].append({"id": f"u{state['unit_serial']}", "general": key})


def _fill_field(game, entry, amount, generals):
    names = []
    for _ in range(min(amount, len(entry["army"]))):
        key = entry["army"].pop()
        _append_unit(game, entry, key)
        names.append(generals[key][0])
    return names


def _leave_field(entry, unit):
    entry["field"].remove(unit)
    entry["retired"].append(unit["general"])


def _update_field(seat, entry):
    seat["generals"] = [unit["general"] for unit in entry["field"]]
    seat["revealed"] = seat["generals"].copy()


def _shuffled_roster(entry, generals, *, official=False):
    rng = SystemRandom()
    faction = generals[entry["commander"]][1]
    ally = entry["alliance"]
    pool = units.GENERALS if official else generals
    fixed = entry.get("roster", [entry["commander"]]).copy()
    main = [key for key, info in pool.items() if info[1] == faction]
    side = [key for key, info in pool.items() if info[1] == ally]
    side_count = min(max(0, 10 - sum(generals[key][1] == ally for key in fixed)), 40 - len(fixed)) if side else 0
    cards = [*fixed, *rng.choices(main, k=40 - len(fixed) - side_count)]
    return cards + (rng.choices(side, k=side_count) if side_count else [])


def _launch_chengtian(game, generals, draw, log, begin_turn):
    for seat in game["seats"]:
        entry = game["tournament"]["entries"][seat["id"]]
        army = entry["roster"].copy()
        first = entry["commander"]
        army.remove(first)
        SystemRandom().shuffle(army)
        entry.update(army=army, field=[], retired=[], used=[], earned=[])
        _append_unit(game, entry, first)
        hp = 5 if entry["alliance"] == "single" else 4
        seat.update(hp=hp, max_hp=hp)
        _update_field(seat, entry)
        draw(game, seat, 4)
        log(game, f"{generals[first][0]}就位，军营确认", seat["id"])
    begin_turn(game)


def play(game, seat, action, value, generals, *, begin_turn, draw, log, complete, finish_end_play,
         prepare_seat=None, engine=None):
    """Apply an already validated tournament action; return False for core actions."""
    mode = game.get("mode")
    if mode not in MODES:
        return False
    state = game["tournament"]
    own = state["entries"][seat["id"]]
    phase = game["phase"]
    if action == "select_active":
        own["active"] = value
        if not turn(game):
            game["phase"] = "draft_nominate"
    elif action == "nominate_general":
        own["nominees"].append(value)
        if not turn(game):
            for member in game["seats"]:
                key = state["entries"][member["id"]]["active"]
                _reveal(member, key, generals, captain_bonus=member["role"] == "主帅")
                if prepare_seat:
                    prepare_seat(game, member)
            game["phase"] = "draft_reserve"
    elif action == "select_reserve":
        state["teams"][seat["team"]]["reserves"].append(value)
        if not turn(game):
            for member in game["seats"]:
                draw(game, member, 4)
            begin_turn(game)
    elif action == "revive_general":
        target = _seat(game, state.pop("reinforce_target"))
        team = state.pop("reinforce_team")
        state["teams"][team]["reserves"].remove(value)
        _reveal(target, value, generals, captain_bonus=target["role"] == "主帅")
        if prepare_seat:
            prepare_seat(game, target)
        draw(game, target, 4)
        log(game, f"备将{generals[value][0]}上场", seat["id"], target["id"])
        complete(game)
    elif action == "select_commander":
        own["commander"] = value
        if not turn(game):
            game["phase"] = "ct_alliance"
    elif action == "select_alliance":
        own["alliance"] = value
        own["roster"] = [own["commander"]] if state.get("official") else _shuffled_roster(own, generals)
        if not turn(game):
            game["phase"] = "ct_compose"
    elif action == "ct_fill":
        own["roster"] = _shuffled_roster(own, generals, official=True)
    elif action in {"add_general", "remove_general"}:
        if action == "add_general":
            own["roster"].append(value)
        else:
            own["roster"].remove(value)
    elif action == "confirm_roster":
        own["ready"] = True
        if not turn(game):
            _launch_chengtian(game, generals, draw, log, begin_turn)
    elif action in {"ct_use_skill", "ct_pass_skill"}:
        window = state.pop("skill_window", None)
        if window:
            game["phase"] = window["resume"]
        if action == "ct_use_skill" and units.apply(game, seat, value, engine):
            return True
        if window:
            pending = game["pending"]
            if window["event"] in {"slash_red", "slash_targeted"}:
                engine._ask_dodge(game, pending)
            elif window["event"] == "dodged_slash":
                engine._after_dodge(game, pending)
            else:
                complete(game)
    elif action == "ct_decide":
        units.decide(game, seat, value, engine)
    elif action in {"trim_general", "rest_general"}:
        target, entry = seat, own
        unit_id = value["cost"] if action == "rest_general" else value
        unit = next(unit for unit in entry["field"] if unit["id"] == unit_id)
        if action == "rest_general":
            issuer = next(unit for unit in entry["field"] if unit["id"] == value["issuer"])
            _apply_skill(game, seat, entry, issuer, unit, generals, draw, log)
        _leave_field(entry, unit)
        _update_field(target, entry)
        if phase == "ct_trim" and len(entry["field"]) <= 5:
            finish_end_play(game, seat)
    else:
        return False
    return True


def _apply_skill(game, seat, entry, issuer, cost, generals, draw, log):
    key = issuer["general"]
    skill = generals[key][3]
    entry["used"].append(issuer["id"])
    if issuer == cost:
        draw(game, seat, 1)
    if skill in {"strategy", "draw", "cycle"}:
        draw(game, seat, 2)
    elif skill in {"fury", "switch", "limit"}:
        entry["earned"].append(skill)
    elif skill in {"recover", "aid"}:
        seat["hp"] = min(seat["max_hp"], seat["hp"] + 1)
    else:
        draw(game, seat, 1)
    log(game, f"{generals[key][0]}发动{SKILL_EFFECTS.get(skill, DEFAULT_SKILL)[0]}，{generals[cost['general']][0]}休整",
        seat["id"])


def extra_actions(game, seat, generals, engine=None):
    if game.get("mode") != CHENG:
        return []
    if game["tournament"].get("official"):
        phase = game["phase"]
        if phase == "play":
            options = units.choices(game, seat, "play", engine)
            limit = 1 if engine._kind(seat.get("weapon")) != "crossbow" else float("inf")
            if game["strikes"] < limit:
                options.extend(units.choices(game, seat, "slash", engine))
            return options
        timing = {"dodge": "dodge", "strike_response": "slash"}.get(phase)
        return units.choices(game, seat, timing, engine) if timing else []
    if game["phase"] != "play":
        return []
    entries = game["tournament"]["entries"]
    own = entries[seat["id"]]
    choices = []
    for issuer in own["field"]:
        if issuer["id"] in own["used"]:
            continue
        name, _, _, skill = generals[issuer["general"]]
        title, _ = SKILL_EFFECTS.get(skill, DEFAULT_SKILL)
        choices.extend(_option("rest_general", f"{name}·{title} · 休整{generals[cost['general']][0]}",
                       {"issuer": issuer["id"], "cost": cost["id"]}) for cost in own["field"])
    return choices


def unit_targets(game, player, *, opponents):
    for owner, entry in game["tournament"]["entries"].items():
        if not opponents or owner != player:
            yield from ((owner, unit) for unit in entry["field"])


def lock_unit(game, card, unit):
    game["tournament"].setdefault("unit_locks", {})[card] = unit


def clear_unit_lock(game, card):
    if game.get("mode") == CHENG:
        game["tournament"].get("unit_locks", {}).pop(card, None)


def swap_general(game, actor, target, unit_id, generals, draw, log):
    entry = game["tournament"]["entries"][target["id"]]
    unit = next((unit for unit in entry["field"] if unit["id"] == unit_id), None)
    if unit is None:
        return
    entry["field"].remove(unit)
    entry["army"].insert(0, unit["general"])
    incoming = _fill_field(game, entry, 1, generals)
    _update_field(target, entry)
    if target is actor:
        draw(game, actor, 1)
    log(game, f"走马换将，{generals[unit['general']][0]}回军营底，{incoming[0]}上阵", actor["id"], target["id"])


def begin_turn(game, seat):
    if game.get("mode") == CHENG and "tournament" in game:
        own = game["tournament"]["entries"][seat["id"]]
        own.update(used=[], earned=[])
        for unit in own["field"]:
            unit.pop("tapped", None)


def earned_skills(game, seat):
    return set(game["tournament"]["entries"][seat["id"]].get("earned", ()))


def end_play(game, seat, generals, log):
    if game.get("mode") != CHENG:
        return False
    own = game["tournament"]["entries"][seat["id"]]
    names = _fill_field(game, own, 2, generals)
    if names:
        _update_field(seat, own)
        log(game, f"上阵 {'、'.join(names)}", seat["id"])
    if len(own["field"]) > 5:
        game["phase"] = "ct_trim"
        return True
    return False


def _winners(game, team):
    return [seat["id"] for seat in game["seats"] if seat["team"] == team]


def on_defeat(game, target, actor, generals, *, draw, log, complete, finish):
    if game.get("mode") != BLOOD:
        return False
    state = game["tournament"]
    if actor is not None and actor["team"] != target["team"]:
        team = actor["team"]
        state["score"][team] += 1
        log(game, f"{team}击破得分，比分 {state['score'][WARM]} : {state['score'][COLD]}", actor["id"])
        if state["score"][team] == 4:
            finish(game, f"{team}率先完成四次击破", _winners(game, team))
            return True
    if target["id"] == game["active"]:
        game["tournament_turn_ended"] = True
    target["chained"] = False
    target["turned_over"] = False
    team = target["team"]
    if state["teams"][team]["reserves"]:
        state.update(reinforce_team=team, reinforce_target=target["id"])
        game["phase"] = "reinforce_reserve"
    elif not any(seat["alive"] for seat in game["seats"] if seat["team"] == team):
        winner = COLD if team == WARM else WARM
        finish(game, f"{team}的上场武将已全部阵亡，{winner}获胜", _winners(game, winner))
    else:
        complete(game)
    return True


def public(game, player, generals):
    """Project private roster information to the correct browser seat."""
    if game.get("mode") not in MODES:
        return None
    state = game["tournament"]
    entry = state["entries"].get(player)
    if game["mode"] == BLOOD:
        seat = _seat(game, player) if entry else None
        team = state["teams"][seat["team"]] if seat else None
        reserve = {key: len(value["reserves"]) for key, value in state["teams"].items()}
        return {
            "phase": game["phase"], "score": state["score"].copy(), "reserve_counts": reserve,
            "your_reserves": [_general(key, generals) for key in team["reserves"]] if team else [],
            "your_nominees": [_general(key, generals) for key in entry["nominees"]] if entry else [],
            "your_active": _general(entry["active"], generals) if entry and entry["active"] else None,
            "reinforce_target": state.get("reinforce_target"),
        }
    def unit_details(unit):
        key = unit["general"]
        profile = _general(key, generals)
        if key in units.BY_KEY:
            locked = unit["id"] in state.get("unit_locks", {}).values()
            return {**profile, "unit": unit["id"], "locked": locked}
        title, description = SKILL_EFFECTS.get(profile["skill"], DEFAULT_SKILL)
        return {**profile, "unit": unit["id"], "skill": title, "description": description}

    roster = Counter(entry.get("roster", [])) if entry else {}
    return {
        "phase": game["phase"],
        "armies": [{"player": key, "count": len(row.get("army", [])), "retired": len(row.get("retired", [])),
                    "field": [unit_details(unit) for unit in row.get("field", [])]} for key, row in state["entries"].items()],
        "your_commander": _general(entry["commander"], generals) if entry and entry["commander"] else None,
        "your_alliance": entry.get("alliance") if entry else None,
        "your_roster": [{**_general(key, generals), "count": count} for key, count in roster.items()]
        if game["phase"] in SETUP else [],
    }
