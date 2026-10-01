# tests/test_edhrec.py
import json
import pathlib

import pytest

from mtgpt import edhrec
from mtgpt.errors import SourceUnavailable

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def payload():
    return json.loads((FIXTURES / "edhrec_commander.json").read_text())


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
    "name,slug",
    [
        ("Atraxa, Praetors' Voice", "atraxa-praetors-voice"),
        ("Kozilek, Butcher of Truth", "kozilek-butcher-of-truth"),
        ("Ur-Dragon", "ur-dragon"),
        ("Jhoira, Weatherlight Captain", "jhoira-weatherlight-captain"),
        # Accented characters are stripped, not escaped.
        ("Nazgûl", "nazgul"),
        ("Minsc & Data, Timeless Heroes", "minsc-and-data-timeless-heroes"),
    ],
)
def test_commander_slug(name, slug):
    assert edhrec.commander_slug(name) == slug


def test_commander_fetches_the_base_page():
    transport = FakeTransport(payload())
    client = edhrec.EdhrecClient(transport=transport, sleep=lambda _: None)
    client.commander("Atraxa, Praetors' Voice")
    assert transport.calls == [
        "https://json.edhrec.com/pages/commanders/atraxa-praetors-voice.json"
    ]


def test_commander_fetches_a_bracket_variant():
    transport = FakeTransport(payload())
    client = edhrec.EdhrecClient(transport=transport, sleep=lambda _: None)
    client.commander("Atraxa, Praetors' Voice", variant="upgraded")
    assert transport.calls[0].endswith("/atraxa-praetors-voice/upgraded.json")


def test_commander_rejects_an_unknown_variant():
    client = edhrec.EdhrecClient(transport=FakeTransport(), sleep=lambda _: None)
    with pytest.raises(ValueError):
        client.commander("Atraxa, Praetors' Voice", variant="nonsense")


def test_commander_wraps_transport_failure():
    client = edhrec.EdhrecClient(
        transport=FakeTransport(OSError("connection reset")), sleep=lambda _: None
    )
    with pytest.raises(SourceUnavailable) as excinfo:
        client.commander("Atraxa, Praetors' Voice")
    assert "EDHREC" in str(excinfo.value)


def test_synergy_cards_carry_evidence():
    cards = edhrec.synergy_cards(payload(), limit=10)
    assert cards, "expected synergy cards from the fixture"
    first = cards[0]
    assert set(first) >= {"name", "synergy", "num_decks", "potential_decks", "list", "inclusion_rate"}
    assert isinstance(first["name"], str)
    # inclusion_rate is derived so a recommendation can cite it directly.
    assert 0.0 <= first["inclusion_rate"] <= 1.0


def test_synergy_cards_are_sorted_by_synergy_descending():
    cards = edhrec.synergy_cards(payload(), limit=20)
    synergies = [c["synergy"] for c in cards]
    assert synergies == sorted(synergies, reverse=True)


def test_synergy_cards_respects_limit():
    assert len(edhrec.synergy_cards(payload(), limit=5)) == 5


def test_synergy_cards_deduplicates_across_lists():
    """A card appearing in several cardlists must be returned once."""
    cards = edhrec.synergy_cards(payload(), limit=200)
    names = [c["name"] for c in cards]
    assert len(names) == len(set(names))


def test_themes_are_sorted_by_deck_count():
    themes = edhrec.themes(payload())
    counts = [t["count"] for t in themes]
    assert counts == sorted(counts, reverse=True)
    assert set(themes[0]) >= {"slug", "label", "count"}


def test_bracket_distribution_keys_are_ints_one_to_five():
    dist = edhrec.bracket_distribution(payload())
    assert set(dist) <= {1, 2, 3, 4, 5}
    assert all(isinstance(v, int) for v in dist.values())


def test_missing_sections_degrade_to_empty_rather_than_raising():
    """EDHREC is unofficial; a shape change must not crash the toolkit."""
    assert edhrec.synergy_cards({}, limit=5) == ()
    assert edhrec.themes({}) == ()
    assert edhrec.bracket_distribution({}) == {}


def test_reshaped_theme_count_is_skipped_not_raised():
    payload = {"tag_counts": [
        {"slug": "good", "value": "Good", "count": 5},
        {"slug": "bad", "value": "Bad", "count": "lots"},
    ]}
    themes = edhrec.themes(payload)
    assert [t["slug"] for t in themes] == ["good"]


def test_reshaped_bracket_value_is_skipped_not_raised():
    payload = {"bracket_counts": {"3": 10, "4": "many"}}
    assert edhrec.bracket_distribution(payload) == {3: 10}


def test_inclusion_rate_is_clamped_to_one():
    """Glitch data from an unofficial source must not claim >100% inclusion."""
    payload = {
        "container": {"json_dict": {"cardlists": [
            {
                "header": "High Synergy Cards",
                "cardviews": [
                    {"name": "Glitched Card", "synergy": 0.5,
                     "num_decks": 140, "potential_decks": 100},
                ],
            },
        ]}}
    }
    cards = edhrec.synergy_cards(payload, limit=10)
    assert cards[0]["inclusion_rate"] == 1.0


def test_synergy_cards_attributes_duplicates_to_the_higher_priority_list():
    """A card in two candidate lists is credited to the higher-priority one."""
    payload = {
        "container": {"json_dict": {"cardlists": [
            # Payload order deliberately opposite of CANDIDATE_LISTS priority,
            # matching the real fixture's own list order.
            {
                "header": "Top Cards",
                "cardviews": [
                    {"name": "Dual-Listed Card", "synergy": 0.1,
                     "num_decks": 10, "potential_decks": 100},
                ],
            },
            {
                "header": "High Synergy Cards",
                "cardviews": [
                    {"name": "Dual-Listed Card", "synergy": 0.1,
                     "num_decks": 10, "potential_decks": 100},
                ],
            },
        ]}}
    }
    cards = edhrec.synergy_cards(payload, limit=10)
    assert len(cards) == 1
    assert cards[0]["list"] == "High Synergy Cards"
