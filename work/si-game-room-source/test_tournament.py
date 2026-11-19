from collections import Counter
from copy import deepcopy
import json
from random import Random
import unittest

import tournament as rules


# The tournament engine accepts the ordinary general catalogue as data. This
# roster gives the tests exactly the four commanders and enough distinct allies.
GENERALS = {key: (key, faction, 4, "draw") for faction, key in rules.COMMANDERS.items()}
GENERALS.update({
    f"{faction}-{index}": (f"{faction}武将{index}", faction, 3, "strategy")
    for faction in rules.COMMANDERS for index in range(8)
})


class Table:
    def __init__(self, mode):
        count = rules.MODES[mode]["capacity"]
        self.game = {
            "mode": mode, "active": "p0", "phase": "play", "discard": [], "log": [],
            "seats": [{"id": f"p{i}", "alive": True, "hand": ["strike"] * 4} for i in range(count)],
            "deck": ["strike"] * 80, "finished": False,
        }
        rules.initialize(self.game, mode, Random(18), GENERALS)

    @staticmethod
    def begin(game):
        game["phase"] = "play"
        rules.begin_turn(game, next(seat for seat in game["seats"] if seat["id"] == game["active"]))

    @staticmethod
    def draw(game, seat, count):
        seat["hand"].extend(game["deck"][-count:])
        del game["deck"][-count:]

    @staticmethod
    def log(game, text, actor=None, target=None):
        game["log"].append((text, actor, target))

    @staticmethod
    def complete(game):
        game["phase"] = "play"

    @staticmethod
    def discard(game, seat):
        game["phase"] = "discard"

    @staticmethod
    def finish(game, text, winners):
        game.update(phase="finished", finished=True, result=text, winners=winners)

    @staticmethod
    def prepare_seat(game, seat):
        seat["general_marks"] = {"prepared": True}

    def seat(self, player=None):
        player = player if player is not None else rules.turn(self.game)
        return next(seat for seat in self.game["seats"] if seat["id"] == player)

    def step(self, verb=None, value=None, player=None):
        seat = self.seat(player)
        options = rules.actions(self.game, seat, GENERALS)
        if options is None:
            options = rules.extra_actions(self.game, seat, GENERALS)
        choice = next(option for option in options if not verb or option["id"] == verb
                      and (value is None or option["value"] == value))
        result = rules.play(self.game, seat, choice["id"], choice["value"], GENERALS, begin_turn=self.begin,
                            draw=self.draw, log=self.log, complete=self.complete, finish_end_play=self.discard,
                            prepare_seat=self.prepare_seat)
        assert result
        return choice["value"]

    def defeat(self, defeated, source):
        defeated["alive"] = False
        self.game["discard"].extend(defeated["hand"])
        defeated["hand"].clear()
        return rules.on_defeat(self.game, defeated, source, GENERALS, draw=self.draw,
                               log=self.log, complete=self.complete, finish=self.finish)


class TournamentRulesTests(unittest.TestCase):
    def test_blood_draft_privacy_unique_generals_and_captain_control(self):
        table = Table(rules.BLOOD)
        game = table.game
        all_offers = [key for entry in game["tournament"]["entries"].values() for key in entry["offers"]]
        self.assertEqual(len(set(all_offers)), 30)
        self.assertTrue(all(not seat["hand"] for seat in game["seats"]))
        chosen = table.step()
        other = table.seat()["id"]
        self.assertIsNone(rules.public(game, other, GENERALS)["your_active"])
        self.assertNotIn(chosen, str(rules.public(game, None, GENERALS)))
        for _ in range(17):
            table.step()
        self.assertEqual(game["phase"], "draft_reserve")
        warm = table.seat()
        self.assertEqual(warm["role"], "主帅")
        self.assertEqual(len(rules.actions(game, warm, GENERALS)), 6)
        reserve = table.step()
        self.assertIn(reserve, str(rules.public(game, warm["id"], GENERALS)["your_reserves"]))
        cold = next(seat for seat in game["seats"] if seat["team"] == rules.COLD)
        self.assertEqual(rules.public(game, cold["id"], GENERALS)["your_reserves"], [])
        for _ in range(5):
            table.step()
        self.assertEqual(game["phase"], "play")
        self.assertEqual(game["active"], warm["id"])
        self.assertTrue(all(len(seat["hand"]) == 4 for seat in game["seats"]))
        self.assertTrue(all(seat["general_marks"] == {"prepared": True} for seat in game["seats"]))

    def test_blood_war_reserves_restore_same_seat_and_four_enemy_kills_win(self):
        table = Table(rules.BLOOD)
        game = table.game
        for _ in range(24):
            table.step()
        victim = next(seat for seat in game["seats"] if seat["team"] == rules.COLD and seat["role"] == "主帅")
        same_side = next(seat for seat in game["seats"] if seat["team"] == rules.COLD and seat is not victim)
        attacker = next(seat for seat in game["seats"] if seat["team"] == rules.WARM and seat["role"] == "主帅")
        game["active"] = victim["id"]
        victim.update(chained=True, turned_over=True, face_down=True, skill_disabled=True,
                      general_marks={"awakened": True})
        starting = victim["generals"][0]
        table.defeat(victim, same_side)
        self.assertEqual(game["tournament"]["score"], {rules.WARM: 0, rules.COLD: 0})
        self.assertTrue(game["tournament_turn_ended"])
        self.assertFalse(victim["chained"] or victim["turned_over"])
        self.assertEqual(rules.turn(game), victim["id"])  # Defeated captain still chooses.
        table.game = json.loads(json.dumps(game))
        table.step("revive_general")
        game = table.game
        victim = table.seat(victim["id"])
        attacker = table.seat(attacker["id"])
        self.assertNotEqual(victim["generals"][0], starting)
        self.assertEqual(len(victim["hand"]), 4)
        self.assertEqual(victim["max_hp"], GENERALS[victim["generals"][0]][2] + 1)
        self.assertNotIn("face_down", victim)
        self.assertNotIn("skill_disabled", victim)
        self.assertEqual(victim["general_marks"], {"prepared": True})
        enemies = [seat for seat in game["seats"] if seat["team"] == rules.COLD]
        for victim in (enemies[0], enemies[0], enemies[0], enemies[1]):
            table.defeat(victim, attacker)
            if game["phase"] == "reinforce_reserve":
                table.step("revive_general")
        self.assertTrue(game["finished"])
        self.assertEqual(game["tournament"]["score"][rules.WARM], 4)
        self.assertEqual(len(game["winners"]), 3)

    def test_chengtian_construct_recruit_trim_and_rest(self):
        table = Table(rules.CHENG)
        game = table.game
        table.step("select_commander", "cao_cao")
        table.step("select_commander", "liu_bei")
        table.step("select_alliance", "single")
        table.step("select_alliance", "吴")
        entries = game["tournament"]["entries"]
        self.assertEqual(len(rules.DECK), 104)
        self.assertEqual(len(entries["p1"]["roster"]), 40)
        self.assertEqual(Counter(GENERALS[key][1] for key in entries["p1"]["roster"]), {"蜀": 30, "吴": 10})
        self.assertEqual(rules.public(game, None, GENERALS)["your_roster"], [])
        unchosen = table.step("remove_general")
        self.assertFalse(any(choice["id"] == "confirm_roster" for choice in rules.actions(game, table.seat(), GENERALS)))
        table.step("add_general", unchosen)
        table.step("confirm_roster")
        table.step("confirm_roster")
        self.assertEqual([seat["hp"] for seat in game["seats"]], [5, 4])
        seat = table.seat("p0")
        entries = game["tournament"]["entries"]
        own = entries["p0"]
        self.assertEqual(own["field"][0]["general"], "cao_cao")
        self.assertEqual(len(own["army"]), 39)
        table.step("rest_general", player="p0")
        self.assertEqual(len(seat["hand"]), 7)  # 两张技能收益，一张休整自身。
        self.assertEqual(len(own["retired"]), 1)
        for _ in range(3):
            rules.end_play(game, seat, GENERALS, table.log)
        self.assertEqual(len(own["field"]), 6)
        self.assertEqual(game["phase"], "ct_trim")
        table.step("trim_general")
        self.assertEqual((len(own["field"]), len(own["army"]), len(own["retired"])), (5, 33, 2))
        self.assertEqual(game["phase"], "discard")
        game["phase"] = "play"
        target = entries["p1"]
        old = deepcopy(target["field"])
        rules.swap_general(game, seat, table.seat("p1"), old[0]["id"], GENERALS, table.draw, table.log)
        self.assertNotEqual(target["field"], old)
        self.assertEqual(len(target["army"]), 39)
        self.assertEqual(target["army"][0], old[0]["general"])


if __name__ == "__main__":
    unittest.main()
