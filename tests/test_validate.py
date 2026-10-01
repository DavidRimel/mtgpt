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


def test_rejects_the_one_ring_as_commander():
    """The One Ring is legendary artifact, not creature, and has no commander text."""
    deck = ResolvedDeck(
        commanders=(card("The One Ring", type_line="Legendary Artifact"),),
        cards=((99, card("Forest", type_line="Basic Land — Forest")),),
    )
    assert "commander_not_legendary" in codes(validate(deck))


def test_rejects_jace_without_commander_text():
    """Jace, the Mind Sculptor is legendary planeswalker without commander text."""
    deck = ResolvedDeck(
        commanders=(card("Jace, the Mind Sculptor", type_line="Legendary Planeswalker — Jace"),),
        cards=((99, card("Forest", type_line="Basic Land — Forest")),),
    )
    assert "commander_not_legendary" in codes(validate(deck))


def test_accepts_daretti_with_commander_text():
    """Daretti, Scrap Savant says it can be a commander in its oracle text."""
    deck = ResolvedDeck(
        commanders=(card("Daretti, Scrap Savant",
                        type_line="Legendary Planeswalker — Daretti",
                        oracle_text="[+1]: ...\n[-1]: ...\n[-4]: ... can be your commander."),),
        cards=((99, card("Forest", type_line="Basic Land — Forest")),),
    )
    violations = [v for v in validate(deck) if v.code == "commander_not_legendary"]
    assert not violations


def test_accepts_atraxa_as_legendary_creature():
    """Atraxa is a legendary creature and always valid as a commander."""
    deck = ResolvedDeck(
        commanders=(card("Atraxa, Praetors' Voice",
                        type_line="Legendary Creature — Phyrexian Angel Horror"),),
        cards=((99, card("Forest", type_line="Basic Land — Forest")),),
    )
    violations = [v for v in validate(deck) if v.code == "commander_not_legendary"]
    assert not violations


def test_flags_duplicate_nonbasic():
    deck = legal_deck(extra=((2, card("Sol Ring")),))
    violations = [v for v in validate(deck) if v.code == "singleton"]
    assert violations and "Sol Ring" in violations[0].message


def test_allows_duplicate_basic_lands():
    deck = legal_deck()
    assert "singleton" not in codes(validate(deck))


def test_allows_any_number_of_relentless_rats():
    """Relentless Rats says 'A deck can have any number of cards named Relentless Rats.'"""
    deck = legal_deck(extra=((10, card("Relentless Rats",
                                        oracle_text="A deck can have any number of cards named Relentless Rats.")),))
    violations = [v for v in validate(deck) if v.code == "singleton"]
    assert not violations


def test_allows_up_to_seven_dwarves():
    """Seven Dwarves says 'A deck can have up to seven cards named Seven Dwarves.'"""
    deck = legal_deck(extra=((7, card("Seven Dwarves",
                                       oracle_text="A deck can have up to seven cards named Seven Dwarves.")),))
    violations = [v for v in validate(deck) if v.code == "singleton"]
    assert not violations


def test_flags_color_identity_violation():
    deck = legal_deck(extra=((1, card("Lightning Bolt", color_identity=frozenset("R"))),))
    violations = [v for v in validate(deck) if v.code == "color_identity"]
    assert violations
    assert "Lightning Bolt" in violations[0].message
    assert violations[0].severity is Severity.ERROR




def test_flags_banned_card():
    deck = legal_deck(extra=((1, card("Dockside Extortionist", legal_commander="banned")),))
    violations = [v for v in validate(deck) if v.code == "banned"]
    assert violations and violations[0].severity is Severity.ERROR


def test_colorless_commander_flags_any_colored_card():
    """Colorless commander allows only colorless cards; {C} must render correctly."""
    kozilek = card("Kozilek, Butcher of Truth",
                   type_line="Legendary Creature — Eldrazi",
                   color_identity=frozenset())
    spells = tuple((1, card(f"Spell {i}")) for i in range(62))
    lands = ((36, card("Wastes", type_line="Basic Land — Wastes")),)
    deck = ResolvedDeck(
        commanders=(kozilek,),
        cards=spells + lands + ((1, card("Llanowar Elves", color_identity=frozenset("G"))),),
    )
    violations = [v for v in validate(deck) if v.code == "color_identity"]
    assert len(violations) == 1
    assert "Llanowar Elves" in violations[0].message
    assert "{C}" in violations[0].message


def test_errors_sort_before_warnings():
    """A not-legal (but not banned) card is a WARNING and must sort after ERRORs."""
    deck = legal_deck(extra=((1, card("Un-Set Card", legal_commander="not_legal")),))
    violations = validate(deck)
    severities = [v.severity for v in violations]
    assert Severity.ERROR in severities and Severity.WARNING in severities
    assert severities == sorted(severities)
    assert severities[0] is Severity.ERROR


def test_flags_a_commander_that_also_appears_in_the_deck():
    """Two copies of one card across zones is illegal and must not read as legal."""
    spells = tuple((1, card(f"Spell {i}")) for i in range(62))
    lands = ((36, card("Forest", type_line="Basic Land — Forest",
                       color_identity=frozenset("G"))),)
    deck = ResolvedDeck(commanders=(ATRAXA,), cards=spells + lands + ((1, ATRAXA),))
    violations = [v for v in validate(deck) if v.code == "duplicate_in_command_zone"]
    assert violations, "a commander duplicated in the 99 must be flagged"
    assert violations[0].severity is Severity.ERROR
    assert "Atraxa" in violations[0].message


def test_duplicate_check_is_case_insensitive():
    lower = card("atraxa, praetors' voice",
                 type_line="Legendary Creature — Phyrexian Angel Horror",
                 color_identity=frozenset("WUBG"))
    deck = ResolvedDeck(commanders=(ATRAXA,), cards=((1, lower),))
    assert "duplicate_in_command_zone" in [v.code for v in validate(deck)]


def test_a_legal_deck_has_no_command_zone_duplicate_violation():
    assert "duplicate_in_command_zone" not in [v.code for v in validate(legal_deck())]


def test_no_duplicate_violation_when_there_is_no_commander():
    deck = ResolvedDeck(commanders=(), cards=((1, card("Sol Ring")),))
    assert "duplicate_in_command_zone" not in [v.code for v in validate(deck)]


# --- "up to N" is a cap, not an exemption ------------------------------------

DWARVES = "A deck can have up to seven cards named Seven Dwarves."
NAZGUL = "A deck can have up to nine cards named Nazgûl."
RATS = "A deck can have any number of cards named Relentless Rats."


def test_eight_seven_dwarves_is_not_legal():
    """`allows_any_number` exempted "up to seven" cards from singleton entirely
    rather than capping them, so a deck with 20 Seven Dwarves reported legal.
    Seven is the ceiling the card states; eight is over it.
    """
    deck = legal_deck(extra=((8, card("Seven Dwarves", oracle_text=DWARVES)),))
    violations = [v for v in validate(deck) if v.code == "singleton"]
    assert violations, "8 copies exceeds the stated limit of 7"
    assert "Seven Dwarves" in violations[0].message
    assert "7" in violations[0].message
    assert violations[0].severity is Severity.ERROR


def test_twenty_seven_dwarves_is_not_legal():
    """The reviewer's case verbatim."""
    deck = legal_deck(extra=((20, card("Seven Dwarves", oracle_text=DWARVES)),))
    assert "singleton" in codes(validate(deck))


def test_nine_nazgul_is_legal_and_ten_is_not():
    """A different number word, to prove the cap is parsed and not hardcoded."""
    nine = legal_deck(extra=((9, card("Nazgûl", oracle_text=NAZGUL)),))
    assert "singleton" not in codes(validate(nine))
    ten = legal_deck(extra=((10, card("Nazgûl", oracle_text=NAZGUL)),))
    assert "singleton" in codes(validate(ten))


def test_any_number_cards_remain_uncapped():
    """Relentless Rats must not acquire a cap as a side effect."""
    deck = legal_deck(extra=((60, card("Relentless Rats", oracle_text=RATS)),))
    assert "singleton" not in codes(validate(deck))


def test_copy_limit_reads_the_stated_number():
    assert card("Seven Dwarves", oracle_text=DWARVES).copy_limit == 7
    assert card("Nazgûl", oracle_text=NAZGUL).copy_limit == 9
    assert card("Relentless Rats", oracle_text=RATS).copy_limit is None
    assert card("Sol Ring").copy_limit == 1


def test_an_unparsable_quantity_declines_to_cap_rather_than_inventing_one():
    """A number word the table does not know must not produce a wrong cap: a
    false violation on a real card is worse than a missed one."""
    odd = card("Odd Card", oracle_text="A deck can have up to seventeen cards named Odd Card.")
    assert odd.copy_limit is None
    deck = legal_deck(extra=((5, odd),))
    assert "singleton" not in codes(validate(deck))
