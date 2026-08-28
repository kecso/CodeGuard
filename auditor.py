#!/usr/bin/env python3
"""CodeGuard orchestrator: fetch → parse → (test) → evaluate → write → shutdown.

Repositories are always audited sequentially. The host is compute-limited;
wall-clock time is not the constraint. Target language and layout do not matter.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from utils.config import AppConfig, ConfigError, RepositoryConfig, load_config
from utils.file_extractor import FileExtractor, chunk_files
from utils.git_manager import GitManager, GitManagerError
from utils.model_runner import ModelRunner, ModelRunnerError, prompt_budget_tokens
from utils.passes import UnknownPassError, resolve_passes
from utils.passes.base import AnalysisPass, PassContext
from utils.reports import (
    STATUS_DRY_RUN,
    STATUS_FULL,
    STATUS_NO_NEW_FINDINGS,
    STATUS_UNCHANGED_COMMIT,
    AuditState,
    ReportSection,
    ReportTarget,
    findings_fingerprint,
    has_new_findings,
    load_state,
    read_report,
    render_empty_report,
    render_report,
    save_state,
    write_report,
)
from utils.harness_docs import assess_harness_docs
from utils.test_runner import TestRunResult, TestRunner

logger = logging.getLogger("codeguard")


@dataclass(frozen=True)
class RepoRunResult:
    name: str
    success: bool
    error: str | None = None
    files_extracted: int = 0
    chunks: int = 0
    tests_detected: bool = False
    status: str = STATUS_FULL
    skipped_inference: bool = False
    report_path: str | None = None


@dataclass(frozen=True)
class AuditResult:
    repos: tuple[RepoRunResult, ...]

    @property
    def failed(self) -> tuple[RepoRunResult, ...]:
        return tuple(item for item in self.repos if not item.success)

    @property
    def ok(self) -> bool:
        return not self.failed


def run_audit(
    config: AppConfig,
    *,
    project_root: Path,
    dry_run: bool = False,
    only_repos: tuple[str, ...] | None = None,
    force: bool = False,
    llama_factory=None,
    now: datetime | None = None,
) -> AuditResult:
    generated_at = now or datetime.now(timezone.utc)
    selected = _select_repos(config.repositories, only_repos)
    passes = resolve_passes(config.analysis_passes)
    git_manager = GitManager(project_root / config.workspace_dir)
    test_runner = TestRunner(config.test_settings)
    model = ModelRunner(
        config.model_settings,
        llama_factory=llama_factory,
        project_root=project_root,
    )
    results: list[RepoRunResult] = []
    total = len(selected)
    try:
        for index, repo in enumerate(selected, start=1):
            logger.info(
                "=== [%s/%s] Auditing %s (sequential) ===",
                index,
                total,
                repo.name,
            )
            results.append(
                _audit_one(
                    repo=repo,
                    config=config,
                    passes=passes,
                    git_manager=git_manager,
                    test_runner=test_runner,
                    model=model,
                    project_root=project_root,
                    dry_run=dry_run,
                    force=force,
                    generated_at=generated_at,
                )
            )
    finally:
        model.unload()
    return AuditResult(repos=tuple(results))


def _target(repo: RepositoryConfig) -> ReportTarget:
    return ReportTarget(directory=repo.output_report_dir, prefix=repo.report_prefix)


def _audit_one(
    *,
    repo: RepositoryConfig,
    config: AppConfig,
    passes: list[AnalysisPass],
    git_manager: GitManager,
    test_runner: TestRunner,
    model: ModelRunner,
    project_root: Path,
    dry_run: bool,
    force: bool,
    generated_at: datetime,
) -> RepoRunResult:
    try:
        checkout = git_manager.prepare(repo)
        commit = git_manager.source_sha(checkout)
        target = _target(repo)
        state = load_state(project_root, target)

        if (
            not force
            and not dry_run
            and config.execution.skip_unchanged_commit
            and state.last_commit
            and state.last_commit == commit
        ):
            logger.info(
                "%s: commit %s matches last real audit; writing empty report",
                repo.name,
                commit[:12],
            )
            markdown = render_empty_report(
                repo_name=repo.name,
                branch=repo.branch,
                model_path=config.model_settings.model_path,
                generated_at=generated_at,
                commit=commit,
                status=STATUS_UNCHANGED_COMMIT,
                previous_real=state.last_real_report,
                reason=(
                    f"HEAD `{commit}` is the same commit as the last audit. "
                    "Inference was skipped. The last real report is unchanged."
                ),
            )
            return _persist(
                project_root=project_root,
                repo=repo,
                target=target,
                state=state,
                markdown=markdown,
                status=STATUS_UNCHANGED_COMMIT,
                commit=commit,
                generated_at=generated_at,
                update_latest_real=False,
                fingerprint=state.last_fingerprint,
                files_extracted=0,
                chunks=0,
                tests_detected=False,
                skipped_inference=True,
            )

        extractor = FileExtractor(config.global_exclusions)
        extraction = extractor.extract(checkout)
        budget = prompt_budget_tokens(
            config.model_settings.context_window,
            config.model_settings.max_tokens,
        )
        chunks = chunk_files(extraction.files, max_tokens=budget) if extraction.files else []
        harness_docs = assess_harness_docs(checkout, config.test_settings)
        logger.info("%s: step 0 harness docs verdict=%s", repo.name, harness_docs.verdict)
        test_result = _maybe_run_tests(passes, test_runner, checkout)
        if not dry_run:
            model.load()
        sections = [harness_docs.to_section()]
        sections.extend(
            _evaluate_chunks(
                repo_name=repo.name,
                passes=passes,
                chunks=chunks,
                test_result=test_result,
                model=model,
                dry_run=dry_run,
                harness_docs=harness_docs,
            )
        )
        status = STATUS_DRY_RUN if dry_run else STATUS_FULL
        preamble = _preamble(
            extraction,
            chunks,
            test_result,
            dry_run=dry_run,
            harness_verdict=harness_docs.verdict,
        )
        full_markdown = render_report(
            repo_name=repo.name,
            branch=repo.branch,
            model_path=config.model_settings.model_path,
            generated_at=generated_at,
            sections=sections,
            preamble=preamble,
            commit=commit,
            status=status,
            previous_real=state.last_real_report,
        )
        previous = read_report(project_root, state.last_real_report)
        update_latest = status == STATUS_FULL
        markdown = full_markdown
        fingerprint = findings_fingerprint(full_markdown)
        if (
            not force
            and not dry_run
            and config.execution.compare_to_latest_real
            and not has_new_findings(previous, full_markdown)
        ):
            logger.info("%s: findings match last real report; writing empty report", repo.name)
            status = STATUS_NO_NEW_FINDINGS
            markdown = render_empty_report(
                repo_name=repo.name,
                branch=repo.branch,
                model_path=config.model_settings.model_path,
                generated_at=generated_at,
                commit=commit,
                status=status,
                previous_real=state.last_real_report,
                reason=(
                    "A full pass ran, but after stripping timestamps the findings "
                    f"match `{state.last_real_report}`. No new issues were recorded."
                ),
            )
            update_latest = False
        return _persist(
            project_root=project_root,
            repo=repo,
            target=target,
            state=state,
            markdown=markdown,
            status=status,
            commit=commit,
            generated_at=generated_at,
            update_latest_real=update_latest,
            fingerprint=fingerprint if update_latest else state.last_fingerprint,
            files_extracted=len(extraction.files),
            chunks=len(chunks),
            tests_detected=test_result.detected if test_result else False,
            skipped_inference=False,
        )
    except (GitManagerError, ModelRunnerError, OSError) as extra:
        logger.exception("Audit failed for %s", repo.name)
        return RepoRunResult(name=repo.name, success=False, error=str(extra))


def _persist(
    *,
    project_root: Path,
    repo: RepositoryConfig,
    target: ReportTarget,
    state: AuditState,
    markdown: str,
    status: str,
    commit: str,
    generated_at: datetime,
    update_latest_real: bool,
    fingerprint: str | None,
    files_extracted: int,
    chunks: int,
    tests_detected: bool,
    skipped_inference: bool,
) -> RepoRunResult:
    stamped = target.timestamped_relpath(generated_at)
    write_report(project_root, stamped, markdown)
    last_real = state.last_real_report
    if update_latest_real:
        latest = target.latest_real_relpath()
        write_report(project_root, latest, markdown)
        last_real = stamped
    next_state = AuditState(
        last_commit=commit,
        last_real_report=last_real,
        last_report=stamped,
        last_status=status,
        last_fingerprint=fingerprint,
    )
    save_state(project_root, target, next_state)
    return RepoRunResult(
        name=repo.name,
        success=True,
        files_extracted=files_extracted,
        chunks=chunks,
        tests_detected=tests_detected,
        status=status,
        skipped_inference=skipped_inference,
        report_path=stamped,
    )


def _maybe_run_tests(
    passes: list[AnalysisPass],
    test_runner: TestRunner,
    checkout: Path,
) -> TestRunResult | None:
    if not any(pass_.id == "test_coverage" for pass_ in passes):
        return None
    return test_runner.run(checkout)


def _evaluate_chunks(
    *,
    repo_name: str,
    passes: list[AnalysisPass],
    chunks: list[str],
    test_result: TestRunResult | None,
    model: ModelRunner,
    dry_run: bool,
    harness_docs=None,
) -> list[ReportSection]:
    if not chunks:
        return [
            ReportSection(
                title="Extraction",
                body="No source files remained after applying exclusion filters.",
            )
        ]
    sections: list[ReportSection] = []
    for pass_ in passes:
        bodies: list[str] = []
        for index, chunk in enumerate(chunks, start=1):
            context = PassContext(
                repo_name=repo_name,
                chunk_index=index,
                chunk_count=len(chunks),
                test_result=test_result,
                harness_docs=harness_docs,
            )
            prompt = pass_.build_prompt(chunk, context)
            if dry_run:
                bodies.append(
                    f"### Chunk {index}/{len(chunks)}\n\n"
                    f"_Dry run — prompt would be {len(prompt)} characters._"
                )
            else:
                answer = model.complete(prompt)
                heading = f"### Chunk {index}/{len(chunks)}\n\n" if len(chunks) > 1 else ""
                bodies.append(f"{heading}{answer}")
        sections.append(ReportSection(title=pass_.title, body="\n\n".join(bodies)))
    return sections


def _preamble(extraction, chunks: list[str], test_result: TestRunResult | None, *, dry_run: bool, harness_verdict: str | None = None) -> str:
    lines = [
        "## Run metadata",
        "",
        f"- Mode: {'dry-run' if dry_run else 'full'}",
        "- Execution: sequential (compute-limited; runtime is not optimized)",
        "- Target stack: any (language and layout do not matter)",
        f"- Step 0 (test docs): {harness_verdict or 'n/a'}",
        f"- Files extracted: {len(extraction.files)}",
        f"- Chunks: {len(chunks)}",
        f"- Skipped binary: {len(extraction.skipped_binary)}",
        f"- Skipped oversize: {len(extraction.skipped_oversize)}",
        f"- Skipped unreadable: {len(extraction.skipped_unreadable)}",
    ]
    if test_result is not None:
        if test_result.detected:
            coverage = (
                f"{test_result.coverage_percent:.2f}%"
                if test_result.coverage_percent is not None
                else "n/a"
            )
            lines.append(
                f"- Tests: command `{' '.join(test_result.command)}`, "
                f"exit {test_result.exit_code}, coverage {coverage}"
            )
        else:
            lines.append(
                "- Tests: no auto-detected command; model should infer harness from the tree"
            )
    return "\n".join(lines)


def _select_repos(
    repositories: tuple[RepositoryConfig, ...],
    only_repos: tuple[str, ...] | None,
) -> tuple[RepositoryConfig, ...]:
    if not only_repos:
        return repositories
    wanted = set(only_repos)
    selected = tuple(repo for repo in repositories if repo.name in wanted)
    missing = wanted - {repo.name for repo in selected}
    if missing:
        raise ConfigError(f"unknown repository name(s): {', '.join(sorted(missing))}")
    return selected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CodeGuard — on-demand local codebase auditor",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config.json",
        help="Path to config.json (default: ./config.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch, extract, and run tests without loading the GGUF model",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore same-commit skip and latest-real comparison",
    )
    parser.add_argument(
        "--repo",
        action="append",
        dest="repos",
        metavar="NAME",
        help="Limit the run to one or more repository names from config.json",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    project_root = Path.cwd()
    try:
        config = load_config(Path(args.config))
        result = run_audit(
            config,
            project_root=project_root,
            dry_run=args.dry_run,
            force=args.force,
            only_repos=tuple(args.repos) if args.repos else None,
        )
    except (ConfigError, UnknownPassError) as extra:
        logger.error("%s", extra)
        return 2
    except ModelRunnerError as extra:
        logger.error("%s", extra)
        return 1
    for item in result.repos:
        if item.success:
            logger.info(
                "%s: ok status=%s files=%s chunks=%s report=%s skipped_inference=%s",
                item.name,
                item.status,
                item.files_extracted,
                item.chunks,
                item.report_path,
                item.skipped_inference,
            )
        else:
            logger.error("%s: failed: %s", item.name, item.error)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
