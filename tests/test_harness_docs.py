from __future__ import annotations

from pathlib import Path

from utils.config import TestSettings
from utils.harness_docs import (
    VERDICT_MISALIGNED,
    VERDICT_MISSING,
    VERDICT_VIABLE,
    assess_harness_docs,
    extract_claimed_commands,
)


def test_missing_when_no_docs_and_no_harness(tmp_path: Path) -> None:
    (tmp_path / "main.c").write_text("int main() { return 0; }\n", encoding="utf-8")
    result = assess_harness_docs(tmp_path, TestSettings())
    assert result.verdict == VERDICT_MISSING
    assert "No documentation" in result.issues[0]
    section = result.to_section()
    assert section.title.startswith("Step 0")
    assert "`missing`" in section.body


def test_missing_when_runnable_but_undocumented(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        "from app import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    result = assess_harness_docs(tmp_path, TestSettings(timeout_seconds=60))
    assert result.verdict == VERDICT_MISSING
    assert result.auto_detected
    assert "no viable documentation" in result.issues[0]


def test_viable_when_readme_matches_pytest(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "# Demo\n\nRun tests with:\n\n    python -m pytest\n",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        "from app import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    result = assess_harness_docs(tmp_path, TestSettings(timeout_seconds=60))
    assert result.verdict == VERDICT_VIABLE
    assert result.claimed
    assert result.claimed[0].argv[:3] == ("python", "-m", "pytest") or result.claimed[
        0
    ].argv[0] in {"python", "pytest"}


def test_misaligned_when_docs_name_a_missing_command(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Run `make test` before you push.\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    result = assess_harness_docs(tmp_path, TestSettings(timeout_seconds=30))
    assert result.verdict == VERDICT_MISALIGNED
    assert any("does not match reality" in issue for issue in result.issues)


def test_extract_npm_and_makefile(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "jest"}}', encoding="utf-8"
    )
    (tmp_path / "Makefile").write_text("test:\n\tjest\n", encoding="utf-8")
    claimed = extract_claimed_commands(tmp_path)
    argv_sets = {item.argv for item in claimed}
    assert ("npm", "test") in argv_sets
    assert ("make", "test") in argv_sets
