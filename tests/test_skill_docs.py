"""The skill's docs must only name commands that exist, and its references must exist."""

import re
from pathlib import Path

from mtgpt import cli

SKILL = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "mtgpt"


def test_skill_and_references_exist():
    assert (SKILL / "SKILL.md").exists()
    for name in ("toolkit", "goldfish", "research", "tuning-loop", "brackets",
                 "deckbuilding-hygiene", "sources"):
        assert (SKILL / "references" / f"{name}.md").exists(), name


def test_every_command_the_skill_names_exists():
    actions = [a for a in cli.build_parser()._actions if a.dest == "command"]
    known = set(actions[0].choices)
    text = "\n".join(p.read_text() for p in SKILL.rglob("*.md"))
    named = set(re.findall(r"mtgpt\.cli ([a-z][a-z-]+)", text))
    assert named, "the skill should show commands as `python3 -m mtgpt.cli <command>`"
    assert named <= known, f"unknown commands in the skill: {sorted(named - known)}"


def test_skill_md_stays_short():
    assert len((SKILL / "SKILL.md").read_text().splitlines()) <= 200
