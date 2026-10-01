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
