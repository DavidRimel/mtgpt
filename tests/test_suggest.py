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
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


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


def _card_json(name, *, cmc, mana_cost, color_identity, oracle_text, type_line="Instant"):
    """A minimal, valid Scryfall card payload for a hand-built candidate."""
    return {
        "object": "card",
        "name": name,
        "cmc": cmc,
        "type_line": type_line,
        "oracle_text": oracle_text,
        "mana_cost": mana_cost,
        "color_identity": color_identity,
        "colors": color_identity,
        "layout": "normal",
        "keywords": [],
        "legalities": {"commander": "legal"},
        "prices": {"usd": "1.00"},
    }


_RHYSTIC_STUDY = _card_json(
    "Rhystic Study", cmc=3.0, mana_cost="{2}{U}", color_identity=["U"],
    type_line="Enchantment",
    oracle_text=(
        "Whenever an opponent casts a spell, that player may pay {1}. "
        "If the player doesn't, you may draw a card."
    ),
)

_MYSTIC_REMORA = _card_json(
    "Mystic Remora", cmc=1.0, mana_cost="{U}", color_identity=["U"],
    type_line="Enchantment",
    oracle_text=(
        "Whenever an opponent casts their second spell each turn, you may draw "
        "a card unless that player pays {4}."
    ),
)

_SYLVAN_LIBRARY = _card_json(
    "Sylvan Library", cmc=1.0, mana_cost="{G}", color_identity=["G"],
    type_line="Enchantment",
    oracle_text="At the beginning of your draw step, draw two additional cards.",
)

_LIGHTNING_BOLT = _card_json(
    "Lightning Bolt", cmc=1.0, mana_cost="{R}", color_identity=["R"],
    oracle_text="Lightning Bolt deals 3 damage to any target.",
)


def _edhrec_payload(*cardviews, header="High Synergy Cards"):
    return {"container": {"json_dict": {"cardlists": [
        {"header": header, "cardviews": list(cardviews)},
    ]}}}


def test_a_real_game_changer_is_excluded_at_bracket_two():
    """Bracket 2 allows zero Game Changers, so a flagged candidate must be
    dropped even though it fills this deck's draw gap.

    Regression: before `commander_synergy` passed `game_changers=` through,
    `is_game_changer` was always False and this filter never fired.
    """
    text = (FIXTURES / "sample_deck.txt").read_text()
    payload = _edhrec_payload(
        {"name": "Rhystic Study", "synergy": 0.5, "num_decks": 50, "potential_decks": 100},
    )
    client = ScryfallClient(
        transport=FakeTransport(
            load("collection_sample_deck.json"), {"data": [], "has_more": False},
            {"data": [_RHYSTIC_STUDY], "not_found": []},
            {"data": [{"object": "card", "name": "Rhystic Study"}], "has_more": False},
        ),
        sleep=lambda _: None,
    )
    result = api.suggest_additions(
        text, target=2, limit=20, client=client, edhrec_client=FakeEdhrec(payload)
    )
    assert all(not s["is_game_changer"] for s in result["suggestions"])
    assert "Rhystic Study" not in [s["name"] for s in result["suggestions"]]


def test_game_changer_budget_is_consumed_across_suggestions():
    """Each Game Changer suggestion must consume the bracket's allowance.

    Bracket 3 allows 3; the deck already has 2 (Sol Ring and Cultivate are
    marked as Game Changers via the search response here, purely to seed the
    count — not a claim about the real list). Three flagged, on-colour
    candidates that all fill the draw gap are offered; only one more fits the
    remaining budget of 1.

    Regression: before the fix, `gc_in_deck` was never incremented, so the
    static check `gc_in_deck >= gc_allowance` (2 >= 3) never fired and all
    three would have been suggested.
    """
    text = (FIXTURES / "sample_deck.txt").read_text()
    payload = _edhrec_payload(
        {"name": "Rhystic Study", "synergy": 0.9, "num_decks": 90, "potential_decks": 100},
        {"name": "Mystic Remora", "synergy": 0.8, "num_decks": 80, "potential_decks": 100},
        {"name": "Sylvan Library", "synergy": 0.7, "num_decks": 70, "potential_decks": 100},
    )
    client = ScryfallClient(
        transport=FakeTransport(
            load("collection_sample_deck.json"),
            {"data": [{"object": "card", "name": "Sol Ring"},
                      {"object": "card", "name": "Cultivate"}], "has_more": False},
            {"data": [_RHYSTIC_STUDY, _MYSTIC_REMORA, _SYLVAN_LIBRARY], "not_found": []},
            {"data": [{"object": "card", "name": "Rhystic Study"},
                      {"object": "card", "name": "Mystic Remora"},
                      {"object": "card", "name": "Sylvan Library"}], "has_more": False},
        ),
        sleep=lambda _: None,
    )
    result = api.suggest_additions(
        text, target=3, limit=20, client=client, edhrec_client=FakeEdhrec(payload)
    )
    gc_suggested = [s["name"] for s in result["suggestions"] if s["is_game_changer"]]
    assert len(gc_suggested) == 1, gc_suggested


def test_an_off_colour_candidate_is_excluded():
    """The shipped colour test passed even with the filter deleted, because
    EDHREC only recommends on-colour cards for the fixture it used. Inject an
    off-colour candidate deliberately: Lightning Bolt fills this deck's
    spot-removal gap and would otherwise be suggested, but it is red and
    Atraxa's identity is {W}{U}{B}{G}.
    """
    text = (FIXTURES / "sample_deck.txt").read_text()
    payload = _edhrec_payload(
        {"name": "Lightning Bolt", "synergy": 0.9, "num_decks": 90, "potential_decks": 100},
    )
    client = ScryfallClient(
        transport=FakeTransport(
            load("collection_sample_deck.json"), {"data": [], "has_more": False},
            {"data": [_LIGHTNING_BOLT], "not_found": []},
            {"data": [], "has_more": False},
        ),
        sleep=lambda _: None,
    )
    result = api.suggest_additions(
        text, target=3, limit=20, client=client, edhrec_client=FakeEdhrec(payload)
    )
    assert "Lightning Bolt" not in [s["name"] for s in result["suggestions"]]


def test_a_game_changers_outage_is_named_in_degraded():
    """The bracket allowance cannot be enforced without the list, and the
    caller must be able to tell. Reporting degraded: [] here would claim full
    health while every candidate silently reports is_game_changer=False.
    """
    text = (FIXTURES / "sample_deck.txt").read_text()
    payload = _edhrec_payload(
        {"name": "Rhystic Study", "synergy": 0.5, "num_decks": 50, "potential_decks": 100},
    )
    client = ScryfallClient(
        transport=FakeTransport(
            load("collection_sample_deck.json"), {"data": [], "has_more": False},
            {"data": [_RHYSTIC_STUDY], "not_found": []},
            # The Game Changers search itself fails, after candidates resolved fine.
            OSError("Game Changers search down"),
        ),
        sleep=lambda _: None,
    )
    result = api.suggest_additions(
        text, target=2, limit=5, client=client, edhrec_client=FakeEdhrec(payload)
    )
    assert any("Game Changer" in d for d in result["degraded"]), result["degraded"]
    # The gap analysis must still be returned.
    assert result["gaps"]


def test_no_degradation_is_reported_when_the_list_is_available():
    """The healthy path must not cry wolf."""
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
    assert result["degraded"] == []


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
