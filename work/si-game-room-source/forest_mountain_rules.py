"""Settlement for the printed 林 and 山 expansion skills."""

from itertools import combinations
from random import SystemRandom


SKILLS = {
    "fangzhu": ("放逐", "受到伤害后，可令一名其他角色摸等同你已损失体力值的牌，然后翻面。"),
    "songwei": ("颂威", "主公技。其他魏势力角色完成黑色判定后，可以令你摸一张牌。"),
    "roulin": ("肉林", "锁定技。你与女性角色之间使用的杀均需两张闪才能抵消。"),
    "baonue": ("暴虐", "主公技。其他群势力角色造成伤害后，可以判定。黑桃令你回复一点体力。"),
    "lieren": ("烈刃", "杀造成伤害后，可与存活的受伤角色拼点，赢则获得其一张牌。"),
    "zaiqi": ("再起", "受伤时可用再起替代摸牌。翻开等同已损体力的牌，每张红桃回复一点体力并弃置，获得其余牌。"),
    "luanwu": ("乱武", "限定技。令其他角色依次对距离最近的另一名角色出杀，否则各失去一点体力。"),
    "haoshi": ("好施", "摸牌阶段可以多摸两张。若手牌超过五张，将一半手牌交给手牌最少的一名其他角色。"),
    "qiaobian_phases": ("巧变", "可弃一张手牌跳过判定、摸牌、出牌或弃牌。跳过摸牌可取至多两人的暗手牌，跳过出牌可移动场上一张牌。"),
    "zaoxian": ("凿险", "觉醒技。准备阶段至少有三张田时，减一点体力上限并获得急袭。"),
    "jixi": ("急袭", "出牌阶段可将一张田当顺手牵羊使用。"),
    "zhiji": ("志继", "觉醒技。准备阶段无手牌时，选择回一点体力或摸两张，再减一点体力上限并获得观星。"),
    "guanxing": ("观星", "准备阶段观看牌堆顶至多五张牌，自行安排至牌堆顶或牌堆底。"),
    "fangquan": ("放权", "可跳过出牌阶段，此回合结束时可弃一张手牌，使一名其他角色获得额外回合。"),
    "ruoyu": ("若愚", "主公觉醒技。准备阶段体力为场上最低之一时，加一点体力上限，回复一点体力并获得激将。"),
    "jijiang": ("激将", "主公技。你需要使用或打出杀时，可请其他蜀势力角色依次响应。"),
    "zhiba": ("制霸", "主公技。每位其他吴势力角色出牌阶段可发起一次拼点。你觉醒后可拒绝。其未赢时你可以取得拼点牌。"),
    "guzheng": ("固政", "其他角色弃牌阶段结束时，可将该角色于此阶段弃置的一张手牌还给他，并获得剩下的弃牌。"),
    "beige": ("悲歌", "杀造成伤害后，可弃一张牌令存活的受伤者判定。红桃回复，方片摸二，梅花令伤害来源弃二，黑桃令来源翻面。"),
}


def _skills(game, seat, engine):
    return engine.expansion.enabled_skills(game, seat, engine)


def _mark(seat):
    return seat.setdefault("general_marks", {})


def _state(game):
    return game.setdefault("general_state", {})


def _stage(game, label, seat, engine, **data):
    engine.expansion._stage(game, "fm_" + label, seat["id"], **data)


def _others(game, seat):
    return [target for target in game["seats"] if target["alive"] and target is not seat]


def _faction(seat, engine):
    return engine.expansion._faction(seat, engine)


def _option(engine, action, label, value=None, target=None, card=None):
    return engine._option("general_fm_" + action, label, value, target, card)


def _card_label(engine, card):
    return engine.expansion._card_label(card, engine)


def _resume(game, engine):
    _state(game).pop("choice", None)
    engine._complete(game)


def _end(game, engine):
    _state(game).pop("choice", None)
    engine._advance(game)


def _excluded_judgement(game, seat, card, engine):
    virtual = game.get("virtual_delays", {})
    kind = virtual.get(card, engine._kind(card))
    return any(virtual.get(old, engine._kind(old)) == kind for old in seat.get("judgement", ()))


def _moves(game, engine):
    live = [seat for seat in game["seats"] if seat["alive"]]
    for origin in live:
        for target in live:
            if target is origin:
                continue
            for slot in dict.fromkeys(engine.SLOTS.values()):
                if origin.get(slot) and not target.get(slot):
                    yield origin, target, slot, origin[slot]
            for card in origin.get("judgement", ()):
                if (not _excluded_judgement(game, target, card, engine)
                        and not engine.expansion.prevent_trick(game, origin, target, card, engine)):
                    yield origin, target, "judgement", card


def _is_lord(game, seat, skill, engine):
    return game.get("mode") == "identity" and seat["role"] == "主公" and skill in _skills(game, seat, engine)


def derived_skills(seat):
    marks = seat.get("general_marks", {})
    return {skill for mark, skill in (("zaoxian", "jixi"), ("zhiji", "guanxing"), ("ruoyu", "jijiang"))
            if marks.get(mark)}


def avatar_key(seat):
    return seat.get("general_marks", {}).get("avatar_key") if not seat.get("skill_disabled") else None


def start_setup(game, engine):
    if game["turn_number"] != 0:
        return False
    for player in engine._rotate(game, game["active"]):
        seat = engine._seat(game, player)
        if (seat.get("general_marks", {}).get("avatars") and avatar_key(seat) is None
                and "huashen" in _skills(game, seat, engine)):
            engine.expansion._stage(game, "huashen", player, setup=True)
            return True
    return False


def before_phase(game, seat, phase, engine):
    if phase == "draw" and game.get("turn_start", {}).get("skip_draw"):
        return False
    skills = _skills(game, seat, engine)
    phase_skills = {
        "judgement": {"zaoxian", "zhiji", "ruoyu", "guanxing"},
        "draw": {"zaiqi", "haoshi"},
        "play": {"fangquan"},
        "discard": set(),
    }
    if "qiaobian_phases" not in skills and not skills.intersection(phase_skills[phase]):
        return False
    marks = _mark(seat)
    turn = game["turn_number"]
    if phase == "judgement" and marks.get("fm_prepared") != turn:
        if "zaoxian" in skills and not marks.get("zaoxian") and len(seat.get("general_cards", {}).get("field", ())) >= 3:
            marks["zaoxian"] = True
            seat["max_hp"] -= 1
            seat["hp"] = min(seat["hp"], seat["max_hp"])
            engine._add_log(game, "凿险觉醒，减一点体力上限并获得急袭", seat["id"])
        if "zhiji" in skills and not marks.get("zhiji") and not seat["hand"]:
            _stage(game, "zhiji", seat, engine)
            return True
        if _is_lord(game, seat, "ruoyu", engine) and not marks.get("ruoyu") and all(
            target["hp"] >= seat["hp"] for target in _others(game, seat)
        ):
            marks["ruoyu"] = True
            seat["max_hp"] += 1
            seat["hp"] += 1
            engine._add_log(game, "若愚觉醒，增加一点体力上限并获得激将", seat["id"])
        if "guanxing" in skills and marks.get("fm_guanxing") != turn:
            _stage(game, "guanxing", seat, engine)
            return True
        marks["fm_prepared"] = turn
    key = "fm_visit_" + phase
    if marks.get(key) == turn:
        return False
    marks[key] = turn
    if "qiaobian_phases" in skills and seat["hand"]:
        _stage(game, "qiaobian", seat, engine, phase=phase)
        return True
    if phase == "draw" and ("haoshi" in skills or "zaiqi" in skills and seat["hp"] < seat["max_hp"]):
        _stage(game, "draw_choice", seat, engine, skill="haoshi" if "haoshi" in skills else "zaiqi")
        return True
    if phase == "play" and "fangquan" in skills:
        _stage(game, "fangquan_skip", seat, engine)
        return True
    return False


def on_phase_discard(game, seat, card, engine):
    _mark(seat).setdefault("fm_discarded", []).append(card)


def after_discard(game, seat, engine):
    marks = _mark(seat)
    discarded = [card for card in marks.pop("fm_discarded", ()) if card in game["discard"]]
    for uid in engine._after(game, seat["id"]):
        holder = engine._seat(game, uid)
        if holder is not seat and discarded and "guzheng" in _skills(game, holder, engine):
            _stage(game, "guzheng", holder, engine, owner=seat["id"], cards=discarded)
            return True
    return False


def end_turn(game, seat, engine):
    if _mark(seat).get("fm_fangquan") == game["turn_number"] and seat["hand"] and _others(game, seat):
        _mark(seat).pop("fm_fangquan")
        _stage(game, "fangquan_grant", seat, engine)
        return True
    return False


def next_player(game, previous, engine):
    """Return a granted extra turn or resumption after a chain of extra turns."""
    state = _state(game)
    grant = state.pop("fm_extra", None)
    if grant:
        state.setdefault("fm_normal_next", engine._after(game, previous["id"])[0])
        if engine._seat(game, grant)["alive"]:
            return grant
    normal = state.pop("fm_normal_next", None)
    if normal and not engine._seat(game, normal)["alive"]:
        return engine._after(game, normal)[0]
    return normal


def on_invasion_finished(game, source, card, engine):
    if card not in engine.MANIFEST:
        return
    for uid in engine._after(game, source):
        seat = engine._seat(game, uid)
        if uid != source and "juxiang" in _skills(game, seat, engine):
            if card in game["discard"]:
                game["discard"].remove(card)
                seat["hand"].append(card)
                engine._add_log(game, "巨象，取得结算完毕的南蛮入侵", uid)
                break


def offer_songwei(game, context, judged, card, suit, rank, engine):
    if suit not in {"黑桃", "梅花"} or _faction(judged, engine) != "魏":
        return False
    lord = next((seat for seat in _others(game, judged) if _is_lord(game, seat, "songwei", engine)), None)
    if lord is None:
        return False
    _stage(game, "songwei", judged, engine, lord=lord["id"], previous=game["phase"],
           context=context, card=card, suit=suit, rank=rank)
    return True


def require_dodges(game, actor, target, engine):
    return 2 if ("roulin" in _skills(game, actor, engine) and engine._is_female(target)
                 or "roulin" in _skills(game, target, engine) and engine._is_female(actor)) else 1


def active_options(game, seat, engine):
    skills = _skills(game, seat, engine)
    hand = seat["hand"]
    options = []
    if "luanwu" in skills and not seat.get("general_marks", {}).get("fm_luanwu"):
        options.append(_option(engine, "luanwu", "发动乱武"))
    if "jixi" in skills:
        options.extend(_option(engine, "jixi", f"急袭 · 以{_card_label(engine, card)}当顺手牵羊",
                               {"card": card, "target": uid}, uid)
                       for card in seat.get("general_cards", {}).get("field", ())
                       for uid in engine._trick_targets(game, seat, "snatch")
                       if not engine.expansion.prevent_trick(game, seat, engine._seat(game, uid), card, engine))
    if _faction(seat, engine) == "吴" and "fm_zhiba" not in game["used_skills"]:
        for lord in _others(game, seat):
            if _is_lord(game, lord, "zhiba", engine) and any(engine._rank(card) for card in lord["hand"]):
                options.extend(_option(engine, "zhiba", f"制霸 · 以{_card_label(engine, card)}拼点",
                                       {"card": card, "lord": lord["id"]}, lord["id"], card)
                               for card in hand if engine._rank(card))
    if "jijiang" in skills and _allies(game, seat, engine) and "fm_jijiang" not in game["used_skills"]:
        limit = 1 + int("fury" in skills) + int(seat["role"] == "地主")
        limit = engine.expansion.attack_limit(game, seat, limit, engine)
        if limit and (engine._kind(seat.get("weapon")) == "crossbow" or game["strikes"] < limit):
            options.extend(_option(engine, "jijiang", "激将 · 请蜀势力角色出杀", uid, uid)
                           for uid in engine._in_range(game, seat))
    return options


def _allies(game, lord, engine):
    return [uid for uid in engine._after(game, lord["id"]) if uid != lord["id"]
            and _faction(engine._seat(game, uid), engine) == "蜀"]


def response_options(game, seat, engine):
    pending = game.get("pending") or {}
    if game["phase"] in {"strike_response", "borrow", "follow_up"} and not pending.get("fm_jijiang"):
        if game["phase"] != "follow_up" or pending.get("weapon_choice") == "blade":
            if "jijiang" in _skills(game, seat, engine) and _allies(game, seat, engine):
                return [_option(engine, "jijiang_response", "激将 · 请求蜀势力响应杀")]
    return []


def _zaiqi(game, seat, engine):
    lost = seat["max_hp"] - seat["hp"]
    revealed = [card for _ in range(lost) if (card := engine._take(game)) is not None]
    for card in revealed:
        if engine.expansion.suit(game, seat, card, engine) == "红桃":
            game["discard"].append(card)
            seat["hp"] += 1
        else:
            seat["hand"].append(card)
        engine._add_log(game, f"再起，亮出{_card_label(engine, card)}", seat["id"])


def _advance_preplay(game, target, engine):
    game["phase"] = "play"
    _state(game).pop("choice", None)
    engine._finish_end_play(game, target)


def skip_discard(game, seat):
    return seat.get("general_marks", {}).get("fm_skip_discard") == game["turn_number"]


def _lose_hp(game, target, source, engine):
    target["hp"] -= 1
    engine._add_log(game, "乱武，失去一点体力", target["id"])
    if target["hp"] > 0:
        engine._complete(game)
    else:
        game.update(phase="rescue", pending={"actor": source, "target": target["id"], "card": "strike",
                    "no_source": True, "lost_hp": True, "order": engine._rotate(game, target["id"]), "index": 0})
        engine._add_log(game, "进入濒死，等待救援", target["id"])


def after_damage(game, pending, amount, engine):
    if pending.get("lost_hp") or pending.get("fm_damage_done"):
        return False
    pending["fm_damage_done"] = True
    target = engine._seat(game, pending["target"])
    origin = None if pending.get("no_source") else engine._seat(game, pending["actor"])
    survived = target["alive"] and not pending.get("eliminated")
    tasks = []
    for uid in engine._rotate(game, game["active"]):
        seat = engine._seat(game, uid)
        skills = _skills(game, seat, engine)
        if survived and seat is target and "fangzhu" in skills and _others(game, target):
            tasks.append({"label": "fangzhu", "player": uid})
        if survived and "beige" in skills and pending.get("slash") and engine.expansion._own_assets(seat, engine):
            tasks.append({"label": "beige", "player": uid, "target": target["id"], "origin": origin["id"] if origin else None})
        if seat is origin:
            if (survived and origin is not target and "lieren" in skills and pending.get("slash")
                    and any(engine._rank(card) for card in origin["hand"])
                    and any(engine._rank(card) for card in target["hand"])):
                tasks.append({"label": "lieren", "player": uid, "target": target["id"]})
            if _faction(seat, engine) == "群":
                lord = next((other for other in _others(game, origin) if _is_lord(game, other, "baonue", engine)), None)
                if lord:
                    tasks.append({"label": "baonue", "player": uid, "lord": lord["id"]})
    if not tasks:
        return False
    _state(game)["fm_damage_tasks"] = tasks
    _next_damage_skill(game, engine)
    return True


def _next_damage_skill(game, engine):
    state = _state(game)
    state.pop("choice", None)
    tasks = state.get("fm_damage_tasks", [])
    if not tasks:
        state.pop("fm_damage_tasks", None)
        engine._complete(game)
        return
    task = tasks.pop(0)
    label, player = task.pop("label"), task.pop("player")
    _stage(game, label, engine._seat(game, player), engine, **task)


def continue_effects(game, engine):
    state = _state(game)
    if state.get("fm_luanwu"):
        sequence = state["fm_luanwu"]
        while sequence["order"]:
            target = engine._seat(game, sequence["order"].pop(0))
            if target["alive"]:
                _stage(game, "luanwu", target, engine, source=sequence["source"])
                return True
        state.pop("fm_luanwu")
    return False


def _resolve_damage_choice(game, seat, choice, action, value, engine):
    label = choice["label"].removeprefix("fm_")
    if action == "skip":
        _next_damage_skill(game, engine)
        return
    if label == "fangzhu":
        target = engine._seat(game, value)
        engine._draw(game, target, seat["max_hp"] - seat["hp"])
        target["face_down"] = not target.get("face_down", False)
        engine._add_log(game, "放逐，翻转武将牌", seat["id"], value)
    elif label == "baonue":
        _state(game).pop("choice", None)
        _judge(game, seat, {"kind": "forest_baonue", "lord": choice["lord"]}, engine)
        return
    elif label == "beige":
        engine.expansion._discard_asset(game, seat, value, engine)
        target = engine._seat(game, choice["target"])
        _state(game).pop("choice", None)
        _judge(game, target, {"kind": "forest_beige", "target": target["id"], "origin": choice["origin"]}, engine)
        return
    elif label == "beige_discard":
        engine.expansion._discard_asset(game, seat, value, engine)
        choice["remaining"] -= 1
        if choice["remaining"] and engine.expansion._own_assets(seat, engine):
            return
    elif label == "lieren":
        _stage(game, "lieren_pindian", engine._seat(game, choice["target"]), engine, source=seat["id"], source_card=value)
        return
    elif label == "lieren_pindian":
        source, first = engine._seat(game, choice["source"]), choice["source_card"]
        if _pindian(game, source, seat, first, value, "烈刃", engine) and engine._has_assets(seat, include_judgement=False):
            _stage(game, "lieren_claim", source, engine, target=seat["id"])
            return
    elif label == "lieren_claim":
        target = engine._seat(game, choice["target"])
        card = (target["hand"].pop(SystemRandom().randrange(len(target["hand"])))
                if value == "hand" else engine._unequip(game, target, value))
        seat["hand"].append(card)
        engine.expansion.after_spend(game, target, engine)
        engine._add_log(game, "烈刃，取得一张牌", seat["id"], target["id"])
    _next_damage_skill(game, engine)


def _judge(game, seat, context, engine):
    import wind_fire_rules

    return wind_fire_rules.judge(game, seat, context, engine)


def judgement_result(game, context, card, suit, rank, engine):
    kind = context["kind"]
    if kind == "forest_baonue":
        lord = engine._seat(game, context["lord"])
        if suit == "黑桃" and lord["alive"]:
            lord["hp"] = min(lord["max_hp"], lord["hp"] + 1)
            engine._add_log(game, "暴虐，主公回复一点体力", lord["id"])
    elif kind == "forest_beige":
        target = engine._seat(game, context["target"])
        origin = engine._seat(game, context["origin"])
        if suit == "红桃":
            target["hp"] = min(target["max_hp"], target["hp"] + 1)
            engine._add_log(game, "悲歌，目标回复一点体力", target["id"])
        elif suit == "方片":
            engine._draw(game, target, 2)
        elif origin and origin["alive"]:
            if suit == "黑桃":
                origin["face_down"] = not origin.get("face_down", False)
                engine._add_log(game, "悲歌，伤害来源翻面", origin["id"])
            elif suit == "梅花" and engine.expansion._own_assets(origin, engine):
                _stage(game, "beige_discard", origin, engine, remaining=2)
                return True
    _next_damage_skill(game, engine)
    return True


def _pindian(game, source, target, first, second, text, engine):
    engine._spend(game, source, first)
    engine._spend(game, target, second)
    engine._add_log(game, f"{text}拼点 · {_card_label(engine, first)} 对 {_card_label(engine, second)}", source["id"], target["id"])
    return engine._rank(first) > engine._rank(second)


def _jijiang_next(game, request, engine):
    if request["allies"]:
        ally = engine._seat(game, request["allies"].pop(0))
        _stage(game, "jijiang", ally, engine, request=request)
        return
    _state(game).pop("choice", None)
    game["phase"] = request["phase"]
    if request["phase"] == "play":
        game["used_skills"].append("fm_jijiang")
    elif game.get("pending"):
        game["pending"]["fm_jijiang"] = True


def _use_jijiang(game, request, helper, value, engine):
    _state(game).pop("choice", None)
    lord = engine._seat(game, request["lord"])
    card, kind = value["card"], value["kind"]
    engine._spend(game, helper, card)
    engine._add_log(game, "激将，蜀势力角色打出杀", helper["id"], lord["id"])
    phase = request["phase"]
    game["phase"] = phase
    pending = game.get("pending") or {}
    if phase == "strike_response":
        if engine._kind(pending["card"]) == "duel":
            pending["actor"], pending["target"] = pending["target"], pending["actor"]
            pending.pop("fm_jijiang", None)
        else:
            engine._complete(game)
        return
    uid = request.get("target") or pending.get("victim") or pending.get("target")
    target = engine._seat(game, uid)
    engine.expansion._strike(game, lord, target, kind, engine, card=card, consume=phase == "play")


def extra_action(game, seat, action, value, engine):
    action = action.removeprefix("general_fm_")
    if action == "luanwu":
        _mark(seat)["fm_luanwu"] = True
        state = _state(game)
        state["fm_luanwu"] = {"source": seat["id"], "order": [uid for uid in engine._after(game, seat["id"]) if uid != seat["id"]]}
        engine._add_log(game, "发动乱武", seat["id"])
        continue_effects(game, engine)
    elif action == "jixi":
        card = value["card"]
        seat["general_cards"]["field"].remove(card)
        seat["hand"].append(card)
        engine._use_trick(game, seat, card, value["target"], virtual_kind="snatch", materials=(card,))
    elif action == "zhiba":
        lord = engine._seat(game, value["lord"])
        game["used_skills"].append("fm_zhiba")
        _stage(game, "zhiba_pindian", lord, engine, source=seat["id"], source_card=value["card"])
    elif action in {"jijiang", "jijiang_response"}:
        _jijiang_next(game, {"lord": seat["id"], "target": value, "phase": game["phase"],
                             "allies": _allies(game, seat, engine)}, engine)
    else:
        return False
    return True


def _resolve_qiaobian(game, seat, phase, action, value, engine):
    if action == "skip":
        if phase == "discard":
            _advance_preplay(game, seat, engine)
        else:
            _resume(game, engine)
        return
    engine._spend(game, seat, value["card"])
    engine._add_log(game, "发动巧变，跳过阶段", seat["id"])
    start = game.get("turn_start", {})
    if phase == "judgement":
        start["skip_judgement"] = True
    elif phase == "draw":
        start["skip_draw"] = True
        for uid in value["targets"]:
            target = engine._seat(game, uid)
            card = target["hand"].pop(SystemRandom().randrange(len(target["hand"])))
            seat["hand"].append(card)
            engine.expansion.after_spend(game, target, engine)
            engine._add_log(game, "巧变，取得一张暗手牌", seat["id"], uid)
    elif phase == "play":
        start["skip_play"] = True
        if next(_moves(game, engine), None):
            _stage(game, "qiaobian_move", seat, engine)
            return
    else:
        _mark(seat)["fm_skip_discard"] = game["turn_number"]
        _advance_preplay(game, seat, engine)
        return
    _resume(game, engine)


def _resolve_guanxing(game, seat, choice, action, value, engine):
    if action == "skip":
        _mark(seat)["fm_guanxing"] = game["turn_number"]
        _resume(game, engine)
        return
    if action == "guanxing":
        count = min(5, len(engine._live(game)))
        held = [card for _ in range(count) if (card := engine._take(game)) is not None]
        seat.setdefault("general_cards", {})["guanxing"] = held.copy()
        choice.update(cards=held, top=[], bottom=[])
        return
    if action in {"guanxing_top", "guanxing_bottom"}:
        choice["cards"].remove(value)
        choice[action.removeprefix("guanxing_")].append(value)
        return
    deck = game["deck"]
    deck[:0] = reversed(choice["bottom"])
    deck.extend(reversed([*choice["top"], *choice["cards"]]))
    del seat["general_cards"]["guanxing"]
    _mark(seat)["fm_guanxing"] = game["turn_number"]
    engine._add_log(game, "观星，重新安排牌堆", seat["id"])
    _resume(game, engine)


def resolve_choice(game, seat, choice, action, value, engine):
    action = action.removeprefix("general_fm_")
    label = choice["label"].removeprefix("fm_")
    if label in {"fangzhu", "baonue", "beige", "beige_discard", "lieren", "lieren_pindian", "lieren_claim"}:
        _resolve_damage_choice(game, seat, choice, action, value, engine)
    elif label == "songwei":
        import wind_fire_rules

        if action == "songwei":
            lord = engine._seat(game, choice["lord"])
            engine._draw(game, lord, 1)
            engine._add_log(game, "颂威，主公摸一张牌", seat["id"], lord["id"])
        _state(game).pop("choice", None)
        game["phase"] = choice["previous"]
        wind_fire_rules.dispatch_judgement(game, choice["context"], choice["card"], choice["suit"], choice["rank"], engine)
    elif label == "zhiji":
        if action == "heal":
            seat["hp"] += 1
        else:
            engine._draw(game, seat, 2)
        seat["max_hp"] -= 1
        seat["hp"] = min(seat["max_hp"], seat["hp"])
        _mark(seat)["zhiji"] = True
        engine._add_log(game, "志继觉醒，减一点体力上限并获得观星", seat["id"])
        _resume(game, engine)
    elif label == "guanxing":
        _resolve_guanxing(game, seat, choice, action, value, engine)
    elif label == "qiaobian":
        _resolve_qiaobian(game, seat, choice["phase"], action, value, engine)
    elif label == "qiaobian_move":
        if action != "skip":
            origin, target = (engine._seat(game, value[key]) for key in ("from", "to"))
            card, slot = value["card"], value["slot"]
            if slot == "judgement":
                origin["judgement"].remove(card)
                target.setdefault("judgement", []).append(card)
            else:
                target[slot] = engine._unequip(game, origin, slot)
                engine.expansion.after_spend(game, origin, engine)
            engine._add_log(game, f"巧变，转移{_card_label(engine, card)}", origin["id"], target["id"])
        _resume(game, engine)
    elif label == "draw_choice":
        if action == "zaiqi":
            _zaiqi(game, seat, engine)
            game["turn_start"]["skip_draw"] = True
        elif action == "haoshi":
            amount = 4 + int("draw" in _skills(game, seat, engine)) + int(seat["role"] == "地主")
            engine._draw(game, seat, engine.expansion.draw_count(game, seat, amount, engine))
            game["turn_start"]["drawn"] = True
            if len(seat["hand"]) > 5 and _others(game, seat):
                _stage(game, "haoshi_target", seat, engine)
                return True
        _resume(game, engine)
    elif label == "haoshi_target":
        _stage(game, "haoshi_cards", seat, engine, target=value, remaining=len(seat["hand"]) // 2)
    elif label == "haoshi_cards":
        seat["hand"].remove(value)
        target = engine._seat(game, choice["target"])
        target["hand"].append(value)
        choice["remaining"] -= 1
        if not choice["remaining"]:
            engine._add_log(game, "好施，交付一半手牌", seat["id"], target["id"])
            _resume(game, engine)
    elif label == "fangquan_skip":
        if action == "fangquan_skip":
            game["turn_start"]["skip_play"] = True
            _mark(seat)["fm_fangquan"] = game["turn_number"]
        _resume(game, engine)
    elif label == "fangquan_grant":
        if action != "skip":
            engine._spend(game, seat, value["card"])
            _state(game)["fm_extra"] = value["target"]
            engine._add_log(game, "放权，授予额外回合", seat["id"], value["target"])
        _end(game, engine)
    elif label == "guzheng":
        target = engine._seat(game, choice["owner"])
        if action != "skip":
            game["discard"].remove(value)
            target["hand"].append(value)
            rest = choice["cards"].copy()
            rest.remove(value)
            for card in rest:
                if card in game["discard"]:
                    game["discard"].remove(card)
                    seat["hand"].append(card)
            engine._add_log(game, "固政，返还一张并取得其余弃牌", seat["id"], target["id"])
        _end(game, engine)
    elif label == "luanwu":
        _state(game).pop("choice", None)
        if action == "luanwu_slash":
            target = engine._seat(game, value["target"])
            wine = game.get("wine")
            game["wine"] = False
            engine._spend(game, seat, value["card"])
            engine.expansion._strike(game, seat, target, value["kind"], engine, card=value["card"], consume=False)
            game["wine"] = wine
        elif action == "luanwu_lose":
            _lose_hp(game, seat, choice["source"], engine)
        else:
            engine._complete(game)
    elif label == "zhiba_pindian":
        if action == "skip":
            _resume(game, engine)
        else:
            source, first = engine._seat(game, choice["source"]), choice["source_card"]
            if _pindian(game, source, seat, first, value, "制霸", engine):
                _resume(game, engine)
            else:
                _stage(game, "zhiba_accept", seat, engine, cards=[first, value])
    elif label == "zhiba_accept":
        if action != "skip":
            for card in choice["cards"]:
                if card in game["discard"]:
                    game["discard"].remove(card)
                    seat["hand"].append(card)
        _resume(game, engine)
    elif label == "jijiang":
        if action == "skip":
            _jijiang_next(game, choice["request"], engine)
        else:
            _use_jijiang(game, choice["request"], seat, value, engine)
    else:
        return False
    return True


def choice_options(game, seat, choice, engine):
    label = choice["label"].removeprefix("fm_")
    hand = seat["hand"]
    decline = _option(engine, "skip", "跳过")
    others = _others(game, seat)
    if label == "zhiji":
        options = [_option(engine, "draw", "志继 · 摸两张")]
        if seat["hp"] < seat["max_hp"]:
            options.append(_option(engine, "heal", "志继 · 回复一点体力"))
        return options
    if label == "guanxing":
        cards = choice.get("cards")
        if cards is None:
            return [_option(engine, "guanxing", "发动观星"), decline]
        options = [_option(engine, "guanxing_done", "确认观星")]
        for card in cards:
            options.extend(_option(engine, "guanxing_" + place, f"置于牌堆{caption} · {_card_label(engine, card)}", card)
                           for place, caption in (("top", "顶"), ("bottom", "底")))
        return options
    if label == "qiaobian":
        phase = choice["phase"]
        caption = {"judgement": "判定", "draw": "摸牌", "play": "出牌", "discard": "弃牌"}[phase]
        options = [decline]
        for card in hand:
            if phase == "draw":
                candidates = [other for other in others if other["hand"]]
                for size in (0, 1, 2):
                    for picked in combinations(candidates, size):
                        ids = [target["id"] for target in picked]
                        label = f"巧变 · 弃{_card_label(engine, card)}并获取 {size} 人各一张暗手牌" if size else "巧变 · 跳过摸牌"
                        options.append(_option(engine, "qiaobian", label, {"card": card, "targets": ids}, ids[0] if ids else None, card))
            else:
                options.append(_option(engine, "qiaobian", f"巧变 · 弃{_card_label(engine, card)}跳过{caption}", {"card": card}, card=card))
        return options
    if label == "qiaobian_move":
        return [decline, *(_option(engine, "qiaobian_move", f"巧变 · 移动{_card_label(engine, card)}",
                                   {"from": origin["id"], "to": target["id"], "slot": slot, "card": card}, target["id"])
                           for origin, target, slot, card in _moves(game, engine))]
    if label == "draw_choice":
        return [_option(engine, choice["skill"], "发动" + SKILLS[choice["skill"]][0]), decline]
    if label == "haoshi_target":
        fewest = min(len(other["hand"]) for other in others)
        return [_option(engine, "haoshi_target", "好施 · 交给手牌最少者", target["id"], target["id"])
                for target in others if len(target["hand"]) == fewest]
    if label == "haoshi_cards":
        return [_option(engine, "haoshi_cards", f"好施 · 交予{_card_label(engine, card)}", card, choice["target"], card)
                for card in hand]
    if label == "fangquan_skip":
        return [_option(engine, "fangquan_skip", "放权 · 跳过出牌阶段"), decline]
    if label == "fangquan_grant":
        return [decline, *(_option(engine, "fangquan_grant", f"放权 · 弃{_card_label(engine, card)}授予额外回合",
                                   {"card": card, "target": target["id"]}, target["id"], card)
                           for target in others for card in hand)]
    if label == "guzheng":
        return [decline, *(_option(engine, "guzheng", f"固政 · 返还{_card_label(engine, card)}", card, choice["owner"], card)
                           for card in dict.fromkeys(choice["cards"]) if card in game["discard"])]
    if label == "luanwu":
        if not others:
            return [decline]
        distance = min(engine._distance(game, seat, target) for target in others)
        in_range = set(engine._in_range(game, seat))
        wushen = "wushen" in _skills(game, seat, engine)
        return [*(_option(engine, "luanwu_slash", f"乱武 · 使用{_card_label(engine, card)}",
                          {"card": card, "kind": kind, "target": target["id"]}, target["id"], card)
                  for target in others if engine._distance(game, seat, target) == distance
                  for card, kind in engine.expansion.strike_cards(game, seat, engine)
                  if (target["id"] in in_range or wushen and engine.expansion.suit(game, seat, card, engine) == "红桃")),
                _option(engine, "luanwu_lose", "乱武 · 失去一点体力")]
    if label == "fangzhu":
        return [decline, *(_option(engine, "fangzhu", "放逐 · 摸牌并翻面", target["id"], target["id"]) for target in others)]
    if label in {"songwei", "baonue"}:
        return [_option(engine, label, "发动" + SKILLS[label][0], target=choice["lord"]), decline]
    if label == "beige":
        return [decline, *(_option(engine, "beige", f"悲歌 · 弃{_card_label(engine, card)}", card, choice["target"], card)
                           for card in engine.expansion._own_assets(seat, engine))]
    if label == "beige_discard":
        return [_option(engine, "beige_discard", f"悲歌 · 弃置{_card_label(engine, card)}", card, card=card)
                for card in engine.expansion._own_assets(seat, engine)]
    if label in {"lieren", "lieren_pindian", "zhiba_pindian"}:
        options = [] if label == "lieren_pindian" else [decline]
        if label == "zhiba_pindian" and not seat.get("general_marks", {}).get("awakened"):
            options = []
        return [*options, *(_option(engine, label, f"拼点 · {_card_label(engine, card)}", card, choice.get("target"), card)
                            for card in hand if engine._rank(card))]
    if label == "zhiba_accept":
        return [_option(engine, "zhiba_accept", "制霸 · 取得两张拼点牌"), decline]
    if label == "lieren_claim":
        target = engine._seat(game, choice["target"])
        options = [_option(engine, "lieren_claim", "烈刃 · 取得一张暗手牌", "hand", target["id"])] if target["hand"] else []
        options.extend(_option(engine, "lieren_claim", f"烈刃 · 取得{_card_label(engine, target[slot])}", slot, target["id"])
                       for slot in dict.fromkeys(engine.SLOTS.values()) if target.get(slot))
        return options
    if label == "jijiang":
        return [decline, *(_option(engine, "jijiang_offer", f"激将 · 打出{_card_label(engine, card)}",
                                   {"card": card, "kind": kind}, card=card)
                           for card, kind in engine.expansion.strike_cards(game, seat, engine))]
    return []
