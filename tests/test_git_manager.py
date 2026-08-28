from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from tests.conftest import make_mirror
from utils.config import GitIdentity, RepositoryConfig
from utils.git_manager import GitManager, GitManagerError


def repo_cfg(url: str, name: str = "sample") -> RepositoryConfig:
    return RepositoryConfig(
        name=name,
        local_mirror_url=str(url),
        branch="main",
        output_report_dir="reports",
        report_prefix="audit",
    )


def test_clone_then_reset_and_publish(tmp_path: Path, identity: GitIdentity) -> None:
    mirror = make_mirror(tmp_path, {"src/app.py": "print(1)\n"})
    manager = GitManager(tmp_path / "workspace", identity)
    cfg = repo_cfg(mirror)
    checkout = manager.prepare(cfg)
    assert (checkout / "src" / "app.py").read_text(encoding="utf-8") == "print(1)\n"

    working = Repo.clone_from(mirror, tmp_path / "pusher")
    with working.config_writer() as writer:
        writer.set_value("user", "name", "U")
        writer.set_value("user", "email", "u@t")
    (tmp_path / "pusher" / "src" / "app.py").write_text("print(2)\n", encoding="utf-8")
    working.index.add(["src/app.py"])
    working.index.commit("update")
    working.remotes.origin.push()

    (checkout / "dirty.txt").write_text("stale", encoding="utf-8")
    manager.prepare(cfg)
    assert (checkout / "src" / "app.py").read_text(encoding="utf-8") == "print(2)\n"
    assert not (checkout / "dirty.txt").exists()

    report = checkout / "reports" / "audit.md"
    report.parent.mkdir(parents=True)
    report.write_text("# audit\n", encoding="utf-8")
    published = manager.publish_report(checkout, "reports/audit.md", "chore: audit")
    assert published is True

    verify = tmp_path / "verify"
    Repo.clone_from(mirror, verify)
    assert (verify / "reports" / "audit.md").is_file()


def test_publish_skips_when_unchanged(tmp_path: Path, identity: GitIdentity) -> None:
    mirror = make_mirror(tmp_path, {"reports/audit.md": "# old\n", "a.py": "a\n"})
    manager = GitManager(tmp_path / "workspace", identity)
    checkout = manager.prepare(repo_cfg(mirror))
    (checkout / "reports" / "audit.md").write_text("# old\n", encoding="utf-8")
    assert manager.publish_report(checkout, "reports/audit.md", "noop") is False


def test_skip_push_still_commits(tmp_path: Path, identity: GitIdentity) -> None:
    mirror = make_mirror(tmp_path, {"a.py": "a\n"})
    manager = GitManager(tmp_path / "workspace", identity)
    checkout = manager.prepare(repo_cfg(mirror))
    report = checkout / "reports" / "audit.md"
    report.parent.mkdir()
    report.write_text("# new\n", encoding="utf-8")
    assert (
        manager.publish_report(
            checkout, "reports/audit.md", "local", skip_push=True
        )
        is True
    )
    repo = Repo(checkout)
    assert "local" in repo.head.commit.message


def test_replaces_non_git_destination(tmp_path: Path, identity: GitIdentity) -> None:
    mirror = make_mirror(tmp_path, {"a.py": "a\n"})
    dest = tmp_path / "workspace" / "sample"
    dest.mkdir(parents=True)
    (dest / "junk").write_text("x", encoding="utf-8")
    manager = GitManager(tmp_path / "workspace", identity)
    checkout = manager.prepare(repo_cfg(mirror))
    assert (checkout / "a.py").is_file()
    assert not (checkout / "junk").exists()


def test_clone_failure(tmp_path: Path, identity: GitIdentity) -> None:
    manager = GitManager(tmp_path / "workspace", identity)
    with pytest.raises(GitManagerError, match="clone failed"):
        manager.prepare(repo_cfg("/no/such/mirror.git"))


def test_publish_missing_report(tmp_path: Path, identity: GitIdentity) -> None:
    mirror = make_mirror(tmp_path, {"a.py": "a\n"})
    manager = GitManager(tmp_path / "workspace", identity)
    checkout = manager.prepare(repo_cfg(mirror))
    with pytest.raises(GitManagerError, match="does not exist"):
        manager.publish_report(checkout, "reports/audit.md", "x")


def test_open_non_repo(tmp_path: Path, identity: GitIdentity) -> None:
    manager = GitManager(tmp_path / "workspace", identity)
    with pytest.raises(GitManagerError, match="not a git repository"):
        manager.publish_report(tmp_path, "x.md", "x")


def test_reset_bad_branch(tmp_path: Path, identity: GitIdentity) -> None:
    mirror = make_mirror(tmp_path, {"a.py": "a\n"})
    manager = GitManager(tmp_path / "workspace", identity)
    cfg = repo_cfg(mirror)
    manager.prepare(cfg)
    bad = RepositoryConfig(
        name="sample",
        local_mirror_url=str(mirror),
        branch="does-not-exist",
        output_report_dir="reports",
        report_prefix="audit",
    )
    with pytest.raises(GitManagerError, match="failed to reset"):
        manager.prepare(bad)


def test_head_sha_and_empty_publish(tmp_path: Path, identity: GitIdentity) -> None:
    mirror = make_mirror(tmp_path, {"a.py": "a\n"})
    manager = GitManager(tmp_path / "workspace", identity)
    checkout = manager.prepare(repo_cfg(mirror))
    assert len(manager.head_sha(checkout)) == 40
    assert manager.source_sha(checkout) == manager.head_sha(checkout)
    with pytest.raises(GitManagerError, match="no report paths"):
        manager.publish_paths(checkout, [], "x")


def test_stage_commit_push_errors(tmp_path: Path, identity: GitIdentity, monkeypatch: pytest.MonkeyPatch) -> None:
    from git import GitCommandError

    mirror = make_mirror(tmp_path, {"a.py": "a\n"})
    manager = GitManager(tmp_path / "workspace", identity)
    checkout = manager.prepare(repo_cfg(mirror))
    report = checkout / "reports" / "audit.md"
    report.parent.mkdir()
    report.write_text("# new\n", encoding="utf-8")

    repo = Repo(checkout)
    original_add = repo.index.add

    def boom_add(*args, **kwargs):
        raise GitCommandError("add", 1)

    monkeypatch.setattr(type(repo.index), "add", boom_add)
    with pytest.raises(GitManagerError, match="failed to stage"):
        manager.publish_report(checkout, "reports/audit.md", "x")
    monkeypatch.setattr(type(repo.index), "add", original_add)

    def boom_commit(*args, **kwargs):
        raise GitCommandError("commit", 1)

    monkeypatch.setattr(type(repo.index), "commit", boom_commit)
    with pytest.raises(GitManagerError, match="failed to commit"):
        manager.publish_report(checkout, "reports/audit.md", "x")


def test_push_error(tmp_path: Path, identity: GitIdentity, monkeypatch: pytest.MonkeyPatch) -> None:
    from git import GitCommandError
    from git.remote import Remote

    mirror = make_mirror(tmp_path, {"a.py": "a\n"})
    manager = GitManager(tmp_path / "workspace", identity)
    checkout = manager.prepare(repo_cfg(mirror))
    report = checkout / "reports" / "audit.md"
    report.parent.mkdir()
    report.write_text("# new\n", encoding="utf-8")

    def boom_push(self, *args, **kwargs):
        raise GitCommandError("push", 1)

    monkeypatch.setattr(Remote, "push", boom_push)
    with pytest.raises(GitManagerError, match="failed to push"):
        manager.publish_report(checkout, "reports/audit.md", "x")
