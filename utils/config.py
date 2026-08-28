"""Shared types and JSON loading for CodeGuard."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when config.json is missing required fields or has invalid values."""


@dataclass(frozen=True)
class ModelSettings:
    model_path: str
    gpu_layers: int = -1
    context_window: int = 65536
    n_threads: int | None = None
    n_batch: int = 512
    offload_kqv: bool = True
    max_tokens: int = 4096
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GlobalExclusions:
    directories: tuple[str, ...]
    extensions: tuple[str, ...]
    max_file_bytes: int = 1_000_000


@dataclass(frozen=True)
class RepositoryConfig:
    name: str
    git_url: str
    branch: str
    output_report_dir: str
    report_prefix: str = ""


@dataclass(frozen=True)
class TestSettings:
    __test__ = False
    enabled: bool = True
    timeout_seconds: int = 600


@dataclass(frozen=True)
class ExecutionSettings:
    """Runtime policy. Parallel execution is rejected: the host is compute-limited."""

    sequential: bool = True
    skip_unchanged_commit: bool = True
    compare_to_latest_real: bool = True


@dataclass(frozen=True)
class AppConfig:
    model_settings: ModelSettings
    global_exclusions: GlobalExclusions
    repositories: tuple[RepositoryConfig, ...]
    analysis_passes: tuple[str, ...]
    test_settings: TestSettings = field(default_factory=TestSettings)
    execution: ExecutionSettings = field(default_factory=ExecutionSettings)
    workspace_dir: str = "workspace"


DEFAULT_PASSES: tuple[str, ...] = (
    "security",
    "memory",
    "algorithmic",
    "test_coverage",
)

REQUIRED_REPO_KEYS = ("name", "branch")


def _require_dict(raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} must be an object")
    return raw


def _require_str(raw: Any, where: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(f"{where} must be a non-empty string")
    return raw


def _require_int(raw: Any, where: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ConfigError(f"{where} must be an integer")
    return raw


def _as_str_tuple(raw: Any, where: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ConfigError(f"{where} must be an array of strings")
    return tuple(raw)


def _parse_model_settings(raw: Any) -> ModelSettings:
    data = _require_dict(raw, "model_settings")
    known = {
        "model_path",
        "gpu_layers",
        "context_window",
        "n_threads",
        "n_batch",
        "offload_kqv",
        "max_tokens",
    }
    extra = {key: value for key, value in data.items() if key not in known}
    n_threads = data.get("n_threads")
    if n_threads is not None:
        n_threads = _require_int(n_threads, "model_settings.n_threads")
    context_window = _require_int(
        data.get("context_window", 65536), "model_settings.context_window"
    )
    if context_window < 512:
        raise ConfigError("model_settings.context_window must be >= 512")
    return ModelSettings(
        model_path=_require_str(data.get("model_path"), "model_settings.model_path"),
        gpu_layers=_require_int(data.get("gpu_layers", -1), "model_settings.gpu_layers"),
        context_window=context_window,
        n_threads=n_threads,
        n_batch=_require_int(data.get("n_batch", 512), "model_settings.n_batch"),
        offload_kqv=bool(data.get("offload_kqv", True)),
        max_tokens=_require_int(data.get("max_tokens", 4096), "model_settings.max_tokens"),
        extra=extra,
    )


def _parse_exclusions(raw: Any) -> GlobalExclusions:
    data = _require_dict(raw, "global_exclusions")
    max_file_bytes = _require_int(
        data.get("max_file_bytes", 1_000_000), "global_exclusions.max_file_bytes"
    )
    if max_file_bytes < 1:
        raise ConfigError("global_exclusions.max_file_bytes must be >= 1")
    return GlobalExclusions(
        directories=_as_str_tuple(
            data.get("directories", []), "global_exclusions.directories"
        ),
        extensions=_as_str_tuple(
            data.get("extensions", []), "global_exclusions.extensions"
        ),
        max_file_bytes=max_file_bytes,
    )


def _parse_repository(raw: Any, index: int) -> RepositoryConfig:
    data = _require_dict(raw, f"repositories[{index}]")
    missing = [key for key in REQUIRED_REPO_KEYS if key not in data]
    if missing:
        raise ConfigError(f"repositories[{index}] missing keys: {', '.join(missing)}")
    directory, prefix = _parse_report_location(data, index)
    return RepositoryConfig(
        name=_require_str(data["name"], f"repositories[{index}].name"),
        git_url=_parse_git_url(data, index),
        branch=_require_str(data["branch"], f"repositories[{index}].branch"),
        output_report_dir=directory,
        report_prefix=prefix,
    )


def _parse_git_url(data: dict[str, Any], index: int) -> str:
    raw = data.get("git_url", data.get("local_mirror_url"))
    if raw is None:
        raise ConfigError(f"repositories[{index}] needs git_url")
    return _require_str(raw, f"repositories[{index}].git_url")


def _parse_report_location(data: dict[str, Any], index: int) -> tuple[str, str]:
    from utils.reports import report_target_from_paths

    directory_raw = data.get("output_report_dir")
    path_raw = data.get("output_report_path")
    prefix_raw = data.get("report_prefix")
    if directory_raw is None and path_raw is None:
        raise ConfigError(
            f"repositories[{index}] needs output_report_dir or output_report_path"
        )
    directory = None if directory_raw is None else _require_str(
        directory_raw, f"repositories[{index}].output_report_dir"
    )
    path = None if path_raw is None else _require_str(
        path_raw, f"repositories[{index}].output_report_path"
    )
    prefix = None
    if prefix_raw is not None and prefix_raw != "":
        prefix = _require_str(prefix_raw, f"repositories[{index}].report_prefix")
    target = report_target_from_paths(
        output_report_path=path,
        output_report_dir=directory,
        report_prefix=prefix,
    )
    return target.directory, target.prefix


def _parse_test_settings(raw: Any) -> TestSettings:
    if raw is None:
        return TestSettings()
    data = _require_dict(raw, "test_settings")
    timeout = _require_int(
        data.get("timeout_seconds", 600), "test_settings.timeout_seconds"
    )
    if timeout < 1:
        raise ConfigError("test_settings.timeout_seconds must be >= 1")
    return TestSettings(
        enabled=bool(data.get("enabled", True)),
        timeout_seconds=timeout,
    )


def _parse_execution(raw: Any) -> ExecutionSettings:
    if raw is None:
        return ExecutionSettings()
    data = _require_dict(raw, "execution")
    sequential = bool(data.get("sequential", True))
    if not sequential:
        raise ConfigError(
            "execution.sequential must be true: CodeGuard is compute-limited "
            "and always audits repositories one at a time"
        )
    return ExecutionSettings(
        sequential=True,
        skip_unchanged_commit=bool(data.get("skip_unchanged_commit", True)),
        compare_to_latest_real=bool(data.get("compare_to_latest_real", True)),
    )


def _parse_passes(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return DEFAULT_PASSES
    passes = _as_str_tuple(raw, "analysis_passes")
    if not passes:
        raise ConfigError("analysis_passes must not be empty")
    return passes


def load_config(path: Path | str) -> AppConfig:
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"config file not found: {config_path}")
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {config_path}: {exc}") from exc
    data = _require_dict(payload, "config")
    model_settings = _parse_model_settings(data.get("model_settings"))
    global_exclusions = _parse_exclusions(data.get("global_exclusions"))
    repositories_raw = data.get("repositories")
    if not isinstance(repositories_raw, list) or not repositories_raw:
        raise ConfigError("repositories must be a non-empty array")
    return AppConfig(
        model_settings=model_settings,
        global_exclusions=global_exclusions,
        repositories=tuple(
            _parse_repository(item, index)
            for index, item in enumerate(repositories_raw)
        ),
        analysis_passes=_parse_passes(data.get("analysis_passes")),
        test_settings=_parse_test_settings(data.get("test_settings")),
        execution=_parse_execution(data.get("execution")),
        workspace_dir=_require_str(
            data.get("workspace_dir", "workspace"), "workspace_dir"
        ),
    )
