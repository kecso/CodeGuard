"""Markdown report assembly, timestamped archives, and finding comparison."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUS_FULL = "full"
STATUS_DRY_RUN = "dry-run"
STATUS_UNCHANGED_COMMIT = "unchanged-commit"
STATUS_NO_NEW_FINDINGS = "no-new-findings"

_METADATA_HEADING = "Run metadata"
_SKIP_HEADINGS = {_METADATA_HEADING, "Delta"}
_META_LINE = re.compile(
    r"^- (Generated|Commit|Status|Previous real report|Compared to|Fingerprint):",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReportSection:
    title: str
    body: str


@dataclass(frozen=True)
class ReportTarget:
    """Where timestamped reports live under the CodeGuard deployment root."""

    directory: str
    prefix: str = ""

    def _join(self, name: str) -> str:
        directory = self.directory.strip("/")
        if directory in ("", "."):
            return name
        return f"{directory}/{name}"

    def _prefixed(self, stem: str) -> str:
        if self.prefix:
            return f"{self.prefix}-{stem}"
        return stem

    def timestamped_relpath(self, generated_at: datetime) -> str:
        return self._join(f"{self._prefixed(file_stamp(generated_at))}.md")

    def latest_real_relpath(self) -> str:
        return self._join(f"{self._prefixed('latest-real')}.md")

    def state_relpath(self) -> str:
        return self._join(f"{self._prefixed('state')}.json")


@dataclass
class AuditState:
    last_commit: str | None = None
    last_real_report: str | None = None
    last_report: str | None = None
    last_status: str | None = None
    last_fingerprint: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "last_commit": self.last_commit,
            "last_real_report": self.last_real_report,
            "last_report": self.last_report,
            "last_status": self.last_status,
            "last_fingerprint": self.last_fingerprint,
        }

    @classmethod
    def from_json(cls, payload: Any) -> AuditState:
        if not isinstance(payload, dict):
            return cls()
        return cls(
            last_commit=_optional_str(payload.get("last_commit")),
            last_real_report=_optional_str(payload.get("last_real_report")),
            last_report=_optional_str(payload.get("last_report")),
            last_status=_optional_str(payload.get("last_status")),
            last_fingerprint=_optional_str(payload.get("last_fingerprint")),
        )


def file_stamp(generated_at: datetime) -> str:
    return generated_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def display_stamp(generated_at: datetime) -> str:
    return generated_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def report_target_from_paths(
    *,
    output_report_path: str | None = None,
    output_report_dir: str | None = None,
    report_prefix: str | None = None,
) -> ReportTarget:
    if output_report_dir:
        prefix = (report_prefix or "").strip()
        return ReportTarget(directory=output_report_dir.rstrip("/"), prefix=prefix)
    if not output_report_path:
        raise ValueError("output_report_dir or output_report_path is required")
    path = Path(output_report_path)
    if path.suffix.lower() in {".md", ".markdown"}:
        directory = path.parent.as_posix()
        if directory in (".", ""):
            directory = "reports"
        return ReportTarget(directory=directory, prefix=path.stem)
    return ReportTarget(directory=path.as_posix().rstrip("/"), prefix="")


def render_report(
    *,
    repo_name: str,
    branch: str,
    model_path: str,
    generated_at: datetime,
    sections: list[ReportSection],
    preamble: str = "",
    commit: str | None = None,
    status: str = STATUS_FULL,
    previous_real: str | None = None,
) -> str:
    lines = [
        f"# CodeGuard Audit: {repo_name}",
        "",
        f"- Generated: {display_stamp(generated_at)}",
        f"- Status: `{status}`",
        f"- Branch: `{branch}`",
        f"- Commit: `{commit or 'unknown'}`",
        f"- Model: `{model_path}`",
    ]
    if previous_real:
        lines.append(f"- Previous real report: `{previous_real}`")
    lines.append("")
    if preamble.strip():
        lines.extend([preamble.strip(), ""])
    for section in sections:
        lines.extend(
            [f"## {section.title}", "", section.body.strip() or "_No output._", ""]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_empty_report(
    *,
    repo_name: str,
    branch: str,
    model_path: str,
    generated_at: datetime,
    commit: str | None,
    status: str,
    reason: str,
    previous_real: str | None = None,
) -> str:
    return render_report(
        repo_name=repo_name,
        branch=branch,
        model_path=model_path,
        generated_at=generated_at,
        commit=commit,
        status=status,
        previous_real=previous_real,
        sections=[
            ReportSection(
                title="No new findings",
                body=reason.strip() or "Nothing new was found relative to the last real report.",
            )
        ],
        preamble="",
    )


def findings_fingerprint(markdown: str) -> str:
    """Stable view of findings with timestamps and run metadata stripped.

    Used to decide whether a new full audit said anything the last real
    report did not. This is a coarse equality check; a later pass can replace
    it with statistical / structured finding diffs.
    """
    sections: list[str] = []
    current: str | None = None
    body: list[str] = []

    def flush() -> None:
        nonlocal current, body
        if current and current not in _SKIP_HEADINGS:
            text = "\n".join(line.rstrip() for line in body).strip()
            if text and text != "_No output._":
                sections.append(f"{current}\n{text}")
        current = None
        body = []

    for line in markdown.splitlines():
        if line.startswith("## "):
            flush()
            current = line[3:].strip()
            continue
        if current is None:
            continue
        if _META_LINE.match(line.strip()):
            continue
        body.append(line)
    flush()
    return "\n\n".join(sections).strip()


def has_new_findings(previous_markdown: str | None, current_markdown: str) -> bool:
    if not previous_markdown:
        return True
    previous = findings_fingerprint(previous_markdown)
    current = findings_fingerprint(current_markdown)
    if not current:
        return False
    return previous != current


def load_state(root: Path, target: ReportTarget) -> AuditState:
    path = root / target.state_relpath()
    if not path.is_file():
        return AuditState()
    try:
        return AuditState.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return AuditState()


def save_state(root: Path, target: ReportTarget, state: AuditState) -> Path:
    return write_report(
        root, target.state_relpath(), json.dumps(state.to_json(), indent=2) + "\n"
    )


def write_report(root: Path, relative_path: str, markdown: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    return path


def read_report(root: Path, relative_path: str | None) -> str | None:
    if not relative_path:
        return None
    path = root / relative_path
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None
