import json
import pathlib

import pytest

from mtgpt.errors import SourceUnavailable, UnresolvedCards
from mtgpt.models import DeckEntry, ParsedDeck
from mtgpt.scryfall import (
    COLLECTION_BATCH_SIZE,
    ScryfallClient,
    card_from_json,
    resolve,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


class FakeTransport:
    """Records calls and returns queued responses."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, payload=None):
        self.calls.append((url, payload))
        if not self.responses:
            raise AssertionError(f"unexpected extra request to {url}")
        return self.responses.pop(0)


def test_batch_size_is_scryfall_limit():
    assert COLLECTION_BATCH_SIZE == 75


def test_card_from_json_maps_basic_fields():
    payload = load("collection_basic.json")["data"][0]
    card = card_from_json(payload)
    assert card.name == "Sol Ring"
    assert card.mana_value == 1.0
    assert card.type_line == "Artifact"
    assert card.mana_cost == "{1}"
    assert card.produced_mana == frozenset({"C"})
    assert card.legal_commander == "legal"
    assert card.usd == 1.54
    assert card.is_game_changer is False


def test_card_from_json_handles_null_price():
    payload = load("collection_basic.json")["data"][2]
    card = card_from_json(payload)
    assert card.usd is None
    assert card.is_banned is True


def test_card_from_json_flags_game_changers():
    payload = load("collection_basic.json")["data"][0]
    card = card_from_json(payload, game_changers=frozenset({"sol ring"}))
    assert card.is_game_changer is True


def test_card_from_json_uses_front_face_for_mdfc():
    payload = load("collection_mdfc.json")["data"][0]
    card = card_from_json(payload)
    # Front face supplies cost, colors, and text; top level supplies type_line.
    assert card.mana_cost == "{X}{B}{B}{B}"
    assert card.colors == frozenset({"B"})
    assert card.type_line == "Sorcery // Land"
    assert "Return from your graveyard" in card.oracle_text
    # The land back face must not leak into the text we classify on.
    assert "Add {B}" not in card.oracle_text
    assert card.is_mdfc_land is True
    assert card.is_land is False


def test_collection_raises_unresolved_with_offending_names():
    transport = FakeTransport({"data": [], "not_found": [{"name": "Nonexistent Xyz"}]})
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    with pytest.raises(UnresolvedCards) as excinfo:
        client.collection(["Nonexistent Xyz"])
    assert excinfo.value.names == ("Nonexistent Xyz",)
    assert "will not guess" in str(excinfo.value)


def test_collection_batches_requests_at_the_limit():
    names = [f"Card {i}" for i in range(76)]
    first = {"data": [{"name": n} for n in names[:75]], "not_found": []}
    second = {"data": [{"name": names[75]}], "not_found": []}
    transport = FakeTransport(first, second)
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    cards, missing = client.collection(names)
    assert len(transport.calls) == 2
    assert len(transport.calls[0][1]["identifiers"]) == 75
    assert len(transport.calls[1][1]["identifiers"]) == 1
    assert len(cards) == 76
    assert missing == ()


def test_collection_sleeps_between_requests():
    names = [f"Card {i}" for i in range(76)]
    transport = FakeTransport(
        {"data": [{"name": n} for n in names[:75]], "not_found": []},
        {"data": [{"name": names[75]}], "not_found": []},
    )
    slept = []
    client = ScryfallClient(transport=transport, sleep=slept.append)
    client.collection(names)
    assert slept and all(s >= 0.1 for s in slept)


def test_game_changers_returns_casefolded_names():
    transport = FakeTransport(load("game_changers.json"))
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    assert client.game_changers() == frozenset(
        {"rhystic study", "cyclonic rift", "smothering tithe"}
    )


def test_game_changers_raises_source_unavailable_on_transport_error():
    def boom(url, payload=None):
        raise OSError("connection reset")

    client = ScryfallClient(transport=boom, sleep=lambda _: None)
    with pytest.raises(SourceUnavailable):
        client.game_changers()


def test_resolve_matches_mdfc_requested_by_front_face_name():
    parsed = ParsedDeck(entries=(DeckEntry(qty=1, name="Agadeem's Awakening"),))
    transport = FakeTransport(load("collection_mdfc.json"), {"data": [], "has_more": False})
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    deck = resolve(parsed, client=client)
    assert deck.cards[0][1].name == "Agadeem's Awakening // Agadeem, the Undercrypt"


def test_resolve_separates_commanders_and_preserves_quantities():
    parsed = ParsedDeck(
        entries=(DeckEntry(qty=3, name="Sol Ring"),),
        commanders=(DeckEntry(qty=1, name="Atraxa, Praetors' Voice", is_commander=True),),
    )
    basic = load("collection_basic.json")
    transport = FakeTransport(basic, {"data": [], "has_more": False})
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    deck = resolve(parsed, client=client)
    assert [c.name for c in deck.commanders] == ["Atraxa, Praetors' Voice"]
    assert deck.cards == ((3, deck.cards[0][1]),)
    assert deck.cards[0][1].name == "Sol Ring"
    assert deck.total_with_commanders == 4


def test_resolve_degrades_when_game_changers_unavailable():
    """A Game Changers outage must not block the audit."""
    calls = {"n": 0}

    def transport(url, payload=None):
        calls["n"] += 1
        if "search" in url:
            raise OSError("scryfall search down")
        return load("collection_basic.json")

    parsed = ParsedDeck(entries=(DeckEntry(qty=1, name="Sol Ring"),))
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    deck = resolve(parsed, client=client)
    assert deck.cards[0][1].is_game_changer is False
