import dataclasses

import pytest

from mtgpt.goldfish.engine import prepare
from mtgpt.goldfish.run import compare, play, simulate
from mtgpt.models import ResolvedDeck

from simdeck import BEAR, NEVER, SOL_RING, SWORDS, TEFERIS_PROTECTION, card, commander, deck, forest

GO_WIDE = {"archetype": "go_wide"}
NEVER_GOAL = {"archetype": "custom", "thing": "commander", "win": NEVER}


def test_fixed_seed_gives_identical_reports():
    d = deck(SOL_RING, BEAR, lands=37)
    assert simulate(d, GO_WIDE, games=40, seed=5) == simulate(d, GO_WIDE, games=40, seed=5)
    assert simulate(d, GO_WIDE, games=40, seed=5) != simulate(d, GO_WIDE, games=40, seed=6)


def test_all_lands_casts_a_four_drop_commander_on_turn_four():
    report = simulate(deck(), GO_WIDE, games=20)
    assert report["commander"]["on_curve_rate"] == 1.0
    assert report["commander"]["cast_turn"]["median"] == 4
    assert report["setup"]["mulligan_rate"] == 1.0  # 7 lands is not a keep
    assert report["setup"]["mana_by_turn"][:4] == [1.0, 2.0, 3.0, 4.0]
    # Turns 1-3: one mana per land, nothing to spend it on.
    assert report["setup"]["pre_commander_mana_spent_on"]["unspent"] == 1.0


def test_go_wide_by_commander_damage_alone():
    # Turn 4 commander, a 4-power attacker from turn 5: 120 needs 30 attacks.
    report = simulate(deck(), GO_WIDE, games=5, turn_cap=10)
    assert report["win"]["win_rate"] == 0.0
    assert report["win"]["win_turn"]["median"] is None


def test_win_turn_and_condition_are_reported():
    goal = {"archetype": "custom", "thing": "commander",
            "win": {"any": [{"commander_damage": 8}, {"board_power": 1000}]}}
    report = simulate(deck(), goal, games=10)
    # Commander lands turn 4, attacks for 4 on turns 5 and 6.
    assert report["win"]["win_rate"] == 1.0
    assert report["win"]["win_turn"]["median"] == 6
    assert report["win"]["by_condition"] == {"commander_damage>=8": 1.0}
    assert report["thing"]["online_turn"]["median"] == 4


def test_disruption_at_zero_never_fires():
    goal = {"archetype": "custom", "thing": "commander", "win": NEVER,
            "disruption": {"commander_removal": 0.0, "board_wipe": 0.0, "from_turn": 1}}
    assert simulate(deck(), goal, games=10)["disruption"]["events"] == 0


def test_disruption_at_one_removes_the_commander_every_turn():
    goal = {"archetype": "custom", "thing": "commander", "win": {"commander_damage": 1},
            "disruption": {"commander_removal": 1.0, "from_turn": 1}}
    report = simulate(deck(), goal, games=10)
    # Cast on turn 4, removed at the start of turn 5 before it can ever attack.
    assert report["win"]["win_rate"] == 0.0
    assert report["disruption"]["landed"] > 0
    assert report["disruption"]["stopped_by_protection_rate"] == 0.0


def test_no_disruption_flag_turns_it_off():
    goal = {"archetype": "custom", "thing": "commander", "win": NEVER,
            "disruption": {"commander_removal": 1.0, "from_turn": 1}}
    report = simulate(deck(), goal, games=5, disruption=False)
    assert report["disruption"]["events"] == 0
    assert report["disruption_enabled"] is False


def test_compare_identical_decks_has_zero_deltas():
    d = deck(SOL_RING, BEAR, SWORDS, lands=37)
    result = compare(d, d, GO_WIDE, games=20)

    def numbers(tree):
        for value in tree.values():
            if isinstance(value, dict):
                yield from numbers(value)
            elif isinstance(value, list):
                yield from value
            else:
                yield value

    assert result["before"] == result["after"]
    # When identical, deltas are 0 or None (if both sides were None)
    assert set(numbers(result["delta"])) == {0, None}
    assert result["delta"]["commander"]["on_curve_rate"] == 0


def test_compare_sees_a_better_deck():
    slow, fast = deck(lands=99), deck(SOL_RING, lands=98)
    result = compare(slow, fast, GO_WIDE, games=200)
    assert result["delta"]["commander"]["on_curve_rate"] == 0.0  # both always on curve
    early = lambda r: min(int(t) for t in r["commander"]["cast_turn"]["histogram"])
    assert early(result["before"]) == 4
    assert early(result["after"]) < 4  # Sol Ring drawn early accelerates it


def test_unmodeled_cards_are_named():
    proliferate = card("Proliferator", "Enchantment",
                       "At the beginning of your end step, proliferate.", mana_cost="{2}")
    report = simulate(deck(proliferate), GO_WIDE, games=2)
    assert report["notes"]["unmodeled"] == ["Proliferator"]


def test_goal_warns_when_no_card_can_carry_a_counted_tag():
    report = simulate(deck(BEAR), {"archetype": "spellslinger"}, games=2)
    warnings = report["notes"]["goal_warnings"]
    assert len(warnings) == 1 and "'payoff'" in warnings[0] and warnings[0].startswith("thing")


def test_goal_warnings_empty_when_every_tag_can_be_carried():
    assert simulate(deck(BEAR), GO_WIDE, games=2)["notes"]["goal_warnings"] == []


def test_games_must_be_positive():
    with pytest.raises(ValueError):
        simulate(deck(), GO_WIDE, games=0)


# --- Review focus: inputs real decks bring ----------------------------------


def test_partner_commanders_both_get_cast():
    partners = (commander("{1}{G}", 2.0, "Partner A"), commander("{2}{G}", 3.0, "Partner B"))
    d = ResolvedDeck(commanders=partners, cards=((98, forest()),))
    setup = prepare(d, GO_WIDE)
    assert setup.goal.commander_turn == 2  # the cheaper partner sets the curve
    s = play(setup, seed=1)
    assert {"Partner A", "Partner B"} <= set(s.cast_names)


def test_running_out_of_library_ends_quietly_at_the_cap():
    small = ResolvedDeck(commanders=(commander(),), cards=((12, forest()),))
    s = play(prepare(small, GO_WIDE), seed=1, turn_cap=30)
    assert s.over and s.turn == 30 and s.library == []


def test_colorless_commander_plays():
    karn = card("Karn", "Legendary Creature — Construct", "", mana_cost="{4}",
                power=4.0, identity="", colors="")
    wastes = card("Wastes", "Basic Land", "{T}: Add {C}.", produced_mana="C",
                  identity="", colors="")
    d = ResolvedDeck(commanders=(karn,), cards=((99, wastes),))
    assert play(prepare(d, GO_WIDE), seed=1).commander_cast_turn == 4


# --- _delta unit tests -------------------------------------------------------


def test_delta_union_of_keys():
    """Delta iterates union of keys, treating missing as 0."""
    from mtgpt.goldfish.run import _delta
    before = {"histogram": {"4": 10, "5": 5}, "median": 4}
    after = {"histogram": {"4": 12, "6": 3}, "median": 4}
    delta = _delta(before, after)
    assert delta["histogram"]["4"] == 2
    assert delta["histogram"]["5"] == -5
    assert delta["histogram"]["6"] == 3
    assert delta["median"] == 0


def test_delta_one_sided_dict():
    """One-sided dicts are diffed against empty dict."""
    from mtgpt.goldfish.run import _delta
    assert _delta({}, {"x": {"y": 2}}) == {"x": {"y": 2}}


def test_delta_lists_pad_and_diff():
    """Lists of different lengths are padded and diffed elementwise."""
    from mtgpt.goldfish.run import _delta
    assert _delta({"l": [1]}, {"l": [1, 3]}) == {"l": [0, 3]}


def test_delta_handles_none_in_lists():
    """None (genuine) in lists: None vs numeric → None."""
    from mtgpt.goldfish.run import _delta
    before = {"mana_by_turn": [1.0, 2.0, None, 4.0]}
    after = {"mana_by_turn": [1.0, 2.5, 3.0, 4.0]}
    delta = _delta(before, after)
    assert delta["mana_by_turn"] == [0.0, 0.5, None, 0.0]


def test_delta_leaves_out_lists_of_names():
    from mtgpt.goldfish.run import _delta
    before = {"notes": {"unmodeled": ["A", "B"]}, "x": 1}
    after = {"notes": {"unmodeled": ["A", "C", "D"]}, "x": 2}
    assert _delta(before, after) == {"x": 1}
    assert _delta({}, {"names": ["A"]}) == {}


# --- Disruption recovery ---


def test_disruption_recovery_all_lands():
    """All-lands deck: T4 cast, removed T6, T7, T9; recovery median is 1."""
    def rem(ft):
        return {"archetype": "custom", "thing": "commander", "win": NEVER,
                "disruption": {"commander_removal": 1.0, "from_turn": ft}}
    r = simulate(deck(), rem(6), games=10)
    assert r["disruption"]["recovery_turns"]["median"] == 1
    assert r["disruption"]["never_recovered"] == 0
    assert r["disruption"]["landed"] == 30


def test_disruption_never_recovered_at_cap():
    """With turn_cap=5, all removals leave commander unrecovered."""
    def rem(ft):
        return {"archetype": "custom", "thing": "commander", "win": NEVER,
                "disruption": {"commander_removal": 1.0, "from_turn": ft}}
    r = simulate(deck(), rem(5), games=10, turn_cap=5)
    assert r["disruption"]["never_recovered"] == 10
    assert r["disruption"]["landed"] == 10


def test_disruption_win_after_event_with_mana():
    """With mana available win condition, all games win after removal."""
    goal = {"archetype": "custom", "thing": "commander",
            "win": {"mana_available": 7},
            "disruption": {"commander_removal": 1.0, "from_turn": 5}}
    r = simulate(deck(), goal, games=10)
    assert r["disruption"]["win_rate_after_event"] == 1.0


def test_disruption_win_after_event_none_when_no_events():
    """When no events land, win_rate_after_event is None."""
    r = simulate(deck(), NEVER_GOAL, games=10)
    assert r["disruption"]["win_rate_after_event"] is None


# --- Protection stops disruption ---


def test_disruption_stopped_by_protection():
    """Deck with many protection cards stops removals."""
    prot = [card(f"Shield {i}", "Instant",
                  "Target creature you control gains hexproof until end of turn.",
                  mana_cost="{G}")
            for i in range(30)]
    def rem(ft):
        return {"archetype": "custom", "thing": "commander", "win": NEVER,
                "disruption": {"commander_removal": 1.0, "from_turn": ft}}
    d = simulate(deck(*prot), rem(1), games=30)["disruption"]
    assert d["stopped_by_protection_rate"] > 0.5
    assert d["landed"] < d["events"]
    assert d["stopped_by_protection_rate"] == round((d["events"] - d["landed"]) / d["events"], 4)


def test_disruption_no_protection_unprotected():
    """Unprotected deck has stopped_by_protection_rate == 0."""
    def rem(ft):
        return {"archetype": "custom", "thing": "commander", "win": NEVER,
                "disruption": {"commander_removal": 1.0, "from_turn": ft}}
    d = simulate(deck(), rem(1), games=10)["disruption"]
    assert d["stopped_by_protection_rate"] == 0.0


# --- Interaction while online ---


def test_interaction_removal_and_protection():
    """Deck with removal and protection cards: metrics > 0, covered_rate in range."""
    remv = [card(f"Bolt {i}", "Instant", "Destroy target creature.", mana_cost="{G}")
            for i in range(30)]
    prot = [card(f"Shield {i}", "Instant",
                  "Target creature you control gains hexproof until end of turn.",
                  mana_cost="{G}")
            for i in range(30)]
    t = simulate(deck(*remv, *prot), NEVER_GOAL, games=30)["thing"]
    assert t["interaction_while_online"]["removal"] > 1
    assert t["interaction_while_online"]["protection"] > 1
    assert t["interaction_while_online"]["counterspell"] == 0.0
    assert 0.5 < t["covered_rate"] <= 1.0


def test_covered_rate_none_when_thing_never_online():
    """Thing that never comes online: covered_rate is None."""
    goal = {"archetype": "custom", "thing": {"count": "creature", "min": 50}, "win": NEVER}
    t = simulate(deck(), goal, games=5)["thing"]
    assert t["covered_rate"] is None
    assert t["online_rate"] == 0.0


# --- Stall and late reasons ---


def test_stall_with_many_creatures():
    """Deck with 79 creatures and 20 lands: stalled_rate > 0.2, land_light > 0.5."""
    bears = [card(f"Bear {i}", "Creature — Bear", "", mana_cost="{1}{G}", power=2.0)
             for i in range(79)]
    r = simulate(deck(*bears, lands=20), GO_WIDE, games=50)
    assert r["setup"]["stalled_rate"] > 0.2
    assert r["commander"]["late_reasons"]["land_light"] > 0.5


def test_stall_all_lands_is_zero():
    """All-lands deck: stalled_rate == 0.0."""
    r = simulate(deck(), GO_WIDE, games=20)
    assert r["setup"]["stalled_rate"] == 0.0


def test_color_screw_blue_commander_all_forests():
    """Blue commander over all Forests: late_reasons == {'color_screw': 1.0}."""
    blue = dataclasses.replace(commander("{2}{U}{U}", 4.0, "Blue Commander"),
                               color_identity=frozenset("GU"))
    d = ResolvedDeck(commanders=(blue,), cards=((99, forest()),))
    r = simulate(d, GO_WIDE, games=10)
    assert r["commander"]["late_reasons"] == {"color_screw": 1.0}
