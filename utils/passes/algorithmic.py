from __future__ import annotations

from utils.passes.base import PassContext

ALGORITHMIC_INSTRUCTIONS = """You are a staff engineer reviewing algorithms and architecture.
Analyze the provided source for correctness, complexity, and design smell.

Look for:
- Accidental O(n^2)+ hot paths, N+1 queries, unbounded recursion
- Race conditions, TOCTOU, unsafe shared mutability
- Broken invariants, off-by-one, incorrect error swallowing
- God objects, circular dependencies, layering violations
- Dead code that still influences control flow
- Fragile time/timezone/locale handling

Output Markdown with:
1. Findings — severity, file, evidence, complexity/impact, refactor direction
2. Architectural observations
3. Suggested tests — property, concurrency, or regression tests that would lock the behavior

If nothing notable is present, say so explicitly.
"""


class AlgorithmicPass:
    id = "algorithmic"
    title = "Algorithmic Anomalies"

    def build_prompt(self, code: str, context: PassContext) -> str:
        return (
            f"{ALGORITHMIC_INSTRUCTIONS}\n"
            f"Repository: {context.repo_name}\n"
            f"Chunk: {context.chunk_index} of {context.chunk_count}\n\n"
            f"Source:\n{code}\n"
        )
