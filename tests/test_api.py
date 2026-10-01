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
    """A card EDHREC lists but Scryfall cannot resolve must not reach the user.

    Asserted as equality, not `<=`. The shipped version compared against a
    three-name allowlist that the fixture could never produce, so `names` was
    the empty set and the subset assertion was vacuously true — it still passed
    when the skip was mutated to substitute a different resolved card.

    Scryfall here returns Cultivate (a real candidate in the fixture's Top Cards)
    and Kodama's Reach (which the fixture does not list), so the output must be
    exactly Cultivate: the unlisted card is not smuggled in, and the listed
    candidates Scryfall did not return are dropped.
    """
    edh = FakeEdhrec(json.loads((FIXTURES / "edhrec_commander.json").read_text()))
    client = client_for(load("search_results.json"), NO_GAME_CHANGERS)
    result = api.commander_synergy(
        "Atraxa, Praetors' Voice", limit=40, client=client, edhrec_client=edh
    )
    names = {c["name"] for c in result["cards"]}
    assert names == {"Cultivate"}, names
    # Named explicitly: EDHREC lists Rhystic Study for this commander, Scryfall
    # did not verify it here, so it must be absent from the output.
    assert "Rhystic Study" not in names
    assert "Kodama's Reach" not in names


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


def test_candidates_carry_the_real_game_changer_flag():
    """Regression: commander_synergy once omitted game_changers=, so every
    candidate reported False and the bracket filter was unreachable."""
    edh = FakeEdhrec(json.loads((FIXTURES / "edhrec_commander.json").read_text()))
    # Scryfall resolves Rhystic Study, and the Game Changers search returns it.
    rhystic = {
        "object": "card", "name": "Rhystic Study", "cmc": 3.0,
        "type_line": "Enchantment",
        "oracle_text": "Whenever an opponent casts a spell, that player may pay {1}. "
                       "If the player doesn't, you may draw a card.",
        "mana_cost": "{2}{U}", "color_identity": ["U"], "colors": ["U"],
        "layout": "normal", "keywords": [], "legalities": {"commander": "legal"},
        "prices": {"usd": "30.00"},
    }
    client = ScryfallClient(
        transport=FakeTransport(
            {"data": [rhystic], "not_found": []},
            {"data": [{"object": "card", "name": "Rhystic Study"}], "has_more": False},
        ),
        sleep=lambda _: None,
    )
    result = api.commander_synergy(
        "Atraxa, Praetors' Voice", limit=40, client=client, edhrec_client=edh
    )
    flags = {c["name"]: c["is_game_changer"] for c in result["cards"]}
    assert flags.get("Rhystic Study") is True, flags


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


# --- Game Changer threading (Critical 1) ------------------------------------

#: Scryfall's `is:gamechanger` search response, containing Rhystic Study only.
GAME_CHANGERS_RESPONSE = {
    "data": [{"object": "card", "name": "Rhystic Study"}],
    "has_more": False,
}

_RHYSTIC_PAYLOAD = {
    "object": "card", "name": "Rhystic Study", "cmc": 3.0,
    "type_line": "Enchantment",
    "oracle_text": "Whenever an opponent casts a spell, that player may pay {1}. "
                   "If the player doesn't, you may draw a card.",
    "mana_cost": "{2}{U}", "color_identity": ["U"], "colors": ["U"],
    "layout": "normal", "keywords": [], "legalities": {"commander": "legal"},
    "prices": {"usd": "30.00"},
}


def test_lookup_card_distinguishes_a_game_changer_from_an_ordinary_card():
    """`mtgpt card` is what SKILL.md tells the agent to vet candidates with.

    Regression: `lookup_card` called `card_from_json(payload)` with no
    `game_changers=`, so the name was tested against an empty frozenset and
    every card reported `is_game_changer: false`. At bracket 2, whose allowance
    is 0, the agent read that false and certified a Game Changer as legal.

    Two cards, one injected list: a `False` alone proves nothing, because the
    broken version returned `False` for everything. The flag has to discriminate.
    """
    flagged = api.lookup_card(
        "Rhystic Study",
        client=client_for({"data": [_RHYSTIC_PAYLOAD]}, GAME_CHANGERS_RESPONSE),
    )
    assert flagged["is_game_changer"] is True, flagged

    ordinary = api.lookup_card(
        "Sol Ring",
        client=client_for(load("collection_basic.json"), GAME_CHANGERS_RESPONSE),
    )
    assert ordinary["is_game_changer"] is False, ordinary


def test_search_cards_carries_the_game_changer_flag():
    """Regression: `search_cards` omitted `game_changers=` too, so a candidate
    found by `mtgpt search` never reported as a Game Changer."""
    client = client_for({"data": [_RHYSTIC_PAYLOAD], "has_more": False},
                        GAME_CHANGERS_RESPONSE)
    result = api.search_cards("is:gamechanger", client=client)
    assert [c["is_game_changer"] for c in result["cards"]] == [True]


def test_lookup_card_degrades_rather_than_failing_on_a_game_changers_outage():
    """The card data is still worth returning; only the flag is unverified."""
    from mtgpt.errors import SourceUnavailable

    class Outage:
        def __init__(self):
            self.inner = client_for({"data": [_RHYSTIC_PAYLOAD]})

        def collection(self, names, **kw):
            return self.inner.collection(names, **kw)

        def game_changers(self):
            raise SourceUnavailable("Scryfall Game Changers", "404")

    result = api.lookup_card("Rhystic Study", client=Outage())
    assert result["name"] == "Rhystic Study"
    assert result["is_game_changer"] is False


def test_card_from_json_requires_game_changers_to_be_passed():
    """The structural half of the fix.

    A default of `frozenset()` made every omission a silent `False`. Requiring
    the argument turns the same mistake into a TypeError at the call site, which
    is what converts this bug class into an import/test-time failure.
    """
    from mtgpt.scryfall import card_from_json

    with pytest.raises(TypeError):
        card_from_json(_RHYSTIC_PAYLOAD)


# --- Goldfish ----------------------------------------------------------------

GO_WIDE = {"archetype": "go_wide"}


def test_goldfish_simulates_an_invalid_deck_and_warns():
    report = api.goldfish(deck_text(), GO_WIDE, games=5, client=deck_client())
    assert report["games"] == 5
    assert {"setup", "commander", "thing", "disruption", "win", "notes"} <= set(report)
    assert report["warnings"], "the 41-card sample deck should fail validation"


def test_goldfish_compare_resolves_both_decks():
    client = client_for(load("collection_sample_deck.json"), NO_GAME_CHANGERS,
                        load("collection_sample_deck.json"), NO_GAME_CHANGERS)
    result = api.goldfish_compare(deck_text(), deck_text(), GO_WIDE, games=5, client=client)
    assert result["before"]["commander"] == result["after"]["commander"]
    assert "warnings" in result["after"]


def test_pilot_game_round_trips_through_json():
    started = api.goldfish_new(deck_text(), GO_WIDE, seed=2, client=deck_client())
    view = started["view"]
    assert view["turn"] == 1
    assert {"pass": True} in view["legal_actions"]
    assert "library" not in view, "a pilot must not see the library order"
    state = json.loads(json.dumps(started["state"]))
    assert api.goldfish_step(state, {"pass": True})["view"]["turn"] == 2


def test_pilot_game_n_matches_auto_game_n():
    """Pilot game `game` of `seed` is dealt exactly as auto game `game`."""
    from mtgpt.goldfish.engine import new_game, prepare, to_dict
    from mtgpt.scryfall import resolve
    from mtgpt.deckparse import parse

    client = deck_client()
    pilot_result = api.goldfish_new(deck_text(), GO_WIDE, seed=1, game=2, client=client)
    pilot_state = pilot_result["state"]

    resolved = resolve(parse(deck_text()), client=deck_client())
    prepared = prepare(resolved, GO_WIDE)
    auto_state = new_game(prepared, seed="1-2")
    auto_state_dict = to_dict(auto_state)

    assert pilot_state["hand"] == auto_state_dict["hand"]
    assert pilot_state["library"] == auto_state_dict["library"]
    assert pilot_state["rng"] == auto_state_dict["rng"]
    assert pilot_state["dice"] == auto_state_dict["dice"]


def test_pilot_illegal_action_is_data():
    from mtgpt.goldfish.engine import IllegalAction

    started = api.goldfish_new(deck_text(), GO_WIDE, client=deck_client())
    with pytest.raises(IllegalAction) as err:
        api.goldfish_step(started["state"], {"cast": "Black Lotus"})
    payload = api.error_payload(err.value)
    assert payload["action"] == {"cast": "Black Lotus"}
    assert {"pass": True} in payload["legal_actions"]


def test_goal_error_payload_names_the_field():
    from mtgpt.goal import GoalError

    with pytest.raises(GoalError) as err:
        api.goldfish(deck_text(), {"archetype": "elves"}, games=1, client=deck_client())
    payload = api.error_payload(err.value)
    assert (payload["type"], payload["field"], payload["values"]) == (
        "GoalError", "archetype", ["elves"])
