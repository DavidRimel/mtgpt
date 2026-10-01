from mtgpt.models import Card, ResolvedDeck, Severity
from mtgpt.validate import validate


def card(name, **kw):
    defaults = dict(
        name=name,
        mana_value=1.0,
        type_line="Artifact",
        oracle_text="",
        mana_cost="{1}",
        color_identity=frozenset(),
        colors=frozenset(),
        legal_commander="legal",
        produced_mana=frozenset(),
        layout="normal",
        is_game_changer=False,
        usd=None,
        keywords=(),
    )
    defaults.update(kw)
    return Card(**defaults)


ATRAXA = card(
    "Atraxa, Praetors' Voice",
    type_line="Legendary Creature — Phyrexian Angel Horror",
    color_identity=frozenset("WUBG"),
    mana_value=4.0,
)


def legal_deck(extra=()):
    """A structurally valid 100-card deck: commander + 63 spells + 36 basics."""
    spells = tuple((1, card(f"Spell {i}")) for i in range(63))
    lands = ((36, card("Forest", type_line="Basic Land — Forest",
                       color_identity=frozenset("G"))),)
    return ResolvedDeck(commanders=(ATRAXA,), cards=spells + lands + tuple(extra))


def codes(violations):
    return [v.code for v in violations]


def test_valid_deck_has_no_violations():
    assert validate(legal_deck()) == ()


def test_flags_wrong_deck_size():
    deck = ResolvedDeck(commanders=(ATRAXA,), cards=((10, card("Forest")),))
    assert "deck_size" in codes(validate(deck))


def test_flags_missing_commander():
    deck = ResolvedDeck(commanders=(), cards=((99, card("Forest")),))
    assert "commander_missing" in codes(validate(deck))


def test_allows_two_commanders_for_partner():
    partner_a = card("Commander A", type_line="Legendary Creature — Human")
    partner_b = card("Commander B", type_line="Legendary Creature — Human")
    spells = tuple((1, card(f"Spell {i}")) for i in range(62))
    lands = ((36, card("Forest", type_line="Basic Land — Forest")),)
    deck = ResolvedDeck(commanders=(partner_a, partner_b), cards=spells + lands)
    assert "commander_count" not in codes(validate(deck))


def test_flags_three_commanders():
    trio = tuple(card(f"C{i}", type_line="Legendary Creature — Human") for i in range(3))
    deck = ResolvedDeck(commanders=trio, cards=((97, card("Forest")),))
    assert "commander_count" in codes(validate(deck))


def test_flags_non_legendary_commander():
    deck = ResolvedDeck(
        commanders=(card("Grizzly Bears", type_line="Creature — Bear"),),
        cards=((99, card("Forest", type_line="Basic Land — Forest")),),
    )
    assert "commander_not_legendary" in codes(validate(deck))


def test_flags_duplicate_nonbasic():
    deck = legal_deck(extra=((2, card("Sol Ring")),))
    violations = [v for v in validate(deck) if v.code == "singleton"]
    assert violations and "Sol Ring" in violations[0].message


def test_allows_duplicate_basic_lands():
    deck = legal_deck()
    assert "singleton" not in codes(validate(deck))


def test_flags_color_identity_violation():
    deck = legal_deck(extra=((1, card("Lightning Bolt", color_identity=frozenset("R"))),))
    violations = [v for v in validate(deck) if v.code == "color_identity"]
    assert violations
    assert "Lightning Bolt" in violations[0].message
    assert violations[0].severity is Severity.ERROR


def test_colorless_card_never_violates_identity():
    deck = legal_deck()
    assert "color_identity" not in codes(validate(deck))


def test_flags_banned_card():
    deck = legal_deck(extra=((1, card("Dockside Extortionist", legal_commander="banned")),))
    violations = [v for v in validate(deck) if v.code == "banned"]
    assert violations and violations[0].severity is Severity.ERROR


def test_flags_commander_color_identity_against_itself():
    """A commander's own identity defines the deck, so it can never violate."""
    deck = legal_deck()
    assert not [v for v in validate(deck) if "Atraxa" in v.message]


def test_errors_sort_before_warnings():
    deck = ResolvedDeck(commanders=(), cards=((5, card("Forest")),))
    violations = validate(deck)
    severities = [v.severity for v in violations]
    assert severities == sorted(severities)
