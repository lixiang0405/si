"""Printed Military Struggle deck and game-resolution regressions."""

import unittest

import kingdoms
from card_catalog import MANIFEST
from test_kingdoms import all_cards, options, scenario, seat


def printed(kind, suit, rank):
    return next(card for card, row in MANIFEST.items()
                if (row["pack"], row["kind"], row["suit"], row["rank"]) == ("army", kind, suit, rank))


class MilitaryDeckTests(unittest.TestCase):
    def test_each_suit_and_rank_has_the_expected_printed_card(self):
        # Each row lists A through K of the classic fifty-two-card expansion.
        by_suit = {
            "黑桃": (
                "guding", "rattan", "wine", "thunder_strike", "thunder_strike", "thunder_strike",
                "thunder_strike", "thunder_strike", "wine", "supply", "chain", "chain", "counterspell",
            ),
            "梅花": (
                "lion", "rattan", "wine", "supply", "thunder_strike", "thunder_strike",
                "thunder_strike", "thunder_strike", "wine", "chain", "chain", "chain", "chain",
            ),
            "红桃": (
                "counterspell", "fire_attack", "fire_attack", "fire_strike", "peach", "peach",
                "fire_strike", "dodge", "dodge", "fire_strike", "dodge", "dodge", "counterspell",
            ),
            "方片": (
                "fan", "peach", "peach", "fire_strike", "fire_strike", "dodge",
                "dodge", "dodge", "wine", "dodge", "dodge", "fire_attack", "hualiu",
            ),
        }
        expected = {(suit, rank, kind) for suit, kinds in by_suit.items() for rank, kind in enumerate(kinds, 1)}
        army = [row for row in MANIFEST.values() if row["pack"] == "army"]
        self.assertEqual(len(army), 52)
        self.assertEqual({(row["suit"], row["rank"], row["kind"]) for row in army}, expected)

    def test_fire_attack_requires_matching_suit_and_rattan_increases_damage(self):
        game = scenario()
        trick = printed("fire_attack", "红桃", 2)
        revealed = printed("dodge", "红桃", 8)
        match = printed("peach", "红桃", 5)
        wrong_suit = printed("peach", "方片", 3)
        armor = printed("rattan", "黑桃", 2)
        seat(game, "a")["hand"] = [trick, match, wrong_suit]
        seat(game, "b").update(hand=[revealed], armor=armor)
        initial = all_cards(game)

        kingdoms.play(game, "a", "trick", {"card": trick, "target": "b"})
        self.assertEqual((game["phase"], kingdoms.view(game, None)["turn"]), ("fire_reveal", "b"))
        self.assertNotIn("revealed", kingdoms.view(game, None)["event"])
        kingdoms.play(game, "b", "fire_reveal", revealed)
        self.assertIn("红桃8", kingdoms.view(game, None)["event"]["revealed"])
        self.assertEqual([option["value"] for option in options(game, "a") if option["id"] == "fire_pay"], [match])
        with self.assertRaises(ValueError):
            kingdoms.play(game, "a", "fire_pay", wrong_suit)
        kingdoms.play(game, "a", "fire_pay", match)

        self.assertEqual(seat(game, "b")["hp"], 2)
        self.assertEqual(seat(game, "b")["hand"], [revealed])
        self.assertEqual(seat(game, "b")["armor"], armor)
        self.assertEqual(seat(game, "a")["hand"], [wrong_suit])
        self.assertEqual(game["discard"], [trick, match])
        self.assertEqual(all_cards(game), initial)

    def test_wine_thunder_slash_propagates_and_silver_lion_limits_secondary_damage(self):
        game = scenario()
        chain = printed("chain", "梅花", 10)
        thunder_slash = printed("thunder_strike", "黑桃", 4)
        wine = printed("wine", "方片", 9)
        lion = printed("lion", "梅花", 1)
        seat(game, "a")["hand"] = [chain, thunder_slash, wine]
        seat(game, "c")["armor"] = lion
        initial = all_cards(game)

        kingdoms.play(game, "a", "trick", {"card": chain, "targets": ["b", "c"]})
        self.assertTrue(seat(game, "b")["chained"])
        self.assertTrue(seat(game, "c")["chained"])
        kingdoms.play(game, "a", "wine", wine)
        kingdoms.play(game, "a", "attack", {"card": thunder_slash, "target": "b"})
        kingdoms.play(game, "b", "take_damage")

        self.assertEqual([seat(game, player)["hp"] for player in ("a", "b", "c", "d")], [4, 2, 3, 4])
        self.assertFalse(seat(game, "b")["chained"])
        self.assertFalse(seat(game, "c")["chained"])
        self.assertEqual(seat(game, "c")["armor"], lion)
        self.assertEqual((game["phase"], game["queue"]), ("play", []))
        self.assertEqual(all_cards(game), initial)

    def test_nullification_cancels_only_the_current_chain_target(self):
        game = scenario()
        chain = printed("chain", "梅花", 10)
        nullification = printed("counterspell", "黑桃", 13)
        seat(game, "a")["hand"] = [chain]
        seat(game, "c")["hand"] = [nullification]
        initial = all_cards(game)

        kingdoms.play(game, "a", "trick", {"card": chain, "targets": ["b", "c"]})
        self.assertEqual(kingdoms.view(game, None)["event"]["target"], "b")
        self.assertFalse(seat(game, "b").get("chained", False))
        for actor in ("b", "c", "d", "a"):
            kingdoms.play(game, actor, "pass")
        self.assertTrue(seat(game, "b")["chained"])
        self.assertEqual(kingdoms.view(game, None)["event"]["target"], "c")
        kingdoms.play(game, "c", "counterspell", nullification)
        for actor in ("d", "a", "b"):
            kingdoms.play(game, actor, "pass")

        self.assertTrue(seat(game, "b")["chained"])
        self.assertFalse(seat(game, "c").get("chained", False))
        self.assertEqual((game["phase"], game["queue"]), ("play", []))
        self.assertEqual(game["discard"], [chain, nullification])
        self.assertEqual(all_cards(game), initial)

    def test_supply_shortage_judges_the_printed_top_card_before_draw(self):
        cases = [
            (printed("thunder_strike", "梅花", 5), "梅花5", True),
            (printed("thunder_strike", "黑桃", 5), "黑桃5", False),
        ]
        for top, result, should_draw in cases:
            with self.subTest(result=result):
                game = scenario()
                supply = printed("supply", "黑桃", 10)
                second = printed("peach", "红桃", 5)
                third = printed("dodge", "方片", 7)
                game["deck"] = [third, second, top]
                seat(game, "a")["hand"] = [supply]
                initial = all_cards(game)

                legal_targets = [option["target"] for option in options(game, "a") if option["id"] == "trick"]
                self.assertCountEqual(legal_targets, ["b", "d"])
                kingdoms.play(game, "a", "trick", {"card": supply, "target": "b"})
                self.assertEqual(seat(game, "b")["judgement"], [supply])
                self.assertEqual(game["discard"], [])
                kingdoms.play(game, "a", "end")

                self.assertEqual((game["phase"], game["active"]), ("play", "b"))
                self.assertEqual(seat(game, "b")["judgement"], [])
                self.assertEqual(seat(game, "b")["hand"], [second, third] if should_draw else [])
                self.assertEqual(game["deck"], [] if should_draw else [third, second])
                self.assertEqual(game["discard"], [top, supply])
                self.assertTrue(any(f"判定为{result}" in entry["text"] for entry in game["log"]))
                self.assertEqual(any("跳过摸牌阶段" == entry["text"] for entry in game["log"]), not should_draw)
                self.assertEqual(all_cards(game), initial)


if __name__ == "__main__":
    unittest.main()
