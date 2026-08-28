# Repository Specification: CodeGuard

An on-demand, self-terminating codebase security and architectural auditing engine optimized for headless Ubuntu systems utilizing hybrid GPU/CPU hardware.

This document is the structural blueprint. The implementation in this repository follows it, with a few deliberate engineering choices called out in section 7.

## 1. System Overview

CodeGuard is designed to run as an automated, headless overnight cron job on an Ubuntu server equipped with an NVIDIA RTX 4090 (24GB VRAM) and 64GB DDR5 system RAM.

### Core Architectural Pillars

- **On-Demand Execution:** The framework spins up, allocates memory, completes its routine, and terminates. It leaves a zero-memory footprint when idle, preserving hardware resources for other high-performance tasks like game streaming.
- **Local Network Sourcing:** Bypasses external GitHub API constraints by pulling source code directly from a local git mirror server over the LAN.
- **Hybrid Memory Split-Loading:** Uses GGUF quantization to pack maximum layers into 24GB VRAM while offloading overflow structures and KV context pools into system memory.

## 2. Directory Structure

```
CodeGuard/
├── README.md
├── SPECIFICATION.md
├── requirements.txt
├── requirements-dev.txt
├── config.json
├── auditor.py
├── utils/
│   ├── __init__.py
│   ├── config.py
│   ├── git_manager.py
│   ├── file_extractor.py
│   ├── model_runner.py
│   ├── test_runner.py
│   ├── reports.py
│   └── passes/                 # Expandable analysis tasks
├── models/
│   └── .gitkeep
├── workspace/
│   └── .gitkeep
└── tests/
```

## 3. Dependencies & Prerequisites

### System Requirements

- OS: Ubuntu 22.04 LTS or newer (headless audit host)
- Drivers: NVIDIA CUDA Toolkit (v12.x recommended)
- Python: 3.10+ with venv

### Production (`requirements.txt`)

- `GitPython==3.1.43`
- `llama-cpp-python==0.3.1`

### CUDA-aware install

```bash
python3 -m venv .venv
source .venv/bin/activate
CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python==0.3.1 --no-cache-dir
```

## 4. Configuration Schema (`config.json`)

Behavior is governed by a centralized configuration file: target repositories on the local mirror, exclusions, model mapping, and the active analysis-pass list.

See the checked-in `config.json` for the live schema. The initial pass list is:

- `security`
- `memory`
- `algorithmic`
- `test_coverage`

Treat that list as the starting task set. Additional passes should register in `utils/passes/` and be opted into from config.

## 5. Functional Pipeline

Repositories are processed **sequentially** (never in parallel). The host is compute-limited; wall-clock time is not treated as a constraint. Target language and project shape do not matter.

```
[Cron Trigger]
    → Read config
    → For each repository, one at a time:
        1. FETCH: clean copy from the local LAN mirror
        2. If HEAD == last audited commit → write timestamped empty report, next repo
        3. STEP 0: report whether test-run docs exist and match a command that actually runs
        4. PARSE: extract code (any stack), strip excluded noise, chunk to context
        4. TEST:  optional auto-run if a well-known harness is obvious; else the model infers
        5. EVALUATE: sequential prompt passes (model loaded lazily)
        6. COMPARE: if findings match latest-real, keep a timestamped empty report
        7. WRITE: timestamped Markdown (history is append-only)
        8. SYNC: commit & push the new artifacts to the mirror
    → Hard unload + gc → process exit
```

### Module notes

**Git manager** — clone if missing; fetch + hard reset + clean if present; commit/push the report.

**File extractor** — recursive walk, exclusion filters, `// File: path` headers so the model keeps a file map, chunking on file boundaries when the tree exceeds the prompt budget.

**Model runner** — `n_gpu_layers=-1`, configurable `n_ctx`, `offload_kqv` for KV spill into DDR5, explicit `unload()`.

**Test runner** — optional heuristic run of well-known harnesses; otherwise README/CI/Makefile hints go to the model. Target language does not matter.

**Reports** — timestamped, append-only. Same-commit runs write an empty report without inference. After a full pass, findings are compared to `latest-real.md` (today: normalized equality; later: statistical diff).

## 6. Cron

```
0 0 * * * /path/to/CodeGuard/.venv/bin/python /path/to/CodeGuard/auditor.py >> /var/log/codeguard.log 2>&1
```

## 7. Intentional deviations from the original draft

- Repository name is **CodeGuard**, not `nightly-code-auditor`.
- The GGUF model is loaded **once per process**, not once per repository. Unload still happens at process end so the idle footprint is zero. Reloading a 70B Q4 model ten times overnight is wasted hours, not extra safety.
- Analysis tasks live in a **pass registry** so later audits (secrets, dependency CVEs, API contracts, etc.) do not require orchestrator rewrites.
- A **test_coverage** pass prefers actually running a harness when one is obvious, and otherwise lets the model infer how that project is tested. Targets are stack-agnostic.
- Reports are **timestamped and kept**. Same commit → empty report without calling the model. Matching findings vs last real report → empty report after a full pass.
- Sequential execution is mandatory (`execution.sequential` cannot be turned off).
- CodeGuard itself has a pytest suite with a **90% coverage fail-under** in CI, so gaps in *this* engine's tests fail the build and print missing lines.
- CUDA install uses `source .venv/bin/activate` and `CMAKE_ARGS="-DGGML_CUDA=on"` (the draft's `source .venv/bin/python` and `-GGUIDE` flag were not valid).
