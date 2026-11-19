"""Classic LE001–LE008 physical editions and their server-side decisions."""

from itertools import combinations, product
from random import SystemRandom


SKILLS = {
    "wuhun": ("武魂", "每受到一点伤害，伤害来源获得一枚梦魇。阵亡时令梦魇最多的一人判定，非桃或桃园结义则其直接阵亡。"),
    "gongxin": ("攻心", "出牌阶段限一次，查看一名其他角色的手牌，可将其中一张红桃牌亮出后弃置或置于牌堆顶。"),
    "qinyin": ("琴音", "弃牌阶段结束时，若你在此阶段弃置过至少两张手牌，可让所有角色各回复或失去一点体力。"),
    "yeyan_major": ("业炎·大业炎", "限定技，与小业炎共用。弃置四种花色各一张手牌并先失去三点体力，再将至多三点火伤分配给指定角色。"),
    "guixin": ("归心", "每受到一点伤害后，可从每名其他角色的区域获得一张牌，然后翻面。"),
    "wumou": ("无谋", "使用普通锦囊时，必须选择移去一枚暴怒或失去一点体力。"),
    "wuqian": ("无前", "移去两枚暴怒，令选中角色防具失效，本回合你拥有无双。"),
    "longhun": ("龙魂", "用等同于当前体力值（至少一张）的同花色牌转化。红桃视为桃、方片视为火杀、梅花视为闪、黑桃视为无懈。"),
    "jilue": ("极略", "拜印后可移去一枚忍发动鬼才、放逐、集智、制衡或完杀。"),
    "lianpo": ("连破", "你击杀过角色的回合结束后，可获得一个额外回合。"),
}

SUITS = ("黑桃", "梅花", "红桃", "方片")


def _state(game):
    return game.setdefault("divine_state", {})


def _marks(seat):
    return seat.setdefault("general_marks", {})


def _has(game, seat, skill, engine):
    return bool(seat and skill in engine._skills(game, seat))


def _suit(game, seat, card, engine):
    return engine.expansion.suit(game, seat, card, engine)


def hand_kind(game, seat, card, engine):
    if card in seat["hand"] and _has(game, seat, "wushen", engine) and _suit(game, seat, card, engine) == "红桃":
        return "strike"
    return engine._kind(card)


def _stage(game, label, seat, **fields):
    game.setdefault("general_state", {})["choice"] = {"label": label, "player": seat["id"], **fields}
    game["phase"] = "general"


def _unstage(game):
    return game["general_state"].pop("choice")


def _options(cards, action, text, engine):
    return [engine._option(action, f"{text}{engine._printed(card)}", card) for card in dict.fromkeys(cards)]


def _other_seats(game, seat, engine):
    return [engine._seat(game, player) for player in engine._after(game, seat["id"]) if player != seat["id"]]


def _pay_patience(seat):
    _marks(seat)["patience"] -= 1


def _jilue(game, seat, engine):
    marks = seat.get("general_marks", {})
    return _has(game, seat, "jilue", engine) and marks.get("awakened") and marks.get("patience", 0) > 0


def _recover(game, seat, engine):
    if seat["hp"] < seat["max_hp"]:
        seat["hp"] += 1
        engine._add_log(game, "琴音 · 回复一点体力", seat["id"])


def _lose_hp(game, seat, amount, engine):
    """Loss of HP skips damage skills and the source-dependent kill effects."""
    seat["hp"] -= amount
    engine._add_log(game, f"失去 {amount} 点体力", seat["id"])
    if seat["hp"] > 0:
        return False
    game.update(phase="rescue", pending={
        "actor": seat["id"], "target": seat["id"], "card": "strike", "no_source": True,
        "order": engine._rotate(game, game["active"]), "index": 0,
    })
    engine._add_log(game, "进入濒死，等待救援", seat["id"])
    return True


def start_setup(game, engine):
    """Select four from eleven, privately, before the first normal turn."""
    for seat in game["seats"]:
        if seat.pop("choose_initial_stars", False):
            seat["hand"].extend(seat["general_cards"].pop("star"))
            seat["general_cards"]["star"] = []
            needed = max(0, len(seat["hand"]) - 4)
            if needed:
                _stage(game, "divine_stars_setup", seat, left=needed)
                return True
    return False


def before_draw(game, seat, engine):
    if not _has(game, seat, "shelie", engine) or game["turn_start"].get("shelie_checked"):
        return False
    game["turn_start"]["shelie_checked"] = True
    _stage(game, "divine_shelie_start", seat)
    return True


def after_draw(game, seat, engine):
    if not _has(game, seat, "qixing", engine) or game["turn_start"].get("star_exchange"):
        return False
    game["turn_start"]["star_exchange"] = True
    if seat.get("general_cards", {}).get("star") and seat["hand"]:
        _stage(game, "divine_stars_exchange", seat, offered=seat["hand"].copy(), stars=seat["general_cards"]["star"].copy())
        return True
    return False


def armor_ignored(game, seat):
    mark = game.get("divine_state", {}).get("wuqian", {})
    return mark.get("turn") == game["turn_number"] and not mark.get("ended") and seat["id"] in mark.get("targets", ())


def extra_response(game, pending, engine, *, duel=False):
    mark = game.get("divine_state", {}).get("wuqian", {})
    if mark.get("turn") != game["turn_number"] or mark.get("owner") != pending["actor"]:
        return False
    if not _has(game, engine._seat(game, pending["actor"]), "wuqian", engine):
        return False
    if not duel and not pending.get("slash"):
        return False
    key = "wushuang_duel" if duel else "wushuang_slash"
    if pending.pop(key, False):
        return False
    pending[key] = True
    if not duel:
        pending.pop("armor_tried", None)
    engine._add_log(game, "无双，须再打出一张" + ("杀" if duel else "闪"), pending["target"])
    return True


def allow_peach(game, seat, target, engine):
    actor = engine._seat(game, game["active"])
    mark = actor.get("general_marks", {}).get("jilue_wansha")
    return not (actor["alive"] and mark == game["turn_number"] and _has(game, actor, "jilue", engine)) or (
        seat["id"] in {actor["id"], target}
    )


def _assets(seat, engine):
    return [*seat["hand"], *(seat[slot] for slot in dict.fromkeys(engine.SLOTS.values()) if seat.get(slot))]


def _dragon_cards(game, seat, suit, engine):
    if not _has(game, seat, "longhun", engine):
        return iter(())
    cards = (card for card in _assets(seat, engine) if _suit(game, seat, card, engine) == suit)
    return combinations(dict.fromkeys(cards), max(1, seat["hp"]))


def has_counter(game, seat, engine):
    return next(_dragon_cards(game, seat, "黑桃", engine), None) is not None


def _longhun_actions(game, seat, suit, action, label, engine):
    return [engine._option(action, f"龙魂 · {label} · " + "＋".join(map(engine._printed, cards)), list(cards))
            for cards in _dragon_cards(game, seat, suit, engine)]


def _use_dragon(game, seat, cards, engine):
    for card in cards:
        engine._discard_owned(game, seat, card)


def _fire_options(game, seat, engine):
    colors = [[card for card in seat["hand"] if _suit(game, seat, card, engine) == suit] for suit in SUITS]
    if not all(colors):
        return []
    return [engine._option("divine_yeyan_major", "大业炎 · 弃四种花色手牌", list(cards)) for cards in product(*colors)]


def active_actions(game, seat, engine):
    skills = engine._skills(game, seat)
    used = game["used_skills"]
    marks = seat.get("general_marks", {})
    result = []
    if "gongxin" in skills and "gongxin" not in used:
        result.extend(engine._option("divine_gongxin", "攻心 · 观看手牌", other["id"], other["id"])
                      for other in _other_seats(game, seat, engine) if other["hand"])
    if "yeyan_major" in skills and not marks.get("yeyan"):
        result.extend(_fire_options(game, seat, engine))
    if "wuqian" in skills and marks.get("rage", 0) >= 2:
        record = game.get("divine_state", {}).get("wuqian", {})
        targets = record.get("targets", ()) if record.get("turn") == game["turn_number"] else ()
        result.extend(engine._option("divine_wuqian", "无前 · 令目标防具失效", other["id"], other["id"])
                      for other in game["seats"] if other["alive"] and other["id"] not in targets)
    if "longhun" in skills:
        if seat["hp"] < seat["max_hp"]:
            result.extend(_longhun_actions(game, seat, "红桃", "divine_longhun_heal", "桃", engine))
        limit = 1 + int("fury" in skills) + int(seat["role"] == engine.LANDLORD)
        limit = engine.expansion.attack_limit(game, seat, limit, engine)
        if limit:
            for cards in _dragon_cards(game, seat, "方片", engine):
                weapon = seat.get("weapon")
                armed = engine._kind(weapon) if weapon not in cards else None
                if game["strikes"] >= limit and armed != "crossbow":
                    continue
                radius = engine.WEAPONS.get(armed, 1)
                origin = seat if seat.get("steed") not in cards else {**seat, "steed": None}
                live = [other["id"] for other in _other_seats(game, seat, engine)
                        if engine.expansion.attack_reaches(game, origin, other, radius, engine)]
                cap = 3 if set(cards) == set(seat["hand"]) and armed == "halberd" else 1
                for size in range(1, cap + 1):
                    result.extend(engine._option("divine_longhun_attack", "龙魂 · 火杀", {"cards": list(cards), "targets": list(targets)},
                                                 targets[0] if size == 1 else None)
                                  for targets in combinations(live, size))
    if _jilue(game, seat, engine) and marks.get("jilue_wansha") != game["turn_number"]:
        result.append(engine._option("divine_jilue_wansha", "极略 · 完杀 · 移去一枚忍"))
    return result


def _gongxin_actions(game, seat, choice, engine):
    target = engine._seat(game, choice["target"])
    result = [engine._option("divine_gongxin_done", "结束查看手牌")]
    for card in dict.fromkeys(target["hand"]):
        if _suit(game, target, card, engine) != "红桃":
            result.append(engine._option("divine_gongxin_seen", f"查看 · {engine._printed(card)}", card))
            continue
        result.extend(engine._option("divine_gongxin_move", f"{text}{engine._printed(card)}", {"card": card, "where": where})
                      for where, text in (("deck", "置于牌堆顶 · "), ("discard", "弃置 · ")))
    return result


def _asset_choices(target, game, engine):
    result = [engine._option("divine_guixin_pick", "取得一张暗手牌", "hand")] if target["hand"] else []
    for slot in dict.fromkeys(engine.SLOTS.values()):
        if card := target.get(slot):
            result.append(engine._option("divine_guixin_pick", f"取得{engine._printed(card)}", slot))
    for index, card in enumerate(target.get("judgement", [])):
        public = game.get("virtual_delays", {}).get(card, card)
        result.append(engine._option("divine_guixin_pick", f"取得判定区的{engine._card_name(public)}", {"index": index}))
    return result


def response_actions(game, seat, engine):
    phase = game["phase"]
    if phase != "general":
        if phase == "dodge":
            return _longhun_actions(game, seat, "梅花", "divine_longhun_dodge", "闪", engine)
        if phase in {"strike_response", "borrow"} or phase == "follow_up" and game["pending"].get("weapon_choice") == "blade":
            return _longhun_actions(game, seat, "方片", "divine_longhun_strike", "火杀", engine)
        if phase == "counter":
            return _longhun_actions(game, seat, "黑桃", "divine_longhun_counter", "无懈可击", engine)
        if phase == "rescue" and engine.expansion.peach_allowed(game, seat, game["pending"]["target"], engine):
            return _longhun_actions(game, seat, "红桃", "divine_longhun_rescue", "桃", engine)
        return []
    choice = game["general_state"]["choice"]
    label = choice["label"]
    if label == "divine_stars_setup":
        return _options(seat["hand"], label, f"置为星（还需 {choice['left']} 张） · ", engine)
    if label == "divine_stars_exchange":
        options = [engine._option("divine_stars_done", "结束七星交换")]
        options.extend(engine._option("divine_stars_swap", f"{engine._printed(card)} ⇄ {engine._printed(star)}",
                                      {"card": card, "star": star}) for card, star in product(choice["offered"], choice["stars"]))
        return options
    if label == "divine_shelie_start":
        return [engine._option("divine_shelie_use", "涉猎 · 亮出五张牌"), engine._option("divine_shelie_skip", "照常摸牌")]
    if label == "divine_shelie_pick":
        offered = seat.get("general_cards", {}).get("shelie", [])
        return _options(offered, label, "涉猎 · 取得", engine)
    if label == "divine_gongxin_pick":
        return _gongxin_actions(game, seat, choice, engine)
    if label == "divine_yeyan_target":
        options = [engine._option("divine_yeyan_cancel", "放弃发动大业炎")]
        positions = {other["id"]: index for index, other in enumerate(game["seats"], 1)}
        for target in engine._rotate(game, seat["id"]):
            options.append(engine._option("divine_yeyan_fire", "造成三点火焰伤害", [[target, 3]], target))
            options.append(engine._option("divine_yeyan_fire", "造成两点火焰伤害", [[target, 2]], target))
            for extra in engine._rotate(game, seat["id"]):
                if extra != target:
                    label = f"造成两点火焰伤害，并对 {positions[extra]} 号位造成一点"
                    options.append(engine._option("divine_yeyan_fire", label, [[target, 2], [extra, 1]], target))
        return options
    if label == "divine_shenfen_discard":
        return _options(seat["hand"], label, f"神愤 · 弃置手牌（还需 {choice['left']} 张） · ", engine)
    if label == "divine_qinyin":
        return [engine._option("divine_qinyin_heal", "琴音 · 全场回复一点体力"),
                engine._option("divine_qinyin_loss", "琴音 · 全场失去一点体力"),
                engine._option("divine_qinyin_skip", "放弃发动琴音")]
    if label == "divine_wuhun":
        return [engine._option("divine_wuhun_target", "武魂 · 指定判定目标", target, target) for target in choice["targets"]]
    if label == "divine_guixin":
        return [engine._option("divine_guixin_use", "发动一次归心"), engine._option("divine_guixin_skip", "不发动归心")]
    if label == "divine_guixin_pick":
        return _asset_choices(engine._seat(game, choice["target"]), game, engine)
    if label == "divine_wumou":
        options = [engine._option("divine_wumou_hp", "无谋 · 失去一点体力")]
        if seat.get("general_marks", {}).get("rage"):
            options.insert(0, engine._option("divine_wumou_rage", "无谋 · 移去一枚暴怒"))
        return options
    if label == "divine_jilue_jizhi":
        return [engine._option("divine_jizhi_use", "极略 · 集智 · 摸一张牌"), engine._option("divine_jizhi_skip", "放弃发动集智")]
    if label == "divine_jilue_fangzhu":
        count = seat["max_hp"] - seat["hp"]
        return [engine._option("divine_fangzhu_flip", f"极略 · 放逐 · 翻面并摸 {count} 张", target["id"], target["id"])
                for target in _other_seats(game, seat, engine)] + [engine._option("divine_fangzhu_skip", "放弃发动放逐")]
    if label == "divine_lianpo":
        return [engine._option("divine_lianpo_use", "发动连破并执行额外回合"), engine._option("divine_lianpo_skip", "放弃发动连破")]
    return []


def after_damage(game, pending, amount, engine):
    target = engine._seat(game, pending["target"])
    if not pending.get("no_source") and _has(game, target, "wuhun", engine):
        source = engine._seat(game, pending["actor"])
        marks = _marks(source).setdefault("nightmare", {})
        marks[target["id"]] = marks.get(target["id"], 0) + amount
        engine._add_log(game, f"武魂 · 获得 {amount} 枚梦魇", source["id"], target["id"])
    if _has(game, target, "guixin", engine):
        pending["divine_guixin"] = amount
    if _has(game, target, "jilue", engine) and target.get("general_marks", {}).get("awakened"):
        pending["divine_fangzhu"] = True


def after_survival(game, pending, engine):
    target = engine._seat(game, pending["target"])
    if not target["alive"] or target["hp"] <= 0:
        return False
    count = pending.pop("divine_guixin", 0)
    other = _other_seats(game, target, engine)
    if count and any(engine._has_assets(seat) for seat in other):
        _stage(game, "divine_guixin", target, remaining=count)
        return True
    if pending.pop("divine_fangzhu", False) and _jilue(game, target, engine) and other:
        _stage(game, "divine_jilue_fangzhu", target)
        return True
    return False


def _finish_guixin_once(game, seat, remaining, engine):
    seat["face_down"] = not seat.get("face_down", False)
    engine._add_log(game, "归心 · 翻面", seat["id"])
    if remaining > 1 and any(engine._has_assets(other) for other in _other_seats(game, seat, engine)):
        _stage(game, "divine_guixin", seat, remaining=remaining - 1)
    elif not after_survival(game, game["pending"], engine):
        engine._complete(game)


def _gain_from(game, source, target, value, engine):
    if value == "hand":
        card = target["hand"].pop(SystemRandom().randrange(len(target["hand"])))
    elif isinstance(value, dict):
        card = target["judgement"].pop(value["index"])
        game.get("virtual_delays", {}).pop(card, None)
    else:
        card = engine._unequip(game, target, value)
    source["hand"].append(card)
    if not isinstance(value, dict):
        engine.expansion.after_spend(game, target, engine)
    engine._add_log(game, "归心 · 取得一张牌", source["id"], target["id"])


def _advance_guixin(game, seat, choices, remaining, engine):
    while choices:
        target = engine._seat(game, choices.pop(0))
        if target["alive"] and engine._has_assets(target):
            _stage(game, "divine_guixin_pick", seat, target=target["id"], others=choices, remaining=remaining)
            return
    _finish_guixin_once(game, seat, remaining, engine)


def _death_ends_first(game, target, engine):
    mode = engine._mode(game)
    remaining = [other for other in game["seats"] if other["alive"] and other is not target]
    if mode == "identity":
        return target["role"] == engine.LORD or not any(other["role"] in {engine.REBEL, engine.SPY} for other in remaining)
    if mode == "team":
        return len({other["team"] for other in remaining if other["role"] == "主帅"}) == 1
    if mode == "factions":
        return len(remaining) == 1 or all(other["revealed"] for other in remaining) and len({other["team"] for other in remaining}) == 1
    if mode == "blood_battle":
        return False
    return len({other["team"] for other in remaining}) == 1


def before_eliminate(game, pending, engine):
    target = engine._seat(game, pending["target"])
    if pending.get("divine_wuhun_checked") or not _has(game, target, "wuhun", engine) or _death_ends_first(game, target, engine):
        return False
    candidates = {}
    for seat in _other_seats(game, target, engine):
        count = seat.get("general_marks", {}).get("nightmare", {}).get(target["id"], 0)
        if count:
            candidates[seat["id"]] = count
    if not candidates:
        return False
    maximum = max(candidates.values())
    pending["divine_wuhun_checked"] = True
    game["pending"] = pending
    _stage(game, "divine_wuhun", target, targets=[player for player, count in candidates.items() if count == maximum])
    return True


def on_death(game, target, source, engine):
    for other in game["seats"]:
        other.get("general_marks", {}).get("nightmare", {}).pop(target["id"], None)
    unpaid = game.get("divine_state", {}).get("unpaid_trick", {})
    if unpaid.get("actor") == target["id"]:
        unpaid["actor_died"] = True
    fire = game.get("divine_state", {}).get("yeyan_fire", {})
    if fire.get("actor") == target["id"]:
        fire["actor_died"] = True
    for effect in game["queue"]:
        if effect.get("general_skill") in {"shenfen", "yeyan"} and effect["actor"] == target["id"]:
            effect["no_source"] = True
    force = game.get("divine_state", {}).get("wuqian", {})
    if force.get("owner") == target["id"]:
        force["ended"] = True
    shenfen = game.get("general_state", {}).get("after_shenfen", {})
    if isinstance(shenfen, dict) and shenfen.get("actor") == target["id"]:
        shenfen["actor_died"] = True
    active = engine._seat(game, game["active"])
    if source and source["alive"] and (active is target or active["alive"]) and _has(game, source, "lianpo", engine):
        recorded = _state(game).setdefault("lianpo", [])
        if source["id"] not in recorded:
            recorded.append(source["id"])


def on_discard(game, seat, engine):
    if _has(game, seat, "qinyin", engine):
        _state(game)["qinyin_count"] = _state(game).get("qinyin_count", 0) + 1


def end_turn(game, seat, engine):
    state = game.get("divine_state", {})
    count = state.pop("qinyin_count", 0)
    if count >= 2 and seat["alive"] and _has(game, seat, "qinyin", engine):
        _stage(game, "divine_qinyin", seat)
        return True
    recorded = state.get("lianpo", [])
    while recorded:
        source = engine._seat(game, recorded.pop(0))
        if source["alive"] and _has(game, source, "lianpo", engine):
            _stage(game, "divine_lianpo", source, turn=seat["id"])
            return True
    return False


def next_player(game, regular, engine):
    state = game.get("divine_state", {})
    extra = state.get("extra_turns", [])
    while extra:
        player = extra.pop(0)
        if engine._seat(game, player)["alive"]:
            state.setdefault("normal_turn_next", regular)
            return player
    if normal := state.pop("normal_turn_next", None):
        return normal if engine._seat(game, normal)["alive"] else engine._after(game, normal)[0]
    return regular


def has_guicai(game, seat, engine):
    return bool(seat["hand"] and _jilue(game, seat, engine))


def pay_guicai(game, seat, engine):
    _pay_patience(seat)
    engine._add_log(game, "极略 · 鬼才改判", seat["id"])


def before_trick(game, seat, effect, kind, targets, engine):
    has_wumou = kind not in engine.TILTED and _has(game, seat, "wumou", engine)
    has_jizhi = _jilue(game, seat, engine)
    if not (has_wumou or has_jizhi):
        return False
    state = _state(game)
    state["unpaid_trick"] = {"actor": seat["id"], "effect": effect, "kind": kind, "targets": targets}
    _stage(game, "divine_wumou" if has_wumou else "divine_jilue_jizhi", seat)
    return True


def before_counter(game, seat, pending, engine, card=None):
    if not (_has(game, seat, "wumou", engine) or _jilue(game, seat, engine)):
        return False
    engine._spend(game, seat, card or "counterspell")
    _state(game)["unpaid_trick"] = {"actor": seat["id"], "counter": pending}
    _stage(game, "divine_wumou" if _has(game, seat, "wumou", engine) else "divine_jilue_jizhi", seat)
    return True


def _apply_trick(game, engine):
    action = _state(game).pop("unpaid_trick")
    actor = engine._seat(game, action["actor"])
    if "counter" in action:
        pending = action["counter"]
        if not engine._seat(game, pending["target"])["alive"]:
            if pending.get("judging"):
                game["discard"].append(pending["card"])
            engine._complete(game)
            return
        pending["negated"] = not pending["negated"]
        pending["passed"] = [actor["id"]]
        engine._add_log(game, "使用无懈可击", actor["id"], pending["target"])
        engine._counter_window(game, pending, engine._after(game, actor["id"]))
        return
    effect = action["effect"]
    if action.get("actor_died") or not actor["alive"]:
        effect["no_source"] = True
    game["phase"] = "play"
    engine._dispatch_trick(game, actor, effect, action["kind"], action["targets"])


def _after_wumou(game, seat, engine):
    if _jilue(game, seat, engine):
        _stage(game, "divine_jilue_jizhi", seat)
    else:
        _apply_trick(game, engine)


def _fire_chain(game, source, targets, engine, *, source_died=False):
    order = {player: index for index, player in enumerate(engine._rotate(game, game["active"]))}
    game["queue"][:0] = [{"actor": source["id"], "target": player, "card": "strike", "general_skill": "yeyan",
                          "damage": count, "nature": "fire", "settlement": "damage", "no_source": source_died or not source["alive"]}
                         for player, count in sorted(targets, key=lambda pair: order.get(pair[0], len(order)))
                         if not (source_died and player == source["id"])]
    engine._complete(game)


def after_shenfen(game, record, engine):
    # Existing matches may be saved while the damage phase is in progress.
    if isinstance(record, list):
        record = {"actor": game["active"], "targets": record, "actor_already_flipped": True}
    for player in record["targets"]:
        target = engine._seat(game, player)
        if not target["alive"]:
            continue
        slots = [slot for slot in dict.fromkeys(engine.SLOTS.values()) if target.get(slot)]
        for slot in slots:
            game["discard"].append(engine._unequip(game, target, slot))
    return _shenfen_hand(game, record, engine)


def _shenfen_hand(game, record, engine):
    order = record["targets"]
    while order:
        target = engine._seat(game, order.pop(0))
        if target["alive"] and target["hand"]:
            _stage(game, "divine_shenfen_discard", target, shenfen=record, left=min(len(target["hand"]), 4))
            return True
    owner = engine._seat(game, record["actor"])
    if owner["alive"] and not record.get("actor_died") and not record.get("actor_already_flipped"):
        owner["face_down"] = not owner.get("face_down", False)
        engine._add_log(game, "神愤 · 翻面", owner["id"])
    return False


def continue_effects(game, engine):
    state = game.get("divine_state", {})
    if victim_id := state.pop("wuhun_victim", None):
        victim = engine._seat(game, victim_id)
        if victim["alive"]:
            victim["hp"] = 0
            engine._add_log(game, "武魂判定未中，直接阵亡", victim_id)
            engine._eliminate(game, {"actor": victim_id, "target": victim_id, "card": "strike", "no_source": True})
            return True
    if major := state.pop("yeyan_fire", None):
        _fire_chain(game, engine._seat(game, major["actor"]), major["targets"], engine,
                    source_died=major.get("actor_died", False))
        return True
    if state.get("unpaid_trick", {}).pop("resume", False):
        actor = engine._seat(game, state["unpaid_trick"]["actor"])
        if actor["alive"] and not state["unpaid_trick"].get("actor_died"):
            _after_wumou(game, actor, engine)
        else:
            _apply_trick(game, engine)
        return True
    if "song_losses" in state:
        while state["song_losses"]:
            seat = engine._seat(game, state["song_losses"].pop(0))
            if seat["alive"] and _lose_hp(game, seat, 1, engine):
                return True
        state.pop("song_losses")
        engine._advance(game)
        return True
    return False


def judgement_result(game, context, card, suit, rank, engine):
    if context["kind"] != "divine_wuhun":
        return False
    if card and engine._kind(card) not in {"peach", "feast"}:
        _state(game)["wuhun_victim"] = context["victim"]
    else:
        engine._add_log(game, "武魂判定通过，角色存活", context["victim"])
    engine._eliminate(game, context["death"])
    return True


def _dragon_strike(game, seat, targets, engine, *, count):
    if count:
        game["strikes"] += 1
    own_turn = game["active"] == seat["id"]
    hits = 1 + int(own_turn and game.get("wine", False))
    if own_turn:
        game["wine"] = False
    game["queue"][:0] = [{"actor": seat["id"], "target": target, "card": "fire_strike", "damage": hits,
                          "nature": "fire", "slash": True, "color": "red", "settlement": "hit"}
                         for target in targets]
    engine._add_log(game, "龙魂 · 使用火杀", seat["id"])
    engine._complete(game)


def _shelie_pick(game, seat, card, engine):
    cards = seat["general_cards"]["shelie"]
    suit = _suit(game, seat, card, engine)
    cards.remove(card)
    seat["hand"].append(card)
    remainder = []
    for other in cards:
        if _suit(game, seat, other, engine) == suit:
            game["discard"].append(other)
        else:
            remainder.append(other)
    engine._add_log(game, f"涉猎 · 取得{engine._printed(card)}", seat["id"])
    if remainder:
        seat["general_cards"]["shelie"] = remainder
    else:
        seat["general_cards"].pop("shelie")
        _unstage(game)
        game["phase"] = "play"
        engine._finish_draw(game, seat)


def play(game, seat, action, value, engine):
    """Apply an option already validated against the phase's public projection."""
    if not action.startswith("divine_"):
        return False
    player, pending = seat["id"], game["pending"]
    if action == "divine_stars_setup":
        choice = game["general_state"]["choice"]
        seat["hand"].remove(value)
        seat["general_cards"]["star"].append(value)
        choice["left"] -= 1
        if not choice["left"]:
            _unstage(game)
            game["phase"] = "play"
            engine._begin_turn(game)
    elif action == "divine_stars_swap":
        choice = game["general_state"]["choice"]
        card, star = value["card"], value["star"]
        choice["offered"].remove(card)
        choice["stars"].remove(star)
        seat["hand"].remove(card)
        seat["general_cards"]["star"].remove(star)
        seat["hand"].append(star)
        seat["general_cards"]["star"].append(card)
        engine._add_log(game, "七星 · 交换一张星", player)
    elif action == "divine_stars_done":
        _unstage(game)
        game["phase"] = "play"
        engine._finish_draw(game, seat)
    elif action == "divine_shelie_skip":
        _unstage(game)
        game["phase"] = "play"
        if not engine.expansion.replace_draw(game, seat, engine):
            amount = 2 + int("draw" in engine._skills(game, seat)) + int(seat["role"] == engine.LANDLORD)
            engine._draw(game, seat, engine.expansion.draw_count(game, seat, amount, engine))
        engine._finish_draw(game, seat)
    elif action == "divine_shelie_use":
        _unstage(game)
        cards = [card for _ in range(5) if (card := engine._take(game)) is not None]
        if cards:
            seat.setdefault("general_cards", {})["shelie"] = cards
            engine._add_log(game, "涉猎亮牌 · " + "、".join(map(engine._printed, cards)), player)
            _stage(game, "divine_shelie_pick", seat)
        else:
            game["phase"] = "play"
            engine._finish_draw(game, seat)
    elif action == "divine_shelie_pick":
        _shelie_pick(game, seat, value, engine)
    elif action == "divine_gongxin":
        game["used_skills"].append("gongxin")
        _stage(game, "divine_gongxin_pick", seat, target=value)
        engine._add_log(game, "攻心 · 查看目标的手牌", player, value)
    elif action in {"divine_gongxin_done", "divine_gongxin_seen", "divine_gongxin_move"}:
        if action == "divine_gongxin_seen":
            return True
        choice = _unstage(game)
        if action == "divine_gongxin_move":
            target = engine._seat(game, choice["target"])
            card = value["card"]
            if value["where"] == "deck":
                target["hand"].remove(card)
                game["deck"].append(card)
                engine.expansion.after_spend(game, target, engine)
            else:
                engine._spend(game, target, card)
            engine._add_log(game, f"攻心 · 展示{engine._printed(card)}", player, target["id"])
        engine._complete(game)
    elif action == "divine_yeyan_major":
        _stage(game, "divine_yeyan_target", seat, payment=value)
    elif action == "divine_yeyan_cancel":
        _unstage(game)
        engine._complete(game)
    elif action == "divine_yeyan_fire":
        choice = _unstage(game)
        for card in choice["payment"]:
            engine._spend(game, seat, card)
        _marks(seat)["yeyan"] = 1
        _state(game)["yeyan_fire"] = {"actor": player, "targets": value}
        if not _lose_hp(game, seat, 3, engine):
            engine._complete(game)
    elif action == "divine_shenfen_discard":
        choice = game["general_state"]["choice"]
        engine._spend(game, seat, value)
        choice["left"] -= 1
        engine._add_log(game, "神愤 · 弃置手牌", player)
        if not choice["left"] or not seat["hand"]:
            _unstage(game)
            if not _shenfen_hand(game, choice["shenfen"], engine):
                engine._complete(game)
    elif action in {"divine_qinyin_heal", "divine_qinyin_loss", "divine_qinyin_skip"}:
        _unstage(game)
        if action == "divine_qinyin_loss":
            _state(game)["song_losses"] = engine._rotate(game, player)
            engine._complete(game)
        else:
            if action == "divine_qinyin_heal":
                for other in game["seats"]:
                    if other["alive"]:
                        _recover(game, other, engine)
            engine._advance(game)
    elif action == "divine_wuhun_target":
        from wind_fire_rules import judge

        _unstage(game)
        judge(game, engine._seat(game, value), {"kind": "divine_wuhun", "victim": value, "death": pending}, engine)
    elif action in {"divine_guixin_use", "divine_guixin_skip"}:
        choice = _unstage(game)
        if action == "divine_guixin_use":
            _advance_guixin(game, seat, [other["id"] for other in _other_seats(game, seat, engine)], choice["remaining"], engine)
        elif choice["remaining"] > 1:
            _stage(game, "divine_guixin", seat, remaining=choice["remaining"] - 1)
        elif not after_survival(game, pending, engine):
            engine._complete(game)
    elif action == "divine_guixin_pick":
        choice = _unstage(game)
        _gain_from(game, seat, engine._seat(game, choice["target"]), value, engine)
        _advance_guixin(game, seat, choice["others"], choice["remaining"], engine)
    elif action == "divine_wuqian":
        _marks(seat)["rage"] -= 2
        state = _state(game)
        if state.get("wuqian", {}).get("turn") != game["turn_number"] or state["wuqian"].get("owner") != player:
            state["wuqian"] = {"turn": game["turn_number"], "owner": player, "targets": []}
        state["wuqian"]["targets"].append(value)
        engine._add_log(game, "无前 · 防具失效并获得无双", player, value)
    elif action in {"divine_wumou_hp", "divine_wumou_rage"}:
        _unstage(game)
        if action == "divine_wumou_hp" and _lose_hp(game, seat, 1, engine):
            _state(game)["unpaid_trick"]["resume"] = True
        else:
            if action == "divine_wumou_rage":
                _marks(seat)["rage"] -= 1
                engine._add_log(game, "无谋 · 移去一枚暴怒", player)
            _after_wumou(game, seat, engine)
    elif action in {"divine_jizhi_use", "divine_jizhi_skip"}:
        _unstage(game)
        if action == "divine_jizhi_use":
            _pay_patience(seat)
            engine._draw(game, seat, 1)
            engine._add_log(game, "极略 · 集智", player)
        _apply_trick(game, engine)
    elif action in {"divine_fangzhu_flip", "divine_fangzhu_skip"}:
        _unstage(game)
        if action == "divine_fangzhu_flip":
            _pay_patience(seat)
            target = engine._seat(game, value)
            engine._draw(game, target, seat["max_hp"] - seat["hp"])
            target["face_down"] = not target.get("face_down", False)
            engine._add_log(game, "极略 · 放逐翻面", player, value)
        engine._complete(game)
    elif action == "divine_jilue_wansha":
        _pay_patience(seat)
        _marks(seat)["jilue_wansha"] = game["turn_number"]
        engine._add_log(game, "极略 · 本回合获得完杀", player)
    elif action in {"divine_lianpo_use", "divine_lianpo_skip"}:
        _unstage(game)
        if action == "divine_lianpo_use":
            _state(game).setdefault("extra_turns", []).append(player)
            engine._add_log(game, "连破 · 排入额外回合", player)
        engine._advance(game, skip_end=True)
    elif action == "divine_longhun_attack":
        _use_dragon(game, seat, value["cards"], engine)
        _dragon_strike(game, seat, value["targets"], engine, count=True)
    elif action.startswith("divine_longhun_"):
        phase = game["phase"]
        _use_dragon(game, seat, value, engine)
        engine._add_log(game, "龙魂 · 转化同花色牌", player)
        if action == "divine_longhun_heal":
            seat["hp"] += 1
        elif action == "divine_longhun_counter":
            pending["negated"] = not pending["negated"]
            pending["passed"] = [player]
            engine._counter_window(game, pending, engine._after(game, player))
        elif action == "divine_longhun_dodge":
            engine._after_dodge(game, pending)
        elif action == "divine_longhun_rescue":
            target = engine._seat(game, pending["target"])
            target["hp"] += 1
            if target["hp"] > 0:
                engine._complete(game)
        elif phase == "strike_response":
            engine._accept_strike_response(game, pending)
        elif phase in {"borrow", "follow_up"}:
            _dragon_strike(game, seat, [pending["victim" if phase == "borrow" else "target"]], engine, count=False)
    else:
        return False
    return True
