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


# --- find: Scryfall Tagger --------------------------------------------------


def tagger_payload(name, type_line, oracle_text, **extra):
    return {
        "name": name,
        "cmc": 2.0,
        "type_line": type_line,
        "oracle_text": oracle_text,
        "mana_cost": "{1}{G}",
        "color_identity": ["G"],
        "legalities": {"commander": "legal"},
        **extra,
    }


def test_find_cards_builds_an_otag_query_scoped_to_identity_and_commander():
    transport = FakeTransport(load("search_results.json"), NO_GAME_CHANGERS)
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    result = api.find_cards("ramp", identity="wubg", limit=5, client=client)
    assert result["otag"] == "ramp"
    assert result["query"] == "otag:ramp legal:commander ci:wubg"
    url = transport.calls[0][0]
    assert "otag%3Aramp" in url
    assert "legal%3Acommander" in url
    # order=edhrec, so the most-played candidates lead.
    assert "order=edhrec" in url


def test_find_cards_labels_its_source_and_the_tag_used():
    client = client_for(load("search_results.json"), NO_GAME_CHANGERS)
    result = api.find_cards("ramp", client=client)
    for card in result["cards"]:
        assert card["source"] == "scryfall-tagger"
        assert card["otag"] == "ramp"


def test_find_cards_cross_checks_against_our_own_classification():
    client = client_for(load("search_results.json"), NO_GAME_CHANGERS)
    result = api.find_cards("ramp", client=client)
    # Cultivate and Kodama's Reach are ramp by both the community tag and our
    # regex, so agreement is total.
    assert all(c["agrees_with_classify"] for c in result["cards"])
    assert result["recall_estimate"] == 1.0
    assert result["classify_expects"] == ["ramp"]


def test_find_cards_labels_its_number_as_recall_only():
    # The name and the label both exist because an unlabelled 0.67 was read as
    # accuracy and hid a classifier with 135 false positives.
    client = client_for(load("search_results.json"), NO_GAME_CHANGERS)
    result = api.find_cards("ramp", client=client)
    assert "agreement_rate" not in result, "the ambiguous name must not come back"
    assert "recall" in result["measures"]
    assert "blind to false positives" in result["measures"].casefold()


def test_a_disagreement_is_reported_rather_than_resolved():
    # Path to Exile really is otag:ramp — it gives the opponent a basic land —
    # and classify.py really does call it spot removal. Neither is a bug; the
    # caller has to be told so they can judge.
    payload = {"data": [tagger_payload(
        "Path to Exile", "Instant", "Exile target creature. Its controller may "
        "search their library for a basic land card...",
    )], "has_more": False}
    client = client_for(payload, NO_GAME_CHANGERS)
    result = api.find_cards("ramp", client=client)
    assert result["cards"][0]["agrees_with_classify"] is False
    assert result["recall_estimate"] == 0.0


def test_cross_check_can_be_turned_off():
    client = client_for(load("search_results.json"), NO_GAME_CHANGERS)
    result = api.find_cards("ramp", cross_check=False, client=client)
    assert "agrees_with_classify" not in result["cards"][0]
    assert result["recall_estimate"] is None


def test_no_verdict_is_claimed_for_a_tag_classify_cannot_judge():
    client = client_for(load("search_results.json"), NO_GAME_CHANGERS)
    result = api.find_cards("theft", client=client)
    assert result["recall_estimate"] is None
    assert result["classify_expects"] == []
    assert "cross_check_note" in result
    assert "agrees_with_classify" not in result["cards"][0]


def test_find_cards_refuses_an_unverified_tag_without_making_a_request():
    transport = FakeTransport()
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    with pytest.raises(ValueError):
        api.find_cards("mass-land-destruction", client=client)
    assert transport.calls == []


def test_find_cards_reports_no_matches_rather_than_an_outage():
    # Scryfall answers a query matching nothing with 404. For a machine-built
    # query from a verified vocabulary that means "nothing in these colours".
    import urllib.error

    class Failing:
        def __init__(self):
            self.calls = []

        def __call__(self, url, payload=None):
            self.calls.append(url)
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    client = ScryfallClient(transport=Failing(), sleep=lambda _: None)
    result = api.find_cards("extra_turns", identity="w", client=client)
    assert result["count"] == 0
    assert result["cards"] == []
    assert result["recall_estimate"] is None


def test_find_cards_still_raises_when_scryfall_is_actually_down():
    from mtgpt.errors import SourceUnavailable

    def failing(url, payload=None):
        raise OSError("scryfall down")

    client = ScryfallClient(transport=failing, sleep=lambda _: None)
    with pytest.raises(SourceUnavailable):
        api.find_cards("ramp", client=client)


# --- import: Archidekt ------------------------------------------------------


class FakeArchidekt:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def deck(self, identifier):
        self.calls.append(identifier)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def test_import_deck_returns_text_our_parser_understands():
    source = FakeArchidekt(load("archidekt_deck.json"))
    result = api.import_deck(
        "https://archidekt.com/decks/2000000/slug", archidekt_client=source
    )
    assert result["source"] == "archidekt"
    assert result["deck_id"] == "2000000"
    assert result["name"] == "Yuriko, the Tigers Shadow"
    assert result["parsed"]["commanders"] == [
        {"qty": 1, "name": "Yuriko, the Tiger's Shadow"}
    ]
    assert "1 Sol Ring" in result["decklist"]


def test_import_deck_surfaces_the_declared_bracket_as_a_claim():
    source = FakeArchidekt(load("archidekt_deck.json"))
    result = api.import_deck("2000000", archidekt_client=source)
    # The author's claim. `bracket` computes the verdict.
    assert result["declared_bracket"] == 3


def test_import_deck_makes_no_scryfall_request():
    transport = FakeTransport()
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    source = FakeArchidekt(load("archidekt_deck.json"))
    api.import_deck("2000000", client=client, archidekt_client=source)
    assert transport.calls == []


def test_import_deck_propagates_an_outage():
    from mtgpt.errors import SourceUnavailable

    source = FakeArchidekt(SourceUnavailable("Archidekt", "down"))
    with pytest.raises(SourceUnavailable):
        api.import_deck("2000000", archidekt_client=source)


def test_import_deck_rejects_a_moxfield_url_before_fetching():
    source = FakeArchidekt(load("archidekt_deck.json"))
    with pytest.raises(ValueError):
        api.import_deck("https://moxfield.com/decks/abc", archidekt_client=source)
    assert source.calls == []


# --- compare: EDHREC average deck -------------------------------------------


class FakeAverageDeck:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def average_deck(self, name):
        self.calls.append(name)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


AVERAGE_FOR_SAMPLE_DECK = {
    "deck": {
        "commander": ["Atraxa, Praetors' Voice"],
        "cards": {
            # Two the sample deck has, two it does not.
            "Artifact": [["Sol Ring", 1], ["Smothering Tithe", 1]],
            "Sorcery": [["Cultivate", 1], ["Demonic Tutor", 1]],
        },
    }
}


def _card_payload(name, type_line, oracle_text):
    return {
        "name": name,
        "cmc": 2.0,
        "type_line": type_line,
        "oracle_text": oracle_text,
        "mana_cost": "{1}{B}",
        "color_identity": ["B"],
        "legalities": {"commander": "legal"},
    }


#: The two names AVERAGE_FOR_SAMPLE_DECK has that the sample deck does not.
MISSING_CARDS = {
    "data": [
        _card_payload(
            "Smothering Tithe", "Enchantment",
            "Whenever an opponent draws a card, that player may pay {2}. If the "
            "player doesn't, you create a Treasure token.",
        ),
        _card_payload(
            "Demonic Tutor", "Sorcery",
            "Search your library for a card, put that card into your hand, then "
            "shuffle.",
        ),
    ],
    "not_found": [],
}


def compare_client():
    # resolve() for the deck, then the game changers list, then the collection
    # lookup for the names the deck is missing, then game changers again.
    return client_for(
        load("collection_sample_deck.json"),
        NO_GAME_CHANGERS,
        MISSING_CARDS,
        NO_GAME_CHANGERS,
    )


def test_compare_to_average_diffs_both_ways():
    source = FakeAverageDeck(AVERAGE_FOR_SAMPLE_DECK)
    result = api.compare_to_average(
        deck_text(), client=compare_client(), edhrec_client=source
    )
    assert result["average_size"] == 4
    assert "Sol Ring" in result["in_both"]
    assert "Cultivate" in result["in_both"]
    assert result["overlap_pct"] == 50.0
    # Cards the deck already has are not reported as unique to it.
    assert "Sol Ring" not in result["unique_to_yours"]


def test_compare_asks_edhrec_for_the_decks_own_commander():
    source = FakeAverageDeck(AVERAGE_FOR_SAMPLE_DECK)
    result = api.compare_to_average(
        deck_text(), client=compare_client(), edhrec_client=source
    )
    assert source.calls == [result["commander"]]


def test_missing_cards_carry_the_function_they_would_fill():
    source = FakeAverageDeck(AVERAGE_FOR_SAMPLE_DECK)
    result = api.compare_to_average(
        deck_text(), client=compare_client(), edhrec_client=source
    )
    missing = {c["name"]: c["functions"] for c in result["missing_from_yours"]}
    # So the agent can cross the diff with the audit instead of listing cards.
    assert "tutor" in missing["Demonic Tutor"]
    assert missing["Demonic Tutor"]


def test_one_unresolvable_average_name_does_not_abort_the_comparison():
    # The average list is a community source, resolved with strict=False. A name
    # Scryfall cannot resolve is reported, not silently dropped.
    source = FakeAverageDeck(
        {"deck": {"cards": {"Artifact": [["Blatantly Fake Card", 1], ["Sol Ring", 1]]}}}
    )
    client = client_for(
        load("collection_sample_deck.json"),
        NO_GAME_CHANGERS,
        {"data": [], "not_found": [{"name": "Blatantly Fake Card"}]},
        NO_GAME_CHANGERS,
    )
    result = api.compare_to_average(deck_text(), client=client, edhrec_client=source)
    assert result["unresolved_average_names"] == ["Blatantly Fake Card"]
    assert "Sol Ring" in result["in_both"]


def test_compare_raises_when_no_commander_is_declared():
    source = FakeAverageDeck(AVERAGE_FOR_SAMPLE_DECK)
    client = client_for(load("collection_basic.json"), NO_GAME_CHANGERS)
    with pytest.raises(DeckStructureError):
        api.compare_to_average("1 Sol Ring\n", client=client, edhrec_client=source)
    # No point asking EDHREC for an average deck with no commander to key on.
    assert source.calls == []


def test_compare_propagates_an_edhrec_outage():
    from mtgpt.errors import SourceUnavailable

    source = FakeAverageDeck(SourceUnavailable("EDHREC", "down"))
    client = client_for(load("collection_sample_deck.json"), NO_GAME_CHANGERS)
    with pytest.raises(SourceUnavailable):
        api.compare_to_average(deck_text(), client=client, edhrec_client=source)


def test_compare_degrades_to_zero_overlap_on_an_empty_average_deck():
    source = FakeAverageDeck({"deck": {"cards": {}}})
    client = client_for(load("collection_sample_deck.json"), NO_GAME_CHANGERS)
    result = api.compare_to_average(deck_text(), client=client, edhrec_client=source)
    assert result["average_size"] == 0
    assert result["overlap_pct"] == 0.0
    assert result["missing_from_yours"] == []


# --- the reverse direction: precision, not recall -----------------------------


def classify_probe_payload(*cards):
    return {"data": list(cards), "has_more": False}


def test_check_classifier_measures_precision_not_recall():
    # Two cards our regex calls ramp; Scryfall confirms only the first carries
    # otag:ramp. That is precision: of what WE tagged, what did they tag too.
    probe = classify_probe_payload(
        tagger_payload("Sol Ring", "Artifact", "{T}: Add {C}{C}."),
        tagger_payload("Fake Ramp", "Artifact", "{T}: Add {G}."),
    )
    confirmations = {"data": [{"name": "Sol Ring"}], "has_more": False}
    client = client_for(probe, NO_GAME_CHANGERS, confirmations)
    result = api.check_classifier("ramp", client=client)
    assert result["count"] == 2
    assert result["precision_estimate"] == 0.5
    by_name = {c["name"]: c for c in result["cards"]}
    assert by_name["Sol Ring"]["community_agrees"] is True
    assert by_name["Fake Ramp"]["community_agrees"] is False
    assert by_name["Sol Ring"]["source"] == "classify"


def test_check_classifier_labels_its_number_as_precision_only():
    probe = classify_probe_payload(tagger_payload("Sol Ring", "Artifact", "{T}: Add {C}{C}."))
    client = client_for(probe, NO_GAME_CHANGERS, {"data": [{"name": "Sol Ring"}]})
    result = api.check_classifier("ramp", client=client)
    assert "precision" in result["measures"]
    assert "blind to false negatives" in result["measures"].casefold()


def test_check_classifier_does_not_query_otag_to_build_its_sample():
    # Sampling by `otag:` and then measuring against `otag:` would beg the
    # question. The sample comes from oracle text instead.
    transport = FakeTransport(
        classify_probe_payload(tagger_payload("Sol Ring", "Artifact", "{T}: Add {C}{C}.")),
        NO_GAME_CHANGERS,
        {"data": [{"name": "Sol Ring"}]},
    )
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    api.check_classifier("ramp", client=client)
    assert "otag" not in transport.calls[0][0], transport.calls[0][0]
    # The confirmation query is the only one allowed to use the tag.
    assert "otag" in transport.calls[-1][0]


def test_check_classifier_claims_nothing_for_an_uncheckable_tag():
    client = client_for()
    result = api.check_classifier("theft", client=client)
    assert result["precision_estimate"] is None
    assert result["measures"] is None
    assert "nothing to check" in result["cross_check_note"]


def test_check_classifier_degrades_when_the_confirmation_query_fails():
    import urllib.error

    calls = {"n": 0}

    def transport(url, payload=None):
        calls["n"] += 1
        if "otag" in url:
            raise urllib.error.HTTPError(url, 500, "boom", {}, None)
        if calls["n"] == 1:
            return classify_probe_payload(
                tagger_payload("Sol Ring", "Artifact", "{T}: Add {C}{C}.")
            )
        return NO_GAME_CHANGERS

    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    result = api.check_classifier("ramp", client=client)
    # No confirmations means no card is confirmed, not a traceback.
    assert result["precision_estimate"] == 0.0


def test_cross_check_function_reports_both_directions():
    client = client_for(
        load("search_results.json"),            # find_cards search
        NO_GAME_CHANGERS,
        classify_probe_payload(                 # check_classifier probe
            tagger_payload("Sol Ring", "Artifact", "{T}: Add {C}{C}."),
            tagger_payload("Fake Ramp", "Artifact", "{T}: Add {G}."),
        ),
        NO_GAME_CHANGERS,
        {"data": [{"name": "Sol Ring"}]},       # confirmation
    )
    result = api.cross_check_function("ramp", client=client)
    assert result["recall_estimate"] == 1.0
    assert result["precision_estimate"] == 0.5
    # Both labelled, in one place, because reporting either alone has misled.
    assert "recall_estimate:" in result["measures"]
    assert "precision_estimate:" in result["measures"]
    assert result["precision_disagreements"] == ["Fake Ramp"]


def test_every_cross_checkable_function_has_probe_words():
    # `check_classifier` falls back to `o:<label>` when a label is missing here,
    # and an underscored label matches no oracle text, so the sample would come
    # back empty and the precision estimate would be silently None.
    from mtgpt import tagger

    for label in tagger.CROSS_CHECK:
        assert label in api._PROBE_WORDS, label
