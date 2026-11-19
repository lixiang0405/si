"""The classic 104 standard + 4 EX + 52 Military Struggle physical cards."""

from collections import Counter


SUITS = {"S": "黑桃", "C": "梅花", "H": "红桃", "D": "方片"}
NATURES = {"fire_strike": "fire", "thunder_strike": "thunder"}
SLASHES = {"strike", *NATURES}
WEAPONS = {
    "blade": 3, "spear": 3, "crossbow": 1, "qinggang": 2, "double_sword": 2,
    "ice_sword": 2, "axe": 3, "halberd": 4, "bow": 5, "guding": 2, "fan": 4,
}
SLOTS = {
    **dict.fromkeys(WEAPONS, "weapon"),
    **dict.fromkeys(("diagram", "renwang", "rattan", "lion"), "armor"),
    **dict.fromkeys(("guard", "dilu", "zhuahuang", "hualiu"), "guard"),
    **dict.fromkeys(("steed", "chitu", "dayuan"), "steed"),
}
BASIC = {*SLASHES, "dodge", "peach", "wine"}
DELAYED = {"lightning", "indulgence", "supply"}
TRICKS = {
    "insight", "duel", "invasion", "arrows", "snatch", "dismantle", "feast", "harvest",
    "chain", "fire_attack", "collateral", "rotate_general", *DELAYED,
}
LABELS = {
    "strike": ("杀", "攻击范围内的一名角色需打出闪。"),
    "fire_strike": ("火杀", "未闪避时造成火焰伤害。"),
    "thunder_strike": ("雷杀", "未闪避时造成雷电伤害。"),
    "dodge": ("闪", "抵消一张杀，或响应万箭齐发。"),
    "peach": ("桃", "回复一点体力，也用于濒死救援。"),
    "wine": ("酒", "每回合一张，令下一张杀增伤一点；濒死时可自救。"),
    "insight": ("无中生有", "摸两张。"),
    "duel": ("决斗", "由目标开始轮流打出杀，先停的一方受伤。"),
    "invasion": ("南蛮入侵", "其他角色依次打出杀，否则受伤。"),
    "arrows": ("万箭齐发", "其他角色依次打出闪，否则受伤。"),
    "snatch": ("顺手牵羊", "取得一名距离为一的角色的一张牌。"),
    "dismantle": ("过河拆桥", "弃置一名其他角色的一张牌。"),
    "feast": ("桃园结义", "受伤角色各回复一点体力。"),
    "harvest": ("五谷丰登", "亮出若干张牌，按顺序各取一张。"),
    "counterspell": ("无懈可击", "取消一次锦囊效果，或反制上一张无懈。"),
    "indulgence": ("乐不思蜀", "判定结果不是红桃时，目标跳过出牌阶段。"),
    "supply": ("兵粮寸断", "距离一以内的目标判定不为梅花时跳过摸牌。"),
    "lightning": ("闪电", "黑桃二至九造成三点雷电伤害，未命中则向后移。"),
    "chain": ("铁索连环", "横置或重置一至两名角色，亦可重铸摸牌。"),
    "fire_attack": ("火攻", "目标展示一张手牌。弃置同花色牌可造成一点火焰伤害。"),
    "rotate_general": ("走马换将", "将指定玩家的一名场上武将置于其军营底，再令其上阵一将。若选择自己，摸一张牌。"),
    "collateral": ("借刀杀人", "装备武器的目标须对指定角色出杀，否则交出武器。"),
    "blade": ("青龙偃月刀", "攻击范围三。杀被闪抵消后可追加一张杀。"),
    "spear": ("丈八蛇矛", "攻击范围三。两张手牌可当一张杀使用或打出。"),
    "crossbow": ("诸葛连弩", "攻击范围一。本回合可连续使用杀。"),
    "qinggang": ("青釭剑", "攻击范围二。你的杀无视目标防具。"),
    "double_sword": ("雌雄双股剑", "攻击范围二。对异性用杀时可令其弃牌或让你摸牌。"),
    "ice_sword": ("寒冰剑", "攻击范围二。可用弃置目标两张牌替代伤害。"),
    "axe": ("贯石斧", "攻击范围三。杀被闪后可弃两张牌强制命中。"),
    "halberd": ("方天画戟", "攻击范围四。最后一张手牌的杀可选额外目标。"),
    "bow": ("麒麟弓", "攻击范围五。杀命中后可弃置目标的一匹坐骑。"),
    "guding": ("古锭刀", "攻击范围二。杀伤害结算时，目标无手牌则伤害加一。"),
    "fan": ("朱雀羽扇", "攻击范围四。普通杀可当火杀使用。"),
    "diagram": ("八卦阵", "需要出闪时可判定，红色视为打出闪。"),
    "renwang": ("仁王盾", "黑色杀对你无效。"),
    "rattan": ("藤甲", "普通杀、南蛮入侵和万箭齐发对你无效；受到火焰伤害时增伤一点。"),
    "lion": ("白银狮子", "每次受到的伤害最多一点，离开装备区时回复一点体力。"),
    "guard": ("绝影", "其他角色计算与你的距离加一。"),
    "dilu": ("的卢", "其他角色计算与你的距离加一。"),
    "zhuahuang": ("爪黄飞电", "其他角色计算与你的距离加一。"),
    "hualiu": ("骅骝", "其他角色计算与你的距离加一。"),
    "steed": ("紫骍", "你计算与其他角色的距离减一。"),
    "dayuan": ("大宛", "你计算与其他角色的距离减一。"),
    "chitu": ("赤兔", "你计算与其他角色的距离减一。"),
}

# Columns are physical print suits. Repeated ranks are distinct cards.
STANDARD = {
    "strike": {"S": "7 8 8 9 9 10 10", "C": "2 3 4 5 6 7 8 8 9 9 10 10 J J", "H": "10 10 J", "D": "6 7 8 9 10 K"},
    "dodge": {"H": "2 2 K", "D": "2 2 3 4 5 6 7 8 9 10 J J"},
    "peach": {"H": "3 4 6 7 8 9 Q", "D": "Q"},
    "insight": {"H": "7 8 9 J"},
    "duel": {"S": "A", "C": "A", "D": "A"},
    "invasion": {"S": "7 K", "C": "7"},
    "arrows": {"H": "A"},
    "snatch": {"S": "3 4 J", "D": "3 4"},
    "dismantle": {"S": "3 4 Q", "C": "3 4", "H": "Q"},
    "feast": {"H": "A"},
    "harvest": {"H": "3 4"},
    "counterspell": {"S": "J", "C": "Q K"},
    "indulgence": {"S": "6", "H": "6", "C": "6"},
    "lightning": {"S": "A"},
    "collateral": {"C": "Q K"},
    "blade": {"S": "5"}, "spear": {"S": "Q"}, "crossbow": {"C": "A", "D": "A"},
    "qinggang": {"S": "6"}, "double_sword": {"S": "2"}, "axe": {"D": "5"},
    "halberd": {"D": "Q"}, "bow": {"H": "5"}, "diagram": {"S": "2", "C": "2"},
    "guard": {"S": "5"}, "dilu": {"C": "5"}, "zhuahuang": {"H": "K"},
    "steed": {"D": "K"}, "dayuan": {"S": "K"}, "chitu": {"H": "5"},
}
EX = {"ice_sword": {"S": "2"}, "renwang": {"C": "2"}, "lightning": {"H": "Q"}, "counterspell": {"D": "Q"}}
ARMY = {
    "fire_strike": {"H": "4 7 10", "D": "4 5"},
    "thunder_strike": {"S": "4 5 6 7 8", "C": "5 6 7 8"},
    "peach": {"H": "5 6", "D": "2 3"},
    "dodge": {"H": "8 9 J Q", "D": "6 7 8 10 J"},
    "wine": {"S": "3 9", "C": "3 9", "D": "9"},
    "counterspell": {"S": "K", "H": "A K"},
    "chain": {"S": "J Q", "C": "10 J Q K"},
    "fire_attack": {"H": "2 3", "D": "Q"}, "supply": {"S": "10", "C": "4"},
    "guding": {"S": "A"}, "fan": {"D": "A"},
    "lion": {"C": "A"}, "rattan": {"S": "2", "C": "2"}, "hualiu": {"D": "K"},
}
RANKS = "A 2 3 4 5 6 7 8 9 10 J Q K".split()

# 游卡 2026-07-03「承天之弈门店活动」中的公共牌表，按 A 到 K、红桃／方片／梅花／黑桃。
CHENGTIAN_ROWS = (
    "feast arrows crossbow fan duel lion duel guding",
    "fire_attack dodge dodge wine diagram renwang rattan ice_sword",
    "fire_attack peach snatch dodge strike wine wine strike",
    "peach harvest snatch dodge strike supply dismantle strike",
    "rotate_general strike dodge axe dilu dismantle thunder_strike blade",
    "indulgence peach dodge fire_strike strike indulgence indulgence qinggang",
    "fire_strike insight strike dodge thunder_strike invasion strike invasion",
    "peach dodge strike dodge thunder_strike strike strike thunder_strike",
    "insight peach dodge strike wine strike strike dismantle",
    "rotate_general fire_strike strike dodge strike thunder_strike strike snatch",
    "insight peach dodge peach strike dismantle strike supply",
    "dodge lightning counterspell dodge counterspell strike dismantle spear",
    "dodge peach strike steed counterspell strike invasion counterspell",
)


def make_manifest():
    result = {}
    for code, rows in (("standard", STANDARD), ("ex", EX), ("army", ARMY)):
        for kind, suits in rows.items():
            for suit, ranks in suits.items():
                for index, rank in enumerate(ranks.split(), 1):
                    card = f"{kind}@{suit}.{rank}.{code}.{index}"
                    result[card] = {"kind": kind, "suit": SUITS[suit], "rank": RANKS.index(rank) + 1, "pack": code}
    return result


MANIFEST = make_manifest()
CHENGTIAN_MANIFEST = {
    f"{kind}@{suit}.{rank}.chengtian.{column % 2 + 1}": {
        "kind": kind, "suit": SUITS[suit], "rank": index + 1, "pack": "chengtian",
    }
    for index, (rank, row) in enumerate(zip(RANKS, CHENGTIAN_ROWS))
    for column, (suit, kind) in enumerate(zip("HHDDCCSS", row.split(), strict=True))
}
CARD_DETAILS = MANIFEST | CHENGTIAN_MANIFEST
CHENGTIAN_DECK = tuple(CHENGTIAN_MANIFEST)
assert len(CHENGTIAN_DECK) == 104
COUNTS = Counter(details["kind"] for details in MANIFEST.values())
CARDS = {kind: (*labels, COUNTS[kind]) for kind, labels in LABELS.items()}
DECK = tuple(MANIFEST)
assert Counter(row["pack"] for row in MANIFEST.values()) == {"standard": 104, "ex": 4, "army": 52}
assert Counter((row["suit"], row["rank"]) for row in MANIFEST.values() if row["pack"] == "army").most_common(1)[0][1] == 1


def kind(card):
    return card.partition("@")[0] if isinstance(card, str) else card


def suit(card):
    return CARD_DETAILS.get(card, {}).get("suit")


def rank(card):
    return CARD_DETAILS.get(card, {}).get("rank")


def label(card):
    return LABELS[kind(card)][0]


def name_with_rank(card):
    detail = CARD_DETAILS.get(card)
    if not detail:
        return label(card)
    return f"{label(card)} · {detail['suit']}{RANKS[detail['rank'] - 1]}"
