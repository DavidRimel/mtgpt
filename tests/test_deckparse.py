import pytest

from mtgpt.deckparse import parse
from mtgpt.errors import DeckStructureError


def test_parses_bare_quantity_and_name():
    deck = parse("1 Sol Ring\n30 Forest\n")
    assert [(e.qty, e.name) for e in deck.entries] == [(1, "Sol Ring"), (30, "Forest")]


def test_parses_set_code_and_collector_number():
    deck = parse("1 Arcane Signet (ELD) 331\n")
    entry = deck.entries[0]
    assert entry.name == "Arcane Signet"
    assert entry.set_code == "ELD"
    assert entry.collector_number == "331"


def test_keeps_parenthetical_that_is_part_of_the_name():
    # Set codes are 2-6 alphanumerics; a word in parens is part of the name.
    deck = parse("1 Erase (Not the Urza's Legacy One)\n")
    assert deck.entries[0].name == "Erase (Not the Urza's Legacy One)"
    assert deck.entries[0].set_code is None


def test_parses_x_suffix_quantity():
    deck = parse("2x Brainstorm\n")
    assert (deck.entries[0].qty, deck.entries[0].name) == (2, "Brainstorm")


def test_strips_foil_and_other_star_flags():
    deck = parse("1 Sol Ring (LTR) 264 *F*\n")
    assert deck.entries[0].name == "Sol Ring"
    assert deck.entries[0].collector_number == "264"


def test_captures_hash_category_tag():
    deck = parse("1 Cultivate (M21) 177 #Ramp\n")
    assert deck.entries[0].category == "Ramp"
    assert deck.entries[0].name == "Cultivate"


def test_commander_section_marks_commander():
    text = "Commander\n1 Atraxa, Praetors' Voice (C16) 28\n\nDeck\n1 Sol Ring\n"
    deck = parse(text)
    assert [c.name for c in deck.commanders] == ["Atraxa, Praetors' Voice"]
    assert [e.name for e in deck.entries] == ["Sol Ring"]


def test_cmdr_flag_marks_commander_without_section():
    deck = parse("1 Atraxa, Praetors' Voice (C16) 28 *CMDR*\n1 Sol Ring\n")
    assert [c.name for c in deck.commanders] == ["Atraxa, Praetors' Voice"]
    assert [e.name for e in deck.entries] == ["Sol Ring"]


def test_excludes_sideboard_and_maybeboard():
    text = (
        "Deck\n1 Sol Ring\n\n"
        "Sideboard\n1 Mana Crypt\n\n"
        "Maybeboard\n1 Mana Vault\n\n"
        "Considering\n1 Chrome Mox\n"
    )
    deck = parse(text)
    assert [e.name for e in deck.entries] == ["Sol Ring"]


def test_ignores_blank_lines_and_comments():
    deck = parse("\n// my notes\n1 Sol Ring\n\n")
    assert len(deck.entries) == 1


def test_handles_crlf_and_trailing_whitespace():
    deck = parse("1 Sol Ring   \r\n2x Forest\r\n")
    assert [(e.qty, e.name) for e in deck.entries] == [(1, "Sol Ring"), (2, "Forest")]


def test_double_faced_name_with_slashes_survives():
    deck = parse("1 Agadeem's Awakening // Agadeem, the Undercrypt (ZNR) 90\n")
    assert deck.entries[0].name == "Agadeem's Awakening // Agadeem, the Undercrypt"
    assert deck.entries[0].set_code == "ZNR"


def test_merges_duplicate_lines_of_the_same_card():
    deck = parse("1 Forest\n3 Forest\n")
    assert [(e.qty, e.name) for e in deck.entries] == [(4, "Forest")]


def test_raises_when_no_entries_found():
    with pytest.raises(DeckStructureError):
        parse("this is not a decklist at all\n")


def test_raises_on_empty_input():
    with pytest.raises(DeckStructureError):
        parse("   \n\n")


def test_multiword_category_preserves_name_and_set_code():
    # A multi-word category like "#Ramp, Draw" should not corrupt the card name,
    # set code, or collector number.
    deck = parse("1 Cultivate (M21) 177 #Ramp, Draw\n")
    assert deck.entries[0].name == "Cultivate"
    assert deck.entries[0].set_code == "M21"
    assert deck.entries[0].collector_number == "177"
    assert deck.entries[0].category == "Ramp, Draw"


def test_multiple_tags_take_first_as_category():
    # When multiple tags are present, take the first one as the category.
    deck = parse("1 Sol Ring (LTR) 264 #Ramp #Fast\n")
    assert deck.entries[0].name == "Sol Ring"
    assert deck.entries[0].set_code == "LTR"
    assert deck.entries[0].collector_number == "264"
    assert deck.entries[0].category == "Ramp"


def test_bom_on_first_line_does_not_drop_entry():
    # A UTF-8 BOM on the first line should not cause the entry to be dropped.
    deck = parse("﻿1 Sol Ring\n2 Forest\n")
    assert [(e.qty, e.name) for e in deck.entries] == [(1, "Sol Ring"), (2, "Forest")]
