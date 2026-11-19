import json
import unittest
from collections import Counter

import kingdoms as game_engine
from test_general_expansions import choose, equip_hand, fixture
from test_kingdoms import all_cards as inventory


def damage(game, actor, target, amount=1):
    game_engine._damage(game, {"actor": actor, "target": target, "card": "strike", "damage": amount})


def pass_rescues(game):
    while game["phase"] == "rescue":
        choose(game, game_engine._turn(game), "pass")


class DivineExpansionTest(unittest.TestCase):
    def test_wuhun_judges_an_attacker_and_skips_nondamage_rescue(self):
        for kind, survives in [("peach", True), ("dodge", False)]:
            with self.subTest(judgement=kind):
                game = fixture("divine_guan_yu", "b")
                a, b, c, _ = game["seats"]
                b["hp"] = 2
                damage(game, "c", "b")
                self.assertEqual(c["general_marks"]["nightmare"], {"b": 1})
                judge = equip_hand(game, a, kind)
                a["hand"].remove(judge)
                game["deck"].append(judge)
                damage(game, "c", "b")
                pass_rescues(game)
                choose(game, "b", "divine_wuhun_target", lambda option: option["value"] == c["id"])
                self.assertFalse(b["alive"])
                self.assertEqual(c["alive"], survives)
                self.assertEqual(inventory(game), Counter(game_engine.DECK))

    def test_wushen_locks_heart_cards_into_range_free_slashes_and_private_faces(self):
        game = fixture("divine_guan_yu")
        actor, _, distant, _ = game["seats"]
        actor["hp"] = 2
        peach = equip_hand(game, actor, "peach", "红桃")
        trinket = equip_hand(game, actor, "zhuahuang")
        hand = {card["id"]: card for card in game_engine.view(game, "a")["hand"]}
        self.assertEqual(hand[peach]["kind"], "strike")
        self.assertEqual(hand[trinket]["type"], "basic")
        self.assertIn("武神", hand[peach]["description"])
        options = game_engine.view(game, "a")["actions"]
        self.assertFalse(any(option["id"] in {"equip_card", "heal"} for option in options))
        self.assertTrue(any(option["id"] == "attack" and option.get("card") == peach and option["label"] == "使用杀" for option in options))
        choose(game, "a", "general_attack", lambda option: option["value"]["target"] == "c" and option["value"]["card"] == peach)
        choose(game, "c", "take_damage")
        self.assertEqual(distant["hp"], 3)
        self.assertEqual(inventory(game), Counter(game_engine.DECK))

    def test_shelie_selects_from_public_cards_and_gongxin_reveals_only_to_source(self):
        game = fixture("divine_lu_meng")
        actor, other, *_ = game["seats"]
        visible = equip_hand(game, other, "peach", "红桃")
        secret = equip_hand(game, other, "strike", "黑桃")
        game_engine._begin_turn(game)
        choose(game, "a", "divine_shelie_use")
        while game.get("general_state", {}).get("choice", {}).get("label") == "divine_shelie_pick":
            choose(game, "a", "divine_shelie_pick")
        suits = [game_engine._suit(card) for card in actor["hand"]]
        self.assertEqual(len(suits), len(set(suits)))
        choose(game, "a", "divine_gongxin", lambda option: option["value"] == "b")
        self.assertFalse(game_engine.view(game, "c")["actions"])
        self.assertIn(secret, {option.get("value") for option in game_engine.view(game, "a")["actions"] if isinstance(option.get("value"), str)})
        choose(game, "a", "divine_gongxin_move", lambda option: option["value"] == {"card": visible, "where": "deck"})
        self.assertEqual(game["deck"][-1], visible)
        self.assertEqual(inventory(game), Counter(game_engine.DECK))

    def test_major_yeyan_pays_then_deals_two_and_one_and_locks_minor_mode(self):
        game = fixture("divine_zhou_yu")
        actor, b, c, _ = game["seats"]
        cards = [equip_hand(game, actor, "strike", suit) for suit in ("黑桃", "梅花", "红桃", "方片")]
        choose(game, "a", "divine_yeyan_major")
        choose(game, "a", "divine_yeyan_fire", lambda option: option["value"] == [["b", 2], ["c", 1]])
        self.assertEqual((actor["hp"], b["hp"], c["hp"]), (1, 2, 3))
        self.assertTrue(set(cards).issubset(game["discard"]))
        self.assertNotIn("general_yeyan", {option["id"] for option in game_engine.view(game, "a")["actions"]})
        self.assertEqual(inventory(game), Counter(game_engine.DECK))

    def test_qinyin_uses_hp_loss_and_resumes_rotation(self):
        game = fixture("divine_zhou_yu")
        actor, b, c, _ = game["seats"]
        actor["hp"] = 2
        for _ in range(4):
            equip_hand(game, actor, "strike")
        b["hp"] = 1
        c["generals"] = c["revealed"] = ["divine_lu_bu"]
        choose(game, "a", "end")
        choose(game, "a", "discard")
        choose(game, "a", "discard")
        choose(game, "a", "divine_qinyin_loss")
        self.assertEqual(game["pending"]["target"], "b")
        pass_rescues(game)
        self.assertEqual((actor["hp"], b["alive"], c["hp"], game["active"]), (1, False, 3, "c"))
        self.assertEqual(c.get("general_marks", {}).get("rage", 0), 0)
        self.assertEqual(inventory(game), Counter(game_engine.DECK))

    def test_guixin_two_damage_can_take_from_each_zone_and_flip_twice(self):
        game = fixture("divine_cao_cao", "b")
        a, owner, c, d = game["seats"]
        equip_hand(game, a, "strike")
        equip_hand(game, a, "strike")
        wrap = equip_hand(game, c, "rattan")
        c["hand"].remove(wrap)
        c["armor"] = wrap
        delay = equip_hand(game, d, "supply")
        d["hand"].remove(delay)
        d["judgement"].append(delay)
        damage(game, "a", "b", 2)
        choose(game, "b", "divine_guixin_use")
        choose(game, "b", "divine_guixin_pick", lambda option: option["value"] == "armor")
        choose(game, "b", "divine_guixin_pick", lambda option: option["value"] == {"index": 0})
        choose(game, "b", "divine_guixin_pick", lambda option: option["value"] == "hand")
        choose(game, "b", "divine_guixin_use")
        choose(game, "b", "divine_guixin_pick", lambda option: option["value"] == "hand")
        self.assertTrue({wrap, delay}.issubset(owner["hand"]))
        self.assertEqual(len(owner["hand"]), 4)
        self.assertFalse(owner["face_down"])
        self.assertEqual(game["phase"], "play")
        self.assertEqual(inventory(game), Counter(game_engine.DECK))

    def test_wumou_choice_and_wuqian_needs_two_dodges(self):
        game = fixture("divine_lu_bu")
        actor, target, *_ = game["seats"]
        actor["general_marks"] = {"rage": 4}
        equip_hand(game, actor, "insight")
        slash = equip_hand(game, actor, "strike")
        armor = equip_hand(game, target, "rattan")
        target["hand"].remove(armor)
        target["armor"] = armor
        for _ in range(2):
            equip_hand(game, target, "dodge")
        choose(game, "a", "insight")
        self.assertEqual(game["phase"], "general")
        choose(game, "a", "divine_wumou_rage")
        self.assertEqual(actor["general_marks"]["rage"], 3)
        choose(game, "a", "divine_wuqian", lambda option: option["value"] == "b")
        choose(game, "a", "attack", lambda option: option["value"] == {"card": slash, "target": "b"})
        choose(game, "b", "dodge")
        self.assertEqual(game["phase"], "dodge")
        choose(game, "b", "dodge")
        self.assertEqual(game["phase"], "play")
        self.assertEqual(target["hp"], 4)
        self.assertEqual(inventory(game), Counter(game_engine.DECK))

    def test_wumou_fatal_health_cost_still_resolves_previously_used_duel(self):
        game = fixture("divine_lu_bu", "b")
        a, actor, c, _ = game["seats"]
        game["active"], actor["hp"] = "b", 1
        actor["general_marks"] = {"rage": 2}
        card = equip_hand(game, actor, "duel")
        choose(game, "b", "divine_wuqian", lambda option: option["value"] == "c")
        choose(game, "b", "trick", lambda option: option["value"] == {"card": card, "target": "c"})
        self.assertIn(card, game["discard"])
        choose(game, "b", "divine_wumou_hp")
        pass_rescues(game)
        self.assertFalse(actor["alive"])
        self.assertEqual(game["phase"], "strike_response")
        self.assertTrue(game["pending"]["no_source"])
        choose(game, "c", "take_damage")
        self.assertEqual(c["hp"], 3)
        self.assertEqual(game["active"], "c")
        self.assertEqual(inventory(game), Counter(game_engine.DECK))

    def test_longhun_matches_health_and_spends_equipment_as_one_material(self):
        game = fixture("divine_zhao_yun")
        actor, target, *_ = game["seats"]
        actor["hp"] = actor["max_hp"] = 2
        slash = equip_hand(game, actor, "strike", "方片")
        armor = equip_hand(game, actor, "fan", "方片")
        actor["hand"].remove(armor)
        actor["weapon"] = armor
        choose(game, "a", "divine_longhun_attack", lambda option: option["value"]["targets"] == ["b"])
        self.assertEqual(game["pending"]["nature"], "fire")
        self.assertEqual(actor["general_marks"] if "general_marks" in actor else {}, {})
        choose(game, "b", "take_damage")
        self.assertIsNone(actor["weapon"])
        self.assertTrue({slash, armor}.issubset(game["discard"]))
        self.assertEqual(target["hp"], 3)
        self.assertEqual(inventory(game), Counter(game_engine.DECK))

    def test_shenfen_damage_then_equipment_then_each_own_discard_and_flip(self):
        game = fixture("divine_lu_bu")
        actor, b, c, d = game["seats"]
        actor["general_marks"] = {"rage": 6}
        for seat in (b, c, d):
            for _ in range(5):
                equip_hand(game, seat, "strike")
            armor = equip_hand(game, seat, "diagram" if seat is not d else "rattan")
            seat["hand"].remove(armor)
            seat["armor"] = armor
        choose(game, "a", "general_shenfen")
        self.assertEqual([b["hp"], c["hp"], d["hp"]], [3, 3, 3])
        self.assertFalse(any(seat["armor"] for seat in (b, c, d)))
        self.assertFalse(actor.get("face_down"))
        for player in ("b", "c", "d"):
            for _ in range(4):
                choose(game, player, "divine_shenfen_discard")
        self.assertTrue(actor["face_down"])
        self.assertEqual([len(seat["hand"]) for seat in (b, c, d)], [1, 1, 1])
        self.assertEqual(inventory(game), Counter(game_engine.DECK))

    def test_jilue_jizhi_before_trick_and_guicai_spends_another_patience(self):
        game = fixture("divine_sima_yi")
        actor, b, *_ = game["seats"]
        actor["general_marks"] = {"patience": 4, "awakened": 1}
        equip_hand(game, actor, "insight")
        replacement = equip_hand(game, actor, "dodge", "红桃")
        delayed = equip_hand(game, b, "indulgence")
        b["hand"].remove(delayed)
        b["judgement"].append(delayed)
        choose(game, "a", "insight")
        choose(game, "a", "divine_jizhi_use")
        self.assertEqual(len(actor["hand"]), 4)
        self.assertEqual(actor["general_marks"]["patience"], 3)
        game["active"] = "b"
        game_engine._begin_turn(game)
        options = game_engine.view(game, "a")["actions"]
        guicai = next(option for option in options if "鬼才" in option["label"] and game_engine._printed(replacement) in option["label"])
        game_engine.play(game, "a", guicai["id"], guicai.get("value"))
        self.assertEqual(actor["general_marks"]["patience"], 2)
        self.assertEqual(len(b["hand"]), 2)
        self.assertEqual(game["phase"], "play")
        self.assertEqual(inventory(game), Counter(game_engine.DECK))

    def test_jilue_wansha_fangzhu_and_lianpo_preserve_next_regular_seat(self):
        game = fixture("divine_sima_yi")
        actor, b, c, d = game["seats"]
        actor["general_marks"] = {"patience": 4, "awakened": 1}
        choose(game, "a", "divine_jilue_wansha")
        damage(game, "b", "a")
        choose(game, "a", "divine_fangzhu_flip", lambda option: option["value"] == "b")
        self.assertTrue(b["face_down"])
        self.assertEqual(len(b["hand"]), 1)
        b["hp"] = 1
        equip_hand(game, c, "peach")
        damage(game, "a", "b")
        choose(game, "b", "pass")
        self.assertNotIn("rescue", {option["id"] for option in game_engine.view(game, "c")["actions"]})
        pass_rescues(game)
        self.assertFalse(b["alive"])
        actor["hp"] = actor["max_hp"]
        choose(game, "a", "end")
        while game["phase"] == "discard":
            choose(game, "a", "discard")
        choose(game, "a", "divine_lianpo_use")
        game = json.loads(json.dumps(game))
        self.assertEqual(game["active"], "a")
        choose(game, "a", "end")
        while game["phase"] == "discard":
            choose(game, "a", "discard")
        self.assertEqual(game["active"], "c")
        self.assertEqual(inventory(game), Counter(game_engine.DECK))


if __name__ == "__main__":
    unittest.main()
