import json
import pathlib
import urllib.error

import pytest

from mtgpt.errors import SourceUnavailable, UnresolvedCards
from mtgpt.models import DeckEntry, ParsedDeck
from mtgpt.scryfall import (
    COLLECTION_BATCH_SIZE,
    REQUEST_DELAY,
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
    card = card_from_json(payload, game_changers=frozenset())
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
    card = card_from_json(payload, game_changers=frozenset())
    assert card.usd is None
    assert card.is_banned is True


def test_card_from_json_flags_game_changers():
    payload = load("collection_basic.json")["data"][0]
    card = card_from_json(payload, game_changers=frozenset({"sol ring"}))
    assert card.is_game_changer is True


def test_card_from_json_uses_front_face_for_mdfc():
    payload = load("collection_mdfc.json")["data"][0]
    card = card_from_json(payload, game_changers=frozenset())
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


def test_collection_strict_by_default_still_raises():
    transport = FakeTransport({"data": [], "not_found": [{"name": "Nope"}]})
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    with pytest.raises(UnresolvedCards):
        client.collection(["Nope"])


def test_collection_non_strict_returns_missing_instead_of_raising():
    """One unresolvable candidate must not discard the resolvable ones."""
    transport = FakeTransport(
        {"data": [{"name": "Sol Ring"}], "not_found": [{"name": "Nope"}]}
    )
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    cards, missing = client.collection(["Sol Ring", "Nope"], strict=False)
    assert [c["name"] for c in cards] == ["Sol Ring"]
    assert missing == ("Nope",)


def test_collection_non_strict_accumulates_missing_across_batches():
    names = [f"Card {i}" for i in range(76)]
    transport = FakeTransport(
        {"data": [{"name": n} for n in names[:74]], "not_found": [{"name": names[74]}]},
        {"data": [], "not_found": [{"name": names[75]}]},
    )
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    cards, missing = client.collection(names, strict=False)
    assert len(cards) == 74
    assert missing == (names[74], names[75])


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


def test_first_request_does_not_sleep_but_later_ones_do():
    names = [f"Card {i}" for i in range(76)]
    transport = FakeTransport(
        {"data": [{"name": n} for n in names[:75]], "not_found": []},
        {"data": [{"name": names[75]}], "not_found": []},
    )
    slept = []
    client = ScryfallClient(transport=transport, sleep=slept.append)
    client.collection(names)
    assert slept == [REQUEST_DELAY], "exactly one gap between two requests"


def test_throttle_spans_separate_endpoints():
    """collection() then game_changers() must still be spaced."""
    parsed = ParsedDeck(entries=(DeckEntry(qty=1, name="Sol Ring"),))
    transport = FakeTransport(load("collection_basic.json"), {"data": [], "has_more": False})
    slept = []
    client = ScryfallClient(transport=transport, sleep=slept.append)
    resolve(parsed, client=client)
    assert slept == [REQUEST_DELAY], "the second endpoint must be throttled too"


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


def test_game_changers_follows_pagination():
    page_one = {
        "data": [{"name": "Rhystic Study"}],
        "has_more": True,
        "next_page": "https://api.scryfall.com/cards/search?q=is%3Agamechanger&page=2",
    }
    page_two = {"data": [{"name": "Cyclonic Rift"}], "has_more": False}
    transport = FakeTransport(page_one, page_two)
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    assert client.game_changers() == frozenset({"rhystic study", "cyclonic rift"})
    assert len(transport.calls) == 2
    assert "page=2" in transport.calls[1][0]


def test_game_changers_stops_at_the_page_cap():
    """A server that always says has_more must not hang the client."""
    def transport(url, payload=None):
        return {"data": [{"name": "Rhystic Study"}], "has_more": True,
                "next_page": "https://api.scryfall.com/cards/search?page=99"}

    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    with pytest.raises(SourceUnavailable):
        client.game_changers()


def test_resolve_matches_mdfc_requested_by_front_face_name():
    parsed = ParsedDeck(entries=(DeckEntry(qty=1, name="Agadeem's Awakening"),))
    transport = FakeTransport(load("collection_mdfc.json"), {"data": [], "has_more": False})
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    deck = resolve(parsed, client=client)
    assert deck.cards[0][1].name == "Agadeem's Awakening // Agadeem, the Undercrypt"


def test_resolve_raises_when_a_returned_card_cannot_be_matched_to_its_request():
    """collection() can succeed while a name still fails to match the index.

    This is the second line of defense: Scryfall could answer 200 with a card
    whose name normalizes differently than the one requested.
    """
    parsed = ParsedDeck(entries=(DeckEntry(qty=1, name="Sol Ring"),))
    # not_found is empty, yet the returned card is a different card entirely.
    transport = FakeTransport(
        {"data": [{"name": "Mana Crypt", "legalities": {"commander": "legal"}}],
         "not_found": []},
        {"data": [], "has_more": False},
    )
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    with pytest.raises(UnresolvedCards) as excinfo:
        resolve(parsed, client=client)
    assert excinfo.value.names == ("Sol Ring",)


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


def test_search_follows_pagination():
    page_one = {
        "data": [{"name": "Cultivate"}],
        "has_more": True,
        "next_page": "https://api.scryfall.com/cards/search?q=x&page=2",
    }
    page_two = {"data": [{"name": "Kodama's Reach"}], "has_more": False}
    transport = FakeTransport(page_one, page_two)
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    results = client.search("c:g", limit=10)
    assert [c["name"] for c in results] == ["Cultivate", "Kodama's Reach"]
    assert len(transport.calls) == 2
    assert "page=2" in transport.calls[1][0]


def test_search_stops_at_the_page_cap():
    """A server that always says has_more must not hang the client."""
    def transport(url, payload=None):
        return {"data": [], "has_more": True,
                "next_page": "https://api.scryfall.com/cards/search?page=99"}

    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    with pytest.raises(SourceUnavailable):
        client.search("c:g", limit=10)


# --- Reshaped numeric fields do not raise (Important 5) ----------------------


def test_card_from_json_tolerates_reshaped_numeric_fields():
    """The same pattern edhrec.py guards: a `float()` on a field from upstream
    must not turn a card lookup into a traceback."""
    payload = {
        "name": "Weird Card", "cmc": "not a number", "type_line": "Instant",
        "oracle_text": "", "mana_cost": "{1}", "color_identity": [], "colors": [],
        "legalities": {"commander": "legal"}, "layout": "normal", "keywords": [],
        "prices": {"usd": "free"},
    }
    card = card_from_json(payload, game_changers=frozenset())
    assert card.mana_value == 0.0
    assert card.usd is None


def test_card_from_json_still_reads_well_formed_numbers():
    payload = {
        "name": "Normal Card", "cmc": "3", "type_line": "Instant", "oracle_text": "",
        "mana_cost": "{3}", "color_identity": [], "colors": [],
        "legalities": {"commander": "legal"}, "layout": "normal", "keywords": [],
        "prices": {"usd": "1.25"},
    }
    card = card_from_json(payload, game_changers=frozenset())
    assert card.mana_value == 3.0
    assert card.usd == 1.25


# --- Split / Room / DFC names resolve (Important 6) --------------------------


def test_collection_sends_the_front_half_of_a_split_name():
    """Scryfall's /cards/collection rejects every full "A // B" name as an
    identifier: Fire // Ice, Dusk // Dawn, Bottomless Pool // Locker Room and the
    Zendikar MDFC lands all land in `not_found`, while the front half resolves.

    A Moxfield export carries the full name, so sending it verbatim told the user
    a real, correctly-spelled card was misspelled and SKILL.md then had the agent
    stop and ask.
    """
    sent = {}

    def transport(url, payload=None):
        sent["identifiers"] = payload["identifiers"]
        return {"data": [{"object": "card", "name": "Fire // Ice"}], "not_found": []}

    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    found, missing = client.collection(["Fire // Ice", "Sol Ring"])
    assert sent["identifiers"] == [{"name": "Fire"}, {"name": "Sol Ring"}]
    assert missing == ()
    assert found[0]["name"] == "Fire // Ice"


def test_a_split_card_resolves_through_a_whole_decklist():
    """End to end: the parser keeps the full name, resolve maps the response back
    onto it via `_index_by_name`, and no UnresolvedCards is raised."""
    from mtgpt.deckparse import parse

    fire_ice = {
        "object": "card", "name": "Fire // Ice", "cmc": 2.0,
        "type_line": "Instant // Instant", "layout": "split",
        "card_faces": [
            {"name": "Fire", "mana_cost": "{1}{R}", "type_line": "Instant",
             "oracle_text": "Fire deals 2 damage divided as you choose among one or "
                            "two targets.", "colors": ["R"]},
            {"name": "Ice", "mana_cost": "{1}{U}", "type_line": "Instant",
             "oracle_text": "Tap target permanent.\nDraw a card.", "colors": ["U"]},
        ],
        "color_identity": ["R", "U"], "colors": ["R", "U"],
        "legalities": {"commander": "legal"}, "keywords": [], "prices": {"usd": "0.50"},
    }
    niv = {
        "object": "card", "name": "Niv-Mizzet, Parun", "cmc": 6.0,
        "type_line": "Legendary Creature — Dragon Wizard",
        "oracle_text": "This spell can't be countered.", "mana_cost": "{3}{U}{U}{R}{R}",
        "color_identity": ["R", "U"], "colors": ["R", "U"], "layout": "normal",
        "legalities": {"commander": "legal"}, "keywords": ["Flying"],
        "prices": {"usd": "2.00"},
    }
    client = ScryfallClient(
        transport=FakeTransport(
            {"data": [niv, fire_ice], "not_found": []}, {"data": [], "has_more": False},
        ),
        sleep=lambda _: None,
    )
    deck = resolve(parse("Commander\n1 Niv-Mizzet, Parun\n\nDeck\n1 Fire // Ice\n"),
                   client=client)
    assert [c.name for _, c in deck.cards] == ["Fire // Ice"]


def test_an_invented_name_still_raises_even_with_a_slash():
    """The front-half rewrite must not swallow a genuine typo. Scryfall reports
    `not_found` under the front half it was sent, and that is mapped back to what
    the user actually wrote so the error names their line."""
    client = ScryfallClient(
        transport=FakeTransport(
            {"data": [], "not_found": [{"name": "Blatantly Fake"}]},
        ),
        sleep=lambda _: None,
    )
    with pytest.raises(UnresolvedCards) as excinfo:
        client.collection(["Blatantly Fake // Not A Card"])
    assert excinfo.value.names == ("Blatantly Fake // Not A Card",)


def test_card_from_json_reads_power_and_toughness():
    from mtgpt.scryfall import card_from_json

    bear = card_from_json(
        {"name": "Bear", "type_line": "Creature — Bear", "power": "2", "toughness": "2"},
        game_changers=frozenset(),
    )
    star = card_from_json(
        {"name": "Star", "type_line": "Creature", "power": "1+*", "toughness": "*"},
        game_changers=frozenset(),
    )
    rock = card_from_json({"name": "Rock", "type_line": "Artifact"}, game_changers=frozenset())
    mdfc = card_from_json(
        {"name": "A // B", "type_line": "Creature // Land",
         "card_faces": [{"name": "A", "power": "3", "toughness": "1"}, {"name": "B"}]},
        game_changers=frozenset(),
    )
    assert (bear.power, bear.toughness) == (2.0, 2.0)
    assert (star.power, star.toughness) == (1.0, 0.0)
    assert rock.power is None
    assert mdfc.power == 3.0


# --- Rate limit retry with exponential backoff (Task 10) ---------------------


def too_many(url="https://api.scryfall.com/x"):
    return urllib.error.HTTPError(url, 429, "Too Many Requests", None, None)


class FlakyTransport:
    """Raises 429 `fails` times, then answers."""

    def __init__(self, fails, response):
        self.fails, self.response, self.calls = fails, response, 0

    def __call__(self, url, payload=None):
        self.calls += 1
        if self.calls <= self.fails:
            raise too_many(url)
        return self.response


def test_rate_limit_is_retried_with_backoff():
    sleeps = []
    transport = FlakyTransport(2, load("game_changers.json"))
    client = ScryfallClient(transport=transport, sleep=sleeps.append)
    assert client.game_changers()
    assert transport.calls == 3
    assert sleeps == [1.0, 2.0]


def test_rate_limit_gives_up_after_three_retries():
    sleeps = []
    client = ScryfallClient(transport=FlakyTransport(10, {}), sleep=sleeps.append)
    with pytest.raises(SourceUnavailable):
        client.game_changers()
    assert sleeps == [1.0, 2.0, 4.0]
