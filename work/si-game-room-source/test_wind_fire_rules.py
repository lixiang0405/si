"""Printed Wind and Fire skill regressions through normal game actions."""

import json
import unittest
from collections import Counter

import kingdoms
from test_general_expansions import choose, equip_hand, fixture
from test_kingdoms import all_cards


def move_to_top(game, card):
    game["deck"].remove(card)
    game["deck"].append(card)


CLASSIC_INVENTORY = Counter(kingdoms.DECK)


class WindFireTests(unittest.TestCase):
    def test_shensu_skips_both_phases_keeps_delayed_card_and_resolves_distant_slash(self):
        game = fixture("wind_xiahou_yuan")
        actor = kingdoms._seat(game, "a")
        delayed = equip_hand(game, actor, "indulgence")
        actor["judgement"].append(actor["hand"].pop())
        kingdoms._begin_turn(game)
        choose(game, "a", "general_wind_shensu", lambda option: option["value"] == "c")
        choose(game, "c", "take_damage")
        self.assertEqual((actor["judgement"], actor["hand"], kingdoms._seat(game, "c")["hp"]), ([delayed], [], 3))
        self.assertEqual((game["phase"], game["strikes"]), ("play", 0))
        self.assertEqual(all_cards(game), CLASSIC_INVENTORY)

    def test_tianxiang_transfers_full_damage_and_recipient_draws_by_missing_hp(self):
        game = fixture("wind_xiao_qiao", "b")
        attacker, xiao, target, _ = game["seats"]
        cost = equip_hand(game, xiao, "strike", "黑桃")
        equip_hand(game, attacker, "strike")
        equip_hand(game, attacker, "wine")
        choose(game, "a", "wine")
        choose(game, "a", "attack", lambda option: option["target"] == "b")
        choose(game, "b", "take_damage")
        choose(game, "b", "general_wind_tianxiang", lambda option: option["target"] == "c")
        self.assertEqual((xiao["hp"], target["hp"], len(target["hand"])), (4, 2, 2))
        self.assertIn(cost, game["discard"])
        self.assertEqual(all_cards(game), CLASSIC_INVENTORY)

    def test_tianxiang_compensates_wounded_recipient_even_when_fog_blocks_damage(self):
        game = fixture("wind_xiao_qiao", "b")
        attacker, xiao, target, _ = game["seats"]
        target.update(hp=2, general_marks={"fog_source": "d"})
        equip_hand(game, xiao, "peach", "红桃")
        equip_hand(game, attacker, "strike")
        choose(game, "a", "attack", lambda option: option["target"] == "b")
        choose(game, "b", "take_damage")
        choose(game, "b", "general_wind_tianxiang", lambda option: option["target"] == "c")
        self.assertEqual((xiao["hp"], target["hp"], len(target["hand"])), (4, 2, 2))
        self.assertEqual(game["phase"], "play")

    def test_guidao_can_change_leiji_into_damage(self):
        game = fixture("wind_zhang_jiao", "b")
        attacker, zhang, target, _ = game["seats"]
        equip_hand(game, attacker, "strike")
        equip_hand(game, zhang, "dodge")
        black = equip_hand(game, zhang, "strike", "黑桃")
        on_top = next(card for card in game["deck"] if kingdoms._suit(card) == "红桃")
        move_to_top(game, on_top)
        choose(game, "a", "attack", lambda option: option["target"] == "b")
        choose(game, "b", "dodge")
        choose(game, "b", "general_leiji", lambda option: option["value"] == "c")
        self.assertEqual(all_cards(game), CLASSIC_INVENTORY)
        choose(game, "b", "general_wind_guidao", lambda option: option["value"] == black)
        self.assertEqual((zhang["hp"], target["hp"], game["phase"]), (4, 2, "play"))
        self.assertIn(on_top, zhang["hand"])

    def test_guidao_replaces_actual_delayed_judgement_and_takes_revealed_card(self):
        game = fixture("wind_zhang_jiao", "b")
        active, zhang, _, _ = game["seats"]
        black = equip_hand(game, zhang, "strike", "梅花")
        delayed = equip_hand(game, active, "indulgence")
        active["judgement"].append(active["hand"].pop())
        on_top = next(card for card in game["deck"] if kingdoms._suit(card) == "红桃")
        move_to_top(game, on_top)
        kingdoms._begin_turn(game)
        self.assertEqual(all_cards(game), CLASSIC_INVENTORY)
        self.assertEqual(kingdoms.view(game, None)["turn"], "b")
        game = json.loads(json.dumps(game))
        choose(game, "b", "general_wind_guidao", lambda option: option["value"] == black)
        self.assertIn(on_top, kingdoms._seat(game, "b")["hand"])
        self.assertTrue({black, delayed} <= set(game["discard"]))
        self.assertTrue(any("跳过出牌阶段" in row["text"] for row in game["log"]))
        self.assertEqual(all_cards(game), CLASSIC_INVENTORY)

    def test_huangtian_accepts_only_one_transfer_from_a_qun_role_each_turn(self):
        game = fixture("wind_zhang_jiao")
        lord, donor, _, _ = game["seats"]
        donor["generals"] = ["wind_yu_ji"]
        gift = equip_hand(game, donor, "lightning")
        other = equip_hand(game, donor, "dodge")
        game["active"] = "b"
        choose(game, "b", "general_wind_huangtian", lambda option: option["value"] == gift)
        self.assertEqual((lord["hand"], donor["hand"]), ([gift], [other]))
        self.assertFalse(any(item["id"] == "general_wind_huangtian" for item in kingdoms.view(game, "b")["actions"]))

    def test_quhu_winner_selects_target_by_tigers_range_and_tiger_deals_damage(self):
        game = fixture("fire_xun_yu")
        xun, tiger, victim, far = game["seats"]
        xun["hp"] = 3
        high = equip_hand(game, xun, "lightning", "红桃")
        low = equip_hand(game, tiger, "dodge", "红桃")
        choose(game, "a", "general_wind_quhu", lambda option: option["target"] == "b")
        choose(game, "b", "general_wind_quhu_reveal")
        targets = {item["value"] for item in kingdoms.view(game, "a")["actions"]}
        self.assertEqual(targets, {"a", "c"})
        choose(game, "a", "general_wind_quhu_hit", lambda option: option["value"] == "c")
        self.assertTrue({high, low} <= set(game["discard"]))
        self.assertEqual((xun["hp"], tiger["hp"], victim["hp"], far["hp"]), (3, 4, 3, 4))
        self.assertEqual(game["phase"], "play")

    def test_tianyi_following_a_winning_pindian_adds_an_attack_target(self):
        game = fixture("fire_taishi_ci")
        actor, first, second, _ = game["seats"]
        high = equip_hand(game, actor, "lightning", "红桃")
        equip_hand(game, actor, "strike")
        equip_hand(game, first, "dodge", "红桃")
        choose(game, "a", "general_tianyi", lambda action: action["value"] == {"target": "b", "card": high})
        choose(game, "b", "general_pindian")
        choose(game, "a", "attack", lambda action: action["value"].get("targets") == ["b", "c"])
        choose(game, "b", "take_damage")
        choose(game, "c", "take_damage")
        self.assertEqual((first["hp"], second["hp"], game["strikes"]), (3, 3, 1))

    def test_nested_bluff_rescue_returns_to_the_original_bluff(self):
        game = fixture("wind_yu_ji")
        actor, skeptic, _, _ = game["seats"]
        skeptic["hp"] = 1
        actor["hp"] = 3
        outer_card = equip_hand(game, actor, "peach", "红桃")
        inner_card = equip_hand(game, actor, "dodge")
        choose(game, "a", "general_guhuo", lambda option: option["value"] == outer_card)
        choose(game, "a", "general_guhuo_declare", lambda option: option["value"]["kind"] == "peach")
        choose(game, "b", "general_guhuo_doubt")
        choose(game, "c", "general_guhuo_pass")
        choose(game, "d", "general_guhuo_pass")
        for player in ("b", "c", "d"):
            choose(game, player, "pass")
        choose(game, "a", "general_guhuo_response", lambda option: option["value"] == {"kind": "peach", "material": inner_card})
        self.assertEqual(set(actor["general_cards"]["bluff"]), {outer_card, inner_card})
        self.assertEqual(all_cards(game), CLASSIC_INVENTORY)
        game = json.loads(json.dumps(game))
        choose(game, "c", "general_guhuo_pass")
        choose(game, "d", "general_guhuo_pass")
        self.assertEqual(game["phase"], "play")
        self.assertEqual((kingdoms._seat(game, "a")["hp"], kingdoms._seat(game, "b")["hp"]), (4, 1))
        self.assertNotIn("bluff", game["general_state"])
        self.assertTrue({outer_card, inner_card} <= set(game["discard"]))
        self.assertEqual(all_cards(game), CLASSIC_INVENTORY)


if __name__ == "__main__":
    unittest.main()
