from __future__ import annotations

import pytest

from utils.passes import UnknownPassError, available_passes, resolve_passes
from utils.passes.base import PassContext
from utils.test_runner import TestRunResult


def test_registry_contains_initial_passes() -> None:
    ids = available_passes()
    assert ids == ("security", "memory", "algorithmic", "test_coverage")


def test_resolve_unknown() -> None:
    with pytest.raises(UnknownPassError, match="secret-scan"):
        resolve_passes(["security", "secret-scan"])


def test_each_pass_builds_prompt() -> None:
    context = PassContext(repo_name="svc", chunk_index=1, chunk_count=2)
    code = "// File: a.py\nprint(1)\n"
    for pass_ in resolve_passes(available_passes()):
        prompt = pass_.build_prompt(code, context)
        assert "svc" in prompt
        assert "Chunk: 1 of 2" in prompt
        assert "print(1)" in prompt
        assert pass_.title


def test_coverage_pass_includes_test_evidence() -> None:
    pass_ = resolve_passes(["test_coverage"])[0]
    result = TestRunResult(
        detected=True,
        command=("pytest",),
        exit_code=1,
        coverage_percent=40.0,
        notes=("failed",),
    )
    prompt = pass_.build_prompt(
        "code",
        PassContext(
            repo_name="svc",
            chunk_index=1,
            chunk_count=1,
            test_result=result,
        ),
    )
    assert "Line coverage: 40.00%" in prompt
    without = pass_.build_prompt(
        "code",
        PassContext(repo_name="svc", chunk_index=1, chunk_count=1),
    )
    assert "was not executed" in without
    assert "any" in without and "language" in without
