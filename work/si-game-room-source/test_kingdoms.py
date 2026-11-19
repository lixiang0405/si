import json
import random
import unittest
from collections import Counter

import kingdoms


def scenario():
    game = kingdoms.create(["a", "b", "c", "d"], packs=[])
    for seat, role in zip(game["seats"], (kingdoms.LORD, kingdoms.REBEL, kingdoms.LOYALIST, kingdoms.SPY)):
        seat.update(role=role, hp=4, max_hp=4, hand=[], weapon=None, generals=[], revealed=[])
    game.update(active="a", deck=["strike"] * 10, discard=[], log=[])
    return game


def seat(game, key):
    return next(item for item in game["seats"] if item["id"] == key)


def options(game, player):
    return kingdoms.view(game, player)["actions"]


def pass_rescue(game):
    while game["phase"] == "rescue":
        kingdoms.play(game, kingdoms.view(game, None)["turn"], "pass")


def all_cards(game):
    cards = Counter(game["deck"] + game["discard"])
    cards.update(game.get("harvest_cards", []))
    if invasion := game.get("invasion_card"):
        cards[invasion["card"]] += 1
    for player in game["seats"]:
        cards.update(player["hand"])
        cards.update(player.get("judgement", []))
        for stack in player.get("general_cards", {}).values():
            cards.update(stack)
        for slot in set(kingdoms.SLOTS.values()):
            if player.get(slot):
                cards[player[slot]] += 1
    pending = game["pending"] or {}
    if game["phase"] == "counter" and (pending.get("judging")
                                        or kingdoms._kind(pending.get("card")) in kingdoms.TILTED):
        cards[pending["card"]] += 1
    cards.update(kingdoms.wind_fire.bluff_suspended_judgements(game))
    cards.update(kingdoms.wind_fire.judgment_held_cards(game))
    cards.update(kingdoms._held_divine_counters(game))
    cards.update(pending.get("cards", []))
    return cards


class IdentityGameTest(unittest.TestCase):
    def test_public_and_player_views_keep_cards_and_roles_private(self):
        game = json.loads(json.dumps(kingdoms.create(["a", "b", "c", "d", "e", "f"])))
        public = kingdoms.view(game, None)
        self.assertEqual(public["hand"], [])
        self.assertEqual(public["actions"], [])
        self.assertNotIn("deck", public)
        self.assertEqual([s["role"] for s in public["seats"] if s["role"]], [kingdoms.LORD])
        for player in game["seats"]:
            private = kingdoms.view(game, player["id"])
            self.assertEqual(private["your_role"], player["role"])
            self.assertEqual(sum(c["count"] for c in private["hand"]), len(player["hand"]))
            self.assertTrue(all("hand" not in s for s in private["seats"]))
            self.assertTrue(all(s["role"] is None for s in private["seats"]
                                if s["id"] not in (player["id"], game["active"])))

    def test_range_and_attack_limit_survive_dodge(self):
        game = scenario()
        seat(game, "a")["hand"] = ["blade", "strike", "strike"]
        seat(game, "c")["hand"] = ["dodge"]
        before = json.dumps(game)
        with self.assertRaises(ValueError):
            kingdoms.play(game, "a", "attack", "c")
        self.assertEqual(json.dumps(game), before)
        kingdoms.play(game, "a", "equip")
        kingdoms.play(game, "a", "attack", "c")
        self.assertEqual(kingdoms.view(game, None)["turn"], "c")
        with self.assertRaises(ValueError):
            kingdoms.play(game, "a", "end")
        kingdoms.play(game, "c", "dodge")
        self.assertEqual(seat(game, "c")["hp"], 4)
        if game["phase"] == "follow_up":
            kingdoms.play(game, "a", "stop_follow")
        self.assertFalse(any(a["id"] == "attack" for a in options(game, "a")))

    def test_rescue_and_exhausted_rescue_trigger_correct_result(self):
        game = scenario()
        seat(game, "a")["hand"] = ["strike"]
        seat(game, "b")["hp"] = 1
        seat(game, "c")["hand"] = ["peach"]
        kingdoms.play(game, "a", "attack", "b")
        kingdoms.play(game, "b", "take_damage")
        kingdoms.play(game, "b", "pass")
        kingdoms.play(game, "c", "rescue")
        self.assertEqual(seat(game, "b")["hp"], 1)
        self.assertEqual(kingdoms.view(game, None)["turn"], "a")
        game.update(active="b", strikes=0)
        seat(game, "a")["hp"] = 1
        seat(game, "b")["hand"] = ["strike"]
        kingdoms.play(game, "b", "attack", "a")
        kingdoms.play(game, "a", "take_damage")
        pass_rescue(game)
        self.assertTrue(game["finished"])
        self.assertEqual(game["winners"], ["b"])

    def test_lone_spy_and_lord_team_results(self):
        game = scenario()
        game["active"] = "d"
        seat(game, "d")["hand"] = ["strike"]
        seat(game, "a")["hp"] = 1
        seat(game, "b")["alive"] = False
        seat(game, "c")["alive"] = False
        kingdoms.play(game, "d", "attack", "a")
        kingdoms.play(game, "a", "take_damage")
        pass_rescue(game)
        self.assertEqual(game["winners"], ["d"])
        game = scenario()
        seat(game, "a")["hand"] = ["strike"]
        seat(game, "b")["alive"] = False
        seat(game, "d")["hp"] = 1
        kingdoms.play(game, "a", "attack", "d")
        kingdoms.play(game, "d", "take_damage")
        pass_rescue(game)
        self.assertEqual(game["winners"], ["a", "c"])

    def test_legacy_saved_game_keeps_running(self):
        game = scenario()
        for key in ("mode", "used_skills", "wine", "queue", "turn_limit"):
            del game[key]
        for player in game["seats"]:
            for key in ("generals", "revealed", "team", "judgement", "armor", "guard", "steed"):
                del player[key]
        seat(game, "b")["hand"] = ["dodge"]
        game.update(phase="dodge", pending={"actor": "a", "target": "b"})
        legacy = json.loads(json.dumps(game))
        self.assertEqual(kingdoms.view(legacy, None)["mode"], "identity")
        kingdoms.play(legacy, "b", "dodge")
        kingdoms.play(legacy, "a", "end")
        self.assertEqual(kingdoms.view(legacy, None)["turn"], "b")

    def test_classic_identity_roles(self):
        for size, loyalists, rebels in [(5, 1, 2), (6, 1, 3), (8, 2, 4)]:
            game = kingdoms.create([str(n) for n in range(size)])
            roles = Counter(s["role"] for s in game["seats"])
            self.assertEqual(roles, {kingdoms.LORD: 1, kingdoms.SPY: 1,
                                     kingdoms.LOYALIST: loyalists, kingdoms.REBEL: rebels})

    def test_counter_chain_and_delayed_trick_preserve_cards(self):
        for with_counter in (False, True):
            game = scenario()
            seat(game, "a")["hand"] = ["indulgence"]
            if with_counter:
                seat(game, "b")["hand"] = ["counterspell"]
                seat(game, "c")["hand"] = ["counterspell"]
            original = all_cards(game)
            kingdoms.play(game, "a", "trick", {"card": "indulgence", "target": "b"})
            self.assertEqual(all_cards(game), original)
            if with_counter:
                self.assertEqual(kingdoms.view(game, None)["turn"], "b")
                kingdoms.play(game, "b", "counterspell")
                self.assertEqual(kingdoms.view(game, None)["turn"], "c")
                kingdoms.play(game, "c", "counterspell")
                self.assertEqual(kingdoms.view(game, None)["turn"], "d")
                self.assertEqual([entry["id"] for entry in options(game, "d")], ["pass"])
                while game["phase"] == "counter":
                    kingdoms.play(game, kingdoms.view(game, None)["turn"], "pass")
            self.assertEqual(seat(game, "b")["judgement"], ["indulgence"])
            self.assertEqual(all_cards(game), original)
            kingdoms.play(game, "a", "end")
            self.assertEqual(seat(game, "b")["judgement"], [])
            self.assertEqual(all_cards(game), original)

    def test_eliminating_current_player_during_duel_advances_turn(self):
        game = scenario()
        game["active"] = "b"
        seat(game, "b").update(hp=1, hand=["duel"])
        seat(game, "c")["hand"] = ["strike"]
        kingdoms.play(game, "b", "trick", {"card": "duel", "target": "c"})
        kingdoms.play(game, "c", "respond_strike")
        kingdoms.play(game, "b", "take_damage")
        pass_rescue(game)
        self.assertFalse(seat(game, "b")["alive"])
        self.assertEqual(kingdoms.view(game, None)["turn"], "c")

    def test_team_victory_and_landlord_bidding(self):
        game = kingdoms.create(["a", "b", "c", "d"], "pair")
        self.assertEqual([x["team"] for x in kingdoms.view(game, None)["seats"]],
                         [kingdoms.WARM, kingdoms.COLD, kingdoms.COLD, kingdoms.WARM])
        game = kingdoms.create(["a", "b", "c", "d", "e", "f"], "team", packs=[])
        game["active"] = "b"
        seat(game, "b").update(hand=["strike"], weapon="spear", generals=[])
        seat(game, "e").update(hp=1, hand=[], generals=[])
        kingdoms.play(game, "b", "attack", "e")
        kingdoms.play(game, "e", "take_damage")
        pass_rescue(game)
        self.assertEqual(game["winners"], ["a", "b", "c"])
        game = kingdoms.create(["a", "b", "c"], "landlord", packs=[])
        bidder = kingdoms.view(game, None)["turn"]
        kingdoms.play(game, bidder, "bid", 3)
        self.assertEqual(kingdoms.view(game, bidder)["your_role"], kingdoms.LANDLORD)
        self.assertEqual(kingdoms.view(game, None)["turn"], bidder)

    def test_faction_generals_hidden_and_majority_becomes_independent(self):
        game = kingdoms.create(["a", "b", "c", "d"], "factions")
        for player, pair in zip(game["seats"], [("cao_cao", "cao_ren"), ("xun_yu", "guo_jia"),
                                                  ("xiahou_dun", "zhen_ji"), ("liu_bei", "zhao_yun")]):
            player.update(generals=list(pair), role=kingdoms.GENERALS[pair[0]][1], hand=[])
        self.assertTrue(all(not seat["generals"] and not seat["role"] for seat in kingdoms.view(game, None)["seats"]))
        self.assertEqual(len(kingdoms.view(game, "a")["your_generals"]), 2)
        for player in "abc":
            game["active"] = player
            kingdoms.play(game, player, "reveal", seat(game, player)["generals"][0])
        self.assertEqual(seat(game, "c")["role"], "野心家")
        self.assertEqual(seat(game, "c")["team"], "c")
        self.assertEqual(len(kingdoms.view(game, None)["seats"][0]["generals"]), 1)

    def test_simulated_games_preserve_cards_and_reach_settlement(self):
        chooser = random.Random(691)
        for mode, meta in kingdoms.MODES.items():
            counted = (Counter(kingdoms.tournament.DECK) if mode == kingdoms.tournament.CHENG else Counter(kingdoms.DECK))
            for number in {meta["minimum"], meta["capacity"]}:
                for simulation in range(12):
                    game = kingdoms.create([str(n) for n in range(number)], mode)
                    for step in range(4500):
                        active = kingdoms._turn(game)
                        if active is None:
                            break
                        choices = options(game, active)
                        self.assertTrue(choices, (mode, number, simulation, step, active, game["phase"],
                                                  game["pending"], game.get("general_state"), game["log"][-5:]))
                        confirmed = [item for item in choices if item["id"] in {"ct_fill", "confirm_roster"}]
                        action = chooser.choice(confirmed or choices)
                        kingdoms.play(game, active, action["id"], action.get("value"))
                        self.assertEqual(all_cards(game), counted, (mode, number, simulation, step, action))
                        if step % 29 == 0:
                            game = json.loads(json.dumps(game))
                    self.assertTrue(game["finished"], (mode, number, simulation))
                    self.assertTrue(game["result"])


if __name__ == "__main__":
    unittest.main()
