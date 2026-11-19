import copy
import unittest
from collections import Counter

import general_expansions as expansion
import kingdoms


def fixture(general, actor="a"):
    game = kingdoms.create(["a", "b", "c", "d"], packs=[])
    deck = list(kingdoms.DECK)
    for seat, role in zip(game["seats"], (kingdoms.LORD, kingdoms.REBEL, kingdoms.LOYALIST, kingdoms.SPY)):
        keys = [general] if seat["id"] == actor else []
        seat.update(role=role, hp=4, max_hp=4, hand=[], generals=keys, revealed=keys.copy(), judgement=[])
    game.update(active="a", deck=deck, discard=[], log=[], pending=None, queue=[], phase="play", turn_number=0)
    return game


def equip_hand(game, seat, kind, suit=None):
    card = next(card for card in game["deck"] if kingdoms._kind(card) == kind
                and (suit is None or kingdoms._suit(card) == suit))
    game["deck"].remove(card)
    seat["hand"].append(card)
    return card


def choose(game, player, action, check=lambda option: True):
    option = next(option for option in kingdoms.view(game, player)["actions"] if option["id"] == action and check(option))
    kingdoms.play(game, player, action, option.get("value"))


def inventory(game):
    cards = Counter(game["deck"] + game["discard"])
    for seat in game["seats"]:
        cards.update(seat["hand"] + seat["judgement"])
        for pile in seat.get("general_cards", {}).values():
            cards.update(pile)
    suspended = (game.get("general_state", {}).get("bluff") or {}).get("pending")
    for effect in (game.get("pending"), suspended):
        if effect and effect.get("judging"):
            cards[effect["card"]] += 1
    return cards


class ClassicExpansionsTest(unittest.TestCase):
    def test_advertised_roster_matches_executable_skill_catalog(self):
        self.assertEqual(Counter(general["pack"] for general in expansion.GENERAL_CATALOG.values()),
                         {"wind": 8, "fire": 8, "forest": 8, "mountain": 8, "divine": 8})
        self.assertTrue(all(set(general["skills"]) <= expansion.SKILLS.keys()
                            for general in expansion.GENERAL_CATALOG.values()))

    def test_zhang_he_steals_one_card_per_selected_opponent(self):
        game = fixture("mountain_zhang_he")
        actor, first, second, _ = game["seats"]
        cost = equip_hand(game, actor, "dodge")
        card_one = equip_hand(game, first, "strike")
        card_two = equip_hand(game, second, "peach")
        kingdoms._begin_turn(game)
        snapshot = copy.deepcopy(game)
        self.assertFalse(kingdoms.view(game, "c")["actions"])
        self.assertEqual(game, snapshot)
        choose(game, "a", "general_fm_skip")
        choose(game, "a", "general_fm_qiaobian", lambda option: len(option["value"]["targets"]) == 2)
        choose(game, "a", "general_fm_skip")
        self.assertEqual(game["phase"], "play")
        self.assertIn(cost, game["discard"])
        self.assertCountEqual(actor["hand"], [card_one, card_two])
        self.assertEqual(inventory(game), Counter(kingdoms.DECK))

    def test_yan_wen_replaces_draw_and_uses_opposite_color_duel(self):
        game = fixture("fire_yan_wen")
        actor = game["seats"][0]
        opposite = equip_hand(game, actor, "dodge", "方片")
        revealed = equip_hand(game, actor, "strike", "黑桃")
        actor["hand"].remove(revealed)
        game["deck"].append(revealed)
        kingdoms._begin_turn(game)
        choose(game, "a", "general_shuangxiong")
        self.assertCountEqual(actor["hand"], [opposite, revealed])
        choose(game, "a", "general_trick", lambda option: option["value"]["target"] == "b")
        self.assertIn(opposite, game["discard"])
        self.assertEqual(game["pending"]["card"], "duel")
        self.assertEqual(inventory(game), Counter(kingdoms.DECK))

    def test_zuo_ci_changes_to_a_playable_skill(self):
        game = fixture("mountain_zuo_ci")
        actor = game["seats"][0]
        expansion.initialize_seat(game, actor, kingdoms)
        self.assertEqual(len(actor["general_marks"]["avatars"]), 2)
        kingdoms._begin_turn(game)
        choose(game, "a", "general_huashen")
        selected = actor["general_marks"]["avatar_skill"]
        self.assertIn(selected, kingdoms._skills(game, actor))
        actor["skill_disabled"] = True
        self.assertNotIn(selected, kingdoms._skills(game, actor))

    def test_deity_zhuge_liang_keeps_private_stars_and_uses_weather(self):
        game = fixture("divine_zhuge_liang")
        actor, protected, exposed, _ = game["seats"]
        initial = equip_hand(game, actor, "dodge")
        for _ in range(3):
            equip_hand(game, actor, "strike")
        expansion.initialize_seat(game, actor, kingdoms)
        self.assertEqual(len(actor["general_cards"]["star"]), 7)
        kingdoms._begin_turn(game)
        for _ in range(7):
            choose(game, "a", "divine_stars_setup", lambda option: option["value"] != initial)
        choose(game, "a", "divine_stars_swap", lambda option: option["value"]["card"] == initial)
        choose(game, "a", "divine_stars_done")
        self.assertIn(initial, actor["general_cards"]["star"])
        choose(game, "a", "end")
        while game["phase"] == "discard":
            choose(game, "a", "discard")
        choose(game, "a", "general_star_mark",
               lambda option: option["value"]["mark"] == "fog_source" and option["value"]["target"] == "b")
        choose(game, "a", "general_star_mark",
               lambda option: option["value"]["mark"] == "wind_source" and option["value"]["target"] == "c")
        choose(game, "a", "general_decline")
        self.assertEqual(len(actor["general_cards"]["star"]), 5)
        self.assertEqual(protected["general_marks"]["fog_source"], "a")
        self.assertEqual(exposed["general_marks"]["wind_source"], "a")
        fire = {"actor": "a", "target": "b", "card": "fire_strike", "nature": "fire"}
        self.assertEqual(expansion.alter_damage(game, fire, 1, kingdoms), 0)
        self.assertEqual(expansion.alter_damage(game, {**fire, "nature": "thunder"}, 1, kingdoms), 1)
        self.assertEqual(expansion.alter_damage(game, {**fire, "target": "c"}, 1, kingdoms), 2)
        expansion.start_turn(game, actor, kingdoms)
        self.assertFalse(protected["general_marks"])
        self.assertEqual(inventory(game), Counter(kingdoms.DECK))

    def test_deng_ai_uses_physical_judgement_as_field(self):
        game = fixture("mountain_deng_ai", "b")
        actor = game["seats"][1]
        dodge = equip_hand(game, actor, "dodge")
        field = equip_hand(game, actor, "strike", "黑桃")
        actor["hand"].remove(field)
        game["deck"].append(field)
        kingdoms._spend(game, actor, dodge)
        self.assertIn(dodge, game["discard"])
        self.assertEqual(actor["general_cards"]["field"], [field])
        self.assertEqual(inventory(game), Counter(kingdoms.DECK))

    def test_yu_ji_unchallenged_claim_stays_secret_and_resolves(self):
        game = fixture("wind_yu_ji")
        actor = game["seats"][0]
        actor["hp"] = 3
        material = equip_hand(game, actor, "dodge")
        choose(game, "a", "general_guhuo")
        choose(game, "a", "general_guhuo_declare", lambda option: option["value"]["kind"] == "peach")
        self.assertIn(material, actor["general_cards"]["bluff"])
        self.assertNotIn(material, str(kingdoms.view(game, "b")))
        self.assertEqual(inventory(game), Counter(kingdoms.DECK))
        for player in ("b", "c", "d"):
            choose(game, player, "general_guhuo_pass")
        self.assertEqual(actor["hp"], 4)
        self.assertTrue(all("亮牌" not in row["text"] for row in game["log"]))
        self.assertEqual(inventory(game), Counter(kingdoms.DECK))

    def test_yu_ji_challenged_fake_draws_for_each_skeptic_and_cancels(self):
        game = fixture("wind_yu_ji")
        actor, first, second, _ = game["seats"]
        material = equip_hand(game, actor, "peach")
        choose(game, "a", "general_guhuo")
        choose(game, "a", "general_guhuo_declare", lambda option: option["value"] == {"kind": "insight"})
        choose(game, "b", "general_guhuo_doubt")
        choose(game, "c", "general_guhuo_doubt")
        choose(game, "d", "general_guhuo_pass")
        self.assertIn(material, game["discard"])
        self.assertEqual((len(actor["hand"]), len(first["hand"]), len(second["hand"])), (0, 1, 1))
        self.assertTrue(any("亮牌" in row["text"] and "假牌" in row["text"] for row in game["log"]))
        self.assertEqual(inventory(game), Counter(kingdoms.DECK))

    def test_yu_ji_true_red_claim_waits_for_dying_skeptic(self):
        game = fixture("wind_yu_ji")
        actor, skeptic, target, _ = game["seats"]
        skeptic["hp"] = 1
        material = equip_hand(game, actor, "peach", "红桃")
        actor["hp"] = 3
        rescue = equip_hand(game, target, "peach")
        choose(game, "a", "general_guhuo")
        choose(game, "a", "general_guhuo_declare", lambda option: option["value"]["kind"] == "peach")
        choose(game, "b", "general_guhuo_doubt")
        choose(game, "c", "general_guhuo_pass")
        choose(game, "d", "general_guhuo_pass")
        self.assertEqual((game["phase"], game["pending"]["target"]), ("rescue", "b"))
        self.assertEqual(actor["hp"], 3)
        choose(game, "b", "pass")
        choose(game, "c", "rescue")
        self.assertEqual((game["phase"], actor["hp"], skeptic["hp"]), ("play", 4, 1))
        self.assertTrue({material, rescue} <= set(game["discard"]))
        self.assertEqual(inventory(game), Counter(kingdoms.DECK))

    def test_yu_ji_can_declare_a_dodge_during_another_players_turn(self):
        game = fixture("wind_yu_ji", "b")
        attacker, actor, _, _ = game["seats"]
        equip_hand(game, attacker, "strike")
        material = equip_hand(game, actor, "peach")
        choose(game, "a", "attack", lambda option: option.get("target") == "b")
        choose(game, "b", "general_guhuo_response")
        for player in ("c", "d", "a"):
            choose(game, player, "general_guhuo_pass")
        self.assertEqual((game["phase"], game["active"], actor["hp"]), ("play", "a", 4))
        self.assertIn(material, game["discard"])
        self.assertEqual(inventory(game), Counter(kingdoms.DECK))

    def test_yu_ji_can_counter_a_physical_delayed_trick_without_exposing_the_cost(self):
        game = fixture("wind_yu_ji", "b")
        target, actor, _, _ = game["seats"]
        delayed = equip_hand(game, target, "indulgence")
        target["hand"].remove(delayed)
        target["judgement"].append(delayed)
        cost = equip_hand(game, actor, "strike")
        kingdoms._begin_turn(game)
        self.assertEqual(game["phase"], "counter")
        choose(game, "a", "pass")
        choose(game, "b", "general_guhuo_response")
        self.assertEqual(inventory(game), Counter(kingdoms.DECK))
        self.assertNotIn(cost, str(kingdoms.view(game, "c")))
        for player in ("c", "d", "a"):
            choose(game, player, "general_guhuo_pass")
        for player in ("c", "d", "a"):
            if game["phase"] == "counter":
                choose(game, player, "pass")
        self.assertEqual(game["phase"], "play")
        self.assertTrue({delayed, cost} <= set(game["discard"]))
        self.assertEqual(len(target["hand"]), 2)
        self.assertEqual(inventory(game), Counter(kingdoms.DECK))


if __name__ == "__main__":
    unittest.main()
