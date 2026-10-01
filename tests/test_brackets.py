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


def test_exactly_two_extra_turn_spells_do_not_warn():
    """The threshold is 3; two must stay silent or the constant is unpinned."""
    turns = [card(f"Warp {i}", "Sorcery", "Target player takes an extra turn after this one.")
             for i in range(2)]
    assert "extra_turns" not in codes(check(deck_of(turns), target=2))


def test_exactly_three_extra_turn_spells_warn():
    turns = [card(f"Warp {i}", "Sorcery", "Target player takes an extra turn after this one.")
             for i in range(3)]
    assert "extra_turns" in codes(check(deck_of(turns), target=2))


def test_exactly_three_tutors_do_not_warn_at_bracket_two():
    """The threshold is 4; three must stay silent."""
    tutors = [card(f"Tutor {i}", "Sorcery",
                   "Search your library for a card, put that card into your hand, then shuffle.")
              for i in range(3)]
    assert "tutor_density" not in codes(check(deck_of(tutors), target=2))


def test_a_report_with_only_warnings_is_still_compliant():
    """Warnings inform; they must never block. An error that doesn't block would
    be equally wrong, so pin the invariant in both directions."""
    turns = [card(f"Warp {i}", "Sorcery", "Target player takes an extra turn after this one.")
             for i in range(3)]
    report = check(deck_of(turns), target=2)
    assert [f.severity for f in report.findings] == [Severity.WARNING]
    assert report.compliant is True


def test_a_commander_that_is_itself_a_tutor_is_counted():
    tutor_commander = card(
        "Tutor Lord", "Legendary Creature — Avatar",
        "Search your library for a card, put that card into your hand, then shuffle.",
    )
    deck = ResolvedDeck(commanders=(tutor_commander,), cards=((1, card("Bear")),))
    assert check(deck, target=2).tutor_count == 1


def test_a_commander_that_is_mass_land_denial_is_counted():
    mld_commander = card("Land Hater", "Legendary Creature — Avatar", "Destroy all lands.")
    deck = ResolvedDeck(commanders=(mld_commander,), cards=((1, card("Bear")),))
    report = check(deck, target=2)
    assert report.mass_land_denial == ("Land Hater",)
    assert "mass_land_denial" in codes(report)


def test_partial_tags_raises_rather_than_reporting_a_clean_bracket():
    tutors = [card(f"Tutor {i}", "Sorcery",
                   "Search your library for a card, put that card into your hand, then shuffle.")
              for i in range(5)]
    with pytest.raises(ValueError, match="missing"):
        check(deck_of(tutors), tags={}, target=2)


def test_combo_deferred_note_disappears_when_combos_are_supplied():
    report = check(deck_of([card("Bear")]), target=2, combos=())
    assert not any("Spellbook" in note for note in report.deferred_checks)


def test_combo_note_present_when_combos_are_not_supplied():
    report = check(deck_of([card("Bear")]), target=2)
    assert any("Spellbook" in note for note in report.deferred_checks)


def test_two_card_combo_is_an_error_at_brackets_one_and_two():
    combos = [{"card_count": 2, "cards": ("Thassa's Oracle", "Demonic Consultation")}]
    for target in (1, 2):
        report = check(deck_of([card("Bear")]), target=target, combos=combos)
        finding = next(f for f in report.findings if f.code == "two_card_combo")
        assert finding.severity is Severity.ERROR
        assert report.compliant is False


def test_two_card_combo_is_only_a_warning_at_bracket_three():
    """Bracket 3 permits a late-game combo finish, so an ERROR would wrongly
    fail a legal deck. mtgpt cannot judge speed, so it warns."""
    combos = [{"card_count": 2, "cards": ("Thassa's Oracle", "Demonic Consultation")}]
    report = check(deck_of([card("Bear")]), target=3, combos=combos)
    finding = next(f for f in report.findings if f.code == "two_card_combo")
    assert finding.severity is Severity.WARNING
    assert "late-game" in finding.message
    assert report.compliant is True


def test_two_card_combo_is_silent_at_brackets_four_and_five():
    combos = [{"card_count": 2, "cards": ("Thassa's Oracle", "Demonic Consultation")}]
    for target in (4, 5):
        report = check(deck_of([card("Bear")]), target=target, combos=combos)
        assert "two_card_combo" not in [f.code for f in report.findings]


def test_three_card_combo_is_not_flagged_as_a_two_card_combo():
    combos = [{"card_count": 3, "cards": ("A", "B", "C")}]
    report = check(deck_of([card("Bear")]), target=2, combos=combos)
    assert "two_card_combo" not in [f.code for f in report.findings]


# --- A Game Changers outage must not read as compliant (Critical 2) ----------


def _gc_deck(*, available: bool):
    """The same deck twice, differing only in whether the list was fetched.

    When the fetch failed, `scryfall.resolve` has already degraded every
    `is_game_changer` to False, so the two decks are identical except for the
    flag — which is exactly why the flag has to exist.
    """
    commander = card("Atraxa, Praetors' Voice", "Legendary Creature — Angel")
    flagged = available
    return ResolvedDeck(
        commanders=(commander,),
        cards=(
            (1, card("Rhystic Study", "Enchantment", game_changer=flagged)),
            (1, card("Smothering Tithe", "Enchantment", game_changer=flagged)),
            (1, card("Cyclonic Rift", "Instant", game_changer=flagged)),
            (1, card("Mystic Remora", "Enchantment", game_changer=flagged)),
        ),
        game_changers_available=available,
    )


def test_game_changer_allowance_fires_when_the_list_is_available():
    """The healthy half. A test that only checks this is what let the bug
    through: the degraded path produces the opposite verdict."""
    report = check(_gc_deck(available=True), target=3)
    assert len(report.game_changers) == 4
    assert "game_changers" in [f.code for f in report.findings]
    assert report.compliant is False


def test_a_game_changers_outage_is_named_in_deferred_checks():
    """Scryfall returns 404 for a search matching nothing, so a tag rename
    produces exactly this: every `is_game_changer` False, zero Game Changers
    found, and `compliant` flipping False -> True with no note saying why.

    The spec's rule is that no script returns empty results in a way that reads
    as a clean bill of health, and SKILL.md tells the agent `deferred_checks` is
    the complete list of skipped rules. So the verdict may come back compliant,
    but the outage must be named.
    """
    report = check(_gc_deck(available=False), target=3)
    # Degraded exactly as described: nothing found, so nothing to flag.
    assert report.game_changers == ()
    assert "game_changers" not in [f.code for f in report.findings]
    # ...and that silence is accounted for.
    assert any("Game Changers list unavailable" in note for note in report.deferred_checks), (
        report.deferred_checks
    )
    # The pre-existing notes are not displaced.
    assert len(report.deferred_checks) == 3


def test_an_available_list_adds_no_outage_note():
    """The healthy path must not cry wolf."""
    report = check(_gc_deck(available=True), target=3)
    assert not any("unavailable" in note for note in report.deferred_checks)


def test_the_outage_note_reaches_the_api_payload():
    """`_bracket_dict` passes `deferred_checks` straight through, so the agent
    reading JSON sees the gap without any extra plumbing."""
    from mtgpt.api import _bracket_dict

    payload = _bracket_dict(check(_gc_deck(available=False), target=3))
    assert payload["compliant"] is True
    assert any("Game Changers list unavailable" in n for n in payload["deferred_checks"])
