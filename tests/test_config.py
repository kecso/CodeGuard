from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.config import ConfigError, load_config


def test_load_valid_config(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "model_settings": {
                    "model_path": "models/m.gguf",
                    "gpu_layers": -1,
                    "context_window": 8192,
                    "n_threads": 8,
                    "n_batch": 256,
                    "offload_kqv": True,
                    "max_tokens": 1024,
                    "seed": 7,
                },
                "global_exclusions": {
                    "directories": [".git"],
                    "extensions": [".png"],
                    "max_file_bytes": 10,
                },
                "analysis_passes": ["security"],
                "repositories": [
                    {
                        "name": "svc",
                        "local_mirror_url": "/tmp/svc.git",
                        "branch": "main",
                        "output_report_path": "reports/a.md",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.model_settings.model_path == "models/m.gguf"
    assert config.model_settings.extra["seed"] == 7
    assert config.model_settings.n_threads == 8
    assert config.analysis_passes == ("security",)
    assert config.repositories[0].name == "svc"
    assert config.repositories[0].output_report_dir == "reports"
    assert config.repositories[0].report_prefix == "a"
    assert config.execution.sequential is True
    assert config.git.commit_name == "CodeGuard Auditor"
    assert config.test_settings.enabled is True


def test_default_passes_and_optional_sections(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "model_settings": {"model_path": "m.gguf"},
                "global_exclusions": {"directories": [], "extensions": []},
                "repositories": [
                    {
                        "name": "svc",
                        "local_mirror_url": "x",
                        "branch": "main",
                        "output_report_path": "r.md",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    assert "test_coverage" in config.analysis_passes
    assert config.global_exclusions.max_file_bytes == 1_000_000


def test_missing_file() -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config("/no/such/config.json")


def test_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid JSON"):
        load_config(path)


@pytest.mark.parametrize(
    "payload, match",
    [
        ([], "must be an object"),
        ({}, "model_settings must be an object"),
        (
            {"model_settings": {"model_path": "m.gguf"}},
            "global_exclusions must be an object",
        ),
        (
            {
                "model_settings": {"model_path": "m.gguf"},
                "global_exclusions": {"directories": [], "extensions": []},
            },
            "repositories must be a non-empty array",
        ),
        (
            {
                "model_settings": {"model_path": "m.gguf"},
                "global_exclusions": {"directories": [], "extensions": []},
                "repositories": [{}],
            },
            "missing keys",
        ),
        (
            {
                "model_settings": {"model_path": ""},
                "global_exclusions": {"directories": [], "extensions": []},
                "repositories": [
                    {
                        "name": "a",
                        "local_mirror_url": "b",
                        "branch": "c",
                        "output_report_path": "d",
                    }
                ],
            },
            "model_path",
        ),
        (
            {
                "model_settings": {
                    "model_path": "m.gguf",
                    "context_window": 10,
                },
                "global_exclusions": {"directories": [], "extensions": []},
                "repositories": [
                    {
                        "name": "a",
                        "local_mirror_url": "b",
                        "branch": "c",
                        "output_report_path": "d",
                    }
                ],
            },
            "context_window",
        ),
        (
            {
                "model_settings": {"model_path": "m.gguf", "gpu_layers": True},
                "global_exclusions": {"directories": [], "extensions": []},
                "repositories": [
                    {
                        "name": "a",
                        "local_mirror_url": "b",
                        "branch": "c",
                        "output_report_path": "d",
                    }
                ],
            },
            "gpu_layers",
        ),
        (
            {
                "model_settings": {"model_path": "m.gguf"},
                "global_exclusions": {
                    "directories": "nope",
                    "extensions": [],
                },
                "repositories": [
                    {
                        "name": "a",
                        "local_mirror_url": "b",
                        "branch": "c",
                        "output_report_path": "d",
                    }
                ],
            },
            "directories",
        ),
        (
            {
                "model_settings": {"model_path": "m.gguf"},
                "global_exclusions": {
                    "directories": [],
                    "extensions": [],
                    "max_file_bytes": 0,
                },
                "repositories": [
                    {
                        "name": "a",
                        "local_mirror_url": "b",
                        "branch": "c",
                        "output_report_path": "d",
                    }
                ],
            },
            "max_file_bytes",
        ),
        (
            {
                "model_settings": {"model_path": "m.gguf"},
                "global_exclusions": {"directories": [], "extensions": []},
                "analysis_passes": [],
                "repositories": [
                    {
                        "name": "a",
                        "local_mirror_url": "b",
                        "branch": "c",
                        "output_report_path": "d",
                    }
                ],
            },
            "analysis_passes must not be empty",
        ),
        (
            {
                "model_settings": {"model_path": "m.gguf"},
                "global_exclusions": {"directories": [], "extensions": []},
                "test_settings": {"timeout_seconds": 0},
                "repositories": [
                    {
                        "name": "a",
                        "local_mirror_url": "b",
                        "branch": "c",
                        "output_report_path": "d",
                    }
                ],
            },
            "timeout_seconds",
        ),
        (
            {
                "model_settings": {"model_path": "m.gguf"},
                "global_exclusions": {"directories": [], "extensions": []},
                "git": "nope",
                "repositories": [
                    {
                        "name": "a",
                        "local_mirror_url": "b",
                        "branch": "c",
                        "output_report_path": "d",
                    }
                ],
            },
            "git must be an object",
        ),
    ],
)
def test_invalid_payloads(tmp_path: Path, payload, match: str) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match=match):
        load_config(path)


def test_repo_name_must_be_string(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "model_settings": {"model_path": "m.gguf"},
                "global_exclusions": {"directories": [], "extensions": []},
                "repositories": [
                    {
                        "name": 1,
                        "local_mirror_url": "b",
                        "branch": "c",
                        "output_report_path": "d",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="name"):
        load_config(path)


def test_output_report_dir_without_path(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "model_settings": {"model_path": "m.gguf"},
                "global_exclusions": {"directories": [], "extensions": []},
                "repositories": [
                    {
                        "name": "svc",
                        "local_mirror_url": "x",
                        "branch": "main",
                        "output_report_dir": "reports/codeguard",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.repositories[0].output_report_dir == "reports/codeguard"
    assert config.repositories[0].report_prefix == ""


def test_repo_missing_report_location(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "model_settings": {"model_path": "m.gguf"},
                "global_exclusions": {"directories": [], "extensions": []},
                "repositories": [
                    {
                        "name": "svc",
                        "local_mirror_url": "x",
                        "branch": "main",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="output_report_dir or output_report_path"):
        load_config(path)


def test_parallel_execution_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "model_settings": {"model_path": "m.gguf"},
                "global_exclusions": {"directories": [], "extensions": []},
                "execution": {"sequential": False},
                "repositories": [
                    {
                        "name": "svc",
                        "local_mirror_url": "x",
                        "branch": "main",
                        "output_report_path": "reports/a.md",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="sequential"):
        load_config(path)
