"""Clone, reset, and publish audit reports against a local git mirror."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from git import GitCommandError, Repo
from git.exc import InvalidGitRepositoryError, NoSuchPathError

from utils.config import GitIdentity, RepositoryConfig

logger = logging.getLogger("codeguard.git")


class GitManagerError(RuntimeError):
    """Raised when clone, reset, commit, or push fails."""


class GitManager:
    """Manages ephemeral workspace clones of local-mirror repositories."""

    def __init__(self, workspace: Path, identity: GitIdentity) -> None:
        self.workspace = workspace
        self.identity = identity
        self.workspace.mkdir(parents=True, exist_ok=True)

    def destination(self, repo: RepositoryConfig) -> Path:
        return self.workspace / repo.name

    def prepare(self, repo: RepositoryConfig) -> Path:
        """Return a clean checkout of ``repo.branch`` from the local mirror."""
        dest = self.destination(repo)
        if _is_git_checkout(dest):
            logger.info("Resetting existing checkout %s", dest)
            self._reset_and_pull(dest, repo.branch)
            return dest
        if dest.exists():
            logger.warning("Removing non-git workspace path %s", dest)
            shutil.rmtree(dest)
        logger.info("Cloning %s into %s", repo.local_mirror_url, dest)
        self._clone(repo.local_mirror_url, dest, repo.branch)
        return dest

    def publish_report(
        self,
        repo_path: Path,
        report_relpath: str,
        message: str,
        *,
        skip_push: bool = False,
    ) -> bool:
        """Stage the report, commit if it changed, and push to origin."""
        return self.publish_paths(
            repo_path, [report_relpath], message, skip_push=skip_push
        )

    def publish_paths(
        self,
        repo_path: Path,
        relative_paths: list[str],
        message: str,
        *,
        skip_push: bool = False,
    ) -> bool:
        """Stage one or more report artifacts, commit if needed, and push."""
        if not relative_paths:
            raise GitManagerError("no report paths to publish")
        repo = _open_repo(repo_path)
        _configure_identity(repo, self.identity)
        for relpath in relative_paths:
            path = repo_path / relpath
            if not path.exists():
                raise GitManagerError(f"report file does not exist: {path}")
        try:
            repo.index.add(relative_paths)
        except GitCommandError as exc:
            raise GitManagerError(f"failed to stage reports: {exc}") from exc
        if not _has_staged_or_tracked_changes(repo):
            logger.info("No report changes to publish in %s", repo_path)
            return False
        try:
            repo.index.commit(message)
        except GitCommandError as extra:
            raise GitManagerError(f"failed to commit report: {extra}") from extra
        if skip_push:
            logger.info("Skipping push for %s", repo_path)
            return True
        try:
            origin = repo.remote("origin")
            origin.push()
        except GitCommandError as extra:
            raise GitManagerError(f"failed to push report: {extra}") from extra
        logger.info("Pushed audit report for %s", repo_path)
        return True

    def source_sha(self, repo_path: Path) -> str:
        """SHA of the latest commit that is not a CodeGuard report commit."""
        repo = _open_repo(repo_path)
        try:
            sha = repo.git.log(
                "-1",
                "--format=%H",
                "--invert-grep",
                "--grep=^chore(audit):",
            )
        except GitCommandError:
            sha = ""
        sha = (sha or "").strip()
        if sha:
            return sha
        return repo.head.commit.hexsha

    def head_sha(self, repo_path: Path) -> str:
        return _open_repo(repo_path).head.commit.hexsha

    def _clone(self, url: str, dest: Path, branch: str) -> None:
        try:
            cloned = Repo.clone_from(url, dest, branch=branch, single_branch=True)
        except GitCommandError as extra:
            raise GitManagerError(
                f"clone failed for {url} (branch {branch}): {extra}"
            ) from extra
        _configure_identity(cloned, self.identity)

    def _reset_and_pull(self, dest: Path, branch: str) -> None:
        repo = _open_repo(dest)
        _configure_identity(repo, self.identity)
        try:
            repo.git.fetch("origin", branch)
            repo.git.checkout(branch)
            repo.git.reset("--hard", f"origin/{branch}")
            repo.git.clean("-fdx")
        except GitCommandError as extra:
            raise GitManagerError(
                f"failed to reset {dest} onto origin/{branch}: {extra}"
            ) from extra


def _is_git_checkout(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        _open_repo(path)
    except GitManagerError:
        return False
    return True


def _open_repo(path: Path) -> Repo:
    try:
        return Repo(path)
    except (InvalidGitRepositoryError, NoSuchPathError) as extra:
        raise GitManagerError(f"not a git repository: {path}") from extra


def _configure_identity(repo: Repo, identity: GitIdentity) -> None:
    with repo.config_writer() as writer:
        writer.set_value("user", "name", identity.commit_name)
        writer.set_value("user", "email", identity.commit_email)


def _has_staged_or_tracked_changes(repo: Repo) -> bool:
    if repo.is_dirty(index=True, working_tree=True, untracked_files=False):
        return True
    diff = repo.index.diff("HEAD")
    return bool(diff)
