"""Deck projects: one folder per deck under decks/, which git ignores.

Decks are a user's save files. Nothing here is committed: a project holds the
deck's versions (v1.txt, v2.txt, ... — each written once, never edited), its
goal file, research notes, cached combos, and a tuning log. Only card rules,
which every deck reuses, belong in the repo.
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

from .deckparse import parse
from .errors import MtgptError

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "decks"
STAGES = ("scan", "research", "tune", "finish")
_VERSION = re.compile(r"^v(\d+)([a-z]?)$")
LOG_TAIL_LINES = 40


class ProjectError(MtgptError):
    """A deck project that does not exist, already exists, or is asked something invalid."""


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    if not slug:
        raise ProjectError(f"{name!r} has no letters or digits to name a deck folder with")
    return slug


def version_number(name: str) -> int | None:
    match = _VERSION.match(name)
    return int(match.group(1)) if match else None


def versions(root: Path, slug: str) -> list[str]:
    folder = _folder(root, slug)
    names = [p.stem for p in folder.glob("v*.txt") if _VERSION.match(p.stem)]
    return sorted(names, key=lambda n: (version_number(n), n))


def create(root: Path, name: str, text: str, *, bracket: int, source: str | None = None) -> dict:
    slug = slugify(name)
    folder = Path(root) / slug
    if (folder / "project.json").exists():
        raise ProjectError(f"a project named {slug!r} already exists; use `project status {slug}`")
    commanders = [e.name for e in parse(text).commanders]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "v1.txt").write_text(text, encoding="utf-8")
    today = _today()
    project = {"name": name, "slug": slug, "commander": " + ".join(commanders) or None,
               "bracket": bracket, "source": source, "best": "v1", "best_primary": None,
               "stage": "scan", "created": today, "updated": today}
    _write(folder, project)
    (folder / "log.md").write_text(f"# {name} — tuning log\n\n## v1 created {today}\n",
                                   encoding="utf-8")
    return project


def load(root: Path, slug: str) -> dict:
    path = Path(root) / slug / "project.json"
    if not path.exists():
        raise ProjectError(f"no deck project {slug!r}; run `project list` to see them")
    return json.loads(path.read_text(encoding="utf-8"))


def list_projects(root: Path) -> list[dict]:
    root = Path(root)
    if not root.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(root.glob("*/project.json"))]


def status(root: Path, slug: str) -> dict:
    project = load(root, slug)
    lines = (Path(root) / slug / "log.md").read_text(encoding="utf-8").splitlines()
    notes, in_note = [], False
    for line in lines:
        if line.startswith("### Playtest"):
            in_note = True
        elif line.startswith("#"):
            in_note = False
        elif not line.strip():
            in_note = False
        if in_note and line.strip():
            notes.append(line)
    return {"project": project, "versions": versions(root, slug),
            "log_tail": "\n".join(lines[-LOG_TAIL_LINES:]), "playtest_notes": notes}


def save(root: Path, slug: str, text: str, *, note: str = "") -> dict:
    load(root, slug)
    numbers = [version_number(v) for v in versions(root, slug)]
    version = f"v{max(numbers, default=0) + 1}"
    path = Path(root) / slug / f"{version}.txt"
    path.write_text(text, encoding="utf-8")
    log(root, slug, f"## {version} saved {_today()}\n{note}".rstrip())
    _touch(root, slug)
    return {"version": version, "path": str(path)}


def set_best(root: Path, slug: str, version: str, *, primary: float | None = None) -> dict:
    project = load(root, slug)
    if version not in versions(root, slug):
        raise ProjectError(f"{slug} has no version {version!r}")
    project.update(best=version, best_primary=primary, updated=_today())
    _write(Path(root) / slug, project)
    return project


def set_stage(root: Path, slug: str, stage: str) -> dict:
    if stage not in STAGES:
        raise ProjectError(f"stage must be one of {', '.join(STAGES)}, got {stage!r}")
    project = load(root, slug)
    project.update(stage=stage, updated=_today())
    _write(Path(root) / slug, project)
    return project


def note(root: Path, slug: str, text: str) -> None:
    """A note from a real game, read first at the start of the next session."""
    log(root, slug, f"### Playtest {_today()}\n{text}")


def log(root: Path, slug: str, text: str) -> None:
    load(root, slug)
    with open(Path(root) / slug / "log.md", "a", encoding="utf-8") as handle:
        handle.write(f"\n{text}\n")
    _touch(root, slug)


def _folder(root: Path, slug: str) -> Path:
    load(root, slug)
    return Path(root) / slug


def _touch(root: Path, slug: str) -> None:
    project = load(root, slug)
    project["updated"] = _today()
    _write(Path(root) / slug, project)


def _write(folder: Path, project: dict) -> None:
    (folder / "project.json").write_text(json.dumps(project, indent=2) + "\n", encoding="utf-8")


def _today() -> str:
    return datetime.date.today().isoformat()
