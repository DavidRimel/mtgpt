import json
import pathlib
import urllib.error

import pytest

from mtgpt import archidekt, deckparse
from mtgpt.errors import SourceUnavailable

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def payload():
    return json.loads((FIXTURES / "archidekt_deck.json").read_text())


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if not self.responses:
            raise AssertionError(f"unexpected request to {url}")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.parametrize(
    "given",
    [
        "https://archidekt.com/decks/2000000/yuriko-the-tigers-shadow",
        "https://archidekt.com/decks/2000000",
        "https://archidekt.com/decks/2000000/",
        "https://archidekt.com/api/decks/2000000/",
        "archidekt.com/decks/2000000/anything",
        "2000000",
        "  2000000  ",
    ],
)
def test_deck_id_accepts_every_shape_a_user_might_paste(given):
    assert archidekt.deck_id(given) == "2000000"


@pytest.mark.parametrize(
    "given",
    ["https://moxfield.com/decks/abc123", "https://archidekt.com/search", "", "deck"],
)
def test_deck_id_refuses_to_guess(given):
    # Guessing would fetch some other user's deck and report it as theirs.
    with pytest.raises(ValueError):
        archidekt.deck_id(given)


def test_deck_id_error_mentions_moxfield_because_that_is_the_common_mistake():
    with pytest.raises(ValueError) as exc:
        archidekt.deck_id("https://moxfield.com/decks/abc")
    assert "Moxfield" in str(exc.value)


def test_deck_requests_the_api_url():
    transport = FakeTransport(payload())
    client = archidekt.ArchidektClient(transport=transport, sleep=lambda _: None)
    client.deck("https://archidekt.com/decks/2000000/slug")
    assert transport.calls == ["https://archidekt.com/api/decks/2000000/"]


def test_deck_accepts_a_bare_id():
    transport = FakeTransport(payload())
    client = archidekt.ArchidektClient(transport=transport, sleep=lambda _: None)
    client.deck("2000000")
    assert transport.calls == ["https://archidekt.com/api/decks/2000000/"]


def test_a_transport_failure_raises_source_unavailable():
    transport = FakeTransport(urllib.error.URLError("archidekt down"))
    client = archidekt.ArchidektClient(transport=transport, sleep=lambda _: None)
    with pytest.raises(SourceUnavailable) as exc:
        client.deck("2000000")
    assert exc.value.source == "Archidekt"


def test_malformed_json_raises_source_unavailable_not_valueerror():
    transport = FakeTransport(ValueError("not json"))
    client = archidekt.ArchidektClient(transport=transport, sleep=lambda _: None)
    with pytest.raises(SourceUnavailable):
        client.deck("2000000")


def test_the_courtesy_delay_is_honored_between_calls():
    slept = []
    transport = FakeTransport(payload(), payload())
    client = archidekt.ArchidektClient(transport=transport, sleep=slept.append)
    client.deck("1")
    client.deck("2")
    assert slept == [archidekt.REQUEST_DELAY]


def test_to_decklist_emits_a_commander_section():
    text = archidekt.to_decklist(payload())
    lines = text.splitlines()
    assert lines[0] == "Commander"
    assert lines[1] == "1 Yuriko, the Tiger's Shadow"
    assert "Deck" in lines


def test_to_decklist_output_round_trips_through_our_own_parser():
    # Reusing deckparse is the point: one decklist parser, not two that can
    # disagree about what a commander is.
    deck = deckparse.parse(archidekt.to_decklist(payload()))
    assert [e.name for e in deck.commanders] == ["Yuriko, the Tiger's Shadow"]
    assert deck.total_with_commanders == 12  # 1 commander + 8 Island + 3 others
    assert ("8 Island") in archidekt.to_decklist(payload())


def test_to_decklist_preserves_quantities():
    text = archidekt.to_decklist(payload())
    assert "8 Island" in text


def test_to_decklist_drops_entries_in_an_excluded_category():
    data = payload()
    data["cards"].append(
        {
            "quantity": 1,
            "categories": ["Maybeboard"],
            "card": {"oracleCard": {"name": "Demonic Tutor"}},
        }
    )
    assert "Demonic Tutor" not in archidekt.to_decklist(data)


def test_an_author_who_counts_their_sideboard_is_believed():
    # The deck's own includedInDeck flag is authoritative; the static name list
    # is only a fallback for categories the payload did not describe.
    data = payload()
    data["categories"] = [
        {"name": "Sideboard", "includedInDeck": True},
        {"name": "Commander", "includedInDeck": True},
    ]
    data["cards"].append(
        {
            "quantity": 1,
            "categories": ["Sideboard"],
            "card": {"oracleCard": {"name": "Demonic Tutor"}},
        }
    )
    assert "Demonic Tutor" in archidekt.to_decklist(data)


def test_a_category_absent_from_the_flags_falls_back_to_the_name_list():
    data = payload()
    data["categories"] = [{"name": "Commander", "includedInDeck": True}]
    data["cards"].append(
        {
            "quantity": 1,
            "categories": ["Maybeboard"],
            "card": {"oracleCard": {"name": "Demonic Tutor"}},
        }
    )
    assert "Demonic Tutor" not in archidekt.to_decklist(data)


def test_to_decklist_prefers_the_oracle_name_over_the_display_name():
    # displayName carries whatever art variant the author picked and does not
    # always resolve against Scryfall.
    data = payload()
    data["cards"] = [
        {
            "quantity": 1,
            "categories": ["Artifact"],
            "card": {"displayName": "Sol Ring // Secret Lair", "oracleCard": {"name": "Sol Ring"}},
        }
    ]
    assert "1 Sol Ring\n" in archidekt.to_decklist(data)


def test_to_decklist_falls_back_to_the_display_name_when_there_is_no_oracle_card():
    data = payload()
    data["cards"] = [
        {"quantity": 1, "categories": ["Artifact"], "card": {"displayName": "Sol Ring"}}
    ]
    assert "1 Sol Ring" in archidekt.to_decklist(data)


@pytest.mark.parametrize("quantity", [0, -1, None, "many", {}])
def test_an_unusable_quantity_costs_one_card_not_the_whole_import(quantity):
    data = payload()
    data["cards"].append(
        {
            "quantity": quantity,
            "categories": ["Artifact"],
            "card": {"oracleCard": {"name": "Demonic Tutor"}},
        }
    )
    text = archidekt.to_decklist(data)
    assert "Demonic Tutor" not in text
    # Its well-formed siblings survive.
    assert "1 Sol Ring" in text


def test_a_quantity_that_arrived_as_a_string_is_still_coerced():
    data = payload()
    data["cards"] = [
        {"quantity": "3", "categories": ["Artifact"], "card": {"oracleCard": {"name": "Sol Ring"}}}
    ]
    assert "3 Sol Ring" in archidekt.to_decklist(data)


@pytest.mark.parametrize("reshaped", [None, [], "cards", {"cards": "nope"}, {"cards": [None, 7]}])
def test_a_reshaped_payload_degrades_rather_than_crashing(reshaped):
    assert archidekt.to_decklist(reshaped).strip() == "Deck"


def test_declared_bracket_reads_the_authors_claim():
    assert archidekt.declared_bracket(payload()) == 3


@pytest.mark.parametrize("raw", [None, "", "nope", 0, 6, {}, []])
def test_declared_bracket_degrades_to_none_for_anything_unusable(raw):
    data = payload()
    data["edhBracket"] = raw
    assert archidekt.declared_bracket(data) is None


def test_declared_bracket_coerces_a_string():
    data = payload()
    data["edhBracket"] = "4"
    assert archidekt.declared_bracket(data) == 4


def test_deck_name_degrades_to_empty():
    assert archidekt.deck_name({"name": None}) == ""
    assert archidekt.deck_name(payload()) == "Yuriko, the Tigers Shadow"
