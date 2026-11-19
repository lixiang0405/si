"""Classic face-to-face general packs and the executable skills in this build.

This module works on JSON state using the core game engine passed as `engine`.
Names in `PENDING_GENERAL_NAMES` belong to the physical packs but are not
eligible for the draft. Each playable card lists its remaining original skills
so the room can present the exact rule set that is being played.
"""

from itertools import combinations
from random import SystemRandom

import divine_expansion_rules as divine
import wind_fire_rules as wind_fire
import forest_mountain_rules as forest


PACKS = {
    "wind": {"name": "风", "description": "神话再临 · 其疾如风"},
    "fire": {"name": "火", "description": "神话再临 · 侵掠如火"},
    "forest": {"name": "林", "description": "神话再临 · 其徐如林"},
    "mountain": {"name": "山", "description": "神话再临 · 不动如山"},
    "divine": {"name": "神将", "description": "神话再临 · 八位神将的专项将池"},
}

SKILLS = {
    **divine.SKILLS,
    "liegong": ("烈弓", "用杀指定目标时，若其手牌数至少等于你的体力，或至多等于你的攻击范围，可令这张杀无法被闪抵消。"),
    "kuanggu": ("狂骨", "对距离为一的角色造成伤害后，每造成一点伤害回复一点体力。"),
    "shensu_play": ("神速·弃具", "出牌阶段开始时，弃置一张装备并跳过出牌，改为使用一张无距离限制的杀。"),
    "jushou": ("据守", "结束阶段可摸三张牌并翻面。下次轮到你时翻回正面，跳过该回合。"),
    "buqu": ("不屈", "因伤害濒死时亮出一张牌。若与已有创的点数不同，将其保留为创并回复至一点体力。创数就是手牌上限。"),
    "hongyan": ("红颜", "你区域内的黑桃按红桃处理。"),
    "leiji": ("雷击", "打出闪后可选一名其他角色判定，黑桃令其受到两点雷电伤害。"),
    "guhuo": ("蛊惑", "扣置一张手牌，声明使用一种基本牌或普通锦囊。无人质疑则生效；有人质疑才亮牌。真牌使质疑者各失去一点体力，假牌则让他们各摸一张，只有红桃真牌继续结算。"),
    "qiangxi": ("强袭", "每回合一次，弃置武器或在体力大于一点时失去一点体力，令攻击范围内的角色受到一点伤害。"),
    "jieming": ("节命", "受伤后可指定一名角色，将其手牌补至体力上限，至多补到五张。"),
    "bazhen": ("八阵", "没有装备防具时，视为有八卦阵。"),
    "huoji": ("火计", "红色手牌可当火攻使用。"),
    "kanpo": ("看破", "黑色手牌可当无懈可击使用。"),
    "lianhuan": ("连环", "梅花手牌可当铁索连环使用或重铸。"),
    "niepan": ("涅槃", "濒死时每局可用一次。弃掉牌与装备，解除横置和翻面，摸三张，回复至多三点体力。"),
    "tianyi": ("天义", "每回合一次，与一名角色拼点。胜利后本回合可多用一张杀且不限距离，每张杀可多选一个目标；失败则本回合不能用杀。"),
    "mashu": ("马术", "你计算与其他角色的距离时减一。"),
    "mengjin": ("猛进", "你的杀被目标以闪抵消后，可以弃置其一张牌。"),
    "luanji": ("乱击", "可以将两张同花色手牌当万箭齐发使用。"),
    "xueyi": ("血裔", "担任主公时，每位存活的其他群势力角色令你的手牌上限加二。"),
    "shuangxiong": ("双雄", "可在回合开始声明替换摸牌。摸牌阶段改为亮取一张牌，本回合可把另一种颜色的手牌当决斗。"),
    "duanliang": ("断粮", "黑色基本牌或装备牌可当兵粮寸断使用，使用距离至多为二。"),
    "xingshang": ("行殇", "其他角色出局时，可取得其手牌与装备。"),
    "yinghun": ("英魂", "准备阶段若已受伤，可选一名其他角色，让其摸已损失体力数的牌后弃一张，或摸一张后弃等量的牌。"),
    "jiuchi": ("酒池", "黑桃手牌可当酒使用；濒死时也可用它自救。"),
    "benghuai": ("崩坏", "结束阶段若你的体力不是场上最低，选择失去一点体力或一点体力上限。"),
    "juxiang": ("巨象", "南蛮入侵对你无效。其他角色使用的实体南蛮入侵结算完后，你获得此牌。"),
    "huoshou": ("祸首", "南蛮入侵对你无效。你在场时，其他角色使用南蛮入侵所造成的伤害由你承担来源。"),
    "weimu": ("帷幕", "黑色锦囊不能选中你。"),
    "wansha": ("完杀", "你的回合中，只有你和濒死者可以使用桃施救。"),
    "dimeng": ("缔盟", "每回合一次，弃去两名其他角色手牌数之差对应的牌，然后两人交换手牌。"),
    "qiaobian_draw": ("巧变·摸牌", "回合开始可弃去一张手牌，进入摸牌阶段时改为从至多两人各取一张手牌。"),
    "tuntian": ("屯田", "回合外使用或弃置手牌时进行判定，非红桃存为田。每张田令你到他人的距离减一。"),
    "tiaoxin": ("挑衅", "每回合一次，指定攻击范围内包含你的角色。其可以对你出杀，否则你弃置其一张牌。"),
    "xiangle": ("享乐", "其他角色对你用杀时，须再弃置一张基本牌，否则杀无效。"),
    "huashen": ("化身", "从储备武将中展示一张，并取得该牌的一项可用技能及其势力与性别。自己的回合开始或结束时可以改选。"),
    "xinsheng": ("新生", "每受到一点伤害，从可用武将中加入一名新化身。"),
    "jiang": ("激昂", "使用决斗、红色杀，或成为它们的目标时摸一张牌。"),
    "hunzi": ("魂姿", "准备阶段体力为一时觉醒，减一点体力上限，获得英姿和英魂。"),
    "zhijian": ("直谏", "把手牌里的装备直接放到另一人的空装备区，然后摸一张牌。"),
    "duanchang": ("断肠", "你被另一名角色击杀时，令其失去武将技能。"),
    "wushen": ("武神", "锁定技。红桃手牌视为普通杀，使用红桃杀无距离限制。"),
    "shelie": ("涉猎", "可以用亮出五张牌并各取一种花色的方式代替摸牌。其余牌弃置。"),
    "yeyan_minor": ("业炎·小业炎", "每局一次，对至多三名角色各造成一点火焰伤害。"),
    "qixing": ("七星", "开局看十一张，选四张为手牌，七张置为暗星。摸牌阶段结束时可以用手牌等量交换星。"),
    "kuangfeng": ("狂风", "回合结束可弃一张星使一名角色受到的火焰伤害加一点，持续至你下次回合开始。"),
    "dawu": ("大雾", "回合结束可以按目标各弃一张星，防止这些角色受到的非雷电伤害，持续至你下次回合开始。"),
    "feiying": ("飞影", "其他角色计算与你的距离时加一。"),
    "kuangbao": ("狂暴", "开局有两枚暴怒。你每造成或受到一点伤害，获得一枚暴怒。"),
    "shenfen": ("神愤", "每回合一次，移去六枚暴怒，依次对其他角色造成一点伤害，弃置其装备并令其弃至多四张手牌，自己翻面。"),
    "juejing": ("绝境", "摸牌阶段额外摸等于已损失体力的牌，手牌上限加二。"),
    "renjie": ("忍戒", "每受到一点伤害，或在弃牌阶段弃置一张牌，获得一枚忍。"),
    "baiyin": ("拜印", "准备阶段至少有四枚忍时觉醒，减一点体力上限并获得可用的极略选项。"),
    "jilue_cycle": ("极略·制衡", "觉醒后，每回合一次，消耗一枚忍并选择要弃的牌，弃几张便摸几张。"),
    **wind_fire.SKILLS,
    **forest.SKILLS,
}


def _general(name, faction, hp, pack, skills, omitted=()):
    return {"name": name, "faction": faction, "hp": hp, "pack": pack, "skills": skills, "omitted": omitted}


GENERAL_CATALOG = {
    "wind_huang_zhong": _general("黄忠", "蜀", 4, "wind", ("liegong",)),
    "wind_wei_yan": _general("魏延", "蜀", 4, "wind", ("kuanggu",)),
    "wind_xiahou_yuan": _general("夏侯渊", "魏", 4, "wind", ("shensu_judge", "shensu_play")),
    "wind_cao_ren": _general("曹仁", "魏", 4, "wind", ("jushou",)),
    "wind_zhou_tai": _general("周泰", "吴", 4, "wind", ("buqu",)),
    "wind_xiao_qiao": _general("小乔", "吴", 3, "wind", ("hongyan", "tianxiang")),
    "wind_zhang_jiao": _general("张角", "群", 3, "wind", ("leiji", "guidao", "huangtian")),
    "wind_yu_ji": _general("于吉", "群", 3, "wind", ("guhuo",)),
    "fire_dian_wei": _general("典韦", "魏", 4, "fire", ("qiangxi",)),
    "fire_xun_yu": _general("荀彧", "魏", 3, "fire", ("jieming", "quhu")),
    "fire_wolong": _general("卧龙诸葛亮", "蜀", 3, "fire", ("bazhen", "huoji", "kanpo")),
    "fire_pang_tong": _general("庞统", "蜀", 3, "fire", ("lianhuan", "niepan")),
    "fire_taishi_ci": _general("太史慈", "吴", 4, "fire", ("tianyi",)),
    "fire_pang_de": _general("庞德", "群", 4, "fire", ("mashu", "mengjin")),
    "fire_yan_wen": _general("颜良文丑", "群", 4, "fire", ("shuangxiong",)),
    "fire_yuan_shao": _general("袁绍", "群", 4, "fire", ("luanji", "xueyi")),
    "forest_xu_huang": _general("徐晃", "魏", 4, "forest", ("duanliang",)),
    "forest_cao_pi": _general("曹丕", "魏", 3, "forest", ("xingshang", "fangzhu", "songwei")),
    "forest_sun_jian": _general("孙坚", "吴", 4, "forest", ("yinghun",)),
    "forest_dong_zhuo": _general("董卓", "群", 8, "forest", ("jiuchi", "benghuai", "roulin", "baonue")),
    "forest_zhu_rong": _general("祝融", "蜀", 4, "forest", ("juxiang", "lieren")),
    "forest_meng_huo": _general("孟获", "蜀", 4, "forest", ("huoshou", "zaiqi")),
    "forest_jia_xu": _general("贾诩", "群", 3, "forest", ("weimu", "wansha", "luanwu")),
    "forest_lu_su": _general("鲁肃", "吴", 3, "forest", ("dimeng", "haoshi")),
    "mountain_zhang_he": _general("张郃", "魏", 4, "mountain", ("qiaobian_phases",)),
    "mountain_deng_ai": _general("邓艾", "魏", 4, "mountain", ("tuntian", "zaoxian"), ("屯田·部分一次移走多张牌的结算",)),
    "mountain_jiang_wei": _general("姜维", "蜀", 4, "mountain", ("tiaoxin", "zhiji")),
    "mountain_liu_shan": _general("刘禅", "蜀", 3, "mountain", ("xiangle", "fangquan", "ruoyu")),
    "mountain_zuo_ci": _general("左慈", "群", 3, "mountain", ("huashen", "xinsheng"), ("化身·未接入本桌的外部武将牌",)),
    "mountain_sun_ce": _general("孙策", "吴", 4, "mountain", ("jiang", "hunzi", "zhiba")),
    "mountain_zhang_brothers": _general("张昭张纮", "吴", 3, "mountain", ("zhijian", "guzheng")),
    "mountain_cai_wenji": _general("蔡文姬", "群", 3, "mountain", ("duanchang", "beige")),
    "divine_guan_yu": _general("神关羽", "神", 5, "divine", ("wushen", "wuhun")),
    "divine_lu_meng": _general("神吕蒙", "神", 3, "divine", ("shelie", "gongxin")),
    "divine_zhou_yu": _general("神周瑜", "神", 4, "divine", ("yeyan_minor", "yeyan_major", "qinyin")),
    "divine_zhuge_liang": _general("神诸葛亮", "神", 3, "divine", ("qixing", "kuangfeng", "dawu")),
    "divine_cao_cao": _general("神曹操", "神", 3, "divine", ("feiying", "guixin")),
    "divine_lu_bu": _general("神吕布", "神", 5, "divine", ("kuangbao", "wumou", "wuqian", "shenfen")),
    "divine_zhao_yun": _general("神赵云", "神", 2, "divine", ("juejing", "longhun")),
    "divine_sima_yi": _general("神司马懿", "神", 4, "divine", ("renjie", "baiyin", "jilue", "jilue_cycle", "lianpo")),
}

PENDING_GENERAL_NAMES = {pack: () for pack in PACKS}


RED = {"红桃", "方片"}
BLACK = {"黑桃", "梅花"}
ATTACKS = {"strike", "fire_strike", "thunder_strike"}


def enabled_skills(game, seat, engine):
    return engine._skills(game, seat) if seat and not seat.get("skill_disabled") else set()


def suit(game, seat, card, engine):
    color = engine._suit(card)
    return "红桃" if color == "黑桃" and "hongyan" in enabled_skills(game, seat, engine) else color


def _skills_used(game):
    return game.setdefault("used_skills", [])


def _hp(seat, amount):
    seat["hp"] = min(seat["max_hp"], seat["hp"] + amount)


def _marks(seat):
    return seat.setdefault("general_marks", {})


def _gain_mark(seat, mark, amount):
    marks = _marks(seat)
    marks[mark] = marks.get(mark, 0) + amount


def _state(game):
    return game.setdefault("general_state", {})


def _others(game, seat):
    return [other for other in game["seats"] if other["alive"] and other is not seat]


def _stage(game, label, player, **data):
    _state(game)["choice"] = {"label": label, "player": player, **data}
    game["phase"] = "general"


def current_player(game):
    """A decision owned by the expansions' response phase."""
    state = game.get("general_state", {})
    return state.get("choice", {}).get("player") if game["phase"] == "general" else None


def draw_count(game, seat, amount, engine):
    skills = enabled_skills(game, seat, engine)
    if "juejing" in skills:
        amount += seat["max_hp"] - seat["hp"]
    if "hunzi" in skills and seat.get("general_marks", {}).get("awakened"):
        amount += 1
    return amount


def replace_draw(game, seat, engine):
    skills = enabled_skills(game, seat, engine)
    marks = _marks(seat)
    turn = game["turn_number"]
    if "shuangxiong" in skills and marks.get("shuangxiong_draw") == turn:
        if (card := engine._take(game)) is not None:
            seat["hand"].append(card)
            marks["shuangxiong_color"] = "red" if suit(game, seat, card, engine) in RED else "black"
            engine._add_log(game, f"双雄，亮取{_card_label(card, engine)}", seat["id"])
        return True
    if "qiaobian_draw" in skills and marks.get("qiaobian_turn") == turn:
        for player in marks.pop("qiaobian_targets", []):
            target = engine._seat(game, player)
            if target and target["alive"] and target["hand"]:
                card = SystemRandom().choice(target["hand"])
                target["hand"].remove(card)
                seat["hand"].append(card)
                engine._add_log(game, "巧变，取走一张手牌", seat["id"], player)
        return True
    return False


def distance_delta(game, origin, target, engine):
    skills = enabled_skills(game, origin, engine)
    fields = len(origin.get("general_cards", {}).get("field", ())) if "tuntian" in skills else 0
    return int("feiying" in enabled_skills(game, target, engine)) - int("mashu" in skills) - fields


def attack_limit(game, seat, limit, engine):
    status = game.get("general_state", {}).get("tianyi")
    if status == [game["turn_number"], seat["id"], False]:
        return 0
    return limit + int(status == [game["turn_number"], seat["id"], True])


def hand_limit(game, seat, limit, engine):
    skills = enabled_skills(game, seat, engine)
    scars = seat.get("general_cards", {}).get("scar")
    if "buqu" in skills and scars:
        return len(scars)
    return limit + 2 * int("juejing" in skills) + (
        2 * sum(_faction(other, engine) == "群" for other in _others(game, seat))
        if "xueyi" in skills and seat["role"] == "主公" else 0
    )


def prevent_trick(game, actor, target, card, engine):
    skills = enabled_skills(game, target, engine)
    kind = engine._kind(card)
    return (
        kind == "invasion" and bool({"juxiang", "huoshou"} & skills)
        or "weimu" in skills and suit(game, actor, card, engine) in BLACK
    )


def peach_allowed(game, seat, target, engine):
    active = engine._seat(game, game["active"])
    target_id = target["id"] if isinstance(target, dict) else target
    return divine.allow_peach(game, seat, target_id, engine) and (
        "wansha" not in enabled_skills(game, active, engine) or seat["id"] in {game["active"], target_id}
    )


def armor_available(game, seat, engine):
    return engine._kind(seat.get("armor")) == "diagram" or (
        not seat.get("armor") and "bazhen" in enabled_skills(game, seat, engine)
    )


def has_counter(game, seat, engine):
    skills = enabled_skills(game, seat, engine)
    if seat["hand"] and "guhuo" in skills and not game.get("general_state", {}).get("bluff"):
        return True
    return divine.has_counter(game, seat, engine) or (
        "kanpo" in skills and any(suit(game, seat, card, engine) in BLACK for card in seat["hand"])
    )


def alter_damage(game, pending, amount, engine):
    target = engine._seat(game, pending["target"])
    marks = target.get("general_marks", {})
    if marks.get("fog_source") and pending.get("nature") != "thunder":
        engine._add_log(game, "大雾，防止此次伤害", target["id"])
        return 0
    if marks.get("wind_source") and pending.get("nature") == "fire":
        amount += 1
    if pending.get("card") == "invasion":
        holder = next((seat for seat in game["seats"] if seat["alive"] and
                       "huoshou" in enabled_skills(game, seat, engine)), None)
        if holder is not None and holder["id"] != pending["target"]:
            pending["actor"] = holder["id"]
    return amount


def after_damage(game, pending, amount, engine):
    origin = None if pending.get("no_source") else engine._seat(game, pending["actor"])
    target = engine._seat(game, pending["target"])
    for seat in (origin, target):
        if seat and "kuangbao" in enabled_skills(game, seat, engine):
            _gain_mark(seat, "rage", amount)
    if target and "renjie" in enabled_skills(game, target, engine):
        _gain_mark(target, "patience", amount)
    if target and "xinsheng" in enabled_skills(game, target, engine):
        _grant_avatars(game, target, engine, amount)
    if origin and origin["alive"] and origin is not target and "kuanggu" in enabled_skills(game, origin, engine):
        if engine._distance(game, origin, target) <= 1 and origin["hp"] < origin["max_hp"]:
            _hp(origin, amount)
            engine._add_log(game, "狂骨，回复体力", origin["id"])
    if target["hp"] <= 0 and "buqu" in enabled_skills(game, target, engine):
        card = engine._take(game)
        if card is not None:
            scars = target.setdefault("general_cards", {}).setdefault("scar", [])
            rank = engine._rank(card)
            if rank is not None and rank not in {engine._rank(old) for old in scars}:
                scars.append(card)
                target["hp"] = 1
                engine._add_log(game, f"不屈，保留一张点数为 {rank} 的创", target["id"])
            else:
                game["discard"].append(card)
    if target["hp"] > 0 and "jieming" in enabled_skills(game, target, engine):
        _stage(game, "jieming", target["id"], remaining=amount)
        return True
    return False


def before_eliminate(game, pending, engine):
    return False


def on_death(game, target, source, engine):
    skills = enabled_skills(game, target, engine)
    if "duanchang" in skills and source is not None and source is not target:
        source["skill_disabled"] = True
        engine._add_log(game, "断肠，击败者失去武将技能", target["id"], source["id"])
    for holder in _others(game, target):
        if "xingshang" in enabled_skills(game, holder, engine):
            holder["hand"].extend(target["hand"])
            target["hand"].clear()
            for slot in dict.fromkeys(engine.SLOTS.values()):
                if card := target.get(slot):
                    holder["hand"].append(card)
                    target[slot] = None
            engine._add_log(game, "行殇，取得出局者的手牌和装备", holder["id"], target["id"])
            break
    for pile in target.pop("general_cards", {}).values():
        game["discard"].extend(pile)
    _clear_star_marks(game, target["id"])


def after_spend(game, seat, engine):
    if not seat["alive"] or seat["id"] == game["active"] or "tuntian" not in enabled_skills(game, seat, engine):
        return
    if (card := engine._take(game)) is None:
        return
    if suit(game, seat, card, engine) == "红桃":
        game["discard"].append(card)
        engine._add_log(game, "屯田判定为红桃，没有置田", seat["id"])
    else:
        seat.setdefault("general_cards", {}).setdefault("field", []).append(card)
        engine._add_log(game, f"屯田，置{_card_label(card, engine)}为田", seat["id"])


AVATAR_EXCLUDED = {"huashen", "niepan", "luanwu", "hunzi", "zaoxian", "zhiji", "ruoyu", "baiyin",
                   "yeyan_minor", "yeyan_major", "yeyan", "songwei", "baonue", "xueyi", "zhiba", "huangtian"}


def avatar_skills(seat):
    skill = seat.get("general_marks", {}).get("avatar_skill")
    return {skill} if skill and not seat.get("skill_disabled") else set()


def _grant_avatars(game, seat, engine, amount):
    avatars = _marks(seat).setdefault("avatars", [])
    taken = {key for player in game["seats"] for key in player.get("generals", ())}
    taken.update(item[0] for player in game["seats"] for item in player.get("general_marks", {}).get("avatars", ()))
    choices = [[key, skills[0]] for key in engine.GENERALS if key not in taken
               and (skills := _avatar_options(key, engine))]
    SystemRandom().shuffle(choices)
    for key, skill in choices:
        if amount <= 0:
            break
        if key not in taken:
            avatars.append([key, skill])
            taken.add(key)
            amount -= 1


def _avatar_options(key, engine):
    return tuple(skill for skill in GENERAL_CATALOG.get(key, {}).get("skills", (engine.GENERALS[key][3],))
                 if skill in engine.SKILLS and skill not in AVATAR_EXCLUDED)


def _clear_star_marks(game, player):
    for target in game["seats"]:
        marks = target.get("general_marks", {})
        for mark in ("fog_source", "wind_source"):
            if marks.get(mark) == player:
                del marks[mark]


def initialize_seat(game, seat, engine):
    skills = enabled_skills(game, seat, engine)
    if "kuangbao" in skills:
        _gain_mark(seat, "rage", 2)
    if "huashen" in skills:
        _grant_avatars(game, seat, engine, 2)
    if "qixing" in skills:
        seat.setdefault("general_cards", {})["star"] = [
            card for _ in range(7) if (card := engine._take(game)) is not None
        ]
        seat["choose_initial_stars"] = True


def start_turn(game, seat, engine):
    _clear_star_marks(game, seat["id"])
    if seat.pop("face_down", False):
        engine._add_log(game, "翻回正面，跳过回合", seat["id"])
        return True
    skills = enabled_skills(game, seat, engine)
    marks = _marks(seat)
    if not marks.get("awakened") and (
        "hunzi" in skills and seat["hp"] == 1 or "baiyin" in skills and marks.get("patience", 0) >= 4
    ):
        marks["awakened"] = 1
        seat["max_hp"] = max(1, seat["max_hp"] - 1)
        seat["hp"] = min(seat["max_hp"], seat["hp"])
        engine._add_log(game, "武将觉醒，体力上限减少一点", seat["id"])
    if ("yinghun" in skills or "hunzi" in skills and marks.get("awakened")) and _others(game, seat):
        lost = seat["max_hp"] - seat["hp"]
        if lost > 0:
            _stage(game, "yinghun", seat["id"], lost=lost)
    if game["phase"] != "general":
        if "shuangxiong" in skills:
            marks.pop("shuangxiong_color", None)
            _stage(game, "shuangxiong", seat["id"])
        elif "qiaobian_draw" in skills and seat["hand"] and any(other["hand"] for other in _others(game, seat)):
            _stage(game, "qiaobian_draw", seat["id"])
        elif "huashen" in skills and marks.get("avatars"):
            _stage(game, "huashen", seat["id"])
    return False


def card_used(game, actor, target, card, engine):
    kind = engine._kind(card)
    if kind != "duel" and (kind not in ATTACKS or suit(game, actor, card, engine) not in RED):
        return
    for seat in (actor, target):
        if seat and "jiang" in enabled_skills(game, seat, engine):
            engine._draw(game, seat, 1)
            engine._add_log(game, "激昂，额外摸一张牌", seat["id"])


def on_discard(game, seat, engine):
    divine.on_discard(game, seat, engine)
    if "renjie" in enabled_skills(game, seat, engine):
        _gain_mark(seat, "patience", 1)


def _faction(seat, engine):
    if key := forest.avatar_key(seat):
        return engine.GENERALS[key][1]
    if seat.get("faction"):
        return seat["faction"]
    first = (seat.get("generals") or [None])[0]
    return engine._general_info(first)["faction"] if first else None


def _reach(seat, engine):
    return engine.WEAPONS.get(engine._kind(seat.get("weapon")), 1)


def attack_reaches(game, actor, target, attack_range, engine):
    return (game.get("general_state", {}).get("tianyi") == [game["turn_number"], actor["id"], True]
            or engine._distance(game, actor, target) <= attack_range)


def _card_label(card, engine):
    name = engine.CARDS[engine._kind(card)][0]
    color, rank = engine._suit(card), engine._rank(card)
    return f"{name} · {color}{rank}" if color and rank else name


def _material(game, seat, card, engine):
    """Return the attack kind available through a skill."""
    kind = engine._kind(card)
    if "wushen" in enabled_skills(game, seat, engine) and suit(game, seat, card, engine) == "红桃":
        return "strike"
    return kind


def strike_cards(game, seat, engine):
    return [(card, kind) for card in seat["hand"] if (kind := _material(game, seat, card, engine)) in ATTACKS]


def _p(game, seat, skill):
    markers = seat.get("general_marks", {})
    return markers.get(skill, 0)


def _hearts(game, seat, engine):
    return (card for card in seat["hand"] if suit(game, seat, card, engine) == "红桃")


def _target_option(id, label, value, target, engine):
    return engine._option(id, label, dict(value, target=target["id"]), target["id"], value.get("card"))


def _bluff_declarations(game, seat, material, engine):
    skills = enabled_skills(game, seat, engine)
    limit = attack_limit(game, seat, 1 + int("fury" in skills) + int(seat["role"] == engine.LANDLORD), engine)
    if limit and (engine._kind(seat.get("weapon")) == "crossbow" or game["strikes"] < limit):
        for kind in ("strike", "fire_strike", "thunder_strike"):
            yield from (engine._option("general_guhuo_declare", f"声明{engine.CARDS[kind][0]}",
                                       {"kind": kind, "target": target}, target)
                        for target in engine._in_range(game, seat))
    if seat["hp"] < seat["max_hp"]:
        yield engine._option("general_guhuo_declare", "声明桃", {"kind": "peach"})
    if not game.get("wine") and "wine" not in game.get("used_skills", []):
        yield engine._option("general_guhuo_declare", "声明酒", {"kind": "wine"})
    for kind in engine.CARDS:
        if kind not in engine.TRICKS or kind in engine.TILTED or kind == "counterspell":
            continue
        if kind == "insight":
            yield engine._option("general_guhuo_declare", "声明无中生有", {"kind": kind})
            continue
        for option in engine._trick_options(game, seat, kind):
            if option["id"] == "trick":
                value = {key: item for key, item in option["value"].items() if key != "card"}
                target = value.get("target")
                if not target or not prevent_trick(game, seat, engine._seat(game, target), material, engine):
                    yield engine._option("general_guhuo_declare", option["label"].replace("使用", "声明"),
                                         {"kind": kind, **value}, target)


def active_actions(game, seat, engine):
    skills = enabled_skills(game, seat, engine)
    actor = seat["id"]
    used = game.get("used_skills", [])
    if game["phase"] != "play" or game["active"] != actor:
        return []
    forest_options = forest.active_options(game, seat, engine)
    if not skills.intersection(SKILLS):
        return forest_options
    options, opponents = [], _others(game, seat)
    hand = seat["hand"]
    if "guhuo" in skills:
        options.extend(engine._option("general_guhuo", f"蛊惑 · 扣置{_card_label(card, engine)}", card, card=card)
                       for card in dict.fromkeys(hand))
    if skills & {"liegong", "wushen"}:
        budget = 1 + int("fury" in skills) + int(seat["role"] == "地主")
        budget = attack_limit(game, seat, budget, engine)
        if budget and (engine._kind(seat.get("weapon")) == "crossbow" or game["strikes"] < budget):
            for card, kind in strike_cards(game, seat, engine):
                wushen = "wushen" in skills and suit(game, seat, card, engine) == "红桃"
                special = wushen or kind != engine._kind(card)
                for target in opponents:
                    if not wushen and not attack_reaches(game, seat, target, _reach(seat, engine), engine):
                        continue
                    liegong = "liegong" in skills and (len(target["hand"]) >= seat["hp"]
                                                       or len(target["hand"]) <= _reach(seat, engine))
                    if liegong or special:
                        name = "烈弓" if liegong else "武神" if wushen else "龙魂"
                        options.append(_target_option("general_attack", f"{name} · {_card_label(card, engine)}",
                                                      {"card": card, "kind": kind, "pierce": liegong}, target, engine))
    if "qiangxi" in skills and "qiangxi" not in used:
        range_ = _reach(seat, engine)
        weapons = [card for card in hand if engine._kind(card) in engine.WEAPONS]
        if seat.get("weapon"):
            weapons.append(seat["weapon"])
        for target in opponents:
            distance = engine._distance(game, seat, target)
            if distance <= range_:
                if seat["hp"] > 1:
                    options.append(_target_option("general_qiangxi", "强袭 · 失去体力",
                                                  {"cost": "hp"}, target, engine))
                options.extend(
                    _target_option("general_qiangxi", f"强袭 · 弃{_card_label(card, engine)}",
                                   {"cost": card}, target, engine)
                    for card in weapons if card != seat.get("weapon") or distance <= 1
                )
    if "tianyi" in skills and "tianyi" not in used:
        for target in opponents:
            if any(engine._rank(card) is not None for card in target["hand"]):
                options.extend(_target_option("general_tianyi", f"天义 · 以{_card_label(card, engine)}拼点",
                                              {"card": card}, target, engine)
                               for card in hand if engine._rank(card) is not None)
    if "tiaoxin" in skills and "tiaoxin" not in used:
        options.extend(_target_option("general_tiaoxin", "挑衅", {}, target, engine)
                       for target in opponents if engine._distance(game, target, seat) <= _reach(target, engine))
    for card in hand:
        kind = _material(game, seat, card, engine)
        color = suit(game, seat, card, engine)
        marks = seat.get("general_marks", {})
        if ("shuangxiong" in skills and marks.get("shuangxiong_draw") == game["turn_number"]
                and marks.get("shuangxiong_color") not in (None, "red" if color in RED else "black")):
            options.extend(_target_option("general_trick", f"双雄 · {_card_label(card, engine)}",
                                          {"kind": "duel", "cards": [card]}, target, engine)
                           for target in opponents if not prevent_trick(game, seat, target, card, engine))
        if "huoji" in skills and color in RED and "fire_attack" in engine.CARDS:
            options.extend(_target_option("general_trick", f"火计 · {_card_label(card, engine)}",
                                          {"kind": "fire_attack", "cards": [card]}, target, engine)
                           for target in [seat, *opponents] if target["hand"] and (target is not seat or len(hand) > 1))
        if "duanliang" in skills and color in BLACK and (kind in engine.BASIC or kind in engine.SLOTS):
            options.extend(_target_option("general_trick", f"断粮 · {_card_label(card, engine)}",
                                          {"kind": "supply", "cards": [card]}, target, engine)
                           for target in opponents if engine._distance(game, seat, target) <= 2
                           and not prevent_trick(game, seat, target, card, engine)
                           and "supply" not in {
                               game.get("virtual_delays", {}).get(other, engine._kind(other))
                               for other in target.get("judgement", [])
                           })
        if "lianhuan" in skills and color == "梅花" and "chain" in engine.CARDS:
            options.append(engine._option("general_recast", f"连环重铸 · {_card_label(card, engine)}", card))
            for size in (1, 2):
                for pair in combinations([seat, *opponents], size):
                    ids = [target["id"] for target in pair]
                    label = f"连环 · {_card_label(card, engine)}"
                    options.append(engine._option("general_trick", label, {"kind": "chain", "cards": [card],
                                                                           "targets": ids}, ids[0]))
        if "zhijian" in skills and kind in engine.SLOTS:
            slot = engine.SLOTS[kind]
            options.extend(_target_option("general_zhijian", f"直谏 · {_card_label(card, engine)}",
                                          {"card": card}, target, engine)
                           for target in opponents if not target.get(slot))
        if "jiuchi" in skills and color == "黑桃" and not game.get("wine") and "wine" not in used:
            options.append(engine._option("general_jiuchi", f"酒池 · {_card_label(card, engine)}", card))
    if "luanji" in skills:
        for first, second in combinations(hand, 2):
            color = suit(game, seat, first, engine)
            if color and color == suit(game, seat, second, engine):
                opts = {"kind": "arrows", "cards": [first, second]}
                options.append(engine._option("general_trick", f"乱击 · {_card_label(first, engine)} + {_card_label(second, engine)}", opts))
    if "dimeng" in skills and "dimeng" not in used:
        available = _own_assets(seat, engine)
        for first, second in combinations(opponents, 2):
            count = abs(len(first["hand"]) - len(second["hand"]))
            if count <= len(available):
                options.append(engine._option("general_dimeng", f"缔盟 · 弃 {count} 张",
                                              [first["id"], second["id"]], first["id"]))
    if "shensu_play" in skills and not game.get("played_during_turn"):
        gear = [card for card in hand if engine._kind(card) in engine.SLOTS]
        gear.extend(card for slot in dict.fromkeys(engine.SLOTS.values()) if (card := seat.get(slot)))
        for card in gear:
            options.extend(_target_option("general_shensu", f"神速 · 弃{_card_label(card, engine)}",
                                          {"card": card}, target, engine) for target in opponents)
    if "yeyan_minor" in skills and not _p(game, seat, "yeyan"):
        active = engine._rotate(game, actor)
        positions = {entry["id"]: index for index, entry in enumerate(game["seats"], 1)}
        for size in range(1, min(3, len(active)) + 1):
            for targets in combinations(active, size):
                numbers = "、".join(str(positions[target]) for target in targets)
                options.append(engine._option("general_yeyan", f"小业炎 · {numbers} 号位各受一点火伤", list(targets)))
    if "shenfen" in skills and _p(game, seat, "rage") >= 6 and "shenfen" not in used:
        options.append(engine._option("general_shenfen", "神愤 · 移去 6 枚暴怒"))
    if ("jilue_cycle" in skills and _p(game, seat, "awakened") and _p(game, seat, "patience")
            and "jilue_cycle" not in used and _own_assets(seat, engine)):
        options.append(engine._option("general_jilue_start", "极略 · 制衡"))
    options.extend(forest_options)
    options.extend(wind_fire.active_actions(game, seat, engine))
    options.extend(divine.active_actions(game, seat, engine))
    return options


def response_actions(game, seat, engine):
    skills = enabled_skills(game, seat, engine)
    phase, hand = game["phase"], seat["hand"]
    if phase == "general":
        choice = game.get("general_state", {}).get("choice", {})
        if seat["id"] != choice.get("player"):
            return []
        label = choice["label"]
        if label.startswith("divine_"):
            return divine.response_actions(game, seat, engine)
        if label.startswith("fm_"):
            return forest.choice_options(game, seat, choice, engine)
        if label.startswith("wind_"):
            return wind_fire.response_actions(game, seat, choice, engine)
        decline = engine._option("general_decline", "跳过")
        if label == "jushou":
            return [engine._option("general_jushou", "据守 · 摸牌后翻面"), decline]
        if label == "shuangxiong":
            return [engine._option("general_shuangxiong", "双雄 · 替换本回合摸牌"), decline]
        if label == "guhuo_declare":
            return [*(_bluff_declarations(game, seat, choice["material"], engine)), decline]
        if label == "guhuo_challenge":
            kind = game["general_state"]["bluff"]["declaration"]["kind"]
            return [engine._option("general_guhuo_doubt", f"质疑{engine.CARDS[kind][0]}"),
                    engine._option("general_guhuo_pass", "相信于吉")]
        if label == "qiaobian_draw":
            targets = [other for other in _others(game, seat) if other["hand"]]
            options = [engine._option(
                "general_qiaobian", f"巧变 · 弃{_card_label(card, engine)}，取{'、'.join(target['id'] for target in group)}的牌",
                {"card": card, "targets": [target["id"] for target in group]}, group[0]["id"], card)
                for card in hand for size in (1, 2) for group in combinations(targets, size)]
            return [decline, *options]
        if label == "huashen":
            options = [
                engine._option("general_huashen", f"化身 · {engine.GENERALS[key][0]} 的{engine.SKILLS[skill][0]}",
                               {"key": key, "skill": skill})
                for key, _ in seat.get("general_marks", {}).get("avatars", ()) for skill in _avatar_options(key, engine)
            ]
            return options if choice.get("setup") else [decline, *options]
        if label == "stars_end":
            stars = seat.get("general_cards", {}).get("star", ())
            options = [decline]
            for mark, name in (("fog_source", "大雾"), ("wind_source", "狂风")):
                if mark == "wind_source" and choice.get("wind_used"):
                    continue
                for target in game["seats"]:
                    if target["alive"] and mark not in target.get("general_marks", {}):
                        options.extend(
                            _target_option("general_star_mark", f"{name} · 弃{_card_label(star, engine)}",
                                           {"card": star, "mark": mark}, target, engine) for star in stars)
            return options
        if label == "benghuai":
            return [engine._option("general_benghuai", "崩坏 · 失去体力", "hp"),
                    engine._option("general_benghuai", "崩坏 · 扣减体力上限", "limit")]
        if label == "jieming":
            options = [engine._option("general_jieming", "节命 · 补充手牌", player["id"], player["id"])
                       for player in game["seats"] if player["alive"] and len(player["hand"]) < min(5, player["max_hp"])]
            return [*options, decline]
        if label == "yinghun":
            options = []
            for target in _others(game, seat):
                for draw, discard in ((choice["lost"], 1), (1, choice["lost"])):
                    option = _target_option("general_yinghun", f"英魂 · 摸 {draw} 弃 {discard}",
                                            {"draw": draw, "discard": discard}, target, engine)
                    if option not in options:
                        options.append(option)
            return [*options, decline]
        if label == "yinghun_cost":
            return [engine._option("general_yinghun_cost", f"英魂弃{_card_label(card, engine)}", card)
                    for card in _own_assets(seat, engine)]
        if label == "leiji":
            return [*(engine._option("general_leiji", "雷击 · 指定判定目标", target["id"], target["id"])
                      for target in _others(game, seat)), decline]
        if label == "mengjin":
            return [engine._option("general_mengjin", "猛进 · 弃置对方一张牌"), decline]
        if label == "tianyi":
            return [engine._option("general_pindian", f"亮出{_card_label(card, engine)}", card)
                    for card in hand if engine._rank(card) is not None]
        if label == "tiaoxin":
            options = [engine._option("general_tiaoxin_hurt", f"挑衅回应 · {_card_label(card, engine)}", card)
                       for card, _ in strike_cards(game, seat, engine)]
            return [*options, decline]
        if label == "xiangle":
            return [*(engine._option("general_xiangle_cost", f"享乐 · 弃{_card_label(card, engine)}", card)
                      for card in hand if _material(game, seat, card, engine) in engine.BASIC), decline]
        if label == "dimeng":
            return [engine._option("general_dimeng_cost", f"缔盟弃{_card_label(card, engine)}", card)
                    for card in _own_assets(seat, engine)]
        if label == "jilue":
            ops = [engine._option("general_jilue_cost", f"制衡弃{_card_label(card, engine)}", card)
                   for card in _own_assets(seat, engine)]
            if choice["count"]:
                ops.insert(0, engine._option("general_jilue_finish", f"摸回 {choice['count']} 张牌"))
            return ops
        return []
    bluff = [*forest.response_options(game, seat, engine), *divine.response_actions(game, seat, engine)]
    if "guhuo" in skills and wind_fire.can_bluff(game):
        pending = game.get("pending")
        kinds = {"counter": ("counterspell",), "dodge": ("dodge",), "strike_response": ("strike",),
                 "borrow": ("strike",), "follow_up": ("strike",) if pending and pending.get("weapon_choice") == "blade" else (),
                 }.get(phase, ())
        if phase == "rescue":
            kinds = ("peach",) if peach_allowed(game, seat, pending["target"], engine) else ()
            if pending["target"] == seat["id"]:
                kinds += ("wine",)
        bluff.extend(engine._option("general_guhuo_response", f"蛊惑 · 以{_card_label(card, engine)}声明{engine.CARDS[kind][0]}",
                                {"material": card, "kind": kind}, card=card)
                 for kind in kinds for card in dict.fromkeys(hand))
    if phase == "counter":
        return [*bluff, *(engine._option("general_counter", f"看破 · {_card_label(card, engine)}", card)
                for card in hand if engine._kind(card) != "counterspell" and "kanpo" in skills
                and suit(game, seat, card, engine) in BLACK)]
    if phase == "rescue":
        options = bluff
        own = game["pending"]["target"] == seat["id"]
        if own and "niepan" in skills and not _p(game, seat, "niepan"):
            options.append(engine._option("general_niepan", "涅槃"))
        if own and "jiuchi" in skills:
            options.extend(engine._option("general_rescue", f"酒池自救 · {_card_label(card, engine)}", card)
                           for card in hand if suit(game, seat, card, engine) == "黑桃")
        return options
    return bluff


def _discard_asset(game, seat, card, engine):
    if card in seat["hand"]:
        engine._spend(game, seat, card)
        return
    slot = engine.SLOTS[engine._kind(card)]
    game["discard"].append(engine._unequip(game, seat, slot))
    after_spend(game, seat, engine)


def _own_assets(seat, engine):
    return [*seat["hand"], *(seat[slot] for slot in dict.fromkeys(engine.SLOTS.values()) if seat.get(slot))]


def _take_asset(game, seat, engine):
    visible = [(slot, seat[slot]) for slot in dict.fromkeys(engine.SLOTS.values()) if seat.get(slot)]
    visible += [("hand", card) for card in seat["hand"]]
    if not visible:
        return
    slot, card = SystemRandom().choice(visible)
    if slot == "hand":
        seat["hand"].remove(card)
    else:
        engine._unequip(game, seat, slot)
    game["discard"].append(card)
    after_spend(game, seat, engine)


def _resume(game, engine):
    _state(game).pop("choice", None)
    engine._complete(game)


def _hit(game, source, target, card, engine, *, amount=1, nature=None):
    effect = {"actor": source["id"], "target": target["id"], "card": "strike", "general_skill": card, "damage": amount}
    if nature:
        effect["nature"] = nature
    game["pending"] = effect
    engine._damage(game, effect)


def _strike(game, source, target, kind, engine, *, card=None, pierce=False, consume=True):
    if consume:
        game["strikes"] += 1
    color = suit(game, source, card, engine) if card else None
    own_turn = game["active"] == source["id"]
    pending = {
        "actor": source["id"], "target": target["id"], "card": kind, "damage": 1 + int(own_turn and game.get("wine", False)),
        "nature": engine.NATURES.get(kind), "slash": True,
        "color": "black" if color in BLACK else "red" if color in RED else None,
        "pierce": pierce,
    }
    if card:
        pending["physical"] = card
    if own_turn:
        game["wine"] = False
    game["pending"] = pending
    if before_attack(game, source, target, pending, engine, pierce=pierce):
        return
    _ask_dodge(game, pending, engine)


def _ask_dodge(game, pending, engine):
    engine._ask_dodge(game, pending)
    if pending.get("pierce") and game["pending"] is pending and game["phase"] == "dodge":
        engine._hit(game, pending)


def before_attack(game, actor, target, pending, engine, *, pierce=False):
    if pending.get("xiangle_checked") or "xiangle" not in enabled_skills(game, target, engine):
        return False
    pending["xiangle_checked"] = True
    _stage(game, "xiangle", actor["id"], pierce=pierce or pending.get("pierce", False))
    return True


def after_dodge(game, seat, pending, engine):
    actor = engine._seat(game, pending["actor"])
    if seat["alive"] and _others(game, seat) and "leiji" in enabled_skills(game, seat, engine):
        _stage(game, "leiji", seat["id"])
        return True
    if (actor and actor["alive"] and pending["card"] in ATTACKS
            and "mengjin" in enabled_skills(game, actor, engine) and engine._has_assets(seat)):
        _stage(game, "mengjin", actor["id"], target=seat["id"])
        return True
    return False


def end_turn(game, seat, engine):
    marker = [game["turn_number"], seat["id"]]
    if not seat["alive"] or _state(game).get("ended") == marker:
        return False
    _state(game)["ended"] = marker
    skills = enabled_skills(game, seat, engine)
    if "jushou" in skills:
        _stage(game, "jushou", seat["id"])
        return True
    if "benghuai" in skills and any(other["hp"] < seat["hp"] for other in _others(game, seat)):
        _stage(game, "benghuai", seat["id"])
        return True
    if "huashen" in skills and seat.get("general_marks", {}).get("avatars"):
        _stage(game, "huashen", seat["id"], end=True)
        return True
    if skills & {"dawu", "kuangfeng"} and seat.get("general_cards", {}).get("star"):
        _stage(game, "stars_end", seat["id"])
        return True
    return False


def _pay(game, seat, card, engine):
    engine._spend(game, seat, card)


def _gain(game, seat, engine, text, amount=1):
    engine._draw(game, seat, amount)
    engine._add_log(game, text, seat["id"])


def _judgement(game, seat, engine):
    card = engine._take(game)
    if card is None:
        return None
    game["discard"].append(card)
    result = suit(game, seat, card, engine)
    engine._add_log(game, f"判定为{result} · {_card_label(card, engine)}", seat["id"])
    return result


def _dimeng(game, source, ids, engine):
    first, second = (engine._seat(game, player) for player in ids)
    lost = [seat for seat in (first, second) if seat["hand"]]
    first["hand"], second["hand"] = second["hand"], first["hand"]
    for seat in lost:
        after_spend(game, seat, engine)
    engine._add_log(game, "缔盟，两人交换手牌", source["id"], first["id"])
    _resume(game, engine)


def _begin_effects(game, source, targets, label, engine, **effect):
    game["queue"][:0] = [{"actor": source["id"], "target": target, "card": "strike", "general_skill": label,
                        "settlement": "damage", **effect} for target in targets]
    engine._complete(game)


def continue_effects(game, engine):
    """Continue pending compound effects after each damage window."""
    state = _state(game)
    if game["queue"]:
        return False
    if state.get("bluff", {}).get("settled"):
        return _continue_bluff(game, engine)
    if targets := state.pop("after_shenfen", None):
        if divine.after_shenfen(game, targets, engine):
            return True
    if after := state.pop("after_play", None):
        active = engine._seat(game, after)
        if active["alive"]:
            game["phase"] = "play"
            engine._end_play(game, active)
            return True
    return False


def _virtual_trick(game, seat, value, engine):
    kind, materials = value["kind"], value["cards"]
    target = value.get("target")
    engine._use_trick(game, seat, materials[0], target, virtual_kind=kind, materials=materials,
                     targets=value.get("targets"))


def _finish_pindian(game, seat, choice, card, engine):
    _state(game).pop("choice", None)
    source = engine._seat(game, choice["source"])
    original = choice["card"]
    _pay(game, source, original, engine)
    _pay(game, seat, card, engine)
    rank, opponent = engine._rank(original), engine._rank(card)
    won = rank > opponent
    engine._add_log(game, f"天义拼点，亮出 {_card_label(original, engine)} 对 {_card_label(card, engine)}",
                    source["id"], seat["id"])
    _state(game)["tianyi"] = [game["turn_number"], source["id"], won]
    _resume(game, engine)


def _open_bluff(game, source, material, declaration, engine, *, phase="play"):
    source["hand"].remove(material)
    source.setdefault("general_cards", {}).setdefault("bluff", []).append(material)
    order = [player for player in engine._after(game, source["id"])
             if player != source["id"] and engine._seat(game, player)["hp"] > 0]
    state = _state(game)
    previous = state.get("bluff")
    state["bluff"] = {
        "source": source["id"], "material": material, "declaration": declaration, "doubts": [],
        "order": order, "phase": phase, "pending": game.get("pending"), "queue": game["queue"], "parent": previous,
    }
    game.update(pending=None, queue=[])
    engine._add_log(game, f"蛊惑，暗置一张牌并声明{engine.CARDS[declaration['kind']][0]}",
                    source["id"], declaration.get("target"))
    if order:
        _stage(game, "guhuo_challenge", order[0])
    else:
        _settle_bluff(game, engine)


def _settle_bluff(game, engine):
    state = _state(game)
    bluff = state["bluff"]
    state.pop("choice", None)
    source = engine._seat(game, bluff["source"])
    material = bluff["material"]
    called = bluff["declaration"]["kind"]
    doubts = bluff["doubts"]
    bluff.update(settled=True, valid=True, losses=[])
    if doubts:
        real = engine._kind(material)
        truth = real == called or called == "strike" and real in ATTACKS
        bluff["valid"] = truth and suit(game, source, material, engine) == "红桃"
        bluff["losses"] = doubts if truth else []
        engine._add_log(game, f"蛊惑亮牌 · {_card_label(material, engine)}，{'真牌' if truth else '假牌'}", source["id"])
        if not truth:
            for player in doubts:
                _gain(game, engine._seat(game, player), engine, "质疑到假牌，摸一张")
    _continue_bluff(game, engine)


def _continue_bluff(game, engine):
    state = _state(game)
    bluff = state["bluff"]
    while bluff["losses"]:
        target = engine._seat(game, bluff["losses"].pop(0))
        if not target["alive"]:
            continue
        target["hp"] -= 1
        engine._add_log(game, "质疑到真牌，失去一点体力", target["id"])
        if target["hp"] <= 0:
            game.update(phase="rescue", pending={
                "actor": bluff["source"], "target": target["id"], "card": "strike", "no_source": True,
                "order": engine._rotate(game, target["id"]), "index": 0,
            })
            engine._add_log(game, "进入濒死，等待救援", target["id"])
            return True
    if bluff.get("parent"):
        state["bluff"] = bluff["parent"]
    else:
        state.pop("bluff")
    source = engine._seat(game, bluff["source"])
    material = bluff["material"]
    phase, pending = bluff["phase"], bluff["pending"]
    game.update(phase=phase, pending=pending, queue=bluff["queue"])
    pile = source.get("general_cards", {}).get("bluff", [])
    if material not in pile:
        engine._complete(game)
        return True
    pile.remove(material)
    if phase == "rescue":
        pending["order"] = [player for player in pending["order"][pending["index"]:]
                            if engine._seat(game, player)["alive"]]
        pending["index"] = 0
    if (game.get("tournament_turn_ended") or pending and not engine._seat(game, pending["target"])["alive"]
            or phase == "strike_response" and pending["card"] == "duel"
            and not engine._seat(game, pending["actor"])["alive"]):
        game["discard"].append(material)
        if pending and pending.get("judging"):
            game["discard"].append(pending["card"])
        engine._complete(game)
        return True
    if not bluff["valid"]:
        game["discard"].append(material)
        engine._add_log(game, "蛊惑被质疑，所声明的牌作废", source["id"])
        return True
    source["hand"].append(material)
    if phase == "play":
        _launch_bluff(game, source, material, bluff["declaration"], engine)
    else:
        _bluff_response(game, source, material, bluff["declaration"]["kind"], pending, engine)
    return True


def _launch_bluff(game, source, material, declaration, engine):
    kind = declaration["kind"]
    target_id = declaration.get("target")
    target = engine._seat(game, target_id)
    if (target and not target["alive"] or declaration.get("victim")
            and not engine._seat(game, declaration["victim"])["alive"]):
        _pay(game, source, material, engine)
        return
    if kind in ATTACKS:
        _pay(game, source, material, engine)
        _strike(game, source, target, kind, engine, card=material)
    elif kind in {"peach", "wine"}:
        _pay(game, source, material, engine)
        if kind == "peach":
            _hp(source, 1)
        else:
            game["wine"] = True
            _skills_used(game).append("wine")
    else:
        engine._use_trick(game, source, material, target_id, targets=declaration.get("targets"),
                          victim=declaration.get("victim"), virtual_kind=kind, materials=(material,))


def _bluff_response(game, source, material, kind, pending, engine):
    phase, player = game["phase"], source["id"]
    if phase == "rescue":
        engine._save(game, source, material, pending)
        return
    _pay(game, source, material, engine)
    if phase == "dodge":
        engine._after_dodge(game, pending)
    elif phase == "counter":
        pending["negated"] = not pending["negated"]
        pending["passed"] = [player]
        engine._add_log(game, "蛊惑响应无懈可击", player, pending["target"])
        engine._counter_window(game, pending, engine._after(game, player))
    elif phase == "strike_response":
        if pending["card"] == "duel":
            pending["actor"], pending["target"] = pending["target"], pending["actor"]
        else:
            engine._complete(game)
    elif phase in {"borrow", "follow_up"}:
        target = engine._seat(game, pending["victim"] if phase == "borrow" else pending["target"])
        if target["alive"]:
            _strike(game, source, target, kind, engine, card=material, consume=False)
        else:
            engine._complete(game)


def _play_pending(game, seat, action, value, engine):
    choice = _state(game).get("choice")
    if not choice or choice.get("player") != seat["id"]:
        return False
    label, player = choice["label"], seat["id"]
    if label.startswith("fm_"):
        return forest.resolve_choice(game, seat, choice, action, value, engine)
    if label.startswith("wind_"):
        return wind_fire.play_pending(game, seat, choice, action, value, engine)
    if label == "jushou":
        if action != "general_decline":
            seat["face_down"] = True
            _gain(game, seat, engine, "据守，摸三张并翻面", 3)
        _state(game).pop("choice")
        engine._advance(game)
    elif label == "shuangxiong":
        if action == "general_shuangxiong":
            _marks(seat)["shuangxiong_draw"] = game["turn_number"]
        _resume(game, engine)
    elif label == "guhuo_declare":
        if action == "general_decline":
            _resume(game, engine)
        else:
            _open_bluff(game, seat, choice["material"], value, engine)
    elif label == "guhuo_challenge":
        bluff = _state(game)["bluff"]
        if action == "general_guhuo_doubt":
            bluff["doubts"].append(player)
            engine._add_log(game, "质疑蛊惑", player, bluff["source"])
        bluff["order"].pop(0)
        if bluff["order"]:
            _stage(game, "guhuo_challenge", bluff["order"][0])
        else:
            _settle_bluff(game, engine)
    elif label == "qiaobian_draw":
        if action == "general_qiaobian":
            _pay(game, seat, value["card"], engine)
            _marks(seat).update(qiaobian_turn=game["turn_number"], qiaobian_targets=value["targets"])
        _resume(game, engine)
    elif label == "huashen":
        if action == "general_huashen":
            key, skill = value["key"], value["skill"]
            _marks(seat).update(avatar_key=key, avatar_skill=skill)
            engine._add_log(game, f"化身为{engine.GENERALS[key][0]}，取得{engine.SKILLS[skill][0]}", player)
        if choice.get("setup"):
            _state(game).pop("choice")
            engine._begin_turn(game)
        elif choice.get("end"):
            _state(game).pop("choice")
            engine._advance(game)
        else:
            _resume(game, engine)
    elif label == "stars_end":
        if action == "general_star_mark":
            seat["general_cards"]["star"].remove(value["card"])
            game["discard"].append(value["card"])
            target = engine._seat(game, value["target"])
            _marks(target)[value["mark"]] = player
            if value["mark"] == "wind_source":
                choice["wind_used"] = True
            name = "狂风" if value["mark"] == "wind_source" else "大雾"
            engine._add_log(game, f"{name}，消耗一张星", player, target["id"])
            return True
        _state(game).pop("choice")
        engine._advance(game)
    elif label == "benghuai":
        if value == "hp":
            seat["hp"] -= 1
        else:
            seat["max_hp"] -= 1
            seat["hp"] = min(seat["hp"], seat["max_hp"])
        _state(game).pop("choice")
        engine._add_log(game, "崩坏，扣减体力" if value == "hp" else "崩坏，扣减体力上限", player)
        engine._advance(game)
    elif label == "jieming":
        if action != "general_decline":
            target = engine._seat(game, value)
            engine._draw(game, target, min(5, target["max_hp"]) - len(target["hand"]))
            engine._add_log(game, "节命，令目标补充手牌", player, target["id"])
            choice["remaining"] -= 1
            if choice["remaining"]:
                return True
        _resume(game, engine)
    elif label == "yinghun":
        if action != "general_decline":
            target = engine._seat(game, value["target"])
            engine._draw(game, target, value["draw"])
            cost = min(value["discard"], len(_own_assets(target, engine)))
            if cost:
                _stage(game, "yinghun_cost", target["id"], cost=cost)
                return True
        _resume(game, engine)
    elif label == "yinghun_cost":
        _discard_asset(game, seat, value, engine)
        choice["cost"] -= 1
        if not choice["cost"]:
            _resume(game, engine)
    elif label == "leiji":
        _state(game).pop("choice")
        if action == "general_leiji":
            target = engine._seat(game, value)
            wind_fire.judge(game, target, {"kind": "leiji", "source": player, "target": value}, engine)
        else:
            engine._complete(game)
    elif label == "mengjin":
        if action == "general_mengjin":
            target = engine._seat(game, choice["target"])
            _take_asset(game, target, engine)
            engine._add_log(game, "猛进，对方失去一张牌", player, target["id"])
        _resume(game, engine)
    elif label == "tianyi":
        _finish_pindian(game, seat, choice, value, engine)
    elif label == "tiaoxin":
        _state(game).pop("choice")
        source = engine._seat(game, choice["source"])
        if action == "general_tiaoxin_hurt":
            kind = next(kind for card, kind in strike_cards(game, seat, engine) if card == value)
            _pay(game, seat, value, engine)
            # The responding seat's attack belongs to the challenger’s turn.
            wine = game.get("wine")
            game["wine"] = False
            _strike(game, seat, source, kind, engine, card=value, consume=False)
            game["wine"] = wine
        else:
            _take_asset(game, seat, engine)
            engine._add_log(game, "挑衅，弃去目标的一张牌", source["id"], seat["id"])
            engine._complete(game)
    elif label == "xiangle":
        _state(game).pop("choice")
        if action == "general_xiangle_cost":
            _pay(game, seat, value, engine)
            _ask_dodge(game, game["pending"], engine)
        else:
            engine._add_log(game, "享乐，因未付出基本牌此次杀无效", player)
            engine._complete(game)
    elif label == "dimeng":
        _discard_asset(game, seat, value, engine)
        choice["cost"] -= 1
        if choice["cost"] == 0:
            _dimeng(game, seat, choice["targets"], engine)
    elif label == "jilue":
        if action == "general_jilue_cost":
            _discard_asset(game, seat, value, engine)
            choice["count"] += 1
            if _own_assets(seat, engine):
                return True
        _gain(game, seat, engine, "极略制衡，弃牌并等量摸牌", choice["count"])
        _resume(game, engine)
    else:
        return False
    return True


def play_extra(game, seat, action, value, engine):
    """Dispatch a skill action after the shared engine has verified its legality."""
    if action.startswith("divine_"):
        return divine.play(game, seat, action, value, engine)
    if not action.startswith("general_"):
        return False
    if game["phase"] == "general":
        return _play_pending(game, seat, action, value, engine)
    if action.startswith("general_fm_"):
        return forest.extra_action(game, seat, action, value, engine)
    if action.startswith("general_wind_"):
        return wind_fire.play_extra(game, seat, action, value, engine)
    player = seat["id"]
    pending = game.get("pending")
    if action == "general_guhuo":
        _stage(game, "guhuo_declare", player, material=value)
    elif action == "general_guhuo_response":
        _open_bluff(game, seat, value["material"], {"kind": value["kind"]}, engine, phase=game["phase"])
    elif action == "general_attack":
        target = engine._seat(game, value["target"])
        _pay(game, seat, value["card"], engine)
        _strike(game, seat, target, value["kind"], engine, card=value["card"], pierce=value["pierce"])
    elif action == "general_qiangxi":
        _skills_used(game).append("qiangxi")
        if value["cost"] == "hp":
            seat["hp"] -= 1
        else:
            _discard_asset(game, seat, value["cost"], engine)
        target = engine._seat(game, value["target"])
        engine._add_log(game, "强袭，令目标受到一点伤害", player, target["id"])
        _hit(game, seat, target, "qiangxi", engine)
    elif action == "general_tianyi":
        _skills_used(game).append("tianyi")
        _stage(game, "tianyi", value["target"], source=player, card=value["card"])
        engine._add_log(game, "天义，发起拼点", player, value["target"])
    elif action == "general_tiaoxin":
        _skills_used(game).append("tiaoxin")
        _stage(game, "tiaoxin", value["target"], source=player)
        engine._add_log(game, "挑衅，要求目标出杀", player, value["target"])
    elif action == "general_trick":
        _virtual_trick(game, seat, value, engine)
    elif action == "general_recast":
        _pay(game, seat, value, engine)
        _gain(game, seat, engine, "连环重铸，摸一张")
    elif action == "general_zhijian":
        target = engine._seat(game, value["target"])
        card = value["card"]
        seat["hand"].remove(card)
        target[engine.SLOTS[engine._kind(card)]] = card
        _gain(game, seat, engine, "直谏，将装备交给目标后摸一张")
    elif action == "general_jiuchi":
        _pay(game, seat, value, engine)
        game["wine"] = True
        _skills_used(game).append("wine")
        engine._add_log(game, "酒池，用黑桃牌饮酒", player)
    elif action == "general_dimeng":
        one, two = (engine._seat(game, uid) for uid in value)
        cost = abs(len(one["hand"]) - len(two["hand"]))
        _skills_used(game).append("dimeng")
        if cost:
            _stage(game, "dimeng", player, targets=value, cost=cost)
        else:
            _dimeng(game, seat, value, engine)
    elif action == "general_shensu":
        _state(game)["after_play"] = player
        _discard_asset(game, seat, value["card"], engine)
        target = engine._seat(game, value["target"])
        engine._add_log(game, "神速，跳过出牌阶段并使用杀", player, target["id"])
        _strike(game, seat, target, "strike", engine, consume=False)
    elif action == "general_yeyan":
        _gain_mark(seat, "yeyan", 1)
        engine._add_log(game, "小业炎，依次对选中目标造成火焰伤害", player)
        _begin_effects(game, seat, value, "yeyan", engine, nature="fire")
    elif action == "general_shenfen":
        _gain_mark(seat, "rage", -6)
        _skills_used(game).append("shenfen")
        targets = [uid for uid in engine._after(game, player) if uid != player]
        _state(game)["after_shenfen"] = {"actor": player, "targets": targets}
        _begin_effects(game, seat, targets, "shenfen", engine)
    elif action == "general_jilue_start":
        _gain_mark(seat, "patience", -1)
        _skills_used(game).append("jilue_cycle")
        _stage(game, "jilue", player, count=0)
    elif action == "general_counter":
        _pay(game, seat, value, engine)
        pending["negated"] = not pending["negated"]
        pending["passed"] = [player]
        engine._add_log(game, "转化一张牌为无懈可击", player)
        engine._counter_window(game, pending, engine._after(game, player))
    elif action == "general_niepan":
        _gain_mark(seat, "niepan", 1)
        engine._discard_all(game, seat)
        game["discard"].extend(seat.get("judgement", []))
        seat.setdefault("judgement", []).clear()
        seat["hp"], seat["face_down"], seat["chained"] = min(3, seat["max_hp"]), False, False
        _gain(game, seat, engine, "涅槃，重置状态并摸三张", 3)
        engine._complete(game)
    elif action == "general_rescue":
        engine._save(game, seat, value, pending)
    else:
        return False
    return True
