from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from utils.test_runner import TestRunResult


@dataclass(frozen=True)
class PassContext:
    repo_name: str
    chunk_index: int
    chunk_count: int
    test_result: TestRunResult | None = None
    harness_docs: object | None = None


class AnalysisPass(Protocol):
    id: str
    title: str

    def build_prompt(self, code: str, context: PassContext) -> str:
        ...
