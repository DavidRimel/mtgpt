import pytest

from mtgpt.brackets import RULES, check
from mtgpt.models import Card, Function, ResolvedDeck, Severity

F = Function


def card(name, type_line="Creature — Bear", oracle_text="", game_changer=False):
    return Card(
        name=name,
        mana_value=2.0,
        type_line=type_line,
        oracle_text=oracle_text,
        mana_cost="{1}{G}",
        color_identity=frozenset("G"),
        colors=frozenset("G"),
        legal_commander="legal",
        produced_mana=frozenset(),
        layout="normal",
        is_game_changer=game_changer,
        usd=None,
        keywords=(),
    )


def deck_of(cards):
    return ResolvedDeck(commanders=(), cards=tuple((1, c) for c in cards))


def codes(report):
    return [f.code for f in report.findings]


def test_rules_cover_brackets_one_through_five():
    assert set(RULES) == {1, 2, 3, 4, 5}


def test_bracket_four_and_five_are_unrestricted():
    assert RULES[4].game_changers_max is None
    assert RULES[5].game_changers_max is None


def test_clean_deck_is_compliant_at_bracket_two():
    report = check(deck_of([card("Bear")]), target=2)
    assert report.compliant is True
    assert report.findings == ()


def test_game_changer_in_bracket_two_is_an_error():
    report = check(deck_of([card("Rhystic Study", game_changer=True)]), target=2)
    assert "game_changers" in codes(report)
    assert report.findings[0].severity is Severity.ERROR
    assert report.compliant is False


def test_bracket_three_allows_up_to_three_game_changers():
    cards = [card(f"GC {i}", game_changer=True) for i in range(3)]
    report = check(deck_of(cards), target=3)
    assert "game_changers" not in codes(report)


def test_bracket_three_flags_a_fourth_game_changer():
    cards = [card(f"GC {i}", game_changer=True) for i in range(4)]
    report = check(deck_of(cards), target=3)
    assert "game_changers" in codes(report)


def test_bracket_four_permits_many_game_changers():
    cards = [card(f"GC {i}", game_changer=True) for i in range(12)]
    report = check(deck_of(cards), target=4)
    assert report.compliant is True


def test_report_lists_game_changer_names():
    report = check(deck_of([card("Rhystic Study", game_changer=True)]), target=2)
    assert report.game_changers == ("Rhystic Study",)


def test_mass_land_denial_flagged_below_bracket_four():
    armageddon = card("Armageddon", "Sorcery", "Destroy all lands.")
    report = check(deck_of([armageddon]), target=3)
    assert "mass_land_denial" in codes(report)
    assert report.mass_land_denial == ("Armageddon",)


def test_mass_land_denial_allowed_at_bracket_four():
    armageddon = card("Armageddon", "Sorcery", "Destroy all lands.")
    report = check(deck_of([armageddon]), target=4)
    assert "mass_land_denial" not in codes(report)


def test_extra_turn_density_warns_below_bracket_four():
    turns = [card(f"Time Warp {i}", "Sorcery", "Target player takes an extra turn after this one.")
             for i in range(3)]
    report = check(deck_of(turns), target=3)
    assert "extra_turns" in codes(report)
    assert len(report.extra_turns) == 3


def test_single_extra_turn_spell_is_fine():
    warp = card("Time Warp", "Sorcery", "Target player takes an extra turn after this one.")
    report = check(deck_of([warp]), target=2)
    assert "extra_turns" not in codes(report)


def test_tutor_density_warns_at_bracket_two():
    tutors = [card(f"Tutor {i}", "Sorcery",
                   "Search your library for a card, put that card into your hand, then shuffle.")
              for i in range(5)]
    report = check(deck_of(tutors), target=2)
    assert "tutor_density" in codes(report)
    assert report.tutor_count == 5
    severity = next(f.severity for f in report.findings if f.code == "tutor_density")
    assert severity is Severity.WARNING


def test_tutor_density_not_flagged_at_bracket_three():
    tutors = [card(f"Tutor {i}", "Sorcery",
                   "Search your library for a card, put that card into your hand, then shuffle.")
              for i in range(5)]
    report = check(deck_of(tutors), target=3)
    assert "tutor_density" not in codes(report)


def test_combo_detection_is_declared_deferred():
    report = check(deck_of([card("Bear")]), target=2)
    assert any("combo" in note.lower() for note in report.deferred_checks)


def test_invalid_bracket_raises():
    with pytest.raises(ValueError):
        check(deck_of([card("Bear")]), target=9)


def test_report_carries_target_name():
    assert check(deck_of([card("Bear")]), target=1).target_name == "Exhibition"
    assert check(deck_of([card("Bear")]), target=5).target_name == "cEDH"
