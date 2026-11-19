import unittest
from collections import Counter

import kingdoms as k
from test_general_expansions import choose, equip_hand, fixture
from test_kingdoms import all_cards


def next_card(game, seat, kind, suit):
    card = equip_hand(game, seat, kind, suit)
    seat["hand"].remove(card)
    game["deck"].append(card)
    return card


def set_general(seat, general):
    seat.update(generals=[general], revealed=[general])


class PrintedForestMountainSkillsTest(unittest.TestCase):
    def assert_inventory(self, game):
        self.assertEqual(all_cards(game), Counter(k.DECK))

    def test_fangzhu_draw_and_flip(self):
        game = fixture("forest_cao_pi", "b")
        _, cao_pi, recipient, _ = game["seats"]
        cao_pi["hp"] = 3
        k._damage(game, {"actor": "a", "target": "b", "card": "strike", "slash": True})
        choose(game, "b", "general_fm_fangzhu", lambda option: option["value"] == "c")
        self.assertEqual(len(recipient["hand"]), 2)
        self.assertTrue(recipient["face_down"])
        self.assert_inventory(game)

    def test_haoshi_moves_half_the_hand_to_fewest(self):
        game = fixture("forest_lu_su")
        actor, other, recipient, fourth = game["seats"]
        for player in (actor, actor, other, fourth):
            equip_hand(game, player, "dodge")
        k._begin_turn(game)
        choose(game, "a", "general_fm_haoshi")
        self.assertEqual(len(actor["hand"]), 6)
        choose(game, "a", "general_fm_haoshi_target", lambda option: option["value"] == "c")
        for _ in range(3):
            choose(game, "a", "general_fm_haoshi_cards")
        self.assertEqual(game["phase"], "play")
        self.assertEqual([len(actor["hand"]), len(recipient["hand"])], [3, 3])
        self.assert_inventory(game)

    def test_zaiqi_gets_only_nonhearts_and_heals_red_hearts(self):
        game = fixture("forest_meng_huo")
        actor = game["seats"][0]
        actor["hp"] = 2
        diamond = next_card(game, actor, "dodge", "方片")
        heart = next_card(game, actor, "peach", "红桃")
        k._begin_turn(game)
        choose(game, "a", "general_fm_zaiqi")
        self.assertEqual(actor["hp"], 3)
        self.assertEqual(actor["hand"], [diamond])
        self.assertIn(heart, game["discard"])
        self.assert_inventory(game)

    def test_roulin_requires_two_dodges(self):
        game = fixture("forest_dong_zhuo")
        actor, defender, _, _ = game["seats"]
        set_general(defender, "zhen_ji")
        slash = equip_hand(game, actor, "strike")
        for _ in range(2):
            equip_hand(game, defender, "dodge")
        choose(game, "a", "attack", lambda option: option.get("card") == slash and option["target"] == "b")
        choose(game, "b", "dodge")
        self.assertEqual(game["phase"], "dodge")
        choose(game, "b", "dodge")
        self.assertEqual(game["phase"], "play")
        self.assertEqual(defender["hp"], defender["max_hp"])
        self.assert_inventory(game)

    def test_qiaobian_pays_and_moves_judgement_without_extra_draw(self):
        game = fixture("mountain_zhang_he")
        actor, other, receiver, _ = game["seats"]
        cost = equip_hand(game, actor, "dodge")
        delayed = equip_hand(game, other, "indulgence")
        other["hand"].remove(delayed)
        other["judgement"].append(delayed)
        k._begin_turn(game)
        choose(game, "a", "general_fm_skip")
        choose(game, "a", "general_fm_skip")
        choose(game, "a", "general_fm_qiaobian", lambda option: option.get("card") == cost)
        choose(game, "a", "general_fm_qiaobian_move", lambda option: option["value"]["to"] == "c")
        choose(game, "a", "general_fm_skip")
        self.assertEqual(receiver["judgement"], [delayed])
        self.assertEqual(game["active"], "b")
        self.assertEqual(len(actor["hand"]), 2)
        self.assert_inventory(game)

    def test_zaoxian_and_jixi_complete_with_physical_cards(self):
        game = fixture("mountain_deng_ai")
        actor, other, _, _ = game["seats"]
        fields = [equip_hand(game, actor, "strike", "梅花") for _ in range(3)]
        actor["general_cards"] = {"field": fields.copy()}
        actor["hand"].clear()
        reward = equip_hand(game, other, "peach")
        k._begin_turn(game)
        self.assertTrue(actor["general_marks"]["zaoxian"])
        self.assertEqual(actor["max_hp"], 3)
        choose(game, "a", "general_fm_jixi", lambda option: option["value"] == {"target": "b", "card": fields[0]})
        while game["phase"] == "counter":
            choose(game, k._turn(game), "pass")
        choose(game, "a", "select_asset", lambda option: option["value"] == "hand")
        self.assertIn(reward, actor["hand"])
        self.assertIn(fields[0], game["discard"])
        self.assert_inventory(game)

    def test_fangquan_normal_seat_resumes_after_extra_turn(self):
        game = fixture("mountain_liu_shan")
        actor = game["seats"][0]
        cost = equip_hand(game, actor, "strike")
        k._begin_turn(game)
        choose(game, "a", "general_fm_fangquan_skip")
        choose(game, "a", "general_fm_fangquan_grant", lambda option: option["value"] == {"card": cost, "target": "d"})
        self.assertEqual(game["active"], "d")
        choose(game, "d", "end")
        self.assertEqual(game["active"], "b")
        self.assert_inventory(game)

    def test_luanwu_losses_are_not_damage(self):
        game = fixture("forest_jia_xu")
        choose(game, "a", "general_fm_luanwu")
        for uid in "bcd":
            choose(game, uid, "general_fm_luanwu_lose")
        self.assertEqual(game["phase"], "play")
        self.assertEqual([seat["hp"] for seat in game["seats"]], [4, 3, 3, 3])
        self.assertFalse(any(option["id"] == "general_fm_luanwu" for option in k.view(game, "a")["actions"]))
        self.assert_inventory(game)

    def test_lieren_pindian_gains_picked_card_only_on_win(self):
        game = fixture("forest_zhu_rong")
        actor, recipient, _, _ = game["seats"]
        high = equip_hand(game, actor, "wine")
        low = equip_hand(game, recipient, "feast")
        reward = equip_hand(game, recipient, "dodge")
        k._damage(game, {"actor": "a", "target": "b", "card": "strike", "slash": True})
        choose(game, "a", "general_fm_lieren", lambda option: option["value"] == high)
        choose(game, "b", "general_fm_lieren_pindian", lambda option: option["value"] == low)
        choose(game, "a", "general_fm_lieren_claim")
        self.assertEqual(actor["hand"], [reward])
        self.assertTrue({high, low}.issubset(game["discard"]))
        self.assert_inventory(game)

    def test_guzheng_returns_one_and_takes_rest(self):
        game = fixture("mountain_zhang_brothers", "b")
        actor, holder, _, _ = game["seats"]
        held = [equip_hand(game, actor, "strike") for _ in range(6)]
        choose(game, "a", "end")
        for card in held[:2]:
            choose(game, "a", "discard", lambda option: option["value"] == card)
        choose(game, "b", "general_fm_guzheng", lambda option: option["value"] == held[0])
        self.assertIn(held[0], actor["hand"])
        self.assertIn(held[1], holder["hand"])
        self.assertEqual(game["active"], "b")
        self.assert_inventory(game)

    def test_zhiba_lets_lord_keep_both_on_challenger_loss(self):
        game = fixture("mountain_sun_ce")
        lord, challenger, _, _ = game["seats"]
        set_general(challenger, "sun_quan")
        game["active"] = "b"
        low = equip_hand(game, challenger, "feast")
        high = equip_hand(game, lord, "wine")
        choose(game, "b", "general_fm_zhiba")
        choose(game, "a", "general_fm_zhiba_pindian")
        choose(game, "a", "general_fm_zhiba_accept")
        self.assertCountEqual(lord["hand"], [high, low])
        self.assert_inventory(game)

    def test_songwei_waits_after_black_judgement_before_draw(self):
        game = fixture("forest_cao_pi")
        lord, judge, _, _ = game["seats"]
        set_general(judge, "forest_xu_huang")
        delayed = equip_hand(game, judge, "supply")
        judge["hand"].remove(delayed)
        judge["judgement"].append(delayed)
        seen = next_card(game, judge, "dodge", "方片")
        black = next_card(game, judge, "strike", "梅花")
        game["active"] = "b"
        k._begin_turn(game)
        choose(game, "b", "general_fm_songwei")
        self.assertEqual(lord["hand"], [seen])
        self.assertIn(black, game["discard"])
        self.assertIn(delayed, game["discard"])
        self.assertEqual(len(judge["hand"]), 2)
        self.assert_inventory(game)

    def test_juxiang_receives_real_invasion_after_other_targets(self):
        game = fixture("forest_zhu_rong", "b")
        actor, holder, _, _ = game["seats"]
        invasion = equip_hand(game, actor, "invasion")
        choose(game, "a", "trick", lambda option: option.get("card") == invasion)
        self.assertFalse(holder["hand"])
        choose(game, "c", "take_damage")
        choose(game, "d", "take_damage")
        self.assertEqual(holder["hand"], [invasion])
        self.assert_inventory(game)

    def test_ruoyu_unlocks_jijiang_with_shu_response(self):
        game = fixture("mountain_liu_shan")
        actor, target, helper, _ = game["seats"]
        set_general(helper, "zhao_yun")
        actor["hp"] = 3
        slash = equip_hand(game, helper, "strike")
        k._begin_turn(game)
        choose(game, "a", "general_fm_skip")
        self.assertEqual((actor["hp"], actor["max_hp"]), (4, 5))
        choose(game, "a", "general_fm_jijiang", lambda option: option["value"] == "b")
        choose(game, "c", "general_fm_jijiang_offer")
        choose(game, "b", "take_damage")
        self.assertEqual(target["hp"], 3)
        self.assertIn(slash, game["discard"])
        self.assert_inventory(game)

    def test_zhiji_can_guanxing_in_same_turn_without_leaking_cards(self):
        game = fixture("mountain_jiang_wei")
        actor = game["seats"][0]
        actor["hp"] = 2
        candidates = [next_card(game, actor, "strike", "梅花") for _ in range(4)]
        k._begin_turn(game)
        choose(game, "a", "general_fm_heal")
        choose(game, "a", "general_fm_guanxing")
        self.assertCountEqual(actor["general_cards"]["guanxing"], candidates)
        self.assertTrue(all(card not in str(k.view(game, "b")) for card in candidates))
        self.assert_inventory(game)
        choose(game, "a", "general_fm_guanxing_bottom", lambda option: option["value"] == candidates[-1])
        choose(game, "a", "general_fm_guanxing_top", lambda option: option["value"] == candidates[0])
        choose(game, "a", "general_fm_guanxing_done")
        self.assertEqual((actor["hp"], actor["max_hp"]), (3, 3))
        self.assertEqual(game["deck"][0], candidates[-1])
        self.assertEqual(actor["hand"], [candidates[0], candidates[-2]])
        self.assert_inventory(game)

    def test_beige_spends_one_and_turns_over_slash_source(self):
        game = fixture("mountain_cai_wenji", "c")
        actor, target, holder, _ = game["seats"]
        cost = equip_hand(game, holder, "dodge")
        judgement = next_card(game, target, "strike", "黑桃")
        k._damage(game, {"actor": "a", "target": "b", "card": "strike", "slash": True})
        choose(game, "c", "general_fm_beige", lambda option: option["value"] == cost)
        self.assertTrue(actor["face_down"])
        self.assertEqual(target["hp"], 3)
        self.assertTrue({cost, judgement}.issubset(game["discard"]))
        self.assert_inventory(game)

    def test_baonue_reward_is_to_the_lord_on_black_spade(self):
        game = fixture("forest_dong_zhuo")
        lord, donor, target, _ = game["seats"]
        set_general(donor, "forest_jia_xu")
        lord["hp"] = 3
        card = next_card(game, donor, "strike", "黑桃")
        k._damage(game, {"actor": "b", "target": "c", "card": "strike", "slash": True})
        self.assertEqual(target["hp"], 3)
        choose(game, "b", "general_fm_baonue")
        self.assertEqual(lord["hp"], 4)
        self.assertIn(card, game["discard"])
        self.assert_inventory(game)
