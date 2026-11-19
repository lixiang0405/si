import json
import unittest
from collections import Counter

import kingdoms as game_rules
import tournament
from card_catalog import CHENGTIAN_MANIFEST
from test_kingdoms import all_cards, seat


def move_to_hand(game, player, card):
    for owner in game["seats"]:
        if card in owner["hand"]:
            owner["hand"].remove(card)
            break
    else:
        game["deck"].remove(card)
    seat(game, player)["hand"].append(card)


def normal_setup():
    game = game_rules.create(["a", "b"], tournament.CHENG)
    for action, selections in (("select_commander", ["TY01-043", "TY01-072"]), ("select_alliance", ["single", "single"])):
        for value in selections:
            game_rules.play(game, game_rules._turn(game), action, value)
    for _ in range(2):
        player = game_rules._turn(game)
        game_rules.play(game, player, "ct_fill", True)
        game_rules.play(game, player, "confirm_roster", True)
    player = game_rules._turn(game)
    for side in game["seats"]:
        game["deck"].extend(side["hand"])
        side["hand"].clear()
    return game, player, "b" if player == "a" else "a"


class ChengTianPrintedCards(unittest.TestCase):
    def test_physical_manifest_matches_publisher_public_deck(self):
        self.assertEqual(len(CHENGTIAN_MANIFEST), 104)
        self.assertEqual(set(Counter((card["suit"], card["rank"]) for card in CHENGTIAN_MANIFEST.values()).values()), {2})
        kinds = Counter(card["kind"] for card in CHENGTIAN_MANIFEST.values())
        self.assertEqual(kinds["rotate_general"], 2)
        self.assertEqual(kinds["indulgence"], 3)
        self.assertEqual([(row["suit"], row["rank"]) for row in CHENGTIAN_MANIFEST.values() if row["kind"] == "rotate_general"],
                         [("红桃", 5), ("红桃", 10)])

    def test_switch_moves_the_replaced_unit_to_bottom_and_self_draws(self):
        game, user, opponent = normal_setup()
        entries = game["tournament"]["entries"]
        before = entries[user]["field"][0]
        from_top = entries[user]["army"][-1]
        card = "rotate_general@H.5.chengtian.1"
        move_to_hand(game, user, card)
        harmless_draw = "strike@C.3.chengtian.1"
        game["deck"].remove(harmless_draw)
        game["deck"].append(harmless_draw)
        game_rules.play(game, user, "rotate_general", {"card": card, "target": user, "unit": before["id"]})
        self.assertEqual(entries[user]["army"][0], before["general"])
        self.assertEqual(entries[user]["field"][0]["general"], from_top)
        self.assertEqual(len(seat(game, user)["hand"]), 1)
        card = "rotate_general@H.10.chengtian.1"
        move_to_hand(game, user, card)
        target = entries[opponent]["field"][0]
        game_rules.play(game, user, "rotate_general", {"card": card, "target": opponent, "unit": target["id"]})
        self.assertEqual(entries[opponent]["army"][0], target["general"])
        self.assertEqual(len(seat(game, user)["hand"]), 1)
        self.assertEqual(all_cards(game), Counter(tournament.DECK))

    def test_indulgence_blocks_only_a_unit_and_stays_after_black_judgement(self):
        game, user, opponent = normal_setup()
        original = game["tournament"]["entries"][opponent]["field"][0]
        card = "indulgence@H.6.chengtian.1"
        move_to_hand(game, user, card)
        game_rules.play(game, user, "trick", {"card": card, "target": opponent, "unit": original["id"]})
        self.assertEqual(game["tournament"]["unit_locks"][card], original["id"])
        black = next(item for item in game["deck"] if game_rules._suit(item) == "梅花")
        game["deck"].remove(black)
        game["deck"].append(black)
        game_rules.play(game, user, "end")
        self.assertEqual(game["phase"], "play")
        self.assertIn(card, seat(game, opponent)["judgement"])
        self.assertEqual(game_rules._turn(game), opponent)
        game = json.loads(json.dumps(game))
        red = next(item for item in game["deck"] if game_rules._suit(item) == "红桃")
        game["deck"].remove(red)
        game["deck"].append(red)
        game_rules._begin_turn(game)
        while game["phase"] == "counter":
            game_rules.play(game, game_rules._turn(game), "pass")
        self.assertNotIn(card, seat(game, opponent)["judgement"])
        self.assertNotIn(card, game["tournament"]["unit_locks"])
        self.assertEqual(all_cards(game), Counter(tournament.DECK))

    def test_printed_cao_cao_recovers_black_attack_card_on_damage(self):
        game, _, _ = normal_setup()
        game["active"] = "b"
        black = "strike@C.3.chengtian.1"
        move_to_hand(game, "b", black)
        game_rules.play(game, "b", "attack", {"card": black, "target": "a"})
        game_rules.play(game, "a", "take_damage")
        option = next(item for item in game_rules.view(game, "a")["actions"] if item["id"] == "ct_use_skill")
        game_rules.play(game, "a", option["id"], option["value"])
        self.assertIn(black, seat(game, "a")["hand"])
        self.assertEqual(len(seat(game, "a")["hand"]), 3)
        self.assertEqual(game["tournament"]["entries"]["a"]["retired"], ["TY01-043"])
        self.assertEqual(all_cards(game), Counter(tournament.DECK))

    def test_liu_bei_does_not_reveal_army_contents_until_skill_activation(self):
        game, _, _ = normal_setup()
        game["active"] = "b"
        entry = game["tournament"]["entries"]["b"]
        entry["army"][:] = ["TY01-072"] * 39
        self.assertFalse(game_rules.view(game, "a")["actions"])
        skill = next(option for option in game_rules.view(game, "b")["actions"] if option["id"] == "ct_use_skill")
        game_rules.play(game, "b", skill["id"], skill["value"])
        self.assertEqual(game["phase"], "ct_skill_choice")
        choice, = game_rules.view(game, "b")["actions"]
        game_rules.play(game, "b", choice["id"], choice.get("value"))
        self.assertEqual((len(entry["field"]), len(entry["army"]), len(entry["retired"])), (0, 39, 1))
