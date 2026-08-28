"""Lazy llama-cpp bindings with VRAM-first loading and a hard unload path."""

from __future__ import annotations

import gc
import logging
from pathlib import Path
from typing import Any, Callable, Protocol

from utils.config import ModelSettings

logger = logging.getLogger("codeguard.model")


class ModelRunnerError(RuntimeError):
    """Raised when the GGUF model cannot be loaded or inference fails."""


class LlamaLike(Protocol):
    def create_chat_completion(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        ...

    def __call__(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        ...

    def close(self) -> None:  # pragma: no cover - optional on real llama-cpp
        ...


LlamaFactory = Callable[..., LlamaLike]


class ModelRunner:
    """Owns a single llama.cpp instance for sequential prompt passes."""

    def __init__(
        self,
        settings: ModelSettings,
        *,
        llama_factory: LlamaFactory | None = None,
        project_root: Path | None = None,
    ) -> None:
        self.settings = settings
        self._llama_factory = llama_factory
        self._project_root = project_root or Path.cwd()
        self._llm: LlamaLike | None = None

    @property
    def loaded(self) -> bool:
        return self._llm is not None

    @property
    def model_path(self) -> Path:
        path = Path(self.settings.model_path)
        if path.is_absolute():
            return path
        return (self._project_root / path).resolve()

    def load(self) -> None:
        if self._llm is not None:
            return
        path = self.model_path
        if self._llama_factory is None and not path.is_file():
            raise ModelRunnerError(f"GGUF model not found: {path}")
        factory = self._llama_factory or _default_llama_factory()
        kwargs = self._llama_kwargs(path)
        logger.info(
            "Loading model %s (n_gpu_layers=%s, n_ctx=%s, offload_kqv=%s)",
            path,
            kwargs.get("n_gpu_layers"),
            kwargs.get("n_ctx"),
            kwargs.get("offload_kqv"),
        )
        try:
            self._llm = factory(**kwargs)
        except Exception as exc:  # noqa: BLE001 - llama-cpp raises mixed types
            raise ModelRunnerError(f"failed to initialize llama.cpp: {exc}") from exc

    def unload(self) -> None:
        llm = self._llm
        self._llm = None
        if llm is None:
            gc.collect()
            return
        close = getattr(llm, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                logger.debug("llama close() failed during unload", exc_info=True)
        del llm
        gc.collect()
        logger.info("Model unloaded; garbage collector ran")

    def complete(self, prompt: str, *, max_tokens: int | None = None) -> str:
        if self._llm is None:
            raise ModelRunnerError("model is not loaded")
        token_budget = (
            self.settings.max_tokens if max_tokens is None else max_tokens
        )
        llm = self._llm
        try:
            if hasattr(llm, "create_chat_completion"):
                result = llm.create_chat_completion(
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=token_budget,
                    temperature=0.1,
                )
                return _extract_chat_text(result)
            result = llm(prompt, max_tokens=token_budget, temperature=0.1)
            return _extract_completion_text(result)
        except ModelRunnerError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ModelRunnerError(f"inference failed: {exc}") from exc

    def _llama_kwargs(self, path: Path) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model_path": str(path),
            "n_gpu_layers": self.settings.gpu_layers,
            "n_ctx": self.settings.context_window,
            "n_batch": self.settings.n_batch,
            "offload_kqv": self.settings.offload_kqv,
            "verbose": False,
        }
        if self.settings.n_threads is not None:
            kwargs["n_threads"] = self.settings.n_threads
        kwargs.update(self.settings.extra)
        return kwargs


def prompt_budget_tokens(context_window: int, completion_tokens: int) -> int:
    """Leave room for the pass instructions and the model's completion."""
    reserved = completion_tokens + max(1024, context_window // 8)
    budget = context_window - reserved
    return max(512, budget)


def _default_llama_factory() -> LlamaFactory:
    try:
        from llama_cpp import Llama
    except ImportError as exc:  # pragma: no cover - exercised via dedicated test
        raise ModelRunnerError(
            "llama-cpp-python is not installed. On the audit host run: "
            'CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python'
        ) from exc
    return Llama


def _extract_chat_text(result: dict[str, Any]) -> str:
    try:
        content = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelRunnerError(f"unexpected chat completion payload: {result!r}") from exc
    if not isinstance(content, str):
        raise ModelRunnerError("chat completion content is not a string")
    return content.strip()


def _extract_completion_text(result: dict[str, Any]) -> str:
    try:
        text = result["choices"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelRunnerError(f"unexpected completion payload: {result!r}") from exc
    if not isinstance(text, str):
        raise ModelRunnerError("completion text is not a string")
    return text.strip()
