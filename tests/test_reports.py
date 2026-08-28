from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from utils.reports import (
    AuditState,
    ReportSection,
    ReportTarget,
    STATUS_UNCHANGED_COMMIT,
    findings_fingerprint,
    has_new_findings,
    load_state,
    read_report,
    render_empty_report,
    render_report,
    report_target_from_paths,
    save_state,
    write_report,
)


def test_render_and_write(tmp_path: Path) -> None:
    markdown = render_report(
        repo_name="svc",
        branch="main",
        model_path="models/m.gguf",
        generated_at=datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc),
        sections=[ReportSection(title="Security Vulnerabilities", body="  all clear  ")],
        preamble="## Run metadata\n\n- files: 1",
        commit="abc123",
        status="full",
    )
    assert "# CodeGuard Audit: svc" in markdown
    assert "2026-08-28 12:00:00Z" in markdown
    assert "`abc123`" in markdown
    assert "## Security Vulnerabilities" in markdown
    assert "all clear" in markdown
    path = write_report(tmp_path, "reports/audit.md", markdown)
    assert path.read_text(encoding="utf-8") == markdown


def test_empty_section_placeholder() -> None:
    markdown = render_report(
        repo_name="svc",
        branch="dev",
        model_path="m.gguf",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        sections=[ReportSection(title="Empty", body="   ")],
    )
    assert "_No output._" in markdown


def test_fingerprint_ignores_timestamps_and_metadata() -> None:
    left = render_report(
        repo_name="svc",
        branch="main",
        model_path="m.gguf",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        commit="aaa",
        sections=[ReportSection(title="Security Vulnerabilities", body="SQL injection in auth.py")],
        preamble="## Run metadata\n\n- files: 1",
    )
    right = render_report(
        repo_name="svc",
        branch="main",
        model_path="m.gguf",
        generated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        commit="bbb",
        sections=[ReportSection(title="Security Vulnerabilities", body="SQL injection in auth.py")],
        preamble="## Run metadata\n\n- files: 99",
    )
    assert findings_fingerprint(left) == findings_fingerprint(right)
    assert has_new_findings(left, right) is False


def test_has_new_findings_when_body_changes() -> None:
    previous = render_report(
        repo_name="svc",
        branch="main",
        model_path="m.gguf",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        sections=[ReportSection(title="Security Vulnerabilities", body="none")],
    )
    current = render_report(
        repo_name="svc",
        branch="main",
        model_path="m.gguf",
        generated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        sections=[ReportSection(title="Security Vulnerabilities", body="XSS in widget")],
    )
    assert has_new_findings(previous, current) is True
    assert has_new_findings(None, current) is True


def test_empty_report_and_target_paths() -> None:
    text = render_empty_report(
        repo_name="svc",
        branch="main",
        model_path="m.gguf",
        generated_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
        commit="abc",
        status=STATUS_UNCHANGED_COMMIT,
        previous_real="reports/old.md",
        reason="same commit",
    )
    assert "same commit" in text
    assert "`unchanged-commit`" in text
    from_file = report_target_from_paths(output_report_path="reports/nightly.md")
    assert from_file.directory == "reports"
    assert from_file.prefix == "nightly"
    assert from_file.timestamped_relpath(
        datetime(2026, 8, 28, tzinfo=timezone.utc)
    ) == "reports/nightly-20260828T000000Z.md"
    from_dir = report_target_from_paths(output_report_dir="reports/codeguard")
    assert from_dir.latest_real_relpath() == "reports/codeguard/latest-real.md"
    dotted = report_target_from_paths(output_report_path="audit.md")
    assert dotted.directory == "reports"
    rootish = ReportTarget(directory=".", prefix="")
    assert rootish.timestamped_relpath(
        datetime(2026, 8, 28, tzinfo=timezone.utc)
    ) == "20260828T000000Z.md"
    try:
        report_target_from_paths()
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_state_roundtrip_and_corrupt(tmp_path: Path) -> None:
    from utils.reports import _optional_str

    target = ReportTarget(directory="reports", prefix="audit")
    assert load_state(tmp_path, target).last_commit is None
    save_state(tmp_path, target, AuditState(last_commit="abc"))
    assert load_state(tmp_path, target).last_commit == "abc"
    (tmp_path / "reports" / "audit-state.json").write_text("{not json", encoding="utf-8")
    assert load_state(tmp_path, target).last_commit is None
    assert AuditState.from_json("nope").last_commit is None
    assert read_report(tmp_path, None) is None
    assert read_report(tmp_path, "missing.md") is None
    assert _optional_str("") is None
    assert _optional_str(1) is None
