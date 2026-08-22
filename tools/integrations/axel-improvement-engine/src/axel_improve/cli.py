"""Command-line interface for the first Axel improvement-engine slice."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from .diagnose import DiagnosisConfig, diagnose_trajectories
from .candidates import transition_candidate
from .compound import (
    CompoundConfig,
    compound_trajectories,
    default_compound_report_paths,
    write_compound_report,
)
from .errors import AxelImproveError
from .evaluate import (
    ChampionRegistry,
    EvaluationConfig,
    default_evaluation_path,
    evaluate_candidate,
    load_candidate_results,
    write_evaluation,
)
from .redaction import Redactor
from .promotion import approve_candidate, promote_candidate, rollback_candidate
from .retrieval import (
    default_retrieval_path,
    retrieve_approved_assets,
    write_retrieval_context,
    write_retrieval_record,
)
from .skills import SkillBank
from .replay import (
    build_replay_suite,
    default_replay_path,
    load_replay_suite,
    run_replay_suite,
    write_replay_suite,
)
from .store import LedgerStore, atomic_write_text, initialize_layout


def _root_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="engine runtime directory (default: current directory)",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser without opening files or databases."""

    parser = argparse.ArgumentParser(prog="axel-improve")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create the local engine layout")
    _root_argument(init_parser)

    ingest_parser = subparsers.add_parser("ingest", help="atomically ingest a JSONL trajectory file")
    _root_argument(ingest_parser)
    ingest_parser.add_argument("--input", type=Path, required=True, help="JSONL trajectory file")

    export_parser = subparsers.add_parser("export", help="export sanitized trajectories as JSONL")
    _root_argument(export_parser)
    export_parser.add_argument("--output", type=Path, help="output file; stdout when omitted")

    status_parser = subparsers.add_parser("status", help="show local ledger counts")
    _root_argument(status_parser)

    diagnose_parser = subparsers.add_parser("diagnose", help="group trajectories into improvement diagnoses")
    _root_argument(diagnose_parser)
    diagnose_parser.add_argument("--min-recurrence", type=int, default=2)
    diagnose_parser.add_argument("--min-target-confidence", type=float, default=0.5)

    diagnoses_parser = subparsers.add_parser("diagnoses", help="show persisted diagnosis evidence")
    _root_argument(diagnoses_parser)
    diagnoses_parser.add_argument("--unresolved", action="store_true", help="show only unresolved or one-off diagnoses")

    replay_build_parser = subparsers.add_parser("replay-build", help="build a deterministic replay suite")
    _root_argument(replay_build_parser)
    replay_build_parser.add_argument("--seed", type=int, default=0)
    replay_build_parser.add_argument("--held-out-fraction", type=float, default=0.2)
    replay_build_parser.add_argument("--output", type=Path, help="suite file; defaults to the engine replay directory")

    replay_inspect_parser = subparsers.add_parser("replay-inspect", help="inspect a replay suite")
    replay_inspect_parser.add_argument("--suite", type=Path, required=True, help="replay suite JSON file")

    replay_run_parser = subparsers.add_parser("replay-run", help="run deterministic replay cases")
    replay_run_parser.add_argument("--suite", type=Path, required=True, help="replay suite JSON file")
    replay_run_parser.add_argument(
        "--split",
        choices=("all", "development", "held-out"),
        default="all",
    )
    replay_run_parser.add_argument("--case-id", help="run one replay case")

    evaluate_parser = subparsers.add_parser("evaluate", help="compare a candidate against the replay champion")
    _root_argument(evaluate_parser)
    evaluate_parser.add_argument("--suite", type=Path, required=True, help="replay suite JSON file")
    evaluate_parser.add_argument("--candidate-id", required=True)
    evaluate_parser.add_argument("--baseline-digest", required=True)
    evaluate_parser.add_argument("--candidate-digest", required=True)
    evaluate_parser.add_argument("--candidate-asset", type=Path, required=True, help="candidate directory containing SKILL.md and provenance.json")
    evaluate_parser.add_argument("--candidate-results", type=Path, required=True, help="case-ID to result JSON mapping")
    evaluate_parser.add_argument("--min-evidence", type=int, default=2)
    evaluate_parser.add_argument("--min-success-delta", type=float, default=0.0)
    evaluate_parser.add_argument("--non-inferiority-margin", type=float, default=0.0)
    evaluate_parser.add_argument("--max-held-out-regressions", type=int, default=0)
    evaluate_parser.add_argument("--max-critical-regressions", type=int, default=0)
    evaluate_parser.add_argument("--max-regressions", type=int, default=0)
    evaluate_parser.add_argument("--max-token-overhead", type=int)
    evaluate_parser.add_argument("--max-context-overhead", type=int)
    evaluate_parser.add_argument("--max-cost-overhead", type=float)
    evaluate_parser.add_argument("--max-candidate-cost", type=float)
    evaluate_parser.add_argument("--provider-version", default="none")
    evaluate_parser.add_argument("--output", type=Path, help="evaluation artifact; defaults to data/evaluations")
    evaluate_parser.add_argument("--checkpoint", type=Path, help="optional resumable evaluation checkpoint")
    evaluate_parser.add_argument("--champion-registry", type=Path, help="optional champion registry file")
    evaluate_parser.add_argument("--champion-target", default="default")

    approve_parser = subparsers.add_parser("approve", help="record an explicit candidate approval decision")
    _root_argument(approve_parser)
    approve_parser.add_argument("--candidate-id", required=True)
    approve_parser.add_argument("--candidate-digest", required=True)
    approve_parser.add_argument("--evaluation", type=Path, required=True, help="eligible evaluation artifact")
    approve_parser.add_argument("--evaluation-digest", help="optional exact evaluation digest confirmation")
    approve_parser.add_argument("--operator", required=True)
    approve_parser.add_argument("--reason", required=True)
    approve_parser.add_argument("--decision", choices=("approved", "rejected"), default="approved")

    promote_parser = subparsers.add_parser("promote", help="promote an approved candidate into the approved manifest")
    _root_argument(promote_parser)
    promote_parser.add_argument("--candidate-id", required=True)
    promote_parser.add_argument("--evaluation", type=Path, required=True, help="eligible evaluation artifact")
    promote_parser.add_argument("--approval-digest", required=True, help="exact approval record digest")
    promote_parser.add_argument("--candidate-digest", help="optional exact candidate digest confirmation")
    promote_parser.add_argument("--repo-root", type=Path, required=True, help="Git worktree root")

    rollback_parser = subparsers.add_parser("rollback", help="rollback an approved candidate by candidate ID")
    _root_argument(rollback_parser)
    rollback_parser.add_argument("--candidate-id", required=True)
    rollback_parser.add_argument("--repo-root", type=Path, required=True, help="Git worktree root")
    rollback_parser.add_argument("--operator", required=True)
    rollback_parser.add_argument("--reason", required=True)

    retrieve_parser = subparsers.add_parser("retrieve", help="preview and record approved context retrieval")
    _root_argument(retrieve_parser)
    retrieve_parser.add_argument("--query", required=True, help="task query used for deterministic retrieval")
    retrieve_parser.add_argument("--task-id", help="stable task identifier for attribution")
    retrieve_parser.add_argument("--threshold", type=float, default=0.2)
    retrieve_parser.add_argument("--max-items", type=int, default=5)
    retrieve_parser.add_argument("--max-tokens", type=int, default=1200)
    retrieve_parser.add_argument("--output", type=Path, help="context export JSON; stdout when omitted")
    retrieve_parser.add_argument("--record", type=Path, help="attribution record; defaults to data/retrieval")
    retrieve_parser.add_argument("--no-record", action="store_true", help="preview without persisting attribution")

    compound_parser = subparsers.add_parser("compound", help="compound recurring evidence into reviewable candidates")
    _root_argument(compound_parser)
    compound_parser.add_argument("--seed", type=int, default=0)
    compound_parser.add_argument("--min-recurrence", type=int, default=2)
    compound_parser.add_argument("--min-target-confidence", type=float, default=0.5)
    compound_parser.add_argument("--similarity-threshold", type=float, default=0.8)
    compound_parser.add_argument("--max-workers", type=int, default=4)
    compound_parser.add_argument("--output", type=Path, help="compound JSON report")
    compound_parser.add_argument("--markdown", type=Path, help="compound Markdown report")

    return parser


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=True, sort_keys=True))


def _run(args: argparse.Namespace) -> int:
    if args.command == "init":
        root = initialize_layout(args.root)
        with LedgerStore.open(root) as store:
            _print_json({"initialized": True, **store.status()})
        return 0

    if args.command == "replay-inspect":
        _print_json(load_replay_suite(args.suite).summary())
        return 0

    if args.command == "replay-run":
        suite = load_replay_suite(args.suite)
        results = run_replay_suite(suite, split=args.split, case_id=args.case_id)
        summary = {
            "suite_id": suite.id,
            "results": [result.to_dict() for result in results],
            "passed": sum(result.status == "passed" for result in results),
            "failed": sum(result.status == "failed" for result in results),
            "incomplete": sum(result.status == "incomplete" for result in results),
        }
        _print_json(summary)
        return 0 if summary["failed"] == 0 and summary["incomplete"] == 0 else 2

    if args.command == "evaluate":
        suite = load_replay_suite(args.suite)
        candidate_bundle = load_candidate_results(args.candidate_results)
        evaluation = evaluate_candidate(
            suite,
            candidate_id=args.candidate_id,
            baseline_digest=args.baseline_digest,
            candidate_digest=args.candidate_digest,
            candidate_asset=args.candidate_asset,
            candidate_root=args.root / "skills" / "candidates",
            checkpoint_path=args.checkpoint,
            config=EvaluationConfig(
                min_evidence=args.min_evidence,
                min_success_delta=args.min_success_delta,
                non_inferiority_margin=args.non_inferiority_margin,
                max_held_out_regressions=args.max_held_out_regressions,
                max_critical_regressions=args.max_critical_regressions,
                max_regressions=args.max_regressions,
                max_token_overhead=args.max_token_overhead,
                max_context_overhead=args.max_context_overhead,
                max_cost_overhead=args.max_cost_overhead,
                max_candidate_cost=args.max_candidate_cost,
                provider_version=args.provider_version,
            ),
            candidate_results=candidate_bundle.results,
            candidate_manifest=candidate_bundle.manifest(),
        )
        output = args.output or default_evaluation_path(args.root, evaluation)
        protected_roots = (
            args.root / "skills" / "active",
            args.root / "skills" / "candidates",
            args.root / "skills" / "approved",
        )
        write_evaluation(output, evaluation, protected_roots=protected_roots)
        candidate_root = args.root / "skills" / "candidates"
        candidate_provenance_path = candidate_root / args.candidate_id / "provenance.json"
        if candidate_provenance_path.is_file():
            provenance = json.loads(candidate_provenance_path.read_text(encoding="utf-8"))
            if provenance.get("status") == "proposed":
                transition_candidate(
                    candidate_root,
                    args.candidate_id,
                    "tested",
                    expected_status="proposed",
                    expected_candidate_digest=provenance.get("candidate_digest"),
                    expected_parent_digest=provenance.get("parent_digest"),
                    expected_diff_digest=provenance.get("diff_digest"),
                    expected_provenance_digest=provenance.get("provenance_digest"),
                    configured_candidate_root=candidate_root,
                    active_root=args.root / "skills" / "active",
                    approved_root=args.root / "skills" / "approved",
                )
        champion = None
        if args.champion_registry is not None and evaluation.status == "eligible":
            champion = ChampionRegistry(args.champion_registry, protected_roots=protected_roots).record(
                evaluation,
                target=args.champion_target,
            )
        _print_json({"output": str(output), "status": evaluation.status, "evaluation": evaluation.to_dict(), "champion": champion})
        return 0 if evaluation.status == "eligible" else 2

    if args.command == "approve":
        result = approve_candidate(
            args.root / "skills" / "candidates",
            args.candidate_id,
            args.evaluation,
            candidate_digest=args.candidate_digest,
            operator=args.operator,
            reason=args.reason,
            decision=args.decision,
            expected_evaluation_digest=args.evaluation_digest,
            active_root=args.root / "skills" / "active",
            approved_root=args.root / "skills" / "approved",
        )
        _print_json(result.to_dict())
        return 0

    if args.command == "promote":
        result = promote_candidate(
            args.root / "skills" / "candidates",
            args.candidate_id,
            args.evaluation,
            args.root / "skills" / "approved",
            args.repo_root,
            expected_approval_digest=args.approval_digest,
            expected_candidate_digest=args.candidate_digest,
            active_root=args.root / "skills" / "active",
        )
        _print_json(result.to_dict())
        return 0

    if args.command == "rollback":
        result = rollback_candidate(
            args.root / "skills" / "candidates",
            args.candidate_id,
            args.root / "skills" / "approved",
            args.repo_root,
            operator=args.operator,
            reason=args.reason,
            active_root=args.root / "skills" / "active",
        )
        _print_json(result.to_dict())
        return 0

    if args.command == "retrieve":
        protected_roots = (
            args.root / "skills" / "active",
            args.root / "skills" / "candidates",
            args.root / "skills" / "approved",
        )
        result = retrieve_approved_assets(
            args.root / "skills" / "approved",
            query=args.query,
            task_id=args.task_id,
            threshold=args.threshold,
            max_items=args.max_items,
            max_tokens=args.max_tokens,
        )
        record_path = None
        if not args.no_record:
            record_path = args.record or default_retrieval_path(args.root, result.record)
            if args.output is not None and Path(record_path).resolve() == Path(args.output).resolve():
                raise ValueError("retrieval record and context output must use different paths")
            write_retrieval_record(record_path, result.record, protected_roots=protected_roots)
        if args.output is None:
            _print_json({**result.to_dict(), "record_path": str(record_path) if record_path is not None else None})
        else:
            write_retrieval_context(args.output, result, protected_roots=protected_roots)
            _print_json(
                {
                    "output": str(args.output),
                    "record_path": str(record_path) if record_path is not None else None,
                    "selected": len(result.context),
                }
            )
        return 0

    if args.command == "compound":
        with LedgerStore.open(args.root) as store:
            candidate_root = args.root / "skills" / "candidates"
            skill_bank = SkillBank(
                args.root / "skills" / "active",
                candidate_root,
                args.root / "skills" / "approved",
            )
            run = compound_trajectories(
                store.export_records(),
                skill_bank,
                candidate_root,
                config=CompoundConfig(
                    seed=args.seed,
                    min_recurrence=args.min_recurrence,
                    min_target_confidence=args.min_target_confidence,
                    similarity_threshold=args.similarity_threshold,
                    max_workers=args.max_workers,
                ),
                artifact_root=args.root / "data" / "compound",
            )
            saved = store.save_diagnoses((item.to_dict() for item in run.diagnoses), reconcile=True)
            report = run.report()
            default_json, default_markdown = default_compound_report_paths(args.root, report)
            json_path, markdown_path = write_compound_report(
                report,
                args.output or default_json,
                args.markdown or default_markdown,
                protected_roots=(args.root / "skills" / "active", candidate_root, args.root / "skills" / "approved"),
            )
            _print_json(
                {
                    "run_id": run.run_id,
                    "candidates": len(run.candidates),
                    "diagnoses": len(run.diagnoses),
                    "saved_diagnoses": saved,
                    "json_report": str(json_path),
                    "markdown_report": str(markdown_path),
                }
            )
            return 0

    with LedgerStore.open(args.root) as store:
        if args.command == "ingest":
            result = store.ingest_jsonl(args.input, Redactor.from_environment())
            _print_json(result.to_dict())
            return 0 if result.rejected == 0 else 2

        if args.command == "status":
            _print_json(store.status())
            return 0

        if args.command == "export":
            records = store.export_records()
            text = "".join(
                json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n" for record in records
            )
            if args.output is None:
                sys.stdout.write(text)
            else:
                atomic_write_text(args.output, text)
                _print_json({"exported": len(records), "output": str(args.output)})
            return 0

        if args.command == "diagnose":
            diagnoses = diagnose_trajectories(
                store.export_records(),
                DiagnosisConfig(
                    min_recurrence=args.min_recurrence,
                    min_target_confidence=args.min_target_confidence,
                ),
            )
            saved = store.save_diagnoses((item.to_dict() for item in diagnoses), reconcile=True)
            _print_json({"diagnoses": [item.to_dict() for item in diagnoses], **saved})
            return 0

        if args.command == "diagnoses":
            _print_json(store.export_diagnoses(args.unresolved))
            return 0

        if args.command == "replay-build":
            suite = build_replay_suite(
                store.export_records(),
                seed=args.seed,
                held_out_fraction=args.held_out_fraction,
            )
            output = args.output or default_replay_path(store.root, suite)
            write_replay_suite(output, suite)
            _print_json({"output": str(output), **suite.summary()})
            return 0

    raise AxelImproveError(f"unsupported command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except (AxelImproveError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
