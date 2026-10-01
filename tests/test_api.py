# tests/test_api.py
import json
import pathlib

import pytest

from mtgpt import api
from mtgpt.errors import DeckStructureError, UnresolvedCards
from mtgpt.scryfall import ScryfallClient

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def deck_text():
    return (FIXTURES / "sample_deck.txt").read_text()


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, payload=None):
        self.calls.append((url, payload))
        if not self.responses:
            raise AssertionError(f"unexpected extra request to {url}")
        return self.responses.pop(0)


NO_GAME_CHANGERS = {"data": [], "has_more": False}


def client_for(*responses):
    return ScryfallClient(transport=FakeTransport(*responses), sleep=lambda _: None)


def deck_client():
    return client_for(load("collection_sample_deck.json"), NO_GAME_CHANGERS)


def test_lookup_card_returns_card_data_and_tags():
    result = api.lookup_card("Sol Ring", client=deck_client())
    assert result["name"] == "Sol Ring"
    assert result["mana_value"] == 1.0
    assert result["legal_commander"] == "legal"
    assert "ramp" in result["functions"]
    assert result["is_land"] is False


def test_lookup_card_raises_for_an_invented_name():
    client = client_for({"data": [], "not_found": [{"name": "Fake Card"}]})
    with pytest.raises(UnresolvedCards):
        api.lookup_card("Fake Card", client=client)


def test_search_cards_returns_candidates_with_tags():
    client = client_for(load("search_results.json"), NO_GAME_CHANGERS)
    result = api.search_cards("o:'search your library for' t:sorcery c:g", client=client)
    assert result["count"] == 2
    assert [c["name"] for c in result["cards"]] == ["Cultivate", "Kodama's Reach"]
    # Candidates arrive pre-tagged so the agent can confirm they fill the gap.
    assert "ramp" in result["cards"][0]["functions"]
    assert "tutor" not in result["cards"][0]["functions"]


def test_search_cards_respects_limit():
    client = client_for(load("search_results.json"), NO_GAME_CHANGERS)
    assert api.search_cards("c:g", limit=1, client=client)["count"] == 1


def test_classify_cards_maps_names_to_tags():
    result = api.classify_cards(["Sol Ring", "Cultivate"], client=deck_client())
    assert "ramp" in result["Sol Ring"]
    # The land fetch must be ramp, not a tutor — this drives the bracket verdict.
    assert "ramp" in result["Cultivate"]
    assert "tutor" not in result["Cultivate"]


def test_read_deck_needs_no_network():
    result = api.read_deck(deck_text())
    assert result["commanders"] == [{"qty": 1, "name": "Atraxa, Praetors' Voice"}]
    assert result["total_cards"] == 41
    assert {"qty": 36, "name": "Forest"} in result["entries"]
    # Maybeboard is excluded.
    assert all(e["name"] != "Mana Crypt" for e in result["entries"])


def test_read_deck_raises_on_garbage():
    with pytest.raises(DeckStructureError):
        api.read_deck("this is not a decklist")


def test_validate_deck_returns_only_legality():
    result = api.validate_deck(deck_text(), client=deck_client())
    assert result["legal"] is False  # the fixture deck is 41 cards, not 100
    assert "deck_size" in [v["code"] for v in result["violations"]]
    assert "audit" not in result


def test_audit_deck_returns_only_measurements():
    result = api.audit_deck(deck_text(), client=deck_client())
    assert result["land_count"] == 36
    assert "categories" in result and "pips" in result
    assert "violations" not in result


def test_bracket_check_returns_only_the_verdict():
    result = api.bracket_check(deck_text(), target=3, client=deck_client())
    assert result["target"] == 3
    assert result["target_name"] == "Upgraded"
    assert "deferred_checks" in result
    assert "categories" not in result


def test_full_report_composes_every_section():
    result = api.full_report(deck_text(), target=3, client=deck_client())
    for key in ("commanders", "violations", "audit", "bracket", "tags"):
        assert key in result


def test_render_report_surfaces_per_card_tags():
    """Classification is heuristic; the design's mitigation is visible tags."""
    rendered = api.render_report(api.full_report(deck_text(), target=3, client=deck_client()))
    assert "CARD TAGS" in rendered
    assert "Cultivate" in rendered
    assert "ramp" in rendered.lower()


def test_operations_are_independent():
    """bracket_check must work without audit_deck ever being called."""
    assert api.bracket_check(deck_text(), client=deck_client())["target"] == 3


class FakeEdhrec:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def commander(self, name, *, variant=None):
        self.calls.append((name, variant))
        return self.payload


def test_commander_synergy_verifies_candidates_and_tags_them():
    edh = FakeEdhrec(json.loads((FIXTURES / "edhrec_commander.json").read_text()))
    client = client_for(load("collection_basic.json"), NO_GAME_CHANGERS)
    result = api.commander_synergy(
        "Atraxa, Praetors' Voice", limit=3, client=client, edhrec_client=edh
    )
    assert result["commander"] == "Atraxa, Praetors' Voice"
    for card in result["cards"]:
        # Evidence travels with the candidate.
        assert "synergy" in card and "inclusion_rate" in card
        # And it is a verified card with function tags.
        assert "functions" in card and card["legal_commander"]


def test_commander_synergy_drops_candidates_scryfall_cannot_verify():
    """A card EDHREC lists but Scryfall cannot resolve must not reach the user."""
    edh = FakeEdhrec(json.loads((FIXTURES / "edhrec_commander.json").read_text()))
    # Scryfall returns only Sol Ring, whatever EDHREC suggested.
    client = client_for(load("collection_basic.json"), NO_GAME_CHANGERS)
    result = api.commander_synergy(
        "Atraxa, Praetors' Voice", limit=40, client=client, edhrec_client=edh
    )
    names = {c["name"] for c in result["cards"]}
    assert names <= {"Sol Ring", "Atraxa, Praetors' Voice", "Dockside Extortionist"}


def test_commander_synergy_survives_one_unresolvable_candidate():
    """One bad EDHREC name must not zero out the good candidates."""
    edh = FakeEdhrec(json.loads((FIXTURES / "edhrec_commander.json").read_text()))
    # "Cultivate" is a real EDHREC candidate for this commander (it appears in
    # the fixture's "Top Cards" list), so it is the one that must survive.
    good = load("search_results.json")
    # Scryfall's real shape: good cards in data, unmatched ones in not_found.
    partial = {"data": good["data"], "not_found": [{"name": "Some Name Scryfall Lacks"}]}
    client = client_for(partial, NO_GAME_CHANGERS)
    result = api.commander_synergy(
        "Atraxa, Praetors' Voice", limit=40, client=client, edhrec_client=edh
    )
    # The resolvable cards still come back rather than the call aborting.
    assert result["count"] > 0


def test_commander_synergy_passes_the_variant_through():
    edh = FakeEdhrec(json.loads((FIXTURES / "edhrec_commander.json").read_text()))
    client = client_for(load("collection_basic.json"), NO_GAME_CHANGERS)
    api.commander_synergy(
        "Atraxa, Praetors' Voice", variant="upgraded", limit=1,
        client=client, edhrec_client=edh,
    )
    assert edh.calls == [("Atraxa, Praetors' Voice", "upgraded")]


def test_commander_themes_returns_themes_and_bracket_spread():
    edh = FakeEdhrec(json.loads((FIXTURES / "edhrec_commander.json").read_text()))
    result = api.commander_themes("Atraxa, Praetors' Voice", edhrec_client=edh)
    assert result["themes"] and "label" in result["themes"][0]
    assert result["bracket_distribution"]


def test_error_payload_carries_machine_readable_detail():
    from mtgpt.errors import SourceUnavailable, UnresolvedCards

    unresolved = api.error_payload(UnresolvedCards(["Fake Card"]))
    assert unresolved["type"] == "UnresolvedCards"
    assert unresolved["names"] == ["Fake Card"]

    outage = api.error_payload(SourceUnavailable("EDHREC", "down"))
    assert outage["type"] == "SourceUnavailable"
    assert outage["source"] == "EDHREC"
