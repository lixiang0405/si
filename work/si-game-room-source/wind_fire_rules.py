"""Classic Wind and Fire decisions, including transferable judgment windows."""

from random import SystemRandom

from card_catalog import DELAYED, kind

SKILLS = {
    "shensu_judge": ("神速·弃判与摸", "判定阶段开始前，可跳过判定与摸牌，视为使用一张无距离限制的杀。"),
    "tianxiang": ("天香", "受到伤害时，可弃置一张红桃手牌将该伤害转移给另一名角色。其存活时按实际已损失体力摸牌。"),
    "guidao": ("鬼道", "判定牌生效前，可用一张黑色手牌或装备替换之，并取得原判定牌。"),
    "huangtian": ("黄天", "主公技。其他群势力角色出牌阶段限一次，可交给你一张闪或闪电。"),
    "quhu": ("驱虎", "出牌阶段限一次，与体力更高的角色拼点。赢则令其对你选定的、在其攻击范围内的一名角色造成一点伤害，否则由其对你造成一点伤害。"),
}


def slash_target_limit(game, seat, limit):
    won = game.get("general_state", {}).get("tianyi") == [game["turn_number"], seat["id"], True]
    return limit + int(won)


def before_judgement(game, seat, engine):
    turn = game["turn_start"]
    if (turn.get("shensu_asked") or turn.get("skip_judgement") or turn.get("skip_draw")
            or "shensu_judge" not in engine._skills(game, seat) or len(engine._live(game)) < 2):
        return False
    turn["shensu_asked"] = True
    engine.expansion._stage(game, "wind_shensu", seat["id"])
    return True


def before_damage(game, pending, amount, engine):
    target = engine._seat(game, pending["target"])
    if ("tianxiang" not in engine._skills(game, target) or not engine.expansion._others(game, target)
            or not engine.expansion._hearts(game, target, engine)):
        return False
    engine.expansion._stage(game, "wind_tianxiang", target["id"], amount=amount)
    return True


def finish_damage(game, pending, engine):
    for player in reversed((pending or {}).pop("tianxiang_rewards", ())):
        target = engine._seat(game, player)
        lost = max(0, target["max_hp"] - target["hp"])
        if target["alive"] and lost:
            engine._draw(game, target, lost)
            engine._add_log(game, f"天香，按已损失的体力摸 {lost} 张牌", target["id"])


def _divine_guicai(game, seat, engine):
    from divine_expansion_rules import has_guicai

    return has_guicai(game, seat, engine)


def _judge_options(game, seat, engine):
    skills = engine._skills(game, seat)
    options = []
    if "guidao" in skills:
        for card in engine.expansion._own_assets(seat, engine):
            if engine.expansion.suit(game, seat, card, engine) in engine.expansion.BLACK:
                options.append(engine._option("general_wind_guidao", f"鬼道 · 换成{engine._printed(card)}", card, card=card))
    if "jilue" in skills and _divine_guicai(game, seat, engine):
        for card in dict.fromkeys(seat["hand"]):
            options.append(engine._option("general_wind_guicai", f"极略 · 鬼才换成{engine._printed(card)}", card, card=card))
    return options


def judge(game, seat, context, engine):
    """Reveal the actual top card and pause for each eligible modifier in order."""
    card = engine._take(game)
    if card is None:
        dispatch_judgement(game, context, None, None, None, engine)
        return
    game["general_judgement"] = {"player": seat["id"], "context": context, "card": card,
                                 "order": engine._rotate(game, game["active"])}
    engine._add_log(game, f"亮出判定牌 · {engine._printed(card)}", seat["id"])
    _judge_next(game, engine)


def _judge_next(game, engine):
    judgment = game["general_judgement"]
    while judgment["order"]:
        player = judgment["order"].pop(0)
        if _judge_options(game, engine._seat(game, player), engine):
            engine.expansion._stage(game, "wind_judge", player)
            return
    seat = engine._seat(game, judgment["player"])
    card = judgment["card"]
    suit = engine.expansion.suit(game, seat, card, engine)
    rank = engine._rank(card)
    if suit is None or rank is None:
        # Old saved rooms used one string per card kind rather than a manifest.
        suit = suit or SystemRandom().choice(tuple(engine.SUITS.values()))
        rank = rank or SystemRandom().randint(1, 13)
    engine._add_log(game, f"判定为{suit}{rank} · {engine._card_name(card)}", seat["id"])
    context = judgment["context"]
    if suit in engine.expansion.BLACK and any(other.get("role") == engine.LORD
                                              and "songwei" in engine._skills(game, other)
                                              for other in game["seats"] if other["alive"]):
        from forest_mountain_rules import offer_songwei

        if offer_songwei(game, context, seat, card, suit, rank, engine):
            return
    dispatch_judgement(game, context, card, suit, rank, engine)


def dispatch_judgement(game, context, card, suit, rank, engine):
    game.pop("general_judgement", None)
    if card is not None:
        game["discard"].append(card)
    if context["kind"] != "leiji":
        engine._continue_judgement(game, context, card, suit, rank)
        return
    if suit == "黑桃":
        source, target = (engine._seat(game, context[key]) for key in ("source", "target"))
        engine.expansion._hit(game, source, target, "leiji", engine, amount=2, nature="thunder")
    else:
        engine._complete(game)


def active_actions(game, seat, engine):
    skills, used = engine._skills(game, seat), game.get("used_skills", [])
    if "quhu" in skills and "quhu" not in used:
        for target in engine.expansion._others(game, seat):
            if target["hp"] > seat["hp"] and target["hand"]:
                for card in dict.fromkeys(seat["hand"]):
                    yield engine._option("general_wind_quhu", f"驱虎 · 以{engine._printed(card)}拼点",
                                         {"target": target["id"], "card": card}, target["id"], card)
    if (engine._mode(game) == "identity" and engine.expansion._faction(seat, engine) == "群"
            and "huangtian" not in used):
        lord = next((other for other in engine.expansion._others(game, seat)
                     if other["role"] == engine.LORD and "huangtian" in engine._skills(game, other)), None)
        if lord:
            for card in dict.fromkeys(engine._matching(seat, {"dodge", "lightning"})):
                yield engine._option("general_wind_huangtian", f"黄天 · 交给主公{engine._printed(card)}",
                                     card, lord["id"], card)


def response_actions(game, seat, choice, engine):
    label = choice["label"]
    if label == "wind_shensu":
        return [*(engine._option("general_wind_shensu", "神速 · 跳过判定和摸牌", target["id"], target["id"])
                  for target in engine.expansion._others(game, seat)), engine._option("general_decline", "照常判定和摸牌")]
    if label == "wind_tianxiang":
        return [*(engine._option("general_wind_tianxiang", f"天香 · 弃{engine._printed(card)}并转移伤害",
                                {"card": card, "target": target["id"]}, target["id"], card)
                  for card in engine.expansion._hearts(game, seat, engine)
                  for target in engine.expansion._others(game, seat)), engine._option("general_decline", "承受伤害")]
    if label == "wind_judge":
        return [*_judge_options(game, seat, engine), engine._option("general_decline", "保留判定")]
    if label == "wind_quhu_duel":
        return [engine._option("general_wind_quhu_reveal", f"驱虎 · 亮出{engine._printed(card)}", card, card=card)
                for card in dict.fromkeys(seat["hand"])]
    if label == "wind_quhu_victim":
        tiger = engine._seat(game, choice["tiger"])
        return [engine._option("general_wind_quhu_hit", "驱虎 · 令其造成伤害", player, player)
                for player in engine._in_range(game, tiger)]
    return None


def play_pending(game, seat, choice, action, value, engine):
    label = choice["label"]
    if not label.startswith("wind_"):
        return False
    game["general_state"].pop("choice", None)
    if label == "wind_shensu":
        if action == "general_wind_shensu":
            game["turn_start"].update(skip_judgement=True, skip_draw=True)
            engine._add_log(game, "神速，跳过判定与摸牌", seat["id"], value)
            engine.expansion._strike(game, seat, engine._seat(game, value), "strike", engine, consume=False)
        else:
            engine._complete(game)
    elif label == "wind_tianxiang":
        pending = game["pending"]
        if action == "general_decline":
            engine._apply_damage(game, pending, choice["amount"])
        else:
            engine._spend(game, seat, value["card"])
            receiver = value["target"]
            engine._add_log(game, "天香，转移此次伤害", seat["id"], receiver)
            engine._damage(game, pending | {"target": receiver, "damage": choice["amount"],
                                            "tianxiang_rewards": [*pending.get("tianxiang_rewards", []), receiver],
                                            "skip_source_modifier": True})
    elif label == "wind_judge":
        judgment = game["general_judgement"]
        if action == "general_wind_guidao":
            if value in seat["hand"]:
                seat["hand"].remove(value)
            else:
                slot = next(slot for slot in engine.SLOTS.values() if seat.get(slot) == value)
                engine._unequip(game, seat, slot)
            seat["hand"].append(judgment["card"])
            judgment["card"] = value
            engine._add_log(game, f"鬼道换判 · {engine._printed(value)}", seat["id"], judgment["player"])
        elif action == "general_wind_guicai":
            from divine_expansion_rules import pay_guicai

            pay_guicai(game, seat, engine)
            seat["hand"].remove(value)
            game["discard"].append(judgment["card"])
            judgment["card"] = value
            engine._add_log(game, f"极略鬼才换判 · {engine._printed(value)}", seat["id"], judgment["player"])
        _judge_next(game, engine)
    elif label == "wind_quhu_duel":
        source = engine._seat(game, choice["source"])
        own_card = choice["card"]
        engine._spend(game, source, own_card)
        engine._spend(game, seat, value)
        won = (engine._rank(own_card) or 0) > (engine._rank(value) or 0)
        engine._add_log(game, f"驱虎拼点 · {engine._printed(own_card)}对{engine._printed(value)}", source["id"], seat["id"])
        if won:
            if engine._in_range(game, seat):
                engine.expansion._stage(game, "wind_quhu_victim", source["id"], tiger=seat["id"])
            else:
                engine._complete(game)
        else:
            engine.expansion._hit(game, seat, source, "quhu", engine)
    elif label == "wind_quhu_victim":
        engine.expansion._hit(game, engine._seat(game, choice["tiger"]), engine._seat(game, value), "quhu", engine)
    return True


def play_extra(game, seat, action, value, engine):
    if action == "general_wind_quhu":
        game["used_skills"].append("quhu")
        engine.expansion._stage(game, "wind_quhu_duel", value["target"], source=seat["id"], card=value["card"])
    elif action == "general_wind_huangtian":
        lord = next(other for other in engine.expansion._others(game, seat)
                    if other["role"] == engine.LORD and "huangtian" in engine._skills(game, other))
        game["used_skills"].append("huangtian")
        lord["hand"].append(engine._remove(seat, value))
        engine._add_log(game, "黄天，向主公交牌", seat["id"], lord["id"])
    else:
        return False
    return True


def bluff_suspended_judgements(game):
    frame = game.get("general_state", {}).get("bluff")
    while frame:
        pending = frame.get("pending") or {}
        if pending.get("judging") or frame["phase"] == "counter" and kind(pending.get("card")) in DELAYED:
            yield pending["card"]
        frame = frame.get("parent")


def judgment_held_cards(game):
    judgment = game.get("general_judgement")
    if judgment:
        yield judgment["card"]
        effect = judgment["context"].get("effect", {})
        if effect.get("judging"):
            yield effect["card"]


def finish_bluffs(game):
    game["discard"].extend(bluff_suspended_judgements(game))
    if game.get("general_state", {}).pop("bluff", None):
        for seat in game["seats"]:
            game["discard"].extend(seat.get("general_cards", {}).pop("bluff", []))


def can_bluff(game):
    if not game.get("general_state", {}).get("bluff"):
        return True
    return game["phase"] == "rescue"
