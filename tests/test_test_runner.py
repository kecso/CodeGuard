from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from utils.config import TestSettings
from utils.test_runner import (
    TestRunResult,
    TestRunner,
    UncoveredFile,
    detect_test_command,
    parse_coverage_json,
)


def test_detect_pytest(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    command = detect_test_command(tmp_path)
    assert command is not None
    assert command[:3] == [sys.executable, "-m", "pytest"]


def test_detect_pytest_via_tests_dir(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_thing.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    assert detect_test_command(tmp_path) is not None


def test_detect_npm_cargo_go(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest"}}), encoding="utf-8"
    )
    assert detect_test_command(tmp_path)[:2] == ["npm", "test"]

    cargo = tmp_path / "cargo-proj"
    cargo.mkdir()
    (cargo / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    assert detect_test_command(cargo) == ["cargo", "test"]

    go_proj = tmp_path / "go-proj"
    go_proj.mkdir()
    (go_proj / "go.mod").write_text("module x\n", encoding="utf-8")
    assert detect_test_command(go_proj) == ["go", "test", "./..."]


def test_detect_nothing(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")
    assert detect_test_command(tmp_path) is None


def test_harness_hints_are_stack_agnostic(tmp_path: Path) -> None:
    from utils.test_runner import collect_harness_hints

    (tmp_path / "README.md").write_text("# rust? maybe\nmake test\n", encoding="utf-8")
    (tmp_path / "Makefile").write_text("test:\n\tcargo test\n", encoding="utf-8")
    hints = collect_harness_hints(tmp_path)
    assert "README.md" in hints
    assert "Makefile" in hints


def test_invalid_package_json(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{", encoding="utf-8")
    assert detect_test_command(tmp_path) is None


def test_parse_coverage_json(tmp_path: Path) -> None:
    path = tmp_path / "coverage.json"
    path.write_text(
        json.dumps(
            {
                "totals": {"percent_covered": 62.5},
                "files": {
                    "app.py": {
                        "missing_lines": [10, 11, 12],
                        "summary": {"percent_covered": 40.0},
                    },
                    "ok.py": {"missing_lines": [], "summary": {"percent_covered": 100}},
                    "bad.py": "nope",
                },
            }
        ),
        encoding="utf-8",
    )
    percent, uncovered = parse_coverage_json(path)
    assert percent == 62.5
    assert len(uncovered) == 1
    assert uncovered[0].path == "app.py"
    assert uncovered[0].missing_lines == (10, 11, 12)


def test_parse_coverage_edge_cases(tmp_path: Path) -> None:
    path = tmp_path / "coverage.json"
    path.write_text(
        json.dumps(
            {
                "totals": {"percent_covered": "na"},
                "files": {
                    "a.py": {"missing_lines": "nope"},
                    "b.py": {"missing_lines": [1, "x", 2], "summary": []},
                    "c.py": {"missing_lines": [3], "summary": {"percent_covered": 12.5}},
                },
            }
        ),
        encoding="utf-8",
    )
    percent, uncovered = parse_coverage_json(path)
    assert percent is None
    paths = {item.path: item for item in uncovered}
    assert "a.py" not in paths
    assert paths["b.py"].missing_lines == (1, 2)
    assert paths["b.py"].percent_covered == 0.0
    assert paths["c.py"].percent_covered == 12.5


def test_parse_coverage_missing_and_invalid(tmp_path: Path) -> None:
    assert parse_coverage_json(tmp_path / "nope.json") == (None, ())
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert parse_coverage_json(bad) == (None, ())


def test_pytest_without_coverage_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    class Result:
        returncode = 1
        stdout = "failed"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: Result())
    result = TestRunner(TestSettings()).run(tmp_path)
    assert result.exit_code == 1
    assert any("coverage.json" in note for note in result.notes)


def test_prompt_context_truncation() -> None:
    uncovered = tuple(
        UncoveredFile(path=f"f{i}.py", missing_lines=tuple(range(90)), percent_covered=10.0)
        for i in range(42)
    )
    result = TestRunResult(
        detected=True,
        command=("pytest",),
        exit_code=1,
        stdout="out" * 2000,
        stderr="err" * 2000,
        coverage_percent=12.0,
        uncovered=uncovered,
        notes=("flaky",),
    )
    text = result.to_prompt_context()
    assert "Line coverage: 12.00%" in text
    assert "and 2 more files" in text
    assert ",..." in text
    empty = TestRunResult(detected=False)
    assert "any language or stack" in empty.to_prompt_context()


def test_runner_disabled(tmp_path: Path) -> None:
    result = TestRunner(TestSettings(enabled=False)).run(tmp_path)
    assert result.detected is False
    assert "disabled" in result.notes[0]


def test_runner_undetected(tmp_path: Path) -> None:
    result = TestRunner(TestSettings()).run(tmp_path)
    assert result.detected is False


def test_runner_executes_sample_project() -> None:
    sample = Path(__file__).resolve().parents[1] / "testdata" / "sample_project"
    result = TestRunner(TestSettings(timeout_seconds=120)).run(sample)
    assert result.detected is True
    assert result.exit_code == 0
    assert result.coverage_percent is not None
    assert result.coverage_percent < 100
    paths = {item.path for item in result.uncovered}
    assert any(path.endswith("app.py") for path in paths)
    missing = next(item.missing_lines for item in result.uncovered if item.path.endswith("app.py"))
    assert 16 in missing


def test_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=1, output="out", stderr="err")

    monkeypatch.setattr(subprocess, "run", boom)
    result = TestRunner(TestSettings(timeout_seconds=1)).run(tmp_path)
    assert result.timed_out is True
    assert result.passed is None
    assert "out" in result.stdout


def test_missing_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest"}}), encoding="utf-8"
    )

    def boom(*args, **kwargs):
        raise FileNotFoundError("npm")

    monkeypatch.setattr(subprocess, "run", boom)
    result = TestRunner(TestSettings()).run(tmp_path)
    assert result.exit_code == 127


def test_pyproject_pytest_marker(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ntestpaths=['tests']\n", encoding="utf-8"
    )
    assert detect_test_command(tmp_path) is not None


def test_conftest_means_pytest(tmp_path: Path) -> None:
    (tmp_path / "conftest.py").write_text("# fixture\n", encoding="utf-8")
    assert detect_test_command(tmp_path) is not None


def test_root_test_module(tmp_path: Path) -> None:
    (tmp_path / "test_foo.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    assert detect_test_command(tmp_path) is not None
