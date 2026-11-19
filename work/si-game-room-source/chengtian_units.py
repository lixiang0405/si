"""Implemented TY01 printings, transcribed from the publisher's card images."""

from random import SystemRandom

from chengtian_wu_qun import UNITS as WU_QUN


UNITS = {
    4: ("夏侯杰", "魏", "play", "ask_strike", "休整一将。除非对手使用一张杀，否则你摸一张。"),
    5: ("文聘", "魏", "damaged_slash", "recruit", "受到杀伤害后，可休整一将并上阵一将。"),
    7: ("甄姬", "魏", "dodge", "black_dodge", "休整一将，可将黑色牌当闪使用。"),
    8: ("胡遵", "魏", "play", "sort_army", "休整一将，查看军营顶两将，选择一张置于军营底。"),
    43: ("曹操", "魏", "damaged", "draw_black", "受伤后可休整一将并摸一张。伤害牌是黑色时，取得该牌。"),
    54: ("严颜", "蜀", "play", "heart_arrows", "休整一将，将一张红桃牌当万箭齐发使用。"),
    56: ("关平", "蜀", "slash_red", "free_use", "使用红色杀后可休整一将，令这张杀不计入出牌次数。"),
    57: ("傅肜", "蜀", "slash", "convert_fire", "休整一将，将一张杀当火杀使用或打出。"),
    59: ("马良", "蜀", "hit_trick", "recruit", "用锦囊造成伤害后可休整一将，再上阵一将。"),
    61: ("高翔", "蜀", "hit_slash", "discard_draw", "用杀造成伤害后可休整一将，弃一张，再摸两张。"),
    62: ("关羽", "蜀", "slash", "red_slash", "休整一将，将红色牌当杀使用或打出。"),
    65: ("周仓", "蜀", "dodged_slash", "retrieve", "你的杀被抵消后可休整一将，取得这张杀。"),
    72: ("刘备", "蜀", "play", "liu_recruit", "每出牌阶段一次。休整一将，亮出军营顶三将，选一名非刘备的蜀将上阵，其余随机置于军营底。"),
    137: ("孙尚香", "吴", "play", "recruit_equipment", "每出牌阶段一次。休整一将，按己方装备数上阵同样数量的武将，令对手摸同样数量的牌。"),
    150: ("孙权", "吴", "play", "sun_cycle", "每出牌阶段一次。按休整区吴将数加二为上限弃牌并等量摸牌，休整区全为吴将时再摸一张。界限突破免费用的分支待接入。"),
    193: ("于吉", "群", "play", "yu_tax", "休整自身。对手弃一张手牌或受到伤害，伤害量为休整区同卡号的于吉张数。"),
    199: ("貂蝉", "群", "play", "pressure", "每出牌阶段一次，休整一将。若对手本回合尚未用牌或打出牌，你的杀和决斗的基础伤害改为两点，至本回合结束。"),
}
for number, timing, effect in ((107, "play", "black_dismantle"), (109, "dodged_slash", "draw1"), (112, "dodge", "red_dodge"),
                               (114, "slash_targeted", "draw1"), (153, "play", "club_duel"), (162, "slash", "black_slash"),
                               (165, "play", "spade_wine")):
    name, faction, _, _, _, description = WU_QUN[number]
    UNITS[number] = name, faction, timing, effect, description

COMMANDERS = {"魏": (201, 43), "蜀": (204, 72), "吴": (207, 150), "群": (212, 199)}


def key(number):
    return f"TY01-{number:03d}"


BY_KEY = {key(number): values for number, values in UNITS.items()}
GENERALS = {identifier: (name, faction, 0, effect) for identifier, (name, faction, _, effect, _) in BY_KEY.items()}


def info(identifier):
    name, faction, _, _, description = BY_KEY[identifier]
    return {"id": identifier, "name": name, "faction": faction, "skill": identifier, "description": description,
            "pack": "chengtian"}


def try_stage(game, event, player, engine, *, pending=None):
    if game.get("mode") != "chengtian" or not player["alive"]:
        return False
    state = game["tournament"]
    options = choices(game, player, event, engine, pending=pending)
    if not options:
        return False
    state["skill_window"] = {"event": event, "player": player["id"], "resume": game["phase"]}
    game["phase"] = "ct_skill"
    return True


def choices(game, seat, timing, engine, *, pending=None):
    entry = game["tournament"]["entries"][seat["id"]]
    disabled = set(game["tournament"].get("unit_locks", {}).values())
    for_unit = []
    for unit in entry["field"]:
        row = BY_KEY.get(unit["general"])
        if not row or unit["id"] in disabled or unit.get("tapped") or row[2] != timing:
            continue
        name, _, _, effect, desc = row
        if timing in {"damaged_slash", "hit_slash", "slash_red", "dodged_slash"} and not (pending or {}).get("slash"):
            continue
        if effect == "free_use" and not ((pending or {}).get("color") == "red" and game["active"] == seat["id"] and game["strikes"] > 0):
            continue
        if effect == "retrieve" and (pending or {}).get("physical") not in game["discard"]:
            continue
        if effect == "recruit" and not entry["army"]:
            continue
        if effect == "sort_army" and len(entry["army"]) < 2:
            continue
        if effect in {"ask_strike", "heart_arrows", "liu_recruit", "recruit_equipment", "sun_cycle", "club_duel", "spade_wine"}:
            if unit["id"] in entry["used"]:
                continue
        opponent = next(other for other in game["seats"] if other is not seat)
        cost_units = [unit] if effect == "yu_tax" else entry["field"]
        if effect == "spade_wine" and (game.get("wine") or "wine" in game["used_skills"]):
            continue
        for cost in cost_units:
            material = [None]
            if effect == "heart_arrows":
                material = [card for card in seat["hand"] if engine._suit(card) == "红桃"]
            if effect == "discard_draw":
                material = list(seat["hand"])
            if effect in {"black_dodge", "black_slash", "black_dismantle"}:
                material = [card for card in seat["hand"] if engine._suit(card) in {"黑桃", "梅花"}]
            if effect in {"red_slash", "red_dodge"}:
                material = [card for card in seat["hand"] if engine._suit(card) in {"红桃", "方片"}]
            if effect in {"club_duel", "spade_wine"}:
                material = [card for card in seat["hand"] if engine._suit(card) == ("梅花" if effect == "club_duel" else "黑桃")]
            if effect == "black_dismantle" and not engine._has_assets(opponent):
                continue
            if effect == "convert_fire":
                material = [card for card in seat["hand"] if engine._kind(card) in engine.SLASHES]
            if effect == "liu_recruit" and not entry["army"]:
                continue
            if effect == "recruit_equipment" and not any(seat.get(slot) for slot in set(engine.SLOTS.values())):
                continue
            cost_label = f" · 休整{GENERALS[cost['general']][0]}" if cost else " · 界限突破"
            for card in dict.fromkeys(material):
                value = {"issuer": unit["id"], "cost": cost["id"] if cost else None, "card": card}
                label = name + cost_label + (f" · {engine._printed(card)}" if card else "")
                for_unit.append(engine._option("ct_use_skill", label, value, opponent["id"] if timing == "slash" else None, card))
    return for_unit


def pay(game, seat, value, engine):
    entry = game["tournament"]["entries"][seat["id"]]
    unit = next(unit for unit in entry["field"] if unit["id"] == value["issuer"])
    effect = BY_KEY[unit["general"]][3]
    entry["used"].append(unit["id"])
    if value["cost"]:
        cost = next(unit for unit in entry["field"] if unit["id"] == value["cost"])
        entry["field"].remove(cost)
        entry["retired"].append(cost["general"])
        engine.tournament._update_field(seat, entry)
        if unit is cost:
            engine._draw(game, seat, 1)
    engine._add_log(game, f"发动{GENERALS[unit['general']][0]}的武将技能", seat["id"])
    return effect


def apply(game, seat, value, engine):
    state = game["tournament"]
    entry = state["entries"][seat["id"]]
    effect = pay(game, seat, value, engine)
    pending = game.get("pending") or {}
    card = value["card"]
    opponent = next(other for other in game["seats"] if other is not seat)
    if effect == "recruit":
        engine.tournament._fill_field(game, entry, 1, GENERALS)
        engine.tournament._update_field(seat, entry)
    elif effect == "draw_black":
        engine._draw(game, seat, 1)
        physical = pending.get("physical", pending.get("card"))
        if physical in game["discard"] and engine._suit(physical) in {"黑桃", "梅花"}:
            game["discard"].remove(physical)
            seat["hand"].append(physical)
    elif effect == "discard_draw":
        engine._spend(game, seat, card)
        engine._draw(game, seat, 2)
    elif effect in {"black_dodge", "red_dodge"}:
        state["card_users"].append(seat["id"])
        engine._spend(game, seat, card)
        engine._after_dodge(game, pending)
        return True
    elif effect in {"red_slash", "black_slash", "convert_fire"}:
        state["card_users"].append(seat["id"])
        if game["phase"] == "strike_response":
            engine._spend(game, seat, card)
            engine._accept_strike_response(game, pending)
        else:
            engine._launch_slash(game, seat, card, [opponent["id"]], virtual_kind="fire_strike" if effect == "convert_fire" else "strike")
        return True
    elif effect == "free_use":
        game["strikes"] -= 1
    elif effect == "retrieve":
        game["discard"].remove(pending["physical"])
        seat["hand"].append(pending["physical"])
    elif effect in {"heart_arrows", "club_duel", "black_dismantle"}:
        kind = {"heart_arrows": "arrows", "club_duel": "duel", "black_dismantle": "dismantle"}[effect]
        state["card_users"].append(seat["id"])
        engine._use_trick(game, seat, card, None if kind == "arrows" else opponent["id"], virtual_kind=kind, materials=(card,))
        return True
    elif effect == "spade_wine":
        engine._spend(game, seat, card)
        state["card_users"].append(seat["id"])
        game["wine"] = True
        game["used_skills"].append("wine")
    elif effect in {"draw1", "draw2"}:
        engine._draw(game, seat, 1 if effect == "draw1" else 2)
    elif effect == "pressure":
        entry["pressure"] = game["turn_number"]
    elif effect == "recruit_equipment":
        count = sum(bool(seat.get(slot)) for slot in set(engine.SLOTS.values()))
        engine.tournament._fill_field(game, entry, count, GENERALS)
        engine.tournament._update_field(seat, entry)
        engine._draw(game, opponent, count)
    elif effect in {"sort_army", "liu_recruit", "sun_cycle", "ask_strike", "yu_tax"}:
        cards = entry["army"][-(2 if effect == "sort_army" else 3):] if effect in {"sort_army", "liu_recruit"} else []
        retired = entry["retired"]
        count = 2 + sum(GENERALS[key][1] == "吴" for key in retired) if effect == "sun_cycle" else 0
        state["decision"] = {"effect": effect, "source": seat["id"], "player": opponent["id"] if effect in {"ask_strike", "yu_tax"} else seat["id"],
                             "cards": cards, "cap": count, "paid": 0}
        game["phase"] = "ct_skill_choice"
        return True
    return False


def decision_actions(game, seat, engine):
    entry = game["tournament"]["decision"]
    effect, cards = entry["effect"], entry["cards"]
    option = engine._option
    if effect == "sort_army":
        return [option("ct_decide", f"{GENERALS[card][0]} · 置于军营底", index) for index, card in enumerate(cards)]
    if effect == "liu_recruit":
        return [option("ct_decide", f"{GENERALS[card][0]} · 上阵", index) for index, card in enumerate(cards)
                if GENERALS[card][0] != "刘备" and GENERALS[card][1] == "蜀"] or [option("ct_decide", "放回其余武将")]
    if effect == "sun_cycle":
        return [option("ct_decide", f"弃置{engine._printed(card)}", card, card=card) for card in dict.fromkeys(seat["hand"])
                if entry["paid"] < entry["cap"]] + [option("ct_decide", "结束制衡")]
    allowed = seat["hand"] if effect == "yu_tax" else engine._attack_cards(game, seat)
    return [option("ct_decide", f"{('弃置' if effect == 'yu_tax' else '使用')}{engine._printed(card)}", card, card=card)
            for card in dict.fromkeys(allowed)] + [option("ct_decide", "放弃响应")]


def decide(game, seat, value, engine):
    state = game["tournament"]
    choice = state["decision"]
    effect = choice["effect"]
    source = engine._seat(game, choice["source"])
    entry = state["entries"][source["id"]]
    if effect == "sun_cycle" and value:
        engine._spend(game, seat, value)
        choice["paid"] += 1
        return
    state.pop("decision")
    game["phase"] = "play"
    if effect == "sort_army":
        army = entry["army"]
        army.insert(0, army.pop(len(army) - 2 + value))
    elif effect == "liu_recruit":
        cards = choice["cards"]
        del entry["army"][-len(cards):]
        if value is not None:
            engine.tournament._append_unit(game, entry, cards.pop(value))
            engine.tournament._update_field(source, entry)
        SystemRandom().shuffle(cards)
        entry["army"][:0] = cards
    elif effect == "sun_cycle":
        engine._draw(game, source, choice["paid"] + int(bool(entry["retired"]) and all(GENERALS[key][1] == "吴" for key in entry["retired"])))
    elif effect == "ask_strike":
        if value:
            engine._launch_slash(game, seat, value, [source["id"]], count=False)
        else:
            engine._draw(game, source, 1)
    elif value:
        engine._spend(game, seat, value)
    else:
        amount = entry["retired"].count(key(193))
        engine._damage(game, {"actor": source["id"], "target": seat["id"], "damage": amount, "card": "strike"})
