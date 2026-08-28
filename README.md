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
- The system `git` binary on `PATH` (GitPython shells out to it; CodeGuard has no GitHub API client of its own)

## Git access

CodeGuard does **not** take a GitHub token, SSH key, or app installation. It runs `git clone` / `git fetch` as the **OS user that launched `auditor.py`**. Whatever that user can clone non-interactively, the auditor can clone. System-wide or per-user git/SSH config is enough; there is nothing to configure inside CodeGuard besides `repositories[].git_url`.

Each run only **reads** the target: clone or fetch, check out the configured branch, hard-reset to `origin/<branch>`. It never commits and never pushes. **Read-only** access to every listed repo is sufficient (and preferable).

Do not put tokens or passwords in `config.json` or in `git_url`. Those files can land in logs and in this repo.

### Public repositories

HTTPS with no extra credentials works:

```json
"git_url": "https://github.com/kecso/CodeGuard.git"
```

### Private repositories

The audit host must already be able to fetch those remotes **without a password prompt**. Pick one method and make `git_url` match it (SSH URLs need SSH; HTTPS URLs need an HTTPS credential helper or token store).

**SSH deploy key (good for one or a few private repos)**

1. On the audit host, as the user that will run cron:

   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/codeguard_org_repo -C "codeguard@audit-host" -N ""
   ```

   An empty passphrase (`-N ""`) is what makes overnight cron work: cron has no TTY and usually no `ssh-agent`. Protect the key with filesystem permissions (`chmod 600`) and a locked-down account instead of a passphrase.

2. GitHub → the **target** repo → Settings → Deploy keys → add the **public** key (`.pub`) with **Allow write access** left **off**.

3. Point SSH at that key (so it is not mixed up with your personal GitHub key):

   ```
   # ~/.ssh/config  (mode 600)
   Host github.com-org-repo
     HostName github.com
     User git
     IdentityFile ~/.ssh/codeguard_org_repo
     IdentitiesOnly yes
   ```

4. Use a matching URL in `config.json`:

   ```json
   "git_url": "git@github.com-org-repo:org/private-repo.git"
   ```

A given public key can only be a deploy key on one GitHub repo. For many private targets, use a machine user or a PAT instead.

**Machine user (good for many private repos)**

Create a GitHub account used only as a bot. Add it to each target as a collaborator with **read** permission (or to the org with a read-only team). Put **one** SSH key for that account on the audit host (`~/.ssh/id_ed25519` or an `IdentityFile` for `Host github.com`). Then use normal SSH URLs:

```json
"git_url": "git@github.com:org/private-repo.git"
```

**HTTPS personal access token**

Create a **fine-grained** PAT with **Contents: Read** (and Metadata) on only the repos you audit. Store it in the OS git credential helper so `git` never prompts — for example `git config --global credential.helper store` after one successful `git ls-remote`, or a libsecret/manager helper on a desktop. `git_url` stays a normal HTTPS URL. Do not embed `https://user:token@github.com/...` in config.

### Cron and other headless runs

Cron does not load your interactive shell and typically has no `ssh-agent`. Auth must work from a non-interactive session of that same user:

- `HOME` should be that user's home so `~/.ssh/config` and `~/.gitconfig` apply (cron usually sets this).
- Prefer an SSH key **without** a passphrase, or a systemd user `ssh-agent` that is running when cron fires.
- A helper that pops a GUI or waits on stdin will hang the job.

### Check before the first audit

Run these **as the same OS user** (and, if you use cron, with a similarly empty environment):

```bash
git --version
# Public HTTPS
git ls-remote --heads https://github.com/kecso/CodeGuard.git
# Each private target (SSH or HTTPS, matching config.json)
git ls-remote --heads git@github.com:org/private-repo.git
```

`ls-remote` should print refs and return immediately. If it asks for a password or hangs, the auditor will fail the same way. A failed clone is recorded for that repo and the run continues with the next one.

Clones live under `workspace/<name>/` on the audit host. That tree is gitignored here, but it is a full copy of private source — keep the CodeGuard account and disk permissions accordingly.

## Setup

### Development / tests (no GPU)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

`pytest` fails if coverage of `auditor.py` and `utils/` drops below **90%**, and prints uncovered lines. That gate is for CodeGuard itself. Tests build throwaway local git repos; they do not need GitHub credentials.

### Audit host (CUDA)

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
# CUDA toolkit 12.x from NVIDIA, if not already installed

git clone https://github.com/kecso/CodeGuard.git
cd CodeGuard
python3 -m venv .venv
source .venv/bin/activate
pip install GitPython==3.1.43
CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python==0.3.1 --no-cache-dir
```

Configure git access (section above), then `git ls-remote` each `git_url` in `config.json`.

`n_gpu_layers: -1` loads every layer that fits into VRAM and spills the rest (plus KV cache when `offload_kqv` is true) into system RAM. Place the GGUF at `model_settings.model_path`.

## Configuration

Edit `config.json`:

- `repositories[].git_url` — clone URL; scheme must match how that OS user authenticates (see **Git access**)
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

Cron (midnight), as the user that can clone the targets:

```
0 0 * * * cd /path/to/CodeGuard && /path/to/CodeGuard/.venv/bin/python auditor.py >> /var/log/codeguard.log 2>&1
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
