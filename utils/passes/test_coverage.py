from __future__ import annotations

from utils.passes.base import PassContext

COVERAGE_INSTRUCTIONS = """You are a test architect. Target repositories may be any
language, build system, or layout — Python, JS, Rust, Go, Java, C, scripts, or
something idiosyncratic. Infer how THIS project is tested from the source and
any harness hints (README, CI, Makefiles, package manifests). Heuristic
auto-run of pytest/npm/cargo/go is only an optional shortcut.

Use executed-test evidence when present. If it is missing, infer likely
untested paths from the source and say that you are inferring.
Treat a Step 0 verdict of missing or misaligned test documentation as a
first-class coverage hole: nobody can run the suite reliably.

Prioritize:
- Untested branches on security, auth, money, persistence, and cleanup paths
- Tests that exist but assert too little (false confidence)
- Missing negative / error-path tests
- Flaky or environment-coupled tests suggested by output
- Code that is uncovered AND high-risk, not merely uncovered

Output Markdown with:
1. Coverage holes — file, missing behavior, why it is risky, suggested test
2. Suite health — failures, timeouts, missing harness, how this repo appears to run tests
3. Recommended first tests — the smallest set that closes the highest risk

Do not recommend deleting tests solely to raise a percentage.
"""


class TestCoveragePass:
    id = "test_coverage"
    title = "Test Coverage Holes"

    def build_prompt(self, code: str, context: PassContext) -> str:
        extra = (
            context.test_result.to_prompt_context()
            if context.test_result is not None
            else "Test suite was not executed for this run."
        )
        step0 = ""
        if context.harness_docs is not None and hasattr(context.harness_docs, "to_prompt_context"):
            step0 = context.harness_docs.to_prompt_context() + "\n\n"
        return (
            f"{COVERAGE_INSTRUCTIONS}\n"
            f"Repository: {context.repo_name}\n"
            f"Chunk: {context.chunk_index} of {context.chunk_count}\n\n"
            f"{step0}"
            f"Test execution evidence:\n{extra}\n\n"
            f"Source:\n{code}\n"
        )
