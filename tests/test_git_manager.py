from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from tests.conftest import make_mirror
from utils.config import RepositoryConfig
from utils.git_manager import GitManager, GitManagerError


def repo_cfg(url: str, name: str = "sample") -> RepositoryConfig:
    return RepositoryConfig(
        name=name,
        git_url=str(url),
        branch="main",
        output_report_dir="reports",
        report_prefix="audit",
    )


def test_clone_then_reset(tmp_path: Path) -> None:
    mirror = make_mirror(tmp_path, {"src/app.py": "print(1)\n"})
    manager = GitManager(tmp_path / "workspace")
    cfg = repo_cfg(mirror)
    checkout = manager.prepare(cfg)
    assert (checkout / "src" / "app.py").read_text(encoding="utf-8") == "print(1)\n"

    working = Repo.clone_from(mirror, tmp_path / "editor")
    with working.config_writer() as writer:
        writer.set_value("user", "name", "U")
        writer.set_value("user", "email", "u@t")
    (tmp_path / "editor" / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
    working.index.add(["src/app.py"])
    working.index.commit("update")
    working.remotes.origin.push()

    (checkout / "dirty.txt").write_text("stale", encoding="utf-8")
    manager.prepare(cfg)
    assert (checkout / "src" / "app.py").read_text(encoding="utf-8") == "print(2)\n"
    assert not (checkout / "dirty.txt").exists()


def test_replaces_non_git_destination(tmp_path: Path) -> None:
    mirror = make_mirror(tmp_path, {"a.py": "a\n"})
    dest = tmp_path / "workspace" / "sample"
    dest.mkdir(parents=True)
    (dest / "junk").write_text("x", encoding="utf-8")
    manager = GitManager(tmp_path / "workspace")
    checkout = manager.prepare(repo_cfg(mirror))
    assert (checkout / "a.py").is_file()
    assert not (checkout / "junk").exists()


def test_clone_failure(tmp_path: Path) -> None:
    manager = GitManager(tmp_path / "workspace")
    with pytest.raises(GitManagerError, match="clone failed"):
        manager.prepare(repo_cfg("/no/such/mirror.git"))


def test_open_non_repo(tmp_path: Path) -> None:
    manager = GitManager(tmp_path / "workspace")
    with pytest.raises(GitManagerError, match="not a git repository"):
        manager.head_sha(tmp_path)


def test_reset_bad_branch(tmp_path: Path) -> None:
    mirror = make_mirror(tmp_path, {"a.py": "a\n"})
    manager = GitManager(tmp_path / "workspace")
    cfg = repo_cfg(mirror)
    manager.prepare(cfg)
    bad = RepositoryConfig(
        name="sample",
        git_url=str(mirror),
        branch="does-not-exist",
        output_report_dir="reports",
        report_prefix="audit",
    )
    with pytest.raises(GitManagerError, match="failed to reset"):
        manager.prepare(bad)


def test_head_sha(tmp_path: Path) -> None:
    mirror = make_mirror(tmp_path, {"a.py": "a\n"})
    manager = GitManager(tmp_path / "workspace")
    checkout = manager.prepare(repo_cfg(mirror))
    assert len(manager.head_sha(checkout)) == 40
    assert manager.source_sha(checkout) == manager.head_sha(checkout)
