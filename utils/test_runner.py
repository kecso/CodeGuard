"""Execute a target repository's own test suite and harvest coverage holes."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from utils.config import TestSettings

logger = logging.getLogger("codeguard.tests")


@dataclass(frozen=True)
class UncoveredFile:
    __test__ = False
    path: str
    missing_lines: tuple[int, ...]
    percent_covered: float


@dataclass(frozen=True)
class TestRunResult:
    __test__ = False
    detected: bool
    command: tuple[str, ...] = ()
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    coverage_percent: float | None = None
    uncovered: tuple[UncoveredFile, ...] = ()
    notes: tuple[str, ...] = ()
    harness_hints: str = ""

    @property
    def passed(self) -> bool | None:
        if self.exit_code is None:
            return None
        return self.exit_code == 0

    def to_prompt_context(self, *, max_uncovered_files: int = 40) -> str:
        if not self.detected:
            parts = [
                "No test command was executed. That is fine: target repositories "
                "may be any language or stack. Infer how this project is tested "
                "from the source, READMEs, CI files, and harness hints below, "
                "then identify high-risk untested behavior."
            ]
            if self.notes:
                parts.extend(self.notes)
            if self.harness_hints:
                parts.append("Harness / project hints:")
                parts.append(self.harness_hints)
            return "\n".join(parts)
        lines = [
            f"Test command: {' '.join(self.command)}",
            f"Exit code: {self.exit_code}",
            f"Timed out: {self.timed_out}",
            f"Passed: {self.passed}",
        ]
        if self.coverage_percent is not None:
            lines.append(f"Line coverage: {self.coverage_percent:.2f}%")
        if self.notes:
            lines.append("Notes:")
            lines.extend(f"- {note}" for note in self.notes)
        if self.uncovered:
            lines.append("Uncovered files (path → missing lines):")
            for item in self.uncovered[:max_uncovered_files]:
                missing = ",".join(str(num) for num in item.missing_lines[:80])
                if len(item.missing_lines) > 80:
                    missing += ",..."
                lines.append(
                    f"- {item.path} ({item.percent_covered:.1f}% covered): {missing}"
                )
            remaining = len(self.uncovered) - max_uncovered_files
            if remaining > 0:
                lines.append(f"- ... and {remaining} more files with missing coverage")
        stdout = self.stdout.strip()
        if stdout:
            lines.append("Test stdout (truncated):")
            lines.append(stdout[-4000:])
        stderr = self.stderr.strip()
        if stderr:
            lines.append("Test stderr (truncated):")
            lines.append(stderr[-2000:])
        if self.harness_hints:
            lines.append("Harness / project hints:")
            lines.append(self.harness_hints)
        return "\n".join(lines)


class TestRunner:
    __test__ = False

    def __init__(self, settings: TestSettings) -> None:
        self.settings = settings

    def run(self, project_root: Path) -> TestRunResult:
        if not self.settings.enabled:
            return TestRunResult(
                detected=False,
                notes=("Test execution disabled in config.test_settings.",),
            )
        command = detect_test_command(project_root)
        hints = collect_harness_hints(project_root)
        if command is None:
            return TestRunResult(
                detected=False,
                harness_hints=hints,
                notes=(
                    "No well-known test command was auto-detected. "
                    "The model should infer how to test this project from the tree.",
                ),
            )
        coverage_json = project_root / ".codeguard-coverage.json"
        env = _isolated_env(project_root)
        logger.info("Running tests in %s: %s", project_root, " ".join(command))
        try:
            completed = subprocess.run(
                command,
                cwd=project_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.settings.timeout_seconds,
                check=False,
            )
        except FileNotFoundError:
            return TestRunResult(
                detected=True,
                command=tuple(command),
                exit_code=127,
                notes=(f"Test executable not found: {command[0]}",),
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _output_to_str(exc.stdout)
            stderr = _output_to_str(exc.stderr)
            return TestRunResult(
                detected=True,
                command=tuple(command),
                timed_out=True,
                stdout=stdout,
                stderr=stderr,
                notes=(
                    f"Tests exceeded timeout of {self.settings.timeout_seconds}s.",
                ),
            )
        coverage_percent, uncovered = parse_coverage_json(coverage_json)
        notes: list[str] = []
        if command[:3] == [sys.executable, "-m", "pytest"] and coverage_percent is None:
            notes.append("pytest ran but no coverage.json was produced.")
        return TestRunResult(
            detected=True,
            command=tuple(command),
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            coverage_percent=coverage_percent,
            uncovered=uncovered,
            notes=tuple(notes),
            harness_hints=hints,
        )

    def execute(
        self,
        project_root: Path,
        command: list[str],
        *,
        harness_hints: str = "",
        collect_coverage: bool = False,
    ) -> TestRunResult:
        coverage_json = project_root / ".codeguard-coverage.json"
        env = _isolated_env(project_root)
        logger.info("Running tests in %s: %s", project_root, " ".join(command))
        try:
            completed = subprocess.run(
                command,
                cwd=project_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.settings.timeout_seconds,
                check=False,
            )
        except FileNotFoundError:
            return TestRunResult(
                detected=True,
                command=tuple(command),
                exit_code=127,
                notes=(f"Test executable not found: {command[0]}",),
                harness_hints=harness_hints,
            )
        except subprocess.TimeoutExpired as extra:
            return TestRunResult(
                detected=True,
                command=tuple(command),
                timed_out=True,
                stdout=_output_to_str(extra.stdout),
                stderr=_output_to_str(extra.stderr),
                notes=(
                    f"Tests exceeded timeout of {self.settings.timeout_seconds}s.",
                ),
                harness_hints=harness_hints,
            )
        coverage_percent, uncovered = (
            parse_coverage_json(coverage_json) if collect_coverage else (None, ())
        )
        notes: list[str] = []
        if (
            collect_coverage
            and command[:3] == [sys.executable, "-m", "pytest"]
            and coverage_percent is None
        ):
            notes.append("pytest ran but no coverage.json was produced.")
        return TestRunResult(
            detected=True,
            command=tuple(command),
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            coverage_percent=coverage_percent,
            uncovered=uncovered,
            notes=tuple(notes),
            harness_hints=harness_hints,
        )


HINT_FILENAMES = (
    "README.md",
    "README",
    "Makefile",
    "makefile",
    "justfile",
    "CONTRIBUTING.md",
    "CONTRIBUTING",
    "package.json",
    "pyproject.toml",
    "setup.py",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
    "mix.exs",
    "composer.json",
    "CMakeLists.txt",
    "Taskfile.yml",
    "tox.ini",
)


def collect_harness_hints(project_root: Path, *, max_chars: int = 1500) -> str:
    """Language-agnostic snippets the model can use to infer how tests are run."""
    chunks: list[str] = []
    for name in HINT_FILENAMES:
        path = project_root / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")[:max_chars]
        chunks.append(f"--- {name} ---\n{text}")
    workflows = project_root / ".github" / "workflows"
    if workflows.is_dir():
        for workflow in sorted(workflows.glob("*.yml")) + sorted(workflows.glob("*.yaml")):
            text = workflow.read_text(encoding="utf-8", errors="replace")[:max_chars]
            chunks.append(f"--- {workflow.relative_to(project_root).as_posix()} ---\n{text}")
    return "\n\n".join(chunks)


def detect_test_command(project_root: Path) -> list[str] | None:
    coverage_json = project_root / ".codeguard-coverage.json"
    if _looks_like_pytest(project_root):
        return [
            sys.executable,
            "-m",
            "pytest",
            str(project_root),
            "--rootdir",
            str(project_root),
            "--confcutdir",
            str(project_root),
            "-q",
            "--cov",
            f"--cov-report=json:{coverage_json}",
            "--cov-report=term-missing",
        ]
    package_json = project_root / "package.json"
    if package_json.is_file() and _has_npm_test_script(package_json):
        return ["npm", "test", "--silent"]
    if (project_root / "Cargo.toml").is_file():
        return ["cargo", "test"]
    if (project_root / "go.mod").is_file():
        return ["go", "test", "./..."]
    return None


def parse_coverage_json(
    path: Path,
) -> tuple[float | None, tuple[UncoveredFile, ...]]:
    if not path.is_file():
        return None, ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, ()
    totals = payload.get("totals") if isinstance(payload, dict) else None
    percent = None
    if isinstance(totals, dict):
        raw_percent = totals.get("percent_covered")
        if isinstance(raw_percent, (int, float)):
            percent = float(raw_percent)
    uncovered: list[UncoveredFile] = []
    files = payload.get("files") if isinstance(payload, dict) else None
    if isinstance(files, dict):
        for file_path, info in files.items():
            if not isinstance(info, dict):
                continue
            missing = info.get("missing_lines", [])
            if not isinstance(missing, list):
                continue
            missing_lines = tuple(
                int(num) for num in missing if isinstance(num, int)
            )
            if not missing_lines:
                continue
            summary = info.get("summary", {})
            file_percent = 0.0
            if isinstance(summary, dict) and isinstance(
                summary.get("percent_covered"), (int, float)
            ):
                file_percent = float(summary["percent_covered"])
            uncovered.append(
                UncoveredFile(
                    path=str(file_path),
                    missing_lines=missing_lines,
                    percent_covered=file_percent,
                )
            )
    uncovered.sort(key=lambda item: (item.percent_covered, item.path))
    return percent, tuple(uncovered)


def _looks_like_pytest(project_root: Path) -> bool:
    if (project_root / "pytest.ini").is_file():
        return True
    if (project_root / "conftest.py").is_file():
        return True
    pyproject = project_root / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8", errors="replace")
        if "[tool.pytest" in text or "pytest" in text:
            return True
    tests_dir = project_root / "tests"
    if tests_dir.is_dir() and any(tests_dir.rglob("test_*.py")):
        return True
    if any(project_root.glob("test_*.py")):
        return True
    return False


def _has_npm_test_script(package_json: Path) -> bool:
    try:
        payload = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    scripts = payload.get("scripts") if isinstance(payload, dict) else None
    return isinstance(scripts, dict) and isinstance(scripts.get("test"), str)


def _isolated_env(project_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        upper = key.upper()
        if upper.startswith(("COV_", "COVERAGE", "PYTEST_")):
            env.pop(key, None)
    env["COVERAGE_FILE"] = str(project_root / ".coverage.codeguard")
    return env


def _output_to_str(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
