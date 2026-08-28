"""Step 0: is there viable documentation for how to run this project's tests?"""

from __future__ import annotations

import json
import logging
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from utils.config import TestSettings
from utils.reports import ReportSection
from utils.test_runner import TestRunner, collect_harness_hints, detect_test_command

logger = logging.getLogger("codeguard.harness_docs")

VERDICT_VIABLE = "viable"
VERDICT_MISSING = "missing"
VERDICT_MISALIGNED = "misaligned"

_DOC_NAMES = (
    "README.md",
    "README",
    "CONTRIBUTING.md",
    "CONTRIBUTING",
    "Makefile",
    "makefile",
    "justfile",
    "package.json",
    "docs/testing.md",
    "docs/TESTING.md",
    "docs/development.md",
)

_COMMAND_RE = re.compile(
    r"(?:"
    r"npm(?:\s+run)?\s+test[^\n]*"
    r"|pnpm(?:\s+run)?\s+test[^\n]*"
    r"|yarn(?:\s+run)?\s+test[^\n]*"
    r"|bun\s+test[^\n]*"
    r"|python3?\s+-m\s+pytest[^\n]*"
    r"|pytest[^\n]*"
    r"|cargo\s+test[^\n]*"
    r"|go\s+test[^\n]*"
    r"|make\s+test[^\n]*"
    r"|mvn(?:\s+[^\n]*)?\s+test[^\n]*"
    r"|gradlew?\s+test[^\n]*"
    r"|bundle\s+exec\s+rspec[^\n]*"
    r")",
    re.IGNORECASE,
)

_NOT_A_REAL_COMMAND = re.compile(
    r"command not found|no such file or directory|not a target|"
    r"no rule to make target|npm err! missing script: \"?test\"?|"
    r"error: unexpected argument",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ClaimedCommand:
    argv: tuple[str, ...]
    source: str
    snippet: str


@dataclass(frozen=True)
class HarnessDocAssessment:
    verdict: str
    claimed: tuple[ClaimedCommand, ...]
    auto_detected: tuple[str, ...]
    probed: tuple[str, ...] = ()
    exit_code: int | None = None
    timed_out: bool = False
    issues: tuple[str, ...] = ()
    evidence: str = ""

    @property
    def viable(self) -> bool:
        return self.verdict == VERDICT_VIABLE

    def to_section(self) -> ReportSection:
        claimed_txt = (
            ", ".join(
                f"`{' '.join(item.argv)}` ({item.source})" for item in self.claimed
            )
            or "_none_"
        )
        auto = " ".join(self.auto_detected) if self.auto_detected else "_none_"
        probed = " ".join(self.probed) if self.probed else "_none_"
        lines = [
            f"- Verdict: `{self.verdict}`",
            f"- Documented commands: {claimed_txt}",
            f"- Auto-detected command: `{auto}`",
            f"- Probed command: `{probed}`",
        ]
        if self.exit_code is not None:
            lines.append(f"- Probe exit code: {self.exit_code}")
        if self.timed_out:
            lines.append("- Probe timed out")
        if self.issues:
            lines.append("- Issues:")
            lines.extend(f"  - {issue}" for issue in self.issues)
        if self.evidence.strip():
            lines.extend(["", "Evidence (truncated):", "", "```", self.evidence.strip()[:2000], "```"])
        return ReportSection(title="Step 0: Test execution documentation", body="\n".join(lines))

    def to_prompt_context(self) -> str:
        return (
            f"Step 0 harness-doc verdict: {self.verdict}\n"
            f"Issues: {'; '.join(self.issues) or 'none'}\n"
        )


def assess_harness_docs(
    project_root: Path,
    settings: TestSettings,
    *,
    runner: TestRunner | None = None,
) -> HarnessDocAssessment:
    """Deterministic step 0: docs present, and do they match a command that actually runs?"""
    claimed = extract_claimed_commands(project_root)
    auto = detect_test_command(project_root)
    auto_tuple = tuple(auto) if auto else ()
    hints = collect_harness_hints(project_root)
    issues: list[str] = []

    if not claimed and not auto_tuple:
        issues.append(
            "No documentation describes how to run tests, and no well-known "
            "harness was found in the tree."
        )
        return HarnessDocAssessment(
            verdict=VERDICT_MISSING,
            claimed=claimed,
            auto_detected=auto_tuple,
            issues=tuple(issues),
            evidence=hints[:2000],
        )

    if not claimed:
        issues.append(
            "Tests look runnable from the tree, but there is no viable "
            "documentation of the command a human (or CI) should use."
        )
        return HarnessDocAssessment(
            verdict=VERDICT_MISSING,
            claimed=claimed,
            auto_detected=auto_tuple,
            issues=tuple(issues),
            evidence=hints[:2000],
        )

    probe_argv = _canonicalize_argv(list(claimed[0].argv))
    if not settings.enabled:
        issues.append("Documented command was not probed because test_settings.enabled is false.")
        return HarnessDocAssessment(
            verdict=VERDICT_MISALIGNED if not _looks_runnable_locally(project_root, probe_argv) else VERDICT_VIABLE,
            claimed=claimed,
            auto_detected=auto_tuple,
            issues=tuple(issues),
            evidence=hints[:2000],
        )

    runner = runner or TestRunner(settings)
    result = runner.execute(project_root, probe_argv, collect_coverage=False)
    aligned = _probe_matches_reality(result)
    if not aligned:
        issues.append(
            f"Documented command `{' '.join(probe_argv)}` does not match reality "
            "(missing executable, no such target, or the docs describe a command that cannot be run)."
        )
        verdict = VERDICT_MISALIGNED
    elif auto_tuple and not _commands_equivalent(probe_argv, list(auto_tuple)):
        issues.append(
            "Docs describe a different entrypoint than the one auto-detected from the tree. "
            "Treat the documentation as suspect until they agree."
        )
        verdict = VERDICT_MISALIGNED
    else:
        verdict = VERDICT_VIABLE
        if result.passed is False:
            issues.append(
                "The documented command ran, so the recipe is real; the suite itself reported failures."
            )

    evidence = "\n".join(
        part for part in (result.stdout[-1200:], result.stderr[-800:], hints[:800]) if part
    )
    return HarnessDocAssessment(
        verdict=verdict,
        claimed=claimed,
        auto_detected=auto_tuple,
        probed=tuple(probe_argv),
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        issues=tuple(issues),
        evidence=evidence[:2000],
    )


def extract_claimed_commands(project_root: Path) -> tuple[ClaimedCommand, ...]:
    found: list[ClaimedCommand] = []
    seen: set[tuple[str, ...]] = set()

    def add(argv: list[str], source: str, snippet: str) -> None:
        key = tuple(argv)
        if not key or key in seen:
            return
        seen.add(key)
        found.append(ClaimedCommand(argv=key, source=source, snippet=snippet.strip()[:200]))

    for relative in _documentation_files(project_root):
        path = project_root / relative
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if path.name == "package.json":
            script = _npm_test_script(text)
            if script is not None:
                add(["npm", "test"], relative, f"scripts.test: {script}")
            continue
        if path.name.lower() in {"makefile", "justfile"}:
            if re.search(r"^test\s*:", text, re.MULTILINE):
                add(["make", "test"] if path.name.lower() == "makefile" else ["just", "test"], relative, "test: target")
        for match in _COMMAND_RE.finditer(text):
            snippet = match.group(0).strip().lstrip("$").strip()
            snippet = snippet.split("#", 1)[0].strip()
            try:
                argv = shlex.split(snippet)
            except ValueError:
                continue
            if argv:
                add(argv, relative, snippet)

    return tuple(found)


def _documentation_files(project_root: Path) -> list[str]:
    names = list(_DOC_NAMES)
    workflows = project_root / ".github" / "workflows"
    if workflows.is_dir():
        for workflow in sorted(workflows.glob("*.yml")) + sorted(workflows.glob("*.yaml")):
            names.append(workflow.relative_to(project_root).as_posix())
    docs = project_root / "docs"
    if docs.is_dir():
        for extra in sorted(docs.rglob("*.md")):
            rel = extra.relative_to(project_root).as_posix()
            if rel not in names:
                names.append(rel)
    return [name for name in names if (project_root / name).is_file()]


def _npm_test_script(text: str) -> str | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    scripts = payload.get("scripts") if isinstance(payload, dict) else None
    if isinstance(scripts, dict) and isinstance(scripts.get("test"), str):
        return scripts["test"]
    return None


def _canonicalize_argv(argv: list[str]) -> list[str]:
    import sys

    if argv and argv[0] in {"python", "python3"}:
        return [sys.executable, *argv[1:]]
    return argv


def _commands_equivalent(left: list[str], right: list[str]) -> bool:
    return _normalize(left) == _normalize(right)


def _normalize(argv: list[str]) -> tuple[str, ...]:
    from pathlib import Path as _Path

    tokens: list[str] = []
    for token in argv:
        if token.startswith("-"):
            continue
        name = _Path(token).name.lower()
        if name.startswith("python"):
            tokens.append("python")
        else:
            tokens.append(token.lower())
    if "pytest" in tokens:
        return ("pytest",)
    if tokens[:2] == ["npm", "run"] and len(tokens) >= 3 and tokens[2] == "test":
        return ("npm", "test")
    if tokens[:2] == ["npm", "test"]:
        return ("npm", "test")
    if tokens and tokens[0] in {"cargo", "make", "just", "go"}:
        return tuple(tokens[:2])
    return tuple(tokens[:3])


def _probe_matches_reality(result) -> bool:
    if result.timed_out:
        return False
    if result.exit_code == 127:
        return False
    combined = f"{result.stdout}\n{result.stderr}"
    if _NOT_A_REAL_COMMAND.search(combined):
        return False
    notes = " ".join(result.notes)
    if "not found" in notes.lower():
        return False
    return result.exit_code is not None


def _looks_runnable_locally(project_root: Path, argv: list[str]) -> bool:
    if not argv:
        return False
    if argv[0] in {"pytest", "python", "python3"}:
        return bool(detect_test_command(project_root))
    if argv[:2] == ["npm", "test"]:
        return (project_root / "package.json").is_file()
    if argv[:2] == ["make", "test"]:
        return (project_root / "Makefile").is_file() or (project_root / "makefile").is_file()
    if argv[:2] == ["cargo", "test"]:
        return (project_root / "Cargo.toml").is_file()
    if argv[:2] == ["go", "test"]:
        return (project_root / "go.mod").is_file()
    return False
