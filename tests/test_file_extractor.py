from __future__ import annotations

from pathlib import Path

import pytest

from utils.config import GlobalExclusions
from utils.file_extractor import (
    ExtractedFile,
    FileExtractor,
    chunk_files,
    estimate_tokens,
    package_files,
)


def exclusions(**kwargs) -> GlobalExclusions:
    defaults = {
        "directories": (".git", "node_modules", "dist"),
        "extensions": (".png", ".lock", "-lock.json"),
        "max_file_bytes": 1000,
    }
    defaults.update(kwargs)
    return GlobalExclusions(**defaults)


def test_extracts_and_packages_with_file_headers(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.ts").write_text("export const x = 1;\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    result = FileExtractor(exclusions()).extract(tmp_path)
    assert [item.relative_path for item in result.files] == ["README.md", "src/auth.ts"]
    payload = result.payload
    assert "// File: src/auth.ts" in payload
    assert "export const x = 1;" in payload
    assert package_files(result.files) == payload


def test_prunes_excluded_directories_and_suffixes(tmp_path: Path) -> None:
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.js").write_text("nope", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "app.js").write_text("built", encoding="utf-8")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG")
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    (tmp_path / "yarn.lock").write_text("lock", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ok.py").write_text("ok\n", encoding="utf-8")
    result = FileExtractor(exclusions()).extract(tmp_path)
    assert [item.relative_path for item in result.files] == ["src/ok.py"]


def test_skips_binary_oversize_and_extra_paths(tmp_path: Path) -> None:
    (tmp_path / "bin.dat").write_bytes(b"hello\x00world")
    (tmp_path / "huge.py").write_text("x" * 2000, encoding="utf-8")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "audit.md").write_text("old", encoding="utf-8")
    (tmp_path / "keep.py").write_text("keep", encoding="utf-8")
    result = FileExtractor(
        exclusions(max_file_bytes=100),
        extra_exclude_paths=("reports/audit.md",),
    ).extract(tmp_path)
    assert [item.relative_path for item in result.files] == ["keep.py"]
    assert "bin.dat" in result.skipped_binary
    assert "huge.py" in result.skipped_oversize


def test_latin1_fallback_and_utf8(tmp_path: Path) -> None:
    (tmp_path / "utf8.py").write_text("café", encoding="utf-8")
    (tmp_path / "latin.py").write_bytes("café".encode("latin-1"))
    result = FileExtractor(exclusions()).extract(tmp_path)
    contents = {item.relative_path: item.content for item in result.files}
    assert "café" in contents["utf8.py"]
    assert contents["latin.py"]


def test_unreadable_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "gone.py"
    target.write_text("x", encoding="utf-8")

    real_stat = Path.stat

    def fake_stat(self, *args, **kwargs):
        if self == target:
            raise OSError("gone")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)
    result = FileExtractor(exclusions()).extract(tmp_path)
    assert "gone.py" in result.skipped_unreadable


def test_read_bytes_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "bad.py"
    target.write_text("x", encoding="utf-8")
    real_read = Path.read_bytes

    def fake_read(self):
        if self == target:
            raise OSError("nope")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", fake_read)
    result = FileExtractor(exclusions()).extract(tmp_path)
    assert result.files == ()
    assert "bad.py" in result.skipped_binary or result.files == ()


def test_estimate_tokens_and_chunking() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") == 1
    files = (
        ExtractedFile("a.py", "aaaa"),
        ExtractedFile("b.py", "bbbb"),
    )
    chunks = chunk_files(files, max_tokens=64)
    assert chunks
    assert "// File: a.py" in chunks[0]


def test_chunk_splits_huge_file() -> None:
    extracted = ExtractedFile("big.py", "\n".join(f"line-{i}" for i in range(200)))
    chunks = chunk_files([extracted], max_tokens=40)
    assert len(chunks) > 1
    assert "part 1" in chunks[0]
    assert "part 2" in chunks[1]


def test_chunk_rejects_tiny_budget() -> None:
    with pytest.raises(ValueError, match="max_tokens"):
        chunk_files([], max_tokens=1)


def test_directory_name_in_relative_path_excluded(tmp_path: Path) -> None:
    nested = tmp_path / "ok" / "dist" / "nope.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("nope", encoding="utf-8")
    (tmp_path / "ok" / "yes.py").write_text("yes", encoding="utf-8")
    result = FileExtractor(exclusions()).extract(tmp_path)
    assert [item.relative_path for item in result.files] == ["ok/yes.py"]


def test_extra_exclude_dirs(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("ok\n", encoding="utf-8")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "old.md").write_text("# old\n", encoding="utf-8")
    result = FileExtractor(exclusions(), extra_exclude_dirs=("reports",)).extract(tmp_path)
    assert [item.relative_path for item in result.files] == ["src/app.py"]
