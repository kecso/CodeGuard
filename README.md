# CodeGuard

On-demand, self-terminating codebase security and architectural auditor. It is meant to run as a headless overnight job on an Ubuntu host with an NVIDIA GPU (RTX 4090 / 24GB VRAM) and a large DDR5 RAM pool, pull targets from a **local git mirror**, run structured analysis passes, optionally execute each target's own test suite when a command can be inferred, write **timestamped** Markdown reports, push those reports back to the mirror, and then release VRAM/RAM.

**Target repositories can be anything.** Language, framework, and layout do not matter. The extractor treats trees as files; the model is expected to notice how a given project is built and tested (README, CI, Makefile, `package.json`, Cargo, Gradle, ad-hoc scripts, …). A few well-known harnesses are auto-run as an optional shortcut, not as a requirement.

**Execution is always sequential.** The audit host is compute-limited (one 70B-class GGUF plus the working set). Repositories, passes, and chunks run one after another. Runtime is not optimized; overlapping jobs are rejected in config.

The analysis passes in this seed (security, memory/resource leakage, algorithmic anomalies, test-coverage holes) are the initial set. New passes register in `utils/passes/` without changing the fetch/parse/load/write pipeline.

## Layout

```
CodeGuard/
├── auditor.py               # Orchestrator (fetch → parse → test → evaluate → write → sync)
├── config.json              # Repos, exclusions, model, enabled passes
├── requirements.txt         # Production deps (GitPython + llama-cpp-python)
├── requirements-dev.txt     # Test/CI deps (no CUDA build)
├── utils/
│   ├── git_manager.py       # LAN clone, hard reset, report commit/push
│   ├── file_extractor.py    # Walk, filter, pack, chunk
│   ├── model_runner.py      # llama.cpp load / infer / hard unload
│   ├── test_runner.py       # Run the target's tests and parse coverage
│   ├── reports.py           # Markdown assembly
│   └── passes/              # Expandable analysis-pass registry
├── models/                  # GGUF weights (not committed)
├── workspace/               # Ephemeral checkouts (not committed)
└── tests/                   # CodeGuard's own suite + coverage gate
```

## Requirements

- Ubuntu 22.04+ (audit host). Development and CI can run on macOS/Linux without a GPU.
- Python 3.10+
- NVIDIA CUDA Toolkit 12.x on the audit host
- Git, with SSH reachability to the local mirror

## Setup

### Development / tests (no GPU)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

`pytest` is configured to fail if line coverage of `auditor.py` and `utils/` drops below **90%**, and to print uncovered lines (`--cov-report=term-missing`). That is how holes in *this* repository's tests show up in CI.

### Audit host (CUDA)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install GitPython==3.1.43
CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python==0.3.1 --no-cache-dir
```

`n_gpu_layers: -1` in `config.json` tells llama.cpp to put every layer that fits into VRAM and spill the rest (plus KV cache when `offload_kqv` is true) into system RAM.

Place the GGUF file at the path in `model_settings.model_path` (default `models/DeepSeek-R1-Distilled-Llama-70B-Q4_K_M.gguf`).

## Configuration

Edit `config.json`:

- `repositories` — local mirror URLs, branch, report directory inside the target repo
- `analysis_passes` — any of `security`, `memory`, `algorithmic`, `test_coverage` (add more later)
- `global_exclusions` — directories, name suffixes, max file size
- `test_settings` — whether to attempt an auto-detected test command
- `execution.sequential` — must stay `true` (compute-limited host)
- `execution.skip_unchanged_commit` — if HEAD matches the last audit, skip inference and write an empty timestamped report
- `execution.compare_to_latest_real` — after a full pass, if findings match `latest-real.md`, write an empty report instead of a duplicate

The model is loaded **lazily** on the first repo that needs inference, repositories are audited **one at a time**, and `ModelRunner.unload()` plus `gc.collect()` run in a `finally` block so the process leaves a zero GPU footprint when it exits.

## Reports

Every run writes a **new timestamped file**. Older reports are never overwritten, so a gap in reading them does not lose history:

```
reports/codeguard/20260828T050000Z.md
reports/codeguard/20260829T050000Z.md   # may be an empty "nothing changed" report
reports/codeguard/latest-real.md        # last report that actually found (or first-ran) analysis
reports/codeguard/state.json            # last commit + pointers
```

Empty reports are used when:

1. **Same commit as last time** (cheap exit: no model, no tests).
2. **Findings match the last real report** after stripping timestamps (full pass still ran; this is a coarse equality check and can later become a statistical / structured diff).

`--force` skips both shortcuts.

## Run

```bash
# Full overnight job
.venv/bin/python auditor.py -c config.json

# Fetch, extract, and run target tests without loading the GGUF
.venv/bin/python auditor.py --dry-run

# One repo, do not push the report back to the mirror
.venv/bin/python auditor.py --repo core-api-service --skip-push --force
```

Cron (midnight):

```
0 0 * * * /path/to/CodeGuard/.venv/bin/python /path/to/CodeGuard/auditor.py >> /var/log/codeguard.log 2>&1
```

## Step 0: test-run documentation

Before the LLM passes, every full/dry-run audit writes a deterministic **Step 0** section:

- **missing** — no README/CI/Makefile/package script tells you how to run tests (even if a harness can be guessed)
- **misaligned** — docs name a command that does not actually run (wrong target, missing binary, lying recipe)
- **viable** — a documented command exists and can be executed (failing tests still count as a real recipe)

Missing or lying docs are a finding. They do not skip the rest of the audit.

## Test-coverage pass

When `test_coverage` is enabled, CodeGuard may auto-run pytest / npm / cargo / go if those look obvious. That is only a convenience. If the project is something else, the model still sees README/CI/Makefile-style hints and the source, and is expected to figure out how tests are supposed to run. Target stack is not a CodeGuard concern.

## Adding a pass

1. Create `utils/passes/your_pass.py` with `id`, `title`, and `build_prompt(code, context)`.
2. Register it in `utils/passes/__init__.py` `PASS_REGISTRY`.
3. Add the id to `config.json` `analysis_passes`.
4. Cover the prompt and registry in `tests/test_passes.py`.

See [SPECIFICATION.md](SPECIFICATION.md) for the original blueprint and the intentional deviations.
