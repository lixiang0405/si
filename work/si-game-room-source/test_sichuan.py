from collections import Counter
import json
import random
import unittest

import sichuan


PLAYERS = ("A", "B", "C", "D")


def action_id(choice):
    return choice["id"]


def give(game, player, hand):
    game["seats"][player]["hand"] = hand.split()
    game["seats"][player]["missing"] = "s"


def configure_reactions():
    game = sichuan.create(PLAYERS)
    game.update(phase="discard", turn="A", drawn_tile="p9", wall=["s8", "p2"])
    give(game, "A", "p9")
    give(game, "B", "p1 p2 p3 p4 p5 p6 p7 p8 m1 m1 m4 m5 m6")
    give(game, "C", "p7 p8 p1 p1 p1 m2 m3 m4 m5 m6 m7 m8 m8")
    give(game, "D", "p9 p9 s1 s2 s3 s4 s5 s6 s7 m9 m9 m9 p6")
    return game


class SichuanTests(unittest.TestCase):
    def test_win_priority_and_two_winners(self):
        game = configure_reactions()
        sichuan.play(game, "A", "discard", "p9")
        self.assertEqual(sichuan.view(game)["waiting"], 3)
        sichuan.play(game, "D", "pong")
        self.assertNotIn("pong", set(map(action_id, sichuan.view(game, "D")["actions"])))
        sichuan.play(game, "C", "win")
        sichuan.play(game, "B", "win")
        self.assertEqual([winner["id"] for winner in sichuan.view(game)["winners"]], ["B", "C"])
        self.assertEqual(game["turn"], "D")
        self.assertEqual(game["seats"]["D"]["melds"], [])
        self.assertEqual(game["seats"]["A"]["points"], -2)

    def test_third_winner_finishes_from_sole_active_discarder(self):
        game = configure_reactions()
        game["seats"]["D"]["won"] = True
        game["winners"] = [{"id": "D"}]
        sichuan.play(game, "A", "discard", "p9")
        sichuan.play(game, "B", "win")
        sichuan.play(game, "C", "win")
        self.assertTrue(game["finished"])
        self.assertIn("三家已胡牌", game["result"])

    def test_rob_added_kong(self):
        game = configure_reactions()
        game["seats"]["A"]["melds"] = [{"kind": "pong", "tile": "p9"}]
        sichuan.play(game, "A", "added_kong", "p9")
        sichuan.play(game, "B", "win")
        sichuan.play(game, "C", "pass")
        self.assertEqual(game["winners"][0]["kind"], "rob_kong")
        self.assertEqual(game["seats"]["A"]["melds"][0]["kind"], "pong")
        self.assertNotIn("p9", game["seats"]["A"]["hand"])
        self.assertEqual(game["seats"]["A"]["points"], -1)

    def test_hidden_information_and_required_missing_suit(self):
        game = configure_reactions()
        give(game, "A", "m1 m1 m1 m1 s3")
        choices = sichuan.view(game, "A")["actions"]
        self.assertEqual(choices, [{"id": "discard", "label": "打 三条", "value": "s3"}])
        with self.assertRaises(ValueError):
            sichuan.play(game, "A", "concealed_kong", "m1")
        give(game, "A", "m1 m1 m1 m1 p3")
        sichuan.play(game, "A", "concealed_kong", "m1")
        self.assertEqual(sichuan.view(game, "B")["seats"][0]["melds"][0]["tile"], None)
        self.assertEqual(sichuan.view(game, "B")["drawn_tile"], None)
        self.assertEqual(sichuan.view(game, "A")["seats"][0]["melds"][0]["tile"], "m1")
        self.assertEqual(sichuan.view(game)["my_hand"], [])
        self.assertEqual(sichuan.view(game)["actions"], [])

    def test_complete_rooms_and_tile_conservation(self):
        rng = random.Random(2311)
        tile_totals = Counter({tile: 4 for tile in sichuan.TILES})
        for _ in range(30):
            game = sichuan.create(PLAYERS)
            for _ in range(350):
                if game["finished"]:
                    break
                available = [(who, x) for who in PLAYERS for x in sichuan.view(game, who)["actions"]]
                useful = [candidate for candidate in available if candidate[1]["id"] != "exchange_remove"]
                always = [candidate for candidate in useful
                          if candidate[1]["id"] in {"exchange_submit", "self_win", "win"}]
                player, chosen = rng.choice(always or useful)
                sichuan.play(game, player, chosen["id"], chosen.get("value"))
                total = Counter(game["wall"])
                for seat in game["seats"].values():
                    total.update(seat["hand"])
                    total.update(seat["discards"])
                    for meld in seat["melds"]:
                        total[meld["tile"]] += 3 if meld["kind"] == "pong" else 4
                self.assertEqual(total, tile_totals)
                self.assertEqual(sum(seat["points"] for seat in game["seats"].values()), 0)
            self.assertTrue(game["finished"])
            self.assertEqual(json.loads(json.dumps(game)), game)


if __name__ == "__main__":
    unittest.main()
