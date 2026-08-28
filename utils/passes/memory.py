from __future__ import annotations

from utils.passes.base import PassContext

MEMORY_INSTRUCTIONS = """You are a systems engineer reviewing resource lifetime.
Analyze the provided source for leaks, unbounded growth, and lifecycle bugs.

Look for:
- Unclosed files, sockets, DB connections, HTTP sessions
- Missing finally / context-manager usage
- Caches without eviction, global registries that only grow
- Thread/task leaks, forgotten timers, unbounded queues
- GPU / native buffer mishandling if present
- Event-listener accumulation

Output Markdown with:
1. Findings — severity, file, evidence, impact, fix
2. Lifecycle notes — objects that look well-managed
3. Suggested tests — how to catch the leak (including soak/load tests)

If nothing notable is present, say so explicitly.
"""


class MemoryPass:
    id = "memory"
    title = "Memory / Resource Leakage"

    def build_prompt(self, code: str, context: PassContext) -> str:
        return (
            f"{MEMORY_INSTRUCTIONS}\n"
            f"Repository: {context.repo_name}\n"
            f"Chunk: {context.chunk_index} of {context.chunk_count}\n\n"
            f"Source:\n{code}\n"
        )
