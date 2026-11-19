"""Server-side no-limit Texas hold'em for a small table of friends."""

from collections import Counter
from itertools import combinations
from random import SystemRandom


RANKS = "23456789TJQKA"
SUITS = "CDHS"
DECK = [rank + suit for rank in RANKS for suit in SUITS]
RNG = SystemRandom()
HAND_NAMES = ("高牌", "一对", "两对", "三条", "顺子", "同花", "葫芦", "四条", "同花顺")
STREETS = {"preflop": ("flop", 3), "flop": ("turn", 1), "turn": ("river", 1)}
SMALL_BLIND = 10
BIG_BLIND = 20


def _score_five(cards):
    ranks = sorted((RANKS.index(card[0]) + 2 for card in cards), reverse=True)
    distinct = set(ranks)
    flush = len({card[1] for card in cards}) == 1
    straight = None
    if len(distinct) == 5:
        if ranks[0] - ranks[-1] == 4:
            straight = ranks[0]
        elif distinct == {14, 5, 4, 3, 2}:
            straight = 5
    if flush and straight:
        return 8, straight
    counts = Counter(ranks)
    groups = sorted(((count, rank) for rank, count in counts.items()), reverse=True)
    primary_count, primary_rank = groups[0]
    if primary_count == 4:
        return 7, primary_rank, groups[1][1]
    if primary_count == 3 and groups[1][0] == 2:
        return 6, primary_rank, groups[1][1]
    if flush:
        return 5, *ranks
    if straight:
        return 4, straight
    if primary_count == 3:
        return 3, primary_rank, *(rank for _, rank in groups[1:])
    if primary_count == 2 and groups[1][0] == 2:
        return 2, primary_rank, groups[1][1], groups[2][1]
    if primary_count == 2:
        return 1, primary_rank, *(rank for _, rank in groups[1:])
    return 0, *ranks


def evaluate(cards):
    """Compare the best five cards from five, six or seven unique cards."""
    if not 5 <= len(cards) <= 7 or any(card not in DECK for card in cards) or len(set(cards)) != len(cards):
        raise ValueError("牌型需要五至七张互不相同的牌")
    return max(_score_five(choice) for choice in combinations(cards, 5))


def create(players, *, starting_stack=1000, max_hands=5):
    if (not 2 <= len(players) <= 8 or any(not isinstance(player, str) or not player for player in players)
            or len(set(players)) != len(players)):
        raise ValueError("德州需要 2 至 8 名不同的玩家")
    if type(starting_stack) is not int or starting_stack <= 0 or type(max_hands) is not int or max_hands <= 0:
        raise ValueError("初始筹码和手数须为正整数")
    game = {
        "players": [{"id": player, "stack": starting_stack, "cards": []} for player in players],
        "hand": 0, "maxHands": max_hands, "lastResult": None, "button": None, "bigBlind": None,
        "finished": False, "result": "",
    }
    _start_hand(game)
    return game


def _clockwise(game, after):
    seats = game["players"]
    index = next(index for index, seat in enumerate(seats) if seat["id"] == after)
    return seats[index + 1:] + seats[:index + 1]


def _start_hand(game):
    seats = game["players"]
    order = _clockwise(game, game["button"]) if game["button"] else seats
    funded = [seat for seat in order if seat["stack"]]
    button = funded[0]["id"]
    # On entry to heads-up, the last big blind becomes the small blind if funded.
    previous_count = sum(bool(seat["cards"]) for seat in seats)
    if len(funded) == 2 and previous_count > 2 and any(seat["id"] == game["bigBlind"] for seat in funded):
        button = game["bigBlind"]
    game.update({
        "phase": "preflop", "turn": None, "board": [], "deck": DECK.copy(), "button": button,
        "hand": game["hand"] + 1, "currentBet": BIG_BLIND, "minRaise": BIG_BLIND,
        "acted": {}, "refunds": [],
    })
    RNG.shuffle(game["deck"])
    for seat in seats:
        seat.update(cards=[], bet=0, posted=0, status="active" if seat["stack"] else "out")
    eligible = [seat for seat in _clockwise(game, button) if seat["status"] == "active"]
    small, big = reversed(eligible) if len(eligible) == 2 else eligible[:2]
    game["bigBlind"] = big["id"]
    for _ in range(2):
        for seat in eligible:
            seat["cards"].append(game["deck"].pop())
    _put_chips(small, min(small["stack"], SMALL_BLIND))
    _put_chips(big, min(big["stack"], BIG_BLIND))
    _advance(game, big["id"])


def _put_chips(seat, chips):
    seat["stack"] -= chips
    seat["bet"] += chips
    seat["posted"] += chips
    if not seat["stack"]:
        seat["status"] = "all_in"


def _contenders(game):
    return [seat for seat in game["players"] if seat["status"] in ("active", "all_in")]


def _call_target(game, seat):
    # A lone betting player only faces actual chips committed by an opponent.
    others = [other for other in _contenders(game) if other is not seat]
    if any(other["status"] == "active" for other in others):
        return game["currentBet"]
    return max((other["bet"] for other in others), default=0)


def _actions(game, player):
    if player is None or game["finished"]:
        return []
    seat = next((seat for seat in game["players"] if seat["id"] == player), None)
    if seat is None:
        return []
    if game["phase"] == "hand_end":
        return [{"id": "next", "label": "下一手"}] if seat["stack"] else []
    if game["turn"] != player:
        return []
    target = _call_target(game, seat)
    due = max(0, target - seat["bet"])
    actions = [{"id": "fold", "label": "弃牌"}]
    if due:
        payment = min(due, seat["stack"])
        actions.append({"id": "call", "label": f"跟注 {payment}", "amount": payment})
    else:
        actions.append({"id": "check", "label": "过牌"})

    last_action = game["acted"].get(player)
    may_raise = (
        any(other is not seat and other["status"] == "active" for other in game["players"])
        and (last_action is None or game["currentBet"] - last_action >= game["minRaise"])
    )
    maximum = seat["bet"] + seat["stack"]
    minimum = game["currentBet"] + game["minRaise"]
    if may_raise and maximum >= minimum:
        actions.append({
            "id": "raise", "label": "加注" if game["currentBet"] else "下注",
            "min": minimum, "max": maximum, "step": 1,
        })
    if maximum <= target or may_raise:
        actions.append({"id": "all_in", "label": "全下", "amount": seat["stack"]})
    return actions


def play(game, player, action, value=None):
    """Mutate game for one legal action. A raise specifies the total street bet."""
    option = next((option for option in _actions(game, player) if option["id"] == action), None)
    if option is None:
        raise ValueError("现在无法执行这个操作")
    if action == "raise":
        if type(value) is not int or not option["min"] <= value <= option["max"]:
            raise ValueError(f"本轮下注总额须在 {option['min']} 至 {option['max']} 之间")
    elif value is not None:
        raise ValueError("此操作无需下注金额")

    if action == "next":
        _start_hand(game)
        return
    seat = next(seat for seat in game["players"] if seat["id"] == player)
    if action == "fold":
        seat["status"] = "folded"
    elif action != "check":
        amount = value - seat["bet"] if action == "raise" else option["amount"]
        _put_chips(seat, amount)
        if seat["bet"] > game["currentBet"]:
            increment = seat["bet"] - game["currentBet"]
            if increment >= game["minRaise"]:
                game["minRaise"] = increment
            game["currentBet"] = seat["bet"]
    game["acted"][player] = game["currentBet"]
    _advance(game, player)


def _advance(game, after):
    while True:
        contenders = _contenders(game)
        if len(contenders) == 1:
            _refund_uncalled(game)
            _settle(game, contenders)
            return
        actors = [seat for seat in _clockwise(game, after) if seat["status"] == "active"]
        for seat in actors:
            owed = seat["bet"] < _call_target(game, seat)
            must_act = len(actors) > 1 and seat["id"] not in game["acted"]
            if owed or must_act:
                game["turn"] = seat["id"]
                return
        _refund_uncalled(game)
        if game["phase"] == "river":
            _settle(game, contenders)
            return
        phase, count = STREETS[game["phase"]]
        game["deck"].pop()
        game["board"].extend(game["deck"].pop() for _ in range(count))
        for seat in game["players"]:
            seat["bet"] = 0
        game.update(phase=phase, currentBet=0, minRaise=BIG_BLIND, acted={})
        after = game["button"]


def _refund_uncalled(game):
    largest, second, *_ = sorted(game["players"], key=lambda seat: seat["bet"], reverse=True)
    excess = largest["bet"] - second["bet"]
    if not excess:
        return
    largest["stack"] += excess
    largest["bet"] -= excess
    largest["posted"] -= excess
    if largest["status"] == "all_in":
        largest["status"] = "active"
    game["refunds"].append({"id": largest["id"], "amount": excess})


def _settle(game, contenders):
    seats = game["players"]
    pot = sum(seat["posted"] for seat in seats)
    if len(contenders) == 1:
        reason = "fold"
        ranks = {}
        awards = Counter({contenders[0]["id"]: pot})
    else:
        reason = "showdown"
        ranks = {seat["id"]: evaluate(seat["cards"] + game["board"]) for seat in contenders}
        awards = _award_pots(game, ranks)
    payouts = []
    for seat in seats:
        player = seat["id"]
        if awards[player]:
            seat["stack"] += awards[player]
            payouts.append({
                "id": player, "amount": awards[player],
                "hand": HAND_NAMES[ranks[player][0]] if player in ranks else "",
            })
        seat.update(bet=0, posted=0)
    game.update(
        phase="hand_end", turn=None,
        lastResult={"reason": reason, "pot": pot, "payouts": payouts, "refunds": game["refunds"].copy()},
    )
    if game["hand"] >= game["maxHands"] or sum(seat["stack"] > 0 for seat in seats) < 2:
        best = max(seat["stack"] for seat in seats)
        tied = sum(seat["stack"] == best for seat in seats)
        winner = f"{tied} 人并列第一" if tied > 1 else "最高筹码"
        game.update(finished=True, phase="finished", result=f"德州散桌 · 共 {game['hand']} 手 · {winner} {best}")


def _award_pots(game, ranks):
    clockwise = _clockwise(game, game["button"])
    levels = sorted({seat["posted"] for seat in clockwise if seat["posted"]})
    previous = 0
    awards = Counter()
    for level in levels:
        contributors = [seat["id"] for seat in clockwise if seat["posted"] >= level]
        amount = (level - previous) * len(contributors)
        eligible = [player for player in contributors if player in ranks]
        best = max(ranks[player] for player in eligible)
        winners = [player for player in eligible if ranks[player] == best]
        share, remainder = divmod(amount, len(winners))
        for index, player in enumerate(winners):
            awards[player] += share + (index < remainder)
        previous = level
    return awards


def view(game, player=None):
    """Produce a public table plus the authenticated viewer's own hole cards."""
    seats = game["players"]
    last = game["lastResult"]
    return {
        "turn": game["turn"], "phase": game["phase"], "actions": _actions(game, player),
        "board": game["board"].copy(), "pot": sum(seat["posted"] for seat in seats),
        "players": [
            {
                **{key: seat[key] for key in ("id", "stack", "bet", "posted", "status")},
                "cards": seat["cards"].copy() if seat["id"] == player else [None] * len(seat["cards"]),
            }
            for seat in seats
        ],
        "hand": game["hand"], "maxHands": game["maxHands"],
        "button": game["button"], "bigBlind": game["bigBlind"],
        "blinds": {"small": SMALL_BLIND, "big": BIG_BLIND},
        "lastResult": {
            "reason": last["reason"], "pot": last["pot"],
            "payouts": [payout.copy() for payout in last["payouts"]],
            "refunds": [refund.copy() for refund in last["refunds"]],
        } if last else None,
        "rankings": [
            {"id": seat["id"], "stack": seat["stack"]}
            for seat in sorted(seats, key=lambda seat: seat["stack"], reverse=True)
        ],
        "finished": game["finished"], "result": game["result"],
    }
