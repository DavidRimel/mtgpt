import json
import pathlib

import pytest

from mtgpt import api
from mtgpt.scryfall import ScryfallClient

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)

    def __call__(self, url, payload=None):
        return self.responses.pop(0)


class FakeEdhrec:
    def __init__(self, payload):
        self.payload = payload

    def commander(self, name, *, variant=None):
        return self.payload


def test_suggest_reports_the_gaps_it_is_filling():
    """A deck short on ramp must be told so, with the target band."""
    text = (FIXTURES / "sample_deck.txt").read_text()
    client = ScryfallClient(
        transport=FakeTransport(
            load("collection_sample_deck.json"), {"data": [], "has_more": False},
            load("collection_basic.json"), {"data": [], "has_more": False},
        ),
        sleep=lambda _: None,
    )
    result = api.suggest_additions(
        text, target=3, limit=5, client=client,
        edhrec_client=FakeEdhrec(load("edhrec_commander.json")),
    )
    assert result["gaps"], "expected under-served categories"
    gap = result["gaps"][0]
    assert set(gap) >= {"function", "count", "target", "needed"}
    assert gap["needed"] > 0


def test_every_suggestion_is_legal_in_the_commanders_identity():
    text = (FIXTURES / "sample_deck.txt").read_text()
    client = ScryfallClient(
        transport=FakeTransport(
            load("collection_sample_deck.json"), {"data": [], "has_more": False},
            load("collection_basic.json"), {"data": [], "has_more": False},
        ),
        sleep=lambda _: None,
    )
    result = api.suggest_additions(
        text, target=3, limit=10, client=client,
        edhrec_client=FakeEdhrec(load("edhrec_commander.json")),
    )
    allowed = set(result["color_identity"])
    for s in result["suggestions"]:
        assert set(s["color_identity"]) <= allowed, s["name"]
        assert s["legal_commander"] == "legal", s["name"]


def test_suggestions_never_include_a_card_already_in_the_deck():
    text = (FIXTURES / "sample_deck.txt").read_text()
    client = ScryfallClient(
        transport=FakeTransport(
            load("collection_sample_deck.json"), {"data": [], "has_more": False},
            load("collection_basic.json"), {"data": [], "has_more": False},
        ),
        sleep=lambda _: None,
    )
    result = api.suggest_additions(
        text, target=3, limit=10, client=client,
        edhrec_client=FakeEdhrec(load("edhrec_commander.json")),
    )
    present = {"Sol Ring", "Cultivate", "Swords to Plowshares", "Wrath of God", "Forest"}
    assert not ({s["name"] for s in result["suggestions"]} & present)


def test_each_suggestion_states_the_gap_it_fills_and_its_evidence():
    text = (FIXTURES / "sample_deck.txt").read_text()
    client = ScryfallClient(
        transport=FakeTransport(
            load("collection_sample_deck.json"), {"data": [], "has_more": False},
            load("collection_basic.json"), {"data": [], "has_more": False},
        ),
        sleep=lambda _: None,
    )
    result = api.suggest_additions(
        text, target=3, limit=5, client=client,
        edhrec_client=FakeEdhrec(load("edhrec_commander.json")),
    )
    for s in result["suggestions"]:
        assert s["fills"], "every suggestion must name the gap it fills"
        assert "reason" in s and s["reason"]
        assert "synergy" in s or "inclusion_rate" in s


def test_game_changers_are_excluded_below_their_bracket_allowance():
    """A bracket-2 deck must never be offered a Game Changer."""
    text = (FIXTURES / "sample_deck.txt").read_text()
    # Mark Sol Ring as a Game Changer via the search response.
    client = ScryfallClient(
        transport=FakeTransport(
            load("collection_sample_deck.json"),
            {"data": [{"object": "card", "name": "Rhystic Study"}], "has_more": False},
            load("collection_basic.json"),
            {"data": [{"object": "card", "name": "Rhystic Study"}], "has_more": False},
        ),
        sleep=lambda _: None,
    )
    result = api.suggest_additions(
        text, target=2, limit=20, client=client,
        edhrec_client=FakeEdhrec(load("edhrec_commander.json")),
    )
    assert all(not s["is_game_changer"] for s in result["suggestions"])


def test_suggest_degrades_when_edhrec_is_unavailable():
    """Losing the idea source must not lose the gap analysis."""
    from mtgpt.errors import SourceUnavailable

    class DeadEdhrec:
        def commander(self, name, *, variant=None):
            raise SourceUnavailable("EDHREC", "down")

    text = (FIXTURES / "sample_deck.txt").read_text()
    client = ScryfallClient(
        transport=FakeTransport(
            load("collection_sample_deck.json"), {"data": [], "has_more": False}
        ),
        sleep=lambda _: None,
    )
    result = api.suggest_additions(
        text, target=3, limit=5, client=client, edhrec_client=DeadEdhrec()
    )
    assert result["gaps"], "gap analysis must survive an EDHREC outage"
    assert result["suggestions"] == []
    assert "EDHREC" in result["degraded"]
