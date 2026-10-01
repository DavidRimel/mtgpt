import json

import pytest

from mtgpt import card_rules


@pytest.fixture(autouse=True)
def empty_card_rules(tmp_path, monkeypatch):
    """Tests build cards that share names with real ones; the shipped card
    rules library must not change what they do."""
    path = tmp_path / "card_rules.json"
    path.write_text(json.dumps({"version": 1, "cards": {}}))
    monkeypatch.setattr(card_rules, "DEFAULT_PATH", path)
    return path
