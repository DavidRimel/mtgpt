import json

import pytest

from mtgpt.goldfish.engine import (
    TRIGGER_CAP, IllegalAction, apply, available_mana, from_dict, legal_actions,
    new_game, prepare, to_dict,
)

from mtgpt.models import ResolvedDeck

from simdeck import (BEAR, COUNTERSPELL, DEMONIC_TUTOR, LLANOWAR, NEVER, RAMPANT_GROWTH,
                     SOL_RING, SWORDS, TEFERIS_PROTECTION, card, commander, deck, rigged)

PASS = {"pass": True}
TRINKET = card("Trinket", "Artifact", "", mana_cost="{1}")
BONESPLITTER = card("Bonesplitter", "Artifact — Equipment",
                    "Equipped creature gets +2/+0.\nEquip {1}", mana_cost="{1}")
BLOOD_ARTIST = card("Blood Artist", "Creature — Vampire", "", mana_cost="{1}{G}", power=0.0)
SEER = card("Viscera Seer", "Creature — Vampire Wizard", "", mana_cost="{G}", power=1.0)
TOKEN_MAKER = card("Token Maker", "Enchantment", "", mana_cost="{G}")
def names(s, zone):
    return sorted(s.cards[i].name for i in zone)


def test_sol_ring_on_turn_one():
    s = rigged(SOL_RING, hand=["Forest", "Sol Ring"])
    s = apply(s, {"play_land": "Forest"})
    assert {"cast": "Sol Ring"} in legal_actions(s)
    s = apply(s, {"cast": "Sol Ring"})
    assert available_mana(s) == 2


def test_one_land_per_turn():
    s = apply(rigged(hand=["Forest", "Forest"]), {"play_land": "Forest"})
    assert not any("play_land" in a for a in legal_actions(s))


def test_illegal_actions_are_refused():
    s = rigged(SOL_RING, hand=["Sol Ring"])
    with pytest.raises(IllegalAction) as err:
        apply(s, {"cast": "Sol Ring"})  # no mana
    assert PASS in err.value.legal
    with pytest.raises(IllegalAction):
        apply(s, {"cast": "Black Lotus"})


def test_apply_copies_unless_in_place():
    s = rigged(hand=["Forest"])
    after = apply(s, {"play_land": "Forest"})
    assert s.battlefield == [] and len(after.battlefield) == 1
    apply(s, {"play_land": "Forest"}, in_place=True)
    assert len(s.battlefield) == 1


def test_creature_mana_is_summoning_sick():
    s = rigged(LLANOWAR, hand=["Llanowar Elves"], lands_in_play=1)
    s = apply(s, {"cast": "Llanowar Elves"})
    assert available_mana(s) == 0
    s = apply(s, PASS)
    assert available_mana(s) == 2


def test_partly_used_source_floats_the_rest():
    s = rigged(TRINKET, SOL_RING, hand=["Trinket"], on_board=["Sol Ring"])
    s = apply(s, {"cast": "Trinket"})
    assert [sorted(c) for c in s.pool] == [["C"]]
    assert available_mana(s) == 1


def test_rampant_growth_fetches_a_tapped_basic():
    s = rigged(RAMPANT_GROWTH, hand=["Rampant Growth"], lands_in_play=2)
    library_before = len(s.library)
    s = apply(s, {"cast": "Rampant Growth"})
    lands = [p for p in s.battlefield if p.is_land]
    assert len(lands) == 3 and lands[-1].tapped
    assert len(s.library) == library_before - 1


def test_tutor_waits_for_a_choice():
    s = rigged(DEMONIC_TUTOR, SOL_RING, hand=["Demonic Tutor"], lands_in_play=2)
    s = apply(s, {"cast": "Demonic Tutor"})
    legal = legal_actions(s)
    assert PASS not in legal and {"tutor": "Sol Ring"} in legal
    s = apply(s, {"tutor": "Sol Ring"})
    assert "Sol Ring" in names(s, s.hand)


REMOVAL_EVERY_TURN = {"archetype": "custom", "thing": "commander", "win": NEVER,
                      "disruption": {"commander_removal": 1.0, "from_turn": 1}}
WIPE_EVERY_TURN = {"archetype": "custom", "thing": "commander", "win": NEVER,
                   "disruption": {"board_wipe": 1.0, "from_turn": 1}}


def test_removed_commander_costs_two_more():
    s = rigged(lands_in_play=5, commander_out=True, goal=REMOVAL_EVERY_TURN)
    s = apply(s, PASS)
    assert s.command_zone == [0] and s.tax == {0: 2}
    assert s.events[-1] == {"turn": 2, "kind": "commander_removal", "stopped": False, "by": None}
    assert {"cast": "Test Commander"} not in legal_actions(s)  # 5 mana, needs 6


def test_protection_in_hand_stops_removal():
    s = rigged(TEFERIS_PROTECTION, hand=["Teferi's Protection"], lands_in_play=5,
               commander_out=True, goal=REMOVAL_EVERY_TURN)
    s = apply(s, PASS)
    assert s.command_zone == [] and s.events[-1]["stopped"]
    assert "Teferi's Protection" in names(s, s.graveyard)


def test_wipe_spares_lands_and_rocks_and_needs_the_right_answer():
    s = rigged(SOL_RING, BEAR, SWORDS, hand=["Swords to Plowshares"], lands_in_play=3,
               on_board=["Sol Ring", "Grizzly Bears"], commander_out=True, goal=WIPE_EVERY_TURN)
    s = apply(s, PASS)
    assert sorted(p.name for p in s.battlefield) == ["Forest"] * 3 + ["Sol Ring"]
    assert s.tax == {0: 2}
    assert "Swords to Plowshares" in names(s, s.hand)  # removal cannot stop a wipe


def test_counterspell_stops_a_wipe():
    s = rigged(BEAR, COUNTERSPELL, hand=["Counterspell"], on_board=["Grizzly Bears"],
               goal=WIPE_EVERY_TURN)
    s = apply(s, PASS)
    assert "Grizzly Bears" in [p.name for p in s.battlefield]
    assert s.events[-1]["by"] == "Counterspell"


def test_death_trigger_drains_each_opponent():
    goal = {"archetype": "aristocrats", "win": NEVER, "engine": {
        "Blood Artist": {"on": "creature_dies", "drain": 1},
        "Viscera Seer": {"sac_outlet": True}}}
    s = rigged(BLOOD_ARTIST, SEER, BEAR, on_board=["Blood Artist", "Viscera Seer", "Grizzly Bears"],
               goal=goal)
    s = apply(s, {"sacrifice": "Grizzly Bears"})
    assert s.opponent_life_lost == 3


def test_trigger_loops_stop_at_the_cap():
    goal = {"archetype": "custom", "thing": "commander", "win": NEVER,
            "engine": {"Token Maker": {"on": "creature_etb", "tokens": 1}}}
    s = rigged(TOKEN_MAKER, BEAR, hand=["Grizzly Bears"], lands_in_play=2,
               on_board=["Token Maker"], goal=goal)
    s = apply(s, {"cast": "Grizzly Bears"})
    assert sum(1 for p in s.battlefield if p.name == "Token") == TRIGGER_CAP
    assert any("trigger cap" in line for line in s.log)


def test_unblocked_combat_and_commander_damage():
    s = rigged(BEAR, BONESPLITTER, on_board=["Grizzly Bears", "Bonesplitter"], commander_out=True)
    s = apply(s, PASS)
    assert s.opponent_life_lost == 2 + 4 + 2
    assert s.commander_damage == 6


def test_win_ends_the_game():
    goal = {"archetype": "custom", "thing": "commander", "win": {"cast": "Sol Ring"}}
    s = rigged(SOL_RING, hand=["Sol Ring"], lands_in_play=1, goal=goal)
    s = apply(apply(s, {"cast": "Sol Ring"}), PASS)
    assert s.over and s.checkpoints["win"] == 1 and s.win_by == "cast:Sol Ring"
    assert legal_actions(s) == []


def test_turn_cap_ends_the_game():
    s = rigged(turn_cap=2)
    s = apply(apply(s, PASS), PASS)
    assert s.over and s.turn == 2 and s.checkpoints["win"] is None


def test_all_land_hand_mulligans_to_five():
    s = new_game(prepare(deck(), {"archetype": "go_wide"}), seed=3)
    assert s.mulligans == 3 and len(s.hand) == 5


def test_interaction_is_recorded_while_the_thing_is_online():
    s = rigged(SWORDS, TEFERIS_PROTECTION, hand=["Swords to Plowshares", "Teferi's Protection"],
               commander_out=True)
    s = apply(s, PASS)
    assert s.thing_turns[0] == {"turn": 1, "removal": 1, "protection": 1, "counterspell": 0}
    assert s.checkpoints["thing"] == 1


def test_state_round_trips_through_json():
    s = rigged(SOL_RING, LLANOWAR, hand=["Forest", "Sol Ring"], goal=REMOVAL_EVERY_TURN)
    data = to_dict(s)
    restored = from_dict(json.loads(json.dumps(data)))
    assert to_dict(restored) == data
    for action in ({"play_land": "Forest"}, {"cast": "Sol Ring"}, PASS, PASS):
        s, restored = apply(s, action), apply(restored, action)
    assert to_dict(restored) == to_dict(s)


# --- Review focus: inputs real decks bring ----------------------------------


def test_fetch_with_no_basics_left_does_nothing():
    grove = card("Grove", "Land", "{T}: Add {G}.", produced_mana="G")
    d = ResolvedDeck(commanders=(commander(),), cards=((1, RAMPANT_GROWTH), (98, grove)))
    s = rigged(source=d, land="Grove", hand=["Rampant Growth"], lands_in_play=2)
    s = apply(s, {"cast": "Rampant Growth"})
    assert sum(1 for p in s.battlefield if p.is_land) == 2


def test_mdfc_played_as_land_makes_its_color():
    mdfc = card("Spell // Land", "Sorcery // Land", "Draw two cards.", mana_cost="{3}{G}",
                produced_mana="G")
    s = rigged(mdfc, hand=["Spell // Land"])
    s = apply(s, {"play_land": "Spell // Land"})
    assert available_mana(s) == 1
