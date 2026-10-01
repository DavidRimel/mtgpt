import pytest

from mtgpt.models import (
    Card,
    DeckEntry,
    Function,
    ParsedDeck,
    ResolvedDeck,
    Severity,
    Violation,
)


def sample_card(name="Sol Ring", **kw):
    defaults = dict(
        name=name,
        mana_value=1.0,
        type_line="Artifact",
        oracle_text="{T}: Add {C}{C}.",
        mana_cost="{1}",
        color_identity=frozenset(),
        colors=frozenset(),
        legal_commander="legal",
        produced_mana=frozenset("C"),
        layout="normal",
        is_game_changer=False,
        usd=1.54,
        keywords=(),
    )
    defaults.update(kw)
    return Card(**defaults)


def test_card_is_frozen_and_hashable():
    card = sample_card()
    assert hash(card) is not None
    with pytest.raises(AttributeError):
        card.name = "Mana Crypt"


def test_card_is_basic_land_only_for_basics():
    assert sample_card("Forest", type_line="Basic Land — Forest").is_basic_land
    assert not sample_card("Command Tower", type_line="Land").is_basic_land


def test_card_is_land_front_face():
    assert sample_card("Command Tower", type_line="Land").is_land
    assert not sample_card("Cultivate", type_line="Sorcery").is_land


def test_card_mdfc_land_back_is_not_a_land():
    agadeem = sample_card(
        "Agadeem's Awakening // Agadeem, the Undercrypt",
        type_line="Sorcery // Land",
        layout="modal_dfc",
    )
    assert not agadeem.is_land
    assert agadeem.is_mdfc_land


def test_parsed_deck_counts_exclude_commanders():
    entries = (DeckEntry(qty=1, name="Sol Ring"), DeckEntry(qty=30, name="Forest"))
    cmd = (DeckEntry(qty=1, name="Atraxa, Praetors' Voice", is_commander=True),)
    deck = ParsedDeck(entries=entries, commanders=cmd)
    assert deck.total_cards == 31
    assert deck.total_with_commanders == 32


def test_resolved_deck_total_counts_quantities():
    deck = ResolvedDeck(
        commanders=(sample_card("Atraxa, Praetors' Voice"),),
        cards=((1, sample_card()), (30, sample_card("Forest"))),
    )
    assert deck.total_cards == 31
    assert deck.total_with_commanders == 32


def test_violation_orders_by_severity():
    err = Violation(severity=Severity.ERROR, code="deck_size", message="too small")
    warn = Violation(severity=Severity.WARNING, code="curve", message="high")
    assert sorted([warn, err])[0] is err


def test_function_enum_covers_expected_tags():
    expected = {
        "LAND", "RAMP", "DRAW", "SPOT_REMOVAL", "SWEEPER", "TUTOR",
        "COUNTERSPELL", "PROTECTION", "WINCON", "RECURSION",
        "MASS_LAND_DENIAL", "EXTRA_TURNS", "SYNERGY",
    }
    assert {f.name for f in Function} == expected
