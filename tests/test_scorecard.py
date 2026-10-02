import dataclasses

import pytest

from mtgpt import scorecard as sc
from mtgpt.audit import audit
from mtgpt.goldfish.engine import prepare

from simdeck import BEAR, NEVER, SOL_RING, SWORDS, card, deck, forest

NEVER_GOAL = {"archetype": "custom", "thing": "commander", "win": NEVER}
TAPPED_LAND = card("Tapped Grove", "Land", "Tapped Grove enters tapped.\n{T}: Add {G}.",
                   produced_mana="G", colors="")


def fake_score(**over):
    base = {"target_round": 5, "primary": 0.30, "win_rate": 0.6, "on_curve": 0.72,
            "covered": 0.55, "answered": 0.8, "opponent_loss": 0.15,
            "protection_stopped": 0.3, "win_after_event": 0.4, "mulligan": 0.20,
            "mulligan_causes": {}, "color_screw": 0.05, "untapped_share": 0.85,
            "short_colors": []}
    return base | over


def test_target_round_defaults_by_bracket_and_goal_wins():
    assert [sc.target_round({}, b) for b in (1, 2, 3, 4, 5)] == [7, 7, 5, 4, 3]
    assert sc.target_round({"target_round": 6}, 3) == 6


def test_land_base_counts_untapped_share_and_short_colors():
    d = deck(TAPPED_LAND, lands=98)  # 98 Forests + 1 tapped land = 99 lands
    setup = prepare(d, NEVER_GOAL)
    land = sc.land_base(setup, audit(d))
    assert land["lands"] == 99
    assert land["untapped_share"] == round(98 / 99, 4)
    assert land["short_colors"] == []


def test_score_reads_the_report():
    report = {
        "win": {"win_by_round": [0, 0, 0, 0.1, 0.25, 0.4], "win_rate": 0.5},
        "commander": {"on_curve_rate": 0.7, "late_reasons": {"color_screw": 0.1}},
        "thing": {"covered_rate": 0.5},
        "opponent_win": {"answered_rate": 0.8},
        "loss": {"by_reason": {"opponent_win": 0.12}},
        "disruption": {"stopped_by_protection_rate": 0.3, "win_rate_after_event": 0.4},
        "setup": {"mulligan_rate": 0.2, "mulligan_causes": {"few_lands": 0.1}},
    }
    land = {"untapped_share": 0.9, "short_colors": ["G"]}
    s = sc.score(report, land, 5)
    assert s["primary"] == 0.25 and s["opponent_loss"] == 0.12
    assert s["color_screw"] == 0.1 and s["short_colors"] == ["G"]


def test_weaknesses_lists_misses_worst_first():
    s = fake_score(on_curve=0.50, mulligan=0.30, short_colors=["U"])
    names = [w["target"] for w in sc.weaknesses(s)]
    assert names[0] == "short_colors"
    assert names[1:] == ["on_curve", "mulligan"]
    assert sc.weaknesses(fake_score()) == []


def test_verdict_keep():
    v = sc.verdict(fake_score(), fake_score(primary=0.33))
    assert v["verdict"] == "keep" and v["primary_delta"] == 0.03 and not v["close_call"]


def test_verdict_revert_on_primary_drop():
    assert sc.verdict(fake_score(), fake_score(primary=0.28))["verdict"] == "revert"


def test_verdict_revert_on_broken_guard():
    v = sc.verdict(fake_score(), fake_score(primary=0.34, on_curve=0.65))
    assert v["verdict"] == "revert"
    assert [g["guard"] for g in v["broken_guards"]] == ["on_curve"]


def test_verdict_revert_on_new_short_color():
    v = sc.verdict(fake_score(), fake_score(primary=0.34, short_colors=["G"]))
    assert v["verdict"] == "revert"
    assert v["broken_guards"][0]["guard"] == "short_colors"


def test_verdict_mixed_and_close_call():
    v = sc.verdict(fake_score(), fake_score(primary=0.31))
    assert v["verdict"] == "mixed" and v["close_call"]


def test_mark_measurable():
    d = deck(SWORDS, BEAR, SOL_RING)
    setup = prepare(d, NEVER_GOAL)
    impact = {"Swords to Plowshares": {}, "Grizzly Bears": {}, "Sol Ring": {}}
    library = {"Sol Ring": {"status": "ignored", "note": "x"}}
    out = sc.mark_measurable(impact, setup, library)
    assert out["Swords to Plowshares"]["measurable"] is False  # removal: value unseen by a goldfish
    assert out["Sol Ring"]["measurable"] is False              # library says ignored
    assert out["Grizzly Bears"]["measurable"] is True


def removal(i):
    return card(f"Removal {i}", "Instant", "Destroy target creature.", mana_cost="{G}")


def gc(i):
    return dataclasses.replace(card(f"Changer {i}", "Artifact", "", mana_cost="{2}"),
                               is_game_changer=True)


COMBO_PIECE = card("Combo Piece", "Artifact", "", mana_cost="{2}")
COMBOS = [{"cards": ["Test Commander", "Combo Piece"], "card_count": 2}]


def test_floors_reject_dropping_removal_below_its_band():
    before = deck(*[removal(i) for i in range(5)], lands=37)
    after = deck(*[removal(i) for i in range(4)], BEAR, lands=37)
    result = sc.floors(before, after, 3)
    assert not result["ok"]
    assert "spot_removal" in result["rejected"][0]


def test_floors_allow_a_deck_already_below_that_gets_no_worse():
    before = deck(*[removal(i) for i in range(3)], BEAR, lands=37)
    after = deck(*[removal(i) for i in range(3)], SOL_RING, lands=37)
    assert sc.floors(before, after, 3)["ok"]


def test_floors_reject_a_fourth_game_changer_at_bracket_three():
    before = deck(*[gc(i) for i in range(3)], BEAR, lands=37)
    after = deck(*[gc(i) for i in range(4)], lands=37)
    result = sc.floors(before, after, 3)
    assert not result["ok"] and "Game Changers" in result["rejected"][0]


def test_floors_two_card_combo_rejected_at_bracket_two_warned_at_three():
    before = deck(BEAR, lands=37)
    after = deck(COMBO_PIECE, lands=37)
    assert not sc.floors(before, after, 2, COMBOS)["ok"]
    result = sc.floors(before, after, 3, COMBOS)
    assert result["ok"] and "two-card" in result["warnings"][0]


def test_floors_unchanged_or_improved_combo_count_is_not_new():
    piece2 = card("Combo Piece 2", "Artifact", "", mana_cost="{2}")
    combos = COMBOS + [{"cards": ["Test Commander", "Combo Piece 2"], "card_count": 2}]
    before = deck(COMBO_PIECE, piece2, lands=37)
    after = deck(COMBO_PIECE, BEAR, lands=37)
    assert sc.floors(before, after, 2, combos)["ok"]


def test_floors_game_changer_count_compared_not_names():
    before = deck(*[gc(i) for i in range(4)], lands=37)
    swapped = deck(*[gc(i) for i in range(1, 5)], lands=37)
    assert sc.floors(before, swapped, 3)["ok"]
    five = deck(*[gc(i) for i in range(5)], lands=37)
    assert not sc.floors(before, five, 3)["ok"]
