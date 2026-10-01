import json
import pathlib

import pytest

from mtgpt import spellbook
from mtgpt.errors import SourceUnavailable

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def variants_payload():
    return json.loads((FIXTURES / "spellbook_variants.json").read_text())


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_variants_for_card_quotes_the_name():
    transport = FakeTransport(variants_payload())
    client = spellbook.SpellbookClient(transport=transport, sleep=lambda _: None)
    client.variants_for_card("Thassa's Oracle")
    assert "Thassa" in transport.calls[0]
    assert "variants" in transport.calls[0]


def test_variants_for_card_wraps_transport_failure():
    client = spellbook.SpellbookClient(
        transport=FakeTransport(OSError("boom")), sleep=lambda _: None
    )
    with pytest.raises(SourceUnavailable) as excinfo:
        client.variants_for_card("Thassa's Oracle")
    assert "Commander Spellbook" in str(excinfo.value)


def test_parse_variant_extracts_cards_and_outcome():
    raw = variants_payload()["results"][0]
    combo = spellbook.parse_variant(raw)
    assert set(combo) >= {"id", "cards", "produces", "bracket_tag", "salt", "card_count"}
    assert combo["card_count"] == len(combo["cards"])
    assert all(isinstance(c, str) for c in combo["cards"])


def test_combos_in_deck_requires_every_card_present():
    raw = variants_payload()["results"][0]
    combo = spellbook.parse_variant(raw)
    cards = combo["cards"]
    # All pieces present -> detected.
    assert spellbook.combos_in_deck((raw,), set(cards))
    # One piece missing -> not detected.
    assert spellbook.combos_in_deck((raw,), set(cards[:-1])) == ()


def test_combos_in_deck_is_case_insensitive():
    raw = variants_payload()["results"][0]
    cards = {c.lower() for c in spellbook.parse_variant(raw)["cards"]}
    assert spellbook.combos_in_deck((raw,), cards)


def test_combos_in_deck_deduplicates_by_id():
    raw = variants_payload()["results"][0]
    cards = set(spellbook.parse_variant(raw)["cards"])
    assert len(spellbook.combos_in_deck((raw, raw), cards)) == 1


def test_two_card_combos_are_identified():
    raw = variants_payload()["results"][0]
    combo = spellbook.parse_variant(raw)
    assert spellbook.is_two_card_combo(combo) == (combo["card_count"] == 2)


def test_malformed_variant_degrades_rather_than_raising():
    assert spellbook.parse_variant({})["cards"] == ()
    assert spellbook.combos_in_deck(({},), {"anything"}) == ()
