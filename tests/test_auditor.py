from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from auditor import build_parser, main, run_audit
from tests.conftest import FakeLlama, make_mirror, write_config
from utils.config import load_config
from utils.model_runner import ModelRunnerError


def _config_for_mirror(tmp_path: Path, mirror: Path, **overrides):
    write_config(
        tmp_path,
        repositories=[
            {
                "name": "sample",
                "local_mirror_url": str(mirror),
                "branch": "main",
                "output_report_path": "reports/audit.md",
            }
        ],
        **overrides,
    )
    return load_config(tmp_path / "config.json")


def test_full_run_with_fake_model(tmp_path: Path) -> None:
    mirror = make_mirror(
        tmp_path,
        {
            "src/app.py": "def add(a, b):\n    return a + b\n",
            "README.md": "# sample\n",
        },
    )
    created: list[FakeLlama] = []

    def factory(**kwargs):
        llm = FakeLlama(**kwargs)
        created.append(llm)
        return llm

    config = _config_for_mirror(
        tmp_path,
        mirror,
        analysis_passes=["security", "memory"],
    )
    result = run_audit(
        config,
        project_root=tmp_path,
        skip_push=True,
        llama_factory=factory,
        now=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )
    assert result.ok
    assert result.repos[0].files_extracted >= 1
    assert result.repos[0].published is True
    assert created and created[0].prompts
    report = (
        tmp_path / "workspace" / "sample" / "reports" / "audit-20260828T000000Z.md"
    ).read_text(encoding="utf-8")
    assert "# CodeGuard Audit: sample" in report
    assert "Security Vulnerabilities" in report
    assert "`full`" in report
    assert created[0].closed is True
    latest = (
        tmp_path / "workspace" / "sample" / "reports" / "audit-latest-real.md"
    )
    assert latest.is_file()


def test_dry_run_skips_model_and_push(tmp_path: Path) -> None:
    mirror = make_mirror(tmp_path, {"a.py": "print(1)\n"})

    def factory(**kwargs):
        raise AssertionError("model should not load in dry-run")

    config = _config_for_mirror(tmp_path, mirror, analysis_passes=["security"])
    result = run_audit(
        config,
        project_root=tmp_path,
        dry_run=True,
        llama_factory=factory,
        now=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )
    assert result.ok
    report = (
        tmp_path / "workspace" / "sample" / "reports" / "audit-20260828T000000Z.md"
    ).read_text(encoding="utf-8")
    assert "Dry run" in report
    assert result.repos[0].published is False


def test_empty_extraction(tmp_path: Path) -> None:
    mirror = make_mirror(tmp_path, {"logo.png": "x"})
    config = _config_for_mirror(tmp_path, mirror, analysis_passes=["security"])
    result = run_audit(
        config,
        project_root=tmp_path,
        skip_push=True,
        llama_factory=lambda **k: FakeLlama(**k),
        now=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )
    assert result.ok
    report = (
        tmp_path / "workspace" / "sample" / "reports" / "audit-20260828T000000Z.md"
    ).read_text(encoding="utf-8")
    assert "No source files remained" in report


def test_coverage_pass_without_suite(tmp_path: Path) -> None:
    mirror = make_mirror(tmp_path, {"a.py": "print(1)\n"})
    config = _config_for_mirror(tmp_path, mirror, analysis_passes=["test_coverage"])
    result = run_audit(
        config,
        project_root=tmp_path,
        skip_push=True,
        llama_factory=lambda **k: FakeLlama(**k),
        now=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )
    assert result.ok
    report = (
        tmp_path / "workspace" / "sample" / "reports" / "audit-20260828T000000Z.md"
    ).read_text(encoding="utf-8")
    assert "no auto-detected command" in report


def test_same_commit_writes_empty_report(tmp_path: Path) -> None:
    mirror = make_mirror(tmp_path, {"a.py": "print(1)\n"})
    created: list[FakeLlama] = []

    def factory(**kwargs):
        llm = FakeLlama(**kwargs)
        created.append(llm)
        return llm

    config = _config_for_mirror(tmp_path, mirror, analysis_passes=["security"])
    first = run_audit(
        config,
        project_root=tmp_path,
        llama_factory=factory,
        now=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    assert first.ok
    assert first.repos[0].status == "full"
    assert len(created) == 1
    second = run_audit(
        config,
        project_root=tmp_path,
        llama_factory=factory,
        now=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )
    assert second.ok
    assert second.repos[0].skipped_inference is True
    assert second.repos[0].status == "unchanged-commit"
    assert len(created) == 1
    empty = (
        tmp_path / "workspace" / "sample" / "reports" / "audit-20260828T000000Z.md"
    ).read_text(encoding="utf-8")
    assert "No new findings" in empty
    assert (tmp_path / "workspace" / "sample" / "reports" / "audit-20260827T000000Z.md").is_file()


def test_matching_findings_write_empty_report(tmp_path: Path) -> None:
    from git import Repo

    mirror = make_mirror(tmp_path, {"a.py": "print(1)\n"})
    config = _config_for_mirror(tmp_path, mirror, analysis_passes=["security"])
    first = run_audit(
        config,
        project_root=tmp_path,
        llama_factory=lambda **k: FakeLlama(**k),
        now=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    assert first.repos[0].status == "full"

    editor = Repo.clone_from(mirror, tmp_path / "editor")
    with editor.config_writer() as writer:
        writer.set_value("user", "name", "U")
        writer.set_value("user", "email", "u@t")
    (tmp_path / "editor" / "logo.png").write_text("x", encoding="utf-8")
    editor.index.add(["logo.png"])
    editor.index.commit("asset only")
    editor.remotes.origin.push()

    second = run_audit(
        config,
        project_root=tmp_path,
        llama_factory=lambda **k: FakeLlama(**k),
        now=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )
    assert second.ok
    assert second.repos[0].skipped_inference is False
    assert second.repos[0].status == "no-new-findings"
    empty = (
        tmp_path / "workspace" / "sample" / "reports" / "audit-20260828T000000Z.md"
    ).read_text(encoding="utf-8")
    assert "No new findings" in empty


def test_unknown_repo_filter(tmp_path: Path) -> None:
    mirror = make_mirror(tmp_path, {"a.py": "a\n"})
    config = _config_for_mirror(tmp_path, mirror)
    with pytest.raises(Exception, match="unknown repository"):
        run_audit(
            config,
            project_root=tmp_path,
            only_repos=("nope",),
            llama_factory=lambda **k: FakeLlama(**k),
        )


def test_only_repos_selects_named(tmp_path: Path) -> None:
    mirror = make_mirror(tmp_path, {"a.py": "print(1)\n"})
    write_config(
        tmp_path,
        repositories=[
            {
                "name": "sample",
                "local_mirror_url": str(mirror),
                "branch": "main",
                "output_report_path": "reports/audit.md",
            },
            {
                "name": "other",
                "local_mirror_url": str(mirror),
                "branch": "main",
                "output_report_path": "reports/other.md",
            },
        ],
        analysis_passes=["security"],
    )
    config = load_config(tmp_path / "config.json")
    result = run_audit(
        config,
        project_root=tmp_path,
        skip_push=True,
        only_repos=("sample",),
        llama_factory=lambda **k: FakeLlama(**k),
    )
    assert result.ok
    assert [item.name for item in result.repos] == ["sample"]


def test_git_failure_is_isolated(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        repositories=[
            {
                "name": "missing",
                "local_mirror_url": "/definitely/not/a/mirror.git",
                "branch": "main",
                "output_report_path": "reports/audit.md",
            }
        ],
        analysis_passes=["security"],
    )
    config = load_config(tmp_path / "config.json")
    result = run_audit(
        config,
        project_root=tmp_path,
        skip_push=True,
        llama_factory=lambda **k: FakeLlama(**k),
    )
    assert not result.ok
    assert result.failed[0].name == "missing"
    assert result.failed[0].error


def test_test_coverage_pass_runs_suite(tmp_path: Path) -> None:
    sample_src = Path(__file__).resolve().parents[1] / "testdata" / "sample_project"
    files = {
        "app.py": (sample_src / "app.py").read_text(encoding="utf-8"),
        "test_app.py": (sample_src / "test_app.py").read_text(encoding="utf-8"),
        "pyproject.toml": (sample_src / "pyproject.toml").read_text(encoding="utf-8"),
    }
    mirror = make_mirror(tmp_path, files)
    config = _config_for_mirror(
        tmp_path,
        mirror,
        analysis_passes=["test_coverage"],
    )
    result = run_audit(
        config,
        project_root=tmp_path,
        skip_push=True,
        llama_factory=lambda **k: FakeLlama(**k),
        now=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )
    assert result.ok
    assert result.repos[0].tests_detected is True
    report = (
        tmp_path / "workspace" / "sample" / "reports" / "audit-20260828T000000Z.md"
    ).read_text(encoding="utf-8")
    assert "Test Coverage Holes" in report
    assert "coverage" in report.lower()


def test_main_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(tmp_path, analysis_passes=["security"])
    monkeypatch.chdir(tmp_path)

    def fake_run(*args, **kwargs):
        from auditor import AuditResult, RepoRunResult

        return AuditResult(
            repos=(
                RepoRunResult(
                    name="sample", success=True, files_extracted=1, chunks=1
                ),
            )
        )

    monkeypatch.setattr("auditor.run_audit", fake_run)
    assert main(["-c", "config.json", "--dry-run"]) == 0


def test_main_config_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["-c", "missing.json"]) == 2


def test_main_failure_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(tmp_path, analysis_passes=["security"])
    monkeypatch.chdir(tmp_path)

    def fake_run(*args, **kwargs):
        from auditor import AuditResult, RepoRunResult

        return AuditResult(
            repos=(RepoRunResult(name="sample", success=False, error="boom"),)
        )

    monkeypatch.setattr("auditor.run_audit", fake_run)
    assert main(["-c", "config.json"]) == 1


def test_parser_flags() -> None:
    args = build_parser().parse_args(
        ["-c", "cfg.json", "--dry-run", "--skip-push", "--force", "--repo", "a", "--repo", "b", "-v"]
    )
    assert args.dry_run and args.skip_push and args.force
    assert args.repos == ["a", "b"]
    assert args.verbose


def test_unknown_pass_in_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(tmp_path, analysis_passes=["not-a-pass"])
    monkeypatch.chdir(tmp_path)
    assert main(["-c", "config.json"]) == 2


def test_main_model_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(tmp_path, analysis_passes=["security"])
    monkeypatch.chdir(tmp_path)

    def boom(*args, **kwargs):
        raise ModelRunnerError("no cuda")

    monkeypatch.setattr("auditor.run_audit", boom)
    assert main(["-c", "config.json"]) == 1


def test_run_audit_missing_model_file(tmp_path: Path) -> None:
    mirror = make_mirror(tmp_path, {"a.py": "a\n"})
    config = _config_for_mirror(tmp_path, mirror, analysis_passes=["security"])
    result = run_audit(config, project_root=tmp_path)
    assert not result.ok
    assert result.failed[0].error
    assert "not found" in result.failed[0].error
