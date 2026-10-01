import pytest

from mtgpt.goldfish.engine import prepare
from mtgpt.goldfish.run import compare, play, simulate
from mtgpt.models import ResolvedDeck

from simdeck import BEAR, NEVER, SOL_RING, SWORDS, card, commander, deck, forest

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
            else:
                yield value

    assert result["before"] == result["after"]
    assert set(numbers(result["delta"])) == {0}


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
