"""Walk a checkout, drop excluded noise, and pack source into LLM payloads."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from utils.config import GlobalExclusions

FILE_HEADER_TEMPLATE = "// File: {path}"


@dataclass(frozen=True)
class ExtractedFile:
    relative_path: str
    content: str

    @property
    def packaged(self) -> str:
        return f"{FILE_HEADER_TEMPLATE.format(path=self.relative_path)}\n{self.content}"


@dataclass(frozen=True)
class ExtractionResult:
    files: tuple[ExtractedFile, ...]
    skipped_binary: tuple[str, ...]
    skipped_oversize: tuple[str, ...]
    skipped_unreadable: tuple[str, ...]

    @property
    def payload(self) -> str:
        return package_files(self.files)


class FileExtractor:
    def __init__(
        self,
        exclusions: GlobalExclusions,
        *,
        extra_exclude_paths: tuple[str, ...] = (),
        extra_exclude_dirs: tuple[str, ...] = (),
    ) -> None:
        self.exclusions = exclusions
        self.extra_exclude_paths = {
            path.replace("\\", "/").lstrip("./") for path in extra_exclude_paths
        }
        self.extra_exclude_dirs = tuple(
            path.replace("\\", "/").strip("/") for path in extra_exclude_dirs if path
        )

    def extract(self, root: Path) -> ExtractionResult:
        files: list[ExtractedFile] = []
        skipped_binary: list[str] = []
        skipped_oversize: list[str] = []
        skipped_unreadable: list[str] = []
        root = root.resolve()
        for path in _walk_source_files(
            root, self.exclusions.directories, self.extra_exclude_dirs
        ):
            relative = path.relative_to(root).as_posix()
            if relative in self.extra_exclude_paths:
                continue
            if _under_extra_dir(relative, self.extra_exclude_dirs):
                continue
            if _is_excluded_file(path, relative, self.exclusions):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                skipped_unreadable.append(relative)
                continue
            if size > self.exclusions.max_file_bytes:
                skipped_oversize.append(relative)
                continue
            text = _read_text(path)
            if text is None:
                skipped_binary.append(relative)
                continue
            files.append(ExtractedFile(relative_path=relative, content=text))
        files.sort(key=lambda item: item.relative_path)
        return ExtractionResult(
            files=tuple(files),
            skipped_binary=tuple(skipped_binary),
            skipped_oversize=tuple(skipped_oversize),
            skipped_unreadable=tuple(skipped_unreadable),
        )


def package_files(files: tuple[ExtractedFile, ...] | list[ExtractedFile]) -> str:
    return "\n\n".join(item.packaged for item in files)


def estimate_tokens(text: str) -> int:
    """Conservative char/token heuristic so chunks stay inside the context window."""
    if not text:
        return 0
    return max(1, (len(text) + 2) // 3)


def chunk_files(
    files: tuple[ExtractedFile, ...] | list[ExtractedFile],
    *,
    max_tokens: int,
) -> list[str]:
    """Pack files into payloads that fit ``max_tokens``, splitting huge files."""
    if max_tokens < 32:
        raise ValueError("max_tokens must be >= 32")
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for extracted in files:
        pieces = _split_file(extracted, max_tokens)
        for piece in pieces:
            tokens = estimate_tokens(piece)
            if current and current_tokens + tokens > max_tokens:
                chunks.append("\n\n".join(current))
                current = []
                current_tokens = 0
            current.append(piece)
            current_tokens += tokens
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _split_file(extracted: ExtractedFile, max_tokens: int) -> list[str]:
    packaged = extracted.packaged
    if estimate_tokens(packaged) <= max_tokens:
        return [packaged]
    header = FILE_HEADER_TEMPLATE.format(path=extracted.relative_path)
    lines = extracted.content.splitlines(keepends=True)
    pieces: list[str] = []
    buf: list[str] = []
    part = 1
    for line in lines:
        candidate = buf + [line]
        body = "".join(candidate)
        labeled = f"{header} (part {part})\n{body}"
        if buf and estimate_tokens(labeled) > max_tokens:
            pieces.append(f"{header} (part {part})\n{''.join(buf)}")
            part += 1
            buf = [line]
        else:
            buf = candidate
    if buf:
        pieces.append(f"{header} (part {part})\n{''.join(buf)}")
    return pieces


def _walk_source_files(
    root: Path,
    excluded_directories: tuple[str, ...],
    extra_exclude_dirs: tuple[str, ...] = (),
) -> list[Path]:
    import os

    excluded = set(excluded_directories)
    found: list[Path] = []
    for current_str, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(current_str)
        dirnames[:] = [name for name in dirnames if name not in excluded]
        try:
            relative_dir = current.relative_to(root).as_posix()
        except ValueError:
            continue
        if relative_dir != "." and _under_extra_dir(relative_dir, extra_exclude_dirs):
            dirnames[:] = []
            continue
        for filename in filenames:
            found.append(current / filename)
    return found


def _under_extra_dir(relative: str, extra_dirs: tuple[str, ...]) -> bool:
    posix = relative.replace("\\", "/").lstrip("./")
    for extra in extra_dirs:
        if posix == extra or posix.startswith(f"{extra}/"):
            return True
    return False


def _is_excluded_file(
    path: Path, relative: str, exclusions: GlobalExclusions
) -> bool:
    name = path.name
    for marker in exclusions.extensions:
        if name.endswith(marker):
            return True
    for part in Path(relative).parts:
        if part in exclusions.directories:
            return True
    return False


def _read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    sample = data[:8192]
    if b"\x00" in sample:
        return None
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")
