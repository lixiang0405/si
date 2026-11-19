import json
import random
import unittest
from unittest.mock import patch

import holdem


class HoldemTest(unittest.TestCase):
    def test_rank_and_kickers(self):
        def hand(cards):
            return holdem.evaluate(cards.split())

        self.assertEqual(hand("AS 2D 3H 4C 5S TD JS"), (4, 5))
        self.assertGreater(hand("2S 3D 4H 5C 6S TD JS"), hand("AS 2D 3H 4C 5S TD JS"))
        self.assertEqual(hand("AS KS QS JS TS TD JC"), (8, 14))
        self.assertEqual(hand("AS AC AD KS KC KD 7S"), (6, 14, 13))
        self.assertGreater(hand("AS AC KH QH TD 2D 3C"), hand("AD AH KS QS 9D 2H 3S"))

    def test_short_raises_and_reopening(self):
        game = holdem.create(["a", "b", "c", "d"])
        game["players"][1]["stack"] = 65  # 75 total, including the small blind.
        for player, action, value in [
            ("d", "call", None), ("a", "raise", 60), ("b", "all_in", None),
            ("c", "call", None), ("d", "call", None),
        ]:
            holdem.play(game, player, action, value)
        self.assertEqual(game["turn"], "a")
        self.assertEqual([action["id"] for action in holdem.view(game, "a")["actions"]], ["fold", "call"])

        game = holdem.create(["a", "b", "c", "d"])
        game["players"][1]["stack"] = 65
        game["players"][2]["stack"] = 80  # 100 total, including the big blind.
        for player, action, value in [
            ("d", "call", None), ("a", "raise", 60), ("b", "all_in", None),
            ("c", "all_in", None), ("d", "call", None),
        ]:
            holdem.play(game, player, action, value)
        raise_option = next(action for action in holdem.view(game, "a")["actions"] if action["id"] == "raise")
        self.assertEqual(raise_option["min"], 140)

    def test_side_pots_with_folded_chips_and_uncalled_refund(self):
        def seat(player, cards, posted, status):
            return {"id": player, "stack": 500 - posted, "bet": posted,
                    "posted": posted, "cards": cards.split(), "status": status}

        game = {
            "players": [
                seat("a", "KS KH", 100, "all_in"), seat("b", "9C 9H", 300, "all_in"),
                seat("c", "7D 7S", 500, "all_in"), seat("d", "AS AD", 200, "folded"),
            ],
            "board": "2C 3D 7H 9S KC".split(), "button": "a",
            "hand": 1, "maxHands": 5, "refunds": [],
        }
        holdem._refund_uncalled(game)
        holdem._settle(game, holdem._contenders(game))
        self.assertEqual(game["lastResult"], {
            "reason": "showdown", "pot": 900,
            "payouts": [{"id": "a", "amount": 400, "hand": "三条"}, {"id": "b", "amount": 500, "hand": "三条"}],
            "refunds": [{"id": "c", "amount": 200}],
        })
        self.assertEqual(sum(seat["stack"] for seat in game["players"]), 2000)

    def test_odd_chip_goes_to_first_winner_after_button(self):
        game = {
            "players": [{"id": player, "posted": 3} for player in ("a", "b", "c")],
            "button": "a",
        }
        self.assertEqual(holdem._award_pots(game, {"a": (4, 14), "b": (4, 14)}), {"a": 4, "b": 5})

    def test_legal_actions_privacy_and_round_trip(self):
        game = holdem.create(["a", "b"])
        self.assertEqual(game["turn"], "a")
        old_game = json.dumps(game)
        with self.assertRaises(ValueError):
            holdem.play(game, "b", "call")
        with self.assertRaises(ValueError):
            holdem.play(game, "a", "raise", True)
        self.assertEqual(json.dumps(game), old_game)

        rng = random.Random(81)
        with patch.object(holdem, "RNG", rng):
            for count in range(2, 9):
                players = [str(index) for index in range(count)]
                game = holdem.create(players)
                while not game["finished"]:
                    player = game["turn"] or next(seat["id"] for seat in game["players"] if seat["stack"])
                    public = holdem.view(game, player)
                    for seat in public["players"]:
                        if seat["id"] != player:
                            self.assertTrue(all(card is None for card in seat["cards"]))
                    self.assertNotIn("deck", public)
                    self.assertEqual(sum(seat["stack"] + seat["posted"] for seat in game["players"]), count * 1000)
                    option = rng.choice(public["actions"])
                    value = rng.randint(option["min"], option["max"]) if option["id"] == "raise" else None
                    game = json.loads(json.dumps(game))
                    holdem.play(game, player, option["id"], value)


if __name__ == "__main__":
    unittest.main()
