from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import CompletionOnlyLlama, FakeLlama
from utils.config import ModelSettings
from utils.model_runner import (
    ModelRunner,
    ModelRunnerError,
    _default_llama_factory,
    _extract_chat_text,
    _extract_completion_text,
    prompt_budget_tokens,
)


def settings(**kwargs) -> ModelSettings:
    base = {
        "model_path": "models/fake.gguf",
        "gpu_layers": -1,
        "context_window": 8192,
        "n_threads": 4,
        "n_batch": 128,
        "offload_kqv": True,
        "max_tokens": 256,
        "extra": {"seed": 1},
    }
    base.update(kwargs)
    return ModelSettings(**base)


def test_load_maps_hardware_kwargs(tmp_path: Path) -> None:
    created = {}

    def factory(**kwargs):
        created.update(kwargs)
        return FakeLlama(**kwargs)

    runner = ModelRunner(settings(), llama_factory=factory, project_root=tmp_path)
    runner.load()
    runner.load()  # idempotent
    assert created["n_gpu_layers"] == -1
    assert created["n_ctx"] == 8192
    assert created["offload_kqv"] is True
    assert created["n_threads"] == 4
    assert created["seed"] == 1
    assert created["model_path"].endswith("models/fake.gguf")
    assert runner.loaded
    assert runner.complete("hello") == "FINDING: analyzed 5 chars"
    runner.unload()
    assert runner.loaded is False


def test_missing_model_file(tmp_path: Path) -> None:
    runner = ModelRunner(settings(), project_root=tmp_path)
    with pytest.raises(ModelRunnerError, match="not found"):
        runner.load()


def test_factory_failure(tmp_path: Path) -> None:
    def factory(**kwargs):
        raise RuntimeError("cuda explode")

    runner = ModelRunner(settings(), llama_factory=factory, project_root=tmp_path)
    with pytest.raises(ModelRunnerError, match="failed to initialize"):
        runner.load()


def test_complete_requires_load(tmp_path: Path) -> None:
    runner = ModelRunner(settings(), llama_factory=lambda **k: FakeLlama(**k), project_root=tmp_path)
    with pytest.raises(ModelRunnerError, match="not loaded"):
        runner.complete("x")


def test_completion_api_fallback(tmp_path: Path) -> None:
    runner = ModelRunner(
        settings(),
        llama_factory=lambda **k: CompletionOnlyLlama(**k),
        project_root=tmp_path,
    )
    runner.load()
    # create_chat_completion exists on CompletionOnlyLlama but raises AttributeError
    # hasattr will still be True. Adjust: use an object without the method.
    class OnlyCall:
        def __call__(self, prompt, **kwargs):
            return {"choices": [{"text": "plain"}]}

    runner._llm = OnlyCall()
    assert runner.complete("p") == "plain"


def test_inference_failure(tmp_path: Path) -> None:
    class Boom(FakeLlama):
        def create_chat_completion(self, messages, **kwargs):
            raise RuntimeError("oom")

    runner = ModelRunner(settings(), llama_factory=lambda **k: Boom(**k), project_root=tmp_path)
    runner.load()
    with pytest.raises(ModelRunnerError, match="inference failed"):
        runner.complete("x")


def test_bad_payloads() -> None:
    with pytest.raises(ModelRunnerError, match="unexpected chat"):
        _extract_chat_text({})
    with pytest.raises(ModelRunnerError, match="not a string"):
        _extract_chat_text({"choices": [{"message": {"content": 1}}]})
    with pytest.raises(ModelRunnerError, match="unexpected completion"):
        _extract_completion_text({})
    with pytest.raises(ModelRunnerError, match="not a string"):
        _extract_completion_text({"choices": [{"text": 1}]})


def test_close_error_during_unload(tmp_path: Path) -> None:
    class Nasty(FakeLlama):
        def close(self):
            raise RuntimeError("already freed")

    runner = ModelRunner(settings(), llama_factory=lambda **k: Nasty(**k), project_root=tmp_path)
    runner.load()
    runner.unload()
    runner.unload()


def test_absolute_model_path(tmp_path: Path) -> None:
    gguf = tmp_path / "abs.gguf"
    runner = ModelRunner(
        settings(model_path=str(gguf)),
        llama_factory=lambda **k: FakeLlama(**k),
        project_root=tmp_path,
    )
    assert runner.model_path == gguf


def test_n_threads_omitted(tmp_path: Path) -> None:
    created = {}

    def factory(**kwargs):
        created.update(kwargs)
        return FakeLlama(**kwargs)

    runner = ModelRunner(
        settings(n_threads=None),
        llama_factory=factory,
        project_root=tmp_path,
    )
    runner.load()
    assert "n_threads" not in created


def test_prompt_budget() -> None:
    assert prompt_budget_tokens(65536, 4096) >= 512
    assert prompt_budget_tokens(1024, 800) == 512


def test_default_factory_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "llama_cpp":
            raise ImportError("missing llama")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ModelRunnerError, match="not installed"):
        _default_llama_factory()
