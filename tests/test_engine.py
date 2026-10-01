import json

import pytest

from mtgpt.goldfish.engine import (
    TRIGGER_CAP, IllegalAction, apply, available_mana, from_dict, held_counts, legal_actions,
    new_game, prepare, to_dict,
)

from mtgpt.models import ResolvedDeck

from simdeck import (BEAR, COUNTERSPELL, DEMONIC_TUTOR, LLANOWAR, NEVER, RAMPANT_GROWTH,
                     SOL_RING, SWORDS, TEFERIS_PROTECTION, card, commander, deck, forest,
                     rigged)

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


GREAVES = card("Lightning Greaves", "Artifact — Equipment",
               "Equipped creature has shroud and haste.\nEquip {0}", mana_cost="{2}")


def test_protection_on_the_battlefield_stops_removal_and_stays():
    s = rigged(GREAVES, on_board=["Lightning Greaves"], lands_in_play=5,
               commander_out=True, goal=REMOVAL_EVERY_TURN)
    s = apply(s, PASS)
    assert "Test Commander" in [p.name for p in s.battlefield]
    assert s.events[-1] == {"turn": 2, "kind": "commander_removal", "stopped": True,
                            "by": "Lightning Greaves"}
    assert "Lightning Greaves" in [p.name for p in s.battlefield]


def test_protection_on_the_battlefield_does_not_stop_a_wipe():
    s = rigged(GREAVES, on_board=["Lightning Greaves"], lands_in_play=5,
               commander_out=True, goal=WIPE_EVERY_TURN)
    s = apply(s, PASS)
    assert sorted(p.name for p in s.battlefield) == ["Forest"] * 5
    assert s.events[-1]["stopped"] is False


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


# --- Fix round 1 ------------------------------------------------------------

HALF_DISRUPTION = {"archetype": "custom", "thing": "commander", "win": NEVER,
                   "disruption": {"commander_removal": 0.5, "board_wipe": 0.5, "from_turn": 1}}


def test_matched_seeds_roll_matched_dice():
    a = new_game(prepare(deck(SOL_RING), HALF_DISRUPTION), seed=7)
    b = new_game(prepare(deck(*[BEAR] * 40), HALF_DISRUPTION), seed=7)
    assert a.mulligans != b.mulligans
    assert a.dice.getstate() == b.dice.getstate()
    assert [a.dice.random() for _ in range(6)] == [b.dice.random() for _ in range(6)]


def test_disruption_does_not_touch_the_shuffle_rng():
    s = rigged(goal=HALF_DISRUPTION)
    rng_before, dice_before = s.rng.getstate(), s.dice.getstate()
    s = apply(s, PASS)
    assert s.rng.getstate() == rng_before
    assert s.dice.getstate() != dice_before


def test_bottoming_keeps_mdfc_lands():
    from mtgpt.goldfish.engine import _bottom_one
    mdfc = card("Spell // Land", "Sorcery // Land", "Draw two cards.", mana_cost="{3}{G}",
                produced_mana="G")
    s = rigged(mdfc, hand=["Forest"] * 4 + ["Spell // Land"])
    _bottom_one(s)
    assert "Spell // Land" in names(s, s.hand)


def test_malformed_game_file_is_reported():
    from mtgpt.goldfish.engine import InvalidGameState
    with pytest.raises(InvalidGameState):
        from_dict({})


ARENA = card("Phyrexian Arena", "Enchantment",
             "At the beginning of your upkeep, you draw a card and you lose 1 life.",
             mana_cost="{1}{G}{G}")


def test_engine_override_replaces_the_parsed_draw():
    goal = {"archetype": "custom", "thing": "commander", "win": NEVER,
            "engine": {"Phyrexian Arena": {"on": "upkeep", "draw": 1}}}
    s = rigged(ARENA, on_board=["Phyrexian Arena"], goal=goal)
    before = len(s.hand)
    s = apply(s, PASS)
    assert len(s.hand) == before + 2  # the draw step plus the override, not the text too


def test_overridden_protection_is_not_counted_as_held():
    goal = {"archetype": "custom", "thing": "commander", "win": NEVER,
            "engine": {"Teferi's Protection": {"priority": "engine"}}}
    s = rigged(TEFERIS_PROTECTION, hand=["Teferi's Protection"], goal=goal)
    assert held_counts(s)["protection"] == 0


def test_a_tiny_deck_mulligans_without_crashing():
    tiny = ResolvedDeck(commanders=(commander(),), cards=((1, forest()),))
    s = new_game(prepare(tiny, {"archetype": "go_wide"}), seed=1)
    assert s.hand == [] and s.mulligans == 3


def test_goal_named_card_spends_as_engine():
    goal = {"archetype": "custom", "thing": "commander", "win": {"cast": "Trinket"}}
    s = rigged(TRINKET, hand=["Trinket"], lands_in_play=1, goal=goal)
    s = apply(s, {"cast": "Trinket"})
    assert s.spent_this_turn == {"ramp": 0, "engine": 1, "other": 0}


# --- Alternative costs and ongoing mana from engine overrides ---------------

JODAH_TEXT = "Flying\nYou may pay {W}{U}{B}{R}{G} rather than pay the mana cost for spells you cast."
OMNISCIENCE = card("Omniscience", "Enchantment", "You may cast spells from your hand "
                   "without paying their mana costs.", mana_cost="{7}{U}{U}{U}")
CHEAP = card("Cheap Trick", "Sorcery", "", mana_cost="{1}")
TENDER = card("Bloom Tender", "Creature — Elf Druid",
              "{T}: For each color among permanents you control, add one mana of that color.",
              mana_cost="{1}{G}", power=1.0)
ALL_COLORS = frozenset("WUBRG")


def any_land():
    return card("Prism Land", "Land", "{T}: Add one mana of any color.", produced_mana="WUBRG",
                identity="WUBRG", colors="")


def jodah_deck(*spells, lands=None):
    from mtgpt.models import ResolvedDeck
    jodah = card("Jodah", "Legendary Creature — Human Wizard", JODAH_TEXT,
                 mana_cost="{1}{U}{R}{W}", power=3.0, identity="WUBRG")
    lands = 99 - len(spells) if lands is None else lands
    return ResolvedDeck(commanders=(jodah,), cards=tuple((1, s) for s in spells) + ((lands, any_land()),))


ALT_GOAL = {"archetype": "custom", "thing": "commander", "win": NEVER,
            "engine": {"Jodah": {"alt_cost": "{W}{U}{B}{R}{G}"}}}


def test_alt_cost_applies_only_while_its_permanent_is_out():
    s = rigged(source=jodah_deck(OMNISCIENCE), land="Prism Land", lands_in_play=5,
               hand=["Omniscience"], goal=ALT_GOAL)
    assert {"cast": "Omniscience"} not in legal_actions(s)
    s = rigged(source=jodah_deck(OMNISCIENCE), land="Prism Land", lands_in_play=5,
               hand=["Omniscience"], commander_out=True, goal=ALT_GOAL)
    s = apply(s, {"cast": "Omniscience"})
    assert any(p.name == "Omniscience" for p in s.battlefield)
    assert available_mana(s) == 0  # paid exactly WUBRG


def test_the_cheaper_cost_is_paid():
    s = rigged(source=jodah_deck(CHEAP), land="Prism Land", lands_in_play=5,
               hand=["Cheap Trick"], commander_out=True, goal=ALT_GOAL)
    s = apply(s, {"cast": "Cheap Trick"})
    assert available_mana(s) == 4  # paid {1}, not WUBRG


def test_commander_tax_adds_to_the_alt_cost():
    # Fist-of-Suns-style: the alt cost comes from another permanent, the commander pays tax on it.
    fist = card("Fist of Suns", "Artifact", "You may pay {W}{U}{B}{R}{G} rather than pay the "
                "mana cost for spells you cast.", mana_cost="{3}")
    goal = {"archetype": "custom", "thing": "commander", "win": NEVER,
            "engine": {"Fist of Suns": {"alt_cost": "{W}{U}{B}{R}{G}"}}}
    s = rigged(source=jodah_deck(fist), land="Prism Land", lands_in_play=6,
               on_board=["Fist of Suns"], goal=goal)
    s.tax[0] = 2
    # Normal cost 4 + 2 tax = 6, alt 5 + 2 = 7: the normal cost is cheaper.
    s = apply(s, {"cast": "Jodah"})
    assert available_mana(s) == 0


def test_override_mana_taps_for_its_count_but_not_the_turn_it_enters():
    goal = {"archetype": "custom", "thing": "commander", "win": NEVER,
            "engine": {"Bloom Tender": {"mana": 3, "mana_colors": "WUBRG"}}}
    s = rigged(TENDER, hand=["Bloom Tender"], lands_in_play=2, goal=goal)
    s = apply(s, {"cast": "Bloom Tender"})
    assert available_mana(s) == 0
    s = apply(s, {"pass": True})
    assert available_mana(s) == 2 + 3
    tender = next(p for p in s.battlefield if p.name == "Bloom Tender")
    assert s.cards[tender.card].effect.mana_colors == ALL_COLORS


# --- Extra turns --------------------------------------------------------------

STRETCH = card("Time Stretch", "Sorcery", "Target player takes two extra turns after this one.",
               mana_cost="{1}")
NEXUS = card("Nexus of Fate", "Instant", "Take an extra turn after this one.\nIf Nexus of Fate "
             "would be put into a graveyard from anywhere, reveal Nexus of Fate and shuffle it "
             "into its owner's library instead.", mana_cost="{1}")


def test_extra_turns_keep_the_table_turn_number_and_are_full_turns():
    s = rigged(STRETCH, BEAR, hand=["Time Stretch", "Forest", "Forest", "Forest"],
               on_board=["Grizzly Bears"], lands_in_play=1)
    s.command_zone = []
    s = apply(apply(s, {"cast": "Time Stretch"}), PASS)
    assert (s.turn, s.extra_turn) == (1, True)
    hand_before = len(s.hand)
    assert any("play_land" in a for a in legal_actions(s))  # a land drop
    s = apply(s, PASS)
    assert (s.turn, s.extra_turn) == (1, True)
    assert len(s.hand) == hand_before + 1  # a draw step
    s = apply(s, PASS)
    assert (s.turn, s.extra_turn) == (2, False)
    assert s.opponent_life_lost == 2 * 3  # Bears attacked on turn 1 and both extra turns


def test_a_creature_cast_before_an_extra_turn_attacks_in_it():
    s = rigged(STRETCH, BEAR, hand=["Time Stretch", "Grizzly Bears"], lands_in_play=3)
    s.command_zone = []
    s = apply(apply(s, {"cast": "Grizzly Bears"}), {"cast": "Time Stretch"})
    s = apply(s, PASS)  # turn 1 ends; Bears is sick on turn 1
    assert s.opponent_life_lost == 0
    s = apply(s, PASS)  # first extra turn: Bears attacks
    assert s.opponent_life_lost == 2


def test_a_win_in_an_extra_turn_is_credited_to_the_table_turn():
    goal = {"archetype": "custom", "thing": "commander", "win": {"opponent_life_lost": 6}}
    s = rigged(STRETCH, BEAR, hand=["Time Stretch"], on_board=["Grizzly Bears"],
               lands_in_play=1, goal=goal)
    s.command_zone = []
    s = apply(apply(s, {"cast": "Time Stretch"}), PASS)  # turn 1: 2 damage
    s = apply(apply(s, PASS), PASS)  # both extra turns: 6 total
    assert s.over and s.checkpoints["win"] == 1 and s.extra_turn


def test_no_disruption_during_extra_turns():
    s = rigged(STRETCH, hand=["Time Stretch"], lands_in_play=1, commander_out=True,
               goal=REMOVAL_EVERY_TURN)
    s = apply(apply(s, {"cast": "Time Stretch"}), PASS)
    assert s.events == [] and s.command_zone == []  # Jodah-style commander survives the extra turn
    s = apply(apply(s, PASS), PASS)  # finish both extra turns; table turn 2 rolls
    assert s.turn == 2 and len(s.events) == 1


def test_nexus_shuffles_itself_back_into_the_library():
    s = rigged(NEXUS, hand=["Nexus of Fate"], lands_in_play=1)
    s.command_zone = []
    s = apply(s, {"cast": "Nexus of Fate"})
    assert "Nexus of Fate" in [s.cards[i].name for i in s.library]
    assert "Nexus of Fate" not in names(s, s.graveyard)


def test_extra_turns_per_table_turn_are_capped():
    from mtgpt.goldfish.engine import EXTRA_TURN_CAP
    s = rigged(NEXUS, hand=["Nexus of Fate"], lands_in_play=1, turn_cap=3)
    s.command_zone = []
    s.extra_turns_pending = EXTRA_TURN_CAP + 5
    s = apply(s, PASS)
    for _ in range(EXTRA_TURN_CAP):
        s = apply(s, PASS)
    assert s.turn == 2 and not s.extra_turn
    assert any("extra-turn cap" in line for line in s.log)


def test_extra_turn_state_round_trips():
    s = rigged(STRETCH, hand=["Time Stretch"], lands_in_play=1)
    s.command_zone = []
    s = apply(apply(s, {"cast": "Time Stretch"}), PASS)
    data = to_dict(s)
    assert to_dict(from_dict(json.loads(json.dumps(data)))) == data


# --- Enter the Infinite and decking ------------------------------------------

ETI = card("Enter the Infinite", "Sorcery", "Draw cards equal to the number of cards in your "
           "library, then put a card from your hand on top of your library. You have no maximum "
           "hand size until your next turn.", mana_cost="{1}")


def test_drawing_from_an_empty_library_loses():
    s = rigged(lands_in_play=1)
    s.command_zone = []
    s.library = []
    s = apply(s, PASS)
    assert s.over and s.loss_by == "decked" and s.checkpoints["loss"] == 2
    assert legal_actions(s) == []


def test_enter_the_infinite_draws_everything_then_waits_for_a_put_back():
    s = rigged(ETI, hand=["Enter the Infinite"], lands_in_play=1)
    s.command_zone = []
    library_size = len(s.library)
    s = apply(s, {"cast": "Enter the Infinite"})
    assert s.library == [] and s.pending_put_back == 1
    legal = legal_actions(s)
    assert PASS not in legal and {"put_back": "Forest"} in legal
    s = apply(s, {"put_back": "Forest"})
    assert [s.cards[i].name for i in s.library] == ["Forest"]
    assert len(s.hand) == library_size - 1  # Enter the Infinite cast, library drawn, one put back
    s = apply(s, PASS)  # turn 2 draws the Forest
    assert not s.over and s.library == []
    s = apply(s, PASS)  # turn 3 decks
    assert s.loss_by == "decked" and s.checkpoints["loss"] == 3


def test_put_back_nexus_keeps_the_loop_alive():
    s = rigged(ETI, NEXUS, hand=["Enter the Infinite"], lands_in_play=2)
    s.command_zone = []
    s = apply(apply(s, {"cast": "Enter the Infinite"}), {"put_back": "Nexus of Fate"})
    s = apply(s, PASS)  # turn 2 draws Nexus
    s = apply(apply(s, {"cast": "Nexus of Fate"}), PASS)  # extra turn draws Nexus again
    assert not s.over and s.extra_turn
    assert "Nexus of Fate" in names(s, s.hand)


def test_put_back_state_round_trips():
    s = rigged(ETI, hand=["Enter the Infinite"], lands_in_play=1)
    s.command_zone = []
    s = apply(s, {"cast": "Enter the Infinite"})
    data = to_dict(s)
    assert to_dict(from_dict(json.loads(json.dumps(data)))) == data
