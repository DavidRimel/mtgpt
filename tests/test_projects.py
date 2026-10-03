import json

import pytest

from mtgpt import projects
from mtgpt.projects import ProjectError

LIST = "Commander\n1 Hapatra, Vizier of Poisons\n\nDeck\n1 Sol Ring\n98 Swamp\n"


def test_slugify_handles_punctuation_and_spaces():
    assert projects.slugify("Hapatra's Snakes!") == "hapatra-s-snakes"
    assert projects.slugify("  Jodah  B4 ") == "jodah-b4"


def test_slugify_refuses_a_name_without_letters_or_digits():
    with pytest.raises(ProjectError):
        projects.slugify("!!!")


def test_create_writes_project_v1_and_log(tmp_path):
    p = projects.create(tmp_path, "Hapatra", LIST, bracket=3, source="https://x")
    folder = tmp_path / "hapatra"
    assert (folder / "v1.txt").read_text() == LIST
    assert json.loads((folder / "project.json").read_text()) == p
    assert p["commander"] == "Hapatra, Vizier of Poisons"
    assert p["best"] == "v1" and p["stage"] == "scan" and p["bracket"] == 3
    assert (folder / "log.md").read_text().startswith("# Hapatra")


def test_create_refuses_an_existing_project(tmp_path):
    projects.create(tmp_path, "Hapatra", LIST, bracket=3)
    with pytest.raises(ProjectError, match="project status"):
        projects.create(tmp_path, "Hapatra", LIST, bracket=3)


def test_versions_ignore_other_files_and_count_suffixes(tmp_path):
    projects.create(tmp_path, "Hapatra", LIST, bracket=3)
    folder = tmp_path / "hapatra"
    for name in ("v2.txt", "v11b.txt", "v4a.txt", "v8.goal.json", "research.md", "combos.json"):
        (folder / name).write_text("x")
    assert projects.versions(tmp_path, "hapatra") == ["v1", "v2", "v4a", "v11b"]
    assert projects.save(tmp_path, "hapatra", LIST)["version"] == "v12"


def test_save_logs_and_set_best_records_primary(tmp_path):
    projects.create(tmp_path, "Hapatra", LIST, bracket=3)
    saved = projects.save(tmp_path, "hapatra", LIST, note="+Blowfly -Bear")
    assert saved["version"] == "v2"
    assert "## v2 saved" in (tmp_path / "hapatra" / "log.md").read_text()
    p = projects.set_best(tmp_path, "hapatra", "v2", primary=0.277)
    assert p["best"] == "v2" and p["best_primary"] == 0.277


def test_set_best_refuses_a_missing_version(tmp_path):
    projects.create(tmp_path, "Hapatra", LIST, bracket=3)
    with pytest.raises(ProjectError):
        projects.set_best(tmp_path, "hapatra", "v9")


def test_stage_must_be_known(tmp_path):
    projects.create(tmp_path, "Hapatra", LIST, bracket=3)
    assert projects.set_stage(tmp_path, "hapatra", "tune")["stage"] == "tune"
    with pytest.raises(ProjectError):
        projects.set_stage(tmp_path, "hapatra", "done")


def test_list_and_status(tmp_path):
    projects.create(tmp_path, "Hapatra", LIST, bracket=3)
    projects.create(tmp_path, "Jodah B4", LIST, bracket=4)
    (tmp_path / "stray.txt").write_text("not a project")
    assert [p["slug"] for p in projects.list_projects(tmp_path)] == ["hapatra", "jodah-b4"]
    projects.note(tmp_path, "hapatra", "flooded twice")
    projects.log(tmp_path, "hapatra", "v2 vs v1: keep (+3.1)")
    st = projects.status(tmp_path, "hapatra")
    assert st["versions"] == ["v1"]
    assert any("flooded twice" in line for line in st["playtest_notes"])
    assert "v2 vs v1: keep (+3.1)" in st["log_tail"]
    assert not any("keep (+3.1)" in line for line in st["playtest_notes"])


def test_load_of_a_missing_project_is_a_project_error(tmp_path):
    with pytest.raises(ProjectError, match="project list"):
        projects.load(tmp_path, "nope")
