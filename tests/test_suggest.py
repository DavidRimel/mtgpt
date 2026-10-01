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


#: A candidate pool the shipped tests did not have. `collection_basic.json` held
#: only Sol Ring and Atraxa, so all 35 EDHREC candidates but Sol Ring dropped as
#: unresolvable, Sol Ring was already in the deck, and `suggestions` came back
#: empty — which made every `for s in result["suggestions"]` assertion vacuous.
#: This fixture resolves eight real EDHREC candidates for Atraxa plus two cards
#: EDHREC does not list (Lightning Bolt, Black Lotus) for tests that inject them.
CANDIDATES = "collection_suggest_candidates.json"

#: The candidates in CANDIDATES that survive every filter, and the gap each one
#: fills. Asserted by name so a filter that stops working is a failure, not a
#: silently shorter list.
EXPECTED_SUGGESTIONS = {
    "Farseek": "ramp",
    "Tezzeret's Gambit": "draw",
    "Path to Exile": "spot_removal",
    "Farewell": "sweeper",
    "Teferi's Protection": "protection",
}


def suggest_client(*, game_changers=None):
    """A client for one `suggest_additions` call over the sample deck.

    Four requests, in order: resolve the deck, fetch Game Changers for the deck,
    resolve the EDHREC candidates, fetch Game Changers for the candidates.
    """
    gc = game_changers if game_changers is not None else {"data": [], "has_more": False}
    return ScryfallClient(
        transport=FakeTransport(
            load("collection_sample_deck.json"), gc, load(CANDIDATES), gc,
        ),
        sleep=lambda _: None,
    )


def with_extra_candidates(payload, *cardviews):
    """A copy of an EDHREC payload with extra candidates appended.

    EDHREC only recommends on-colour, legal cards, so a filter for the opposite
    cannot be exercised by the recorded fixture alone. Injecting deliberately is
    what makes deleting the filter a test failure.
    """
    import copy

    out = copy.deepcopy(payload)
    out["container"]["json_dict"]["cardlists"].append(
        {"header": "High Synergy Cards", "cardviews": list(cardviews)}
    )
    return out


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
    """Two candidates are injected that must each be rejected by exactly one
    filter, so deleting either filter fails this test rather than shortening a
    list nobody asserts on:

    * Lightning Bolt is legal but red, and Atraxa's identity is {W}{U}{B}{G}.
      It fills the spot-removal gap, so only the colour filter stops it.
    * Black Lotus is banned but colourless, so its identity is inside every
      commander's. It fills the ramp gap, so only the legality filter stops it.
    """
    text = (FIXTURES / "sample_deck.txt").read_text()
    payload = with_extra_candidates(
        load("edhrec_commander.json"),
        {"name": "Lightning Bolt", "synergy": 0.95, "num_decks": 90, "potential_decks": 100},
        {"name": "Black Lotus", "synergy": 0.94, "num_decks": 90, "potential_decks": 100},
    )
    result = api.suggest_additions(
        text, target=3, limit=20, client=suggest_client(),
        edhrec_client=FakeEdhrec(payload),
    )
    names = [s["name"] for s in result["suggestions"]]
    assert names, "the candidate pool must not be empty, or this test asserts nothing"
    assert "Lightning Bolt" not in names, "off-colour candidate was suggested"
    assert "Black Lotus" not in names, "banned candidate was suggested"

    allowed = set(result["color_identity"])
    for s in result["suggestions"]:
        assert set(s["color_identity"]) <= allowed, s["name"]
        assert s["legal_commander"] == "legal", s["name"]


def test_suggestions_never_include_a_card_already_in_the_deck():
    """Cultivate and Swords to Plowshares are both in the sample deck AND in the
    EDHREC fixture's Top Cards list, and both resolve through the candidate
    fixture. They are the cards the already-in-deck filter has to catch; without
    them in the pool, deleting the filter changed nothing.
    """
    text = (FIXTURES / "sample_deck.txt").read_text()
    result = api.suggest_additions(
        text, target=3, limit=20, client=suggest_client(),
        edhrec_client=FakeEdhrec(load("edhrec_commander.json")),
    )
    names = {s["name"] for s in result["suggestions"]}
    assert names, "the candidate pool must not be empty, or this test asserts nothing"
    present = {"Sol Ring", "Cultivate", "Swords to Plowshares", "Wrath of God", "Forest"}
    assert not (names & present), sorted(names & present)
    assert names == set(EXPECTED_SUGGESTIONS), sorted(names)


def test_each_suggestion_states_the_gap_it_fills_and_its_evidence():
    """Counterspell is in the EDHREC fixture's Top Cards, resolves, is on-colour
    and is legal — and counterspells are not one of the audit's target bands, so
    it fills no gap. It is the card the fills-a-gap filter has to drop.

    `limit` is deliberately above the surviving-candidate count: a suggestion
    with no `fills` sorts last, so a tight limit would truncate it away and hide
    the filter's removal rather than failing.
    """
    text = (FIXTURES / "sample_deck.txt").read_text()
    result = api.suggest_additions(
        text, target=3, limit=20, client=suggest_client(),
        edhrec_client=FakeEdhrec(load("edhrec_commander.json")),
    )
    assert result["suggestions"], "the pool must not be empty, or this asserts nothing"
    assert "Counterspell" not in [s["name"] for s in result["suggestions"]]
    for s in result["suggestions"]:
        assert s["fills"], f"{s['name']} names no gap"
        assert EXPECTED_SUGGESTIONS[s["name"]] in s["fills"], s
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


# --- A Game Changer commander counts against the allowance (Critical 3) ------

_TERGRID_DECK = """Commander
1 Tergrid, God of Fright

Deck
1 Rhystic Study
1 Smothering Tithe
1 Sol Ring
36 Swamp
"""

_TERGRID = _card_json(
    "Tergrid, God of Fright", cmc=5.0, mana_cost="{3}{B}{B}", color_identity=["B"],
    type_line="Legendary Creature — God",
    oracle_text=(
        "Menace\nWhenever an opponent sacrifices a nontoken permanent or discards "
        "a permanent card, you may put that card onto the battlefield under your "
        "control."
    ),
)
_SMOTHERING_TITHE = _card_json(
    "Smothering Tithe", cmc=4.0, mana_cost="{3}{W}", color_identity=["W"],
    type_line="Enchantment",
    oracle_text=(
        "Whenever an opponent draws a card, that player may pay {2}. If the player "
        "doesn't, you create a Treasure token."
    ),
)
_SOL_RING = _card_json(
    "Sol Ring", cmc=1.0, mana_cost="{1}", color_identity=[], type_line="Artifact",
    oracle_text="{T}: Add {C}{C}.",
)
_SWAMP = _card_json(
    "Swamp", cmc=0.0, mana_cost="", color_identity=["B"],
    type_line="Basic Land — Swamp", oracle_text="({T}: Add {B}.)",
)
#: A mono-black Game Changer that fills one of this deck's gaps. The candidate
#: has to be BOTH flagged and gap-filling, or the fills-a-gap filter drops it
#: first and the Game Changer budget is never consulted — a tutor would not do,
#: because tutors are not one of the audit's target bands.
_ORCISH_BOWMASTERS = _card_json(
    "Orcish Bowmasters", cmc=2.0, mana_cost="{1}{B}", color_identity=["B"],
    type_line="Creature — Orc Archer",
    oracle_text=(
        "Flash\nWhen Orcish Bowmasters enters and whenever an opponent draws a card "
        "except the first one they draw in each of their draw steps, Orcish Bowmasters "
        "deals 1 damage to any target. Then amass Orcs 1."
    ),
)
_NIGHT_S_WHISPER = _card_json(
    "Night's Whisper", cmc=2.0, mana_cost="{1}{B}", color_identity=["B"],
    type_line="Sorcery", oracle_text="You draw two cards and you lose 2 life.",
)


def test_a_game_changer_commander_counts_against_the_allowance():
    """Three Commander-legal cards are on the live Game Changers list: Tergrid,
    God of Fright, Grand Arbiter Augustin IV, and Braids, Cabal Minion.

    Regression: `gc_in_deck` summed `deck.cards` only, while `brackets.check`
    scans `deck.cards + commanders`. With a Game Changer commander plus two in
    the 99 at bracket 3 (allowance 3), the true count is already 3 but suggest
    computed a budget of 3 - 2 = 1 and offered a fourth — which `bracket` then
    declared non-compliant. The tool contradicted itself.

    Night's Whisper fills the draw gap and is not flagged, so it is still offered:
    the fix must spend the budget correctly, not stop suggesting.
    """
    gc_response = {
        "data": [
            {"object": "card", "name": "Tergrid, God of Fright"},
            {"object": "card", "name": "Rhystic Study"},
            {"object": "card", "name": "Smothering Tithe"},
            {"object": "card", "name": "Orcish Bowmasters"},
        ],
        "has_more": False,
    }
    deck_payloads = {
        "data": [_TERGRID, _RHYSTIC_STUDY, _SMOTHERING_TITHE, _SOL_RING, _SWAMP],
        "not_found": [],
    }
    payload = _edhrec_payload(
        {"name": "Orcish Bowmasters", "synergy": 0.9, "num_decks": 90, "potential_decks": 100},
        {"name": "Night's Whisper", "synergy": 0.5, "num_decks": 50, "potential_decks": 100},
    )
    client = ScryfallClient(
        transport=FakeTransport(
            deck_payloads, gc_response,
            {"data": [_ORCISH_BOWMASTERS, _NIGHT_S_WHISPER], "not_found": []}, gc_response,
        ),
        sleep=lambda _: None,
    )
    result = api.suggest_additions(
        _TERGRID_DECK, target=3, limit=20, client=client,
        edhrec_client=FakeEdhrec(payload),
    )
    names = [s["name"] for s in result["suggestions"]]
    assert "Orcish Bowmasters" not in names, (
        "the allowance of 3 is already met by Tergrid + Rhystic Study + Smothering "
        f"Tithe, so no Game Changer may be suggested; got {names}"
    )
    assert not [s for s in result["suggestions"] if s["is_game_changer"]]
    # The non-flagged candidate still comes through.
    assert "Night's Whisper" in names, names


def test_suggest_and_bracket_agree_after_a_game_changer_suggestion():
    """The contradiction stated as an invariant: whatever suggest proposes, the
    deck plus that card must still satisfy the bracket it was asked about."""
    from mtgpt.brackets import RULES

    gc_response = {
        "data": [
            {"object": "card", "name": "Tergrid, God of Fright"},
            {"object": "card", "name": "Rhystic Study"},
            {"object": "card", "name": "Smothering Tithe"},
            {"object": "card", "name": "Orcish Bowmasters"},
        ],
        "has_more": False,
    }
    deck_payloads = {
        "data": [_TERGRID, _RHYSTIC_STUDY, _SMOTHERING_TITHE, _SOL_RING, _SWAMP],
        "not_found": [],
    }
    payload = _edhrec_payload(
        {"name": "Orcish Bowmasters", "synergy": 0.9, "num_decks": 90, "potential_decks": 100},
    )
    client = ScryfallClient(
        transport=FakeTransport(
            deck_payloads, gc_response,
            {"data": [_ORCISH_BOWMASTERS], "not_found": []}, gc_response,
        ),
        sleep=lambda _: None,
    )
    result = api.suggest_additions(
        _TERGRID_DECK, target=3, limit=20, client=client,
        edhrec_client=FakeEdhrec(payload),
    )
    already = 3  # Tergrid (command zone) + Rhystic Study + Smothering Tithe
    proposed = sum(1 for s in result["suggestions"] if s["is_game_changer"])
    assert already + proposed <= RULES[3].game_changers_max
