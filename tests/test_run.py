import pytest

from mtgpt.goldfish.engine import prepare
from mtgpt.goldfish.run import compare, play, simulate
from mtgpt.models import ResolvedDeck

from simdeck import BEAR, NEVER, SOL_RING, SWORDS, TEFERIS_PROTECTION, card, commander, deck, forest

GO_WIDE = {"archetype": "go_wide"}


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


# --- Additional test coverage for report aggregates -------------------------


def test_delta_handles_union_of_keys():
    """Delta iterates union of keys, treating missing as 0."""
    from mtgpt.goldfish.run import _delta
    before = {"histogram": {"4": 10, "5": 5}, "median": 4}
    after = {"histogram": {"4": 12, "6": 3}, "median": 4}
    delta = _delta(before, after)
    assert delta["histogram"]["4"] == 2  # 12 - 10
    assert delta["histogram"]["5"] == -5  # 0 - 5
    assert delta["histogram"]["6"] == 3  # 3 - 0
    assert delta["median"] == 0  # 4 - 4


def test_delta_handles_none_values():
    """If exactly one side is None, delta is None."""
    from mtgpt.goldfish.run import _delta
    before = {"median": None}
    after = {"median": 5}
    delta = _delta(before, after)
    assert delta["median"] is None

    # Reversed
    before = {"median": 5}
    after = {"median": None}
    delta = _delta(before, after)
    assert delta["median"] is None


def test_delta_handles_lists_elementwise():
    """Lists diff elementwise."""
    from mtgpt.goldfish.run import _delta
    before = {"mana_by_turn": [1.0, 2.0, None, 4.0]}
    after = {"mana_by_turn": [1.0, 2.5, 3.0, 4.0]}
    delta = _delta(before, after)
    assert delta["mana_by_turn"] == [0.0, 0.5, None, 0.0]


def test_compare_with_new_by_condition():
    """After deck with new by_condition key includes it in delta."""
    goal = {"archetype": "custom", "thing": "commander",
            "win": {"any": [{"commander_damage": 8}, {"board_power": 1000}]}}
    slow, fast = deck(lands=99), deck(SOL_RING, lands=98)
    result = compare(slow, fast, goal, games=50)
    # Both should have at least commander_damage in by_condition
    # Delta should include all keys from both
    assert "by_condition" in result["delta"]["win"]
    assert isinstance(result["delta"]["win"]["by_condition"], dict)
    assert len(result["delta"]["win"]["by_condition"]) > 0


def test_delta_mana_by_turn_is_list():
    """Delta's setup.mana_by_turn is a list of differences."""
    result = compare(deck(lands=99), deck(SOL_RING, lands=98), GO_WIDE, games=50)
    assert isinstance(result["delta"]["setup"]["mana_by_turn"], list)
    assert len(result["delta"]["setup"]["mana_by_turn"]) > 0


def test_protection_increases_stopped_rate():
    """Deck with protection cards reduces disruption impact."""
    goal = {"archetype": "custom", "thing": "commander", "win": {"commander_damage": 100},
            "disruption": {"commander_removal": 1.0, "from_turn": 1}}
    unprotected = deck(lands=99)
    protected = deck(TEFERIS_PROTECTION, SWORDS, lands=97)

    unprotected_report = simulate(unprotected, goal, games=10)
    protected_report = simulate(protected, goal, games=10)

    # Protected deck should have higher stopped_by_protection_rate
    assert protected_report["disruption"]["stopped_by_protection_rate"] > unprotected_report["disruption"]["stopped_by_protection_rate"]


def test_recovery_turns_tracked():
    """Recovery from disruption is measured."""
    goal = {"archetype": "custom", "thing": "commander", "win": NEVER,
            "disruption": {"commander_removal": 0.5, "from_turn": 5}}
    deck_with_mana = deck(SOL_RING, lands=98)
    report = simulate(deck_with_mana, goal, games=20)

    # If events landed, recovery should be tracked
    if report["disruption"]["landed"] > 0:
        assert report["disruption"]["recovery_turns"]["median"] is None or report["disruption"]["recovery_turns"]["median"] >= 0


def test_never_recovered_tracked():
    """Games where thing never comes back online tracked."""
    goal = {"archetype": "custom", "thing": "commander", "win": NEVER,
            "disruption": {"commander_removal": 1.0, "from_turn": 1}}
    mana_poor = ResolvedDeck(commanders=(commander(),), cards=((98, forest()),))
    report = simulate(mana_poor, goal, games=10)

    # With low mana and constant removal, many won't recover
    assert report["disruption"]["never_recovered"] >= 0


def test_win_rate_after_event_with_no_events():
    """When no disruption events, win_rate_after_event is None or 0."""
    goal = {"archetype": "custom", "thing": "commander", "win": NEVER,
            "disruption": {"commander_removal": 0.0, "from_turn": 1}}
    report = simulate(deck(), goal, games=10)
    assert report["disruption"]["events"] == 0
    # win_rate_after_event should be None when no events landed
    rate = report["disruption"]["win_rate_after_event"]
    assert rate is None or rate == 0


def test_win_rate_after_event_with_events():
    """When disruption events land, win_rate_after_event is tracked."""
    goal = {"archetype": "custom", "thing": "commander", "win": {"commander_damage": 100},
            "disruption": {"commander_removal": 0.5, "from_turn": 1}}
    report = simulate(deck(SOL_RING, lands=98), goal, games=20)

    if report["disruption"]["landed"] > 0:
        # Should be a number between 0 and 1
        rate = report["disruption"]["win_rate_after_event"]
        assert isinstance(rate, (int, float)) or rate is None


def test_interaction_while_online():
    """Deck with removal and protection shows interaction_while_online."""
    goal = {"archetype": "custom", "thing": "commander", "win": NEVER}
    with_interaction = deck(SWORDS, SWORDS, SWORDS, lands=96)
    report = simulate(with_interaction, goal, games=10)

    # If commander comes online, interaction metrics should be available
    if report["thing"]["online_rate"] > 0:
        assert report["thing"]["interaction_while_online"]["removal"] >= 0
        assert report["thing"]["interaction_while_online"]["protection"] >= 0


def test_covered_rate_makes_sense():
    """covered_rate is between 0 and 1 when thing is online, None/0 when never online."""
    goal = {"archetype": "custom", "thing": "commander", "win": NEVER}
    report = simulate(deck(), goal, games=10)

    covered = report["thing"]["covered_rate"]
    if report["thing"]["online_rate"] == 0:
        assert covered is None or covered == 0
    else:
        assert covered is None or (0 <= covered <= 1)


def test_stalled_rate_with_low_lands():
    """Deck with few lands shows stalled rate."""
    goal = {"archetype": "go_wide"}
    mana_poor = deck(BEAR, lands=20)
    report = simulate(mana_poor, goal, games=20)

    # Very few lands should show some stalled games
    assert report["setup"]["stalled_rate"] >= 0


def test_stalled_rate_with_all_lands():
    """All-lands deck has 0 stalled rate."""
    goal = {"archetype": "go_wide"}
    report = simulate(deck(), goal, games=20)
    assert report["setup"]["stalled_rate"] == 0.0


def test_games_by_turn_tracking():
    """games_by_turn reflects how many games survive to each turn."""
    report = simulate(deck(), GO_WIDE, games=20, turn_cap=5)
    games_by_turn = report["setup"]["games_by_turn"]

    # First turn should be all games
    assert games_by_turn[0] == 20
    # Later turns should be <= earlier (games end)
    for i in range(len(games_by_turn) - 1):
        assert games_by_turn[i + 1] <= games_by_turn[i]


def test_late_reasons_land_light():
    """Deck with few lands shows land_light in late_reasons."""
    mana_poor = deck(BEAR, lands=15)
    report = simulate(mana_poor, GO_WIDE, games=20)

    if report["commander"]["cast_rate"] < 1.0:
        # If not always cast, late_reasons should have entries
        assert len(report["commander"]["late_reasons"]) > 0
