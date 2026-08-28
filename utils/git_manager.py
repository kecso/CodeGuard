"""Clone and reset ephemeral checkouts of target git remotes (typically GitHub)."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from git import GitCommandError, Repo
from git.exc import InvalidGitRepositoryError, NoSuchPathError

from utils.config import RepositoryConfig

logger = logging.getLogger("codeguard.git")


class GitManagerError(RuntimeError):
    """Raised when clone, fetch, or reset fails."""


class GitManager:
    """Manages ephemeral workspace clones of target git repositories."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.workspace.mkdir(parents=True, exist_ok=True)

    def destination(self, repo: RepositoryConfig) -> Path:
        return self.workspace / repo.name

    def prepare(self, repo: RepositoryConfig) -> Path:
        """Return a clean checkout of ``repo.branch`` from ``repo.git_url``."""
        dest = self.destination(repo)
        if _is_git_checkout(dest):
            logger.info("Resetting existing checkout %s", dest)
            self._reset_and_pull(dest, repo.branch)
            return dest
        if dest.exists():
            logger.warning("Removing non-git workspace path %s", dest)
            shutil.rmtree(dest)
        logger.info("Cloning %s into %s", repo.git_url, dest)
        self._clone(repo.git_url, dest, repo.branch)
        return dest

    def source_sha(self, repo_path: Path) -> str:
        """SHA of HEAD in the cloned target (the source under audit)."""
        return self.head_sha(repo_path)

    def head_sha(self, repo_path: Path) -> str:
        return _open_repo(repo_path).head.commit.hexsha

    def _clone(self, url: str, dest: Path, branch: str) -> None:
        try:
            Repo.clone_from(url, dest, branch=branch, single_branch=True)
        except GitCommandError as extra:
            raise GitManagerError(
                f"clone failed for {url} (branch {branch}): {extra}"
            ) from extra

    def _reset_and_pull(self, dest: Path, branch: str) -> None:
        repo = _open_repo(dest)
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
