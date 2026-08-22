"""Run the bounded Ticket 9 compounding demonstration on twenty fixtures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from axel_improve.compound import (
    CompoundConfig,
    compound_trajectories,
    default_compound_report_paths,
    write_compound_report,
)
from axel_improve.skills import SkillBank
from axel_improve.store import LedgerStore, initialize_layout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="temporary or local engine runtime")
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "fixtures" / "reconstructed.jsonl",
    )
    parser.add_argument("--seed", type=int, default=21)
    args = parser.parse_args()

    root = initialize_layout(args.root)
    with LedgerStore.open(root) as store:
        expected = sum(1 for line in args.fixtures.read_text(encoding="utf-8").splitlines() if line.strip())
        ingest = store.ingest_jsonl(args.fixtures)
        if ingest.rejected or ingest.imported + ingest.duplicates != expected:
            raise RuntimeError(
                f"fixture ingestion incomplete: expected {expected}, "
                f"imported {ingest.imported}, duplicates {ingest.duplicates}, rejected {ingest.rejected}"
            )
        bank = SkillBank(
            root / "skills" / "active",
            root / "skills" / "candidates",
            root / "skills" / "approved",
        )
        run = compound_trajectories(
            store.export_records(),
            bank,
            root / "skills" / "candidates",
            config=CompoundConfig(seed=args.seed),
            artifact_root=root / "data" / "compound",
        )
        report = run.report()
        json_path, markdown_path = default_compound_report_paths(root, report)
        write_compound_report(
            report,
            json_path,
            markdown_path,
            protected_roots=(root / "skills" / "active", root / "skills" / "candidates", root / "skills" / "approved"),
        )
    print(
        json.dumps(
            {
                "run_id": run.run_id,
                "trajectories": len(run.trajectories),
                "candidates": len(run.candidates),
                "json_report": str(json_path),
                "markdown_report": str(markdown_path),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
