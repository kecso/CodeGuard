# CodeGuard

On-demand, self-terminating codebase security and architectural auditor. It is meant to run as a headless overnight job on an Ubuntu host with an NVIDIA GPU (RTX 4090 / 24GB VRAM) and a large DDR5 RAM pool.

It clones **GitHub repositories** (HTTPS or SSH), runs structured analysis passes, optionally executes each target's own test suite when a command can be inferred, writes **timestamped** Markdown reports into this deployment (they are gitignored and never committed or pushed), and then releases VRAM/RAM. These audits are not critical: if GitHub is down, the run simply fails.

Canonical remote: [github.com/kecso/CodeGuard](https://github.com/kecso/CodeGuard).

**Target repositories can be anything.** Language, framework, and layout do not matter. The extractor treats trees as files; the model is expected to notice how a given project is built and tested (README, CI, Makefile, `package.json`, Cargo, Gradle, ad-hoc scripts, …). A few well-known harnesses are auto-run as an optional shortcut, not as a requirement.

**Execution is always sequential.** The audit host is compute-limited. Repositories, passes, and chunks run one after another. Runtime is not optimized; `execution.sequential: false` is rejected.

The analysis passes in this seed (security, memory/resource leakage, algorithmic anomalies, test-coverage holes) are the initial set. New passes register in `utils/passes/`.

## Layout

```
CodeGuard/
├── auditor.py               # Orchestrator
├── config.json              # GitHub repos, exclusions, model, passes
├── utils/                   # git, extract, model, tests, reports, passes
├── models/                  # GGUF weights (not committed)
├── workspace/               # Ephemeral clones of *target* GitHub repos
├── reports/                 # Local audit output (gitignored)
├── tests/                   # CodeGuard's own pytest suite
└── testdata/sample_project/ # Tiny fixture used only by CodeGuard's tests
```

`tests/` and `testdata/` verify **this** repository. They are not the projects being audited. What gets audited is whatever you list under `repositories` in `config.json`.

## Requirements

- Ubuntu 22.04+ on the audit host (dev/CI can be macOS/Linux without a GPU)
- Python 3.10+
- NVIDIA CUDA Toolkit 12.x on the audit host
- Git, with credentials that can **clone** the GitHub remotes (read-only is enough)

## Setup

### Development / tests (no GPU)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

`pytest` fails if coverage of `auditor.py` and `utils/` drops below **90%**, and prints uncovered lines. That gate is for CodeGuard itself.

### Audit host (CUDA)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install GitPython==3.1.43
CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python==0.3.1 --no-cache-dir
```

`n_gpu_layers: -1` loads every layer that fits into VRAM and spills the rest (plus KV cache when `offload_kqv` is true) into system RAM. Place the GGUF at `model_settings.model_path`.

## Configuration

Edit `config.json`:

- `repositories[].git_url` — GitHub clone URL (`https://github.com/org/repo.git` or `git@github.com:org/repo.git`)
- `repositories[].branch` and `output_report_dir`
- `analysis_passes` — `security`, `memory`, `algorithmic`, `test_coverage`
- `global_exclusions` — directories, name suffixes, max file size
- `test_settings` — whether to attempt an auto-detected test command
- `execution.sequential` — must stay `true`
- `execution.skip_unchanged_commit` — same source commit as last audit → empty timestamped report, no model
- `execution.compare_to_latest_real` — findings match `latest-real.md` → empty report after a full pass

The model loads **lazily** on the first repo that needs inference. `ModelRunner.unload()` plus `gc.collect()` run in a `finally` block.

If GitHub cannot be reached, clone/fetch fail and that repo is recorded as failed. That is acceptable.

## Reports

Reports stay on the machine that ran the audit: under `output_report_dir` relative to the CodeGuard working directory (the sample uses `reports/codeguard`). They are **not** written into the cloned target, **not** committed, and **not** pushed. `reports/` is gitignored.

Every run writes a **new timestamped file**. Older reports are never overwritten:

```
reports/codeguard/20260828T050000Z.md
reports/codeguard/20260829T050000Z.md   # may be an empty "nothing changed" report
reports/codeguard/latest-real.md
reports/codeguard/state.json
```

Empty reports:

1. **Same source commit as last time** (no model).
2. **Findings match the last real report** after stripping timestamps (coarse equality; can later become a statistical diff).

`--force` skips both shortcuts.

## Run

```bash
.venv/bin/python auditor.py -c config.json
.venv/bin/python auditor.py --dry-run
.venv/bin/python auditor.py --repo CodeGuard --force
```

Cron (midnight):

```
0 0 * * * /path/to/CodeGuard/.venv/bin/python /path/to/CodeGuard/auditor.py >> /var/log/codeguard.log 2>&1
```

## Step 0: test-run documentation

Before the LLM passes, every full/dry-run audit writes a deterministic **Step 0** section:

- **missing** — no README/CI/Makefile/package script tells you how to run tests
- **misaligned** — docs name a command that does not actually run
- **viable** — a documented command exists and can be executed (a red suite still counts as a real recipe)

Missing or lying docs are a finding. They do not skip the rest of the audit.

## Test-coverage pass

When `test_coverage` is enabled, CodeGuard may auto-run pytest / npm / cargo / go if those look obvious. That is only a convenience. Otherwise the model infers the harness from the tree. Target stack is not a CodeGuard concern.

## Adding a pass

1. Create `utils/passes/your_pass.py` with `id`, `title`, and `build_prompt(code, context)`.
2. Register it in `utils/passes/__init__.py`.
3. Add the id to `config.json` `analysis_passes`.
4. Cover it in `tests/test_passes.py`.
