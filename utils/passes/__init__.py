from __future__ import annotations

from utils.passes.algorithmic import AlgorithmicPass
from utils.passes.base import AnalysisPass, PassContext
from utils.passes.memory import MemoryPass
from utils.passes.security import SecurityPass
from utils.passes.test_coverage import TestCoveragePass

PASS_REGISTRY: dict[str, AnalysisPass] = {
    "security": SecurityPass(),
    "memory": MemoryPass(),
    "algorithmic": AlgorithmicPass(),
    "test_coverage": TestCoveragePass(),
}


class UnknownPassError(ValueError):
    """Raised when config.analysis_passes names a pass that is not registered."""


def resolve_passes(pass_ids: tuple[str, ...] | list[str]) -> list[AnalysisPass]:
    unknown = [pass_id for pass_id in pass_ids if pass_id not in PASS_REGISTRY]
    if unknown:
        available = ", ".join(sorted(PASS_REGISTRY))
        raise UnknownPassError(
            f"Unknown analysis passes: {', '.join(unknown)}. Available: {available}"
        )
    return [PASS_REGISTRY[pass_id] for pass_id in pass_ids]


def available_passes() -> tuple[str, ...]:
    return tuple(PASS_REGISTRY.keys())
