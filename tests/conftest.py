from __future__ import annotations

import json
from pathlib import Path

import pytest
from git import Repo


class FakeLlama:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.prompts: list[str] = []
        self.closed = False

    def create_chat_completion(self, messages, **kwargs):
        prompt = messages[0]["content"]
        self.prompts.append(prompt)
        return {
            "choices": [
                {
                    "message": {
                        "content": f"FINDING: analyzed {len(prompt)} chars"
                    }
                }
            ]
        }

    def close(self):
        self.closed = True


class CompletionOnlyLlama(FakeLlama):
    def create_chat_completion(self, messages, **kwargs):
        raise AttributeError("no chat")

    def __call__(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return {"choices": [{"text": "completion-ok"}]}


@pytest.fixture
def fake_llama_factory():
    created: list[FakeLlama] = []

    def factory(**kwargs):
        llm = FakeLlama(**kwargs)
        created.append(llm)
        return llm

    factory.created = created  # type: ignore[attr-defined]
    return factory


def write_config(path: Path, **overrides) -> Path:
    payload = {
        "model_settings": {
            "model_path": "models/fake.gguf",
            "gpu_layers": -1,
            "context_window": 4096,
            "max_tokens": 128,
        },
        "global_exclusions": {
            "directories": ["node_modules", ".git", "dist"],
            "extensions": [".png", ".lock", "-lock.json"],
            "max_file_bytes": 1000000,
        },
        "analysis_passes": ["security", "memory", "algorithmic", "test_coverage"],
        "test_settings": {"enabled": True, "timeout_seconds": 60},
        "repositories": [
            {
                "name": "sample",
                "git_url": "ssh://git@example/sample.git",
                "branch": "main",
                "output_report_path": "reports/audit.md",
            }
        ],
    }
    _deep_update(payload, overrides)
    target = path / "config.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def _deep_update(base: dict, overrides: dict) -> None:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value


def init_repo_with_files(root: Path, files: dict[str, str], branch: str = "main") -> Repo:
    root.mkdir(parents=True, exist_ok=True)
    repo = Repo.init(root, initial_branch=branch)
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Fixture")
        writer.set_value("user", "email", "fixture@test")
    for relative, content in files.items():
        dest = root / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        repo.index.add([relative])
    repo.index.commit("initial")
    return repo


def make_mirror(tmp_path: Path, files: dict[str, str], branch: str = "main") -> Path:
    src = tmp_path / "upstream"
    init_repo_with_files(src, files, branch=branch)
    bare = tmp_path / "mirror.git"
    Repo.clone_from(src, bare, bare=True)
    return bare
