"""Tests for approved-only deterministic retrieval and attribution."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from axel_improve.candidates import provenance_digest
from axel_improve.retrieval import (
    RetrievalError,
    retrieve_approved_assets,
    write_retrieval_context,
    write_retrieval_record,
)
from axel_improve.skills import content_digest


class RetrievalTests(unittest.TestCase):
    def _run_cli(self, root: Path, *arguments: str, command: str = "retrieve") -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        source_path = str(Path(__file__).resolve().parents[1] / "src")
        environment["PYTHONPATH"] = source_path + os.pathsep + environment.get("PYTHONPATH", "")
        return subprocess.run(
            [sys.executable, "-m", "axel_improve", command, "--root", str(root), *arguments],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def _asset(
        self,
        approved_root: Path,
        candidate_id: str,
        asset_key: str,
        content: str,
        evaluation_id: str,
    ) -> dict[str, str]:
        candidate = approved_root / candidate_id
        candidate.mkdir(parents=True)
        skill_path = candidate / "SKILL.md"
        skill_path.write_bytes(content.encode("utf-8"))
        digest = content_digest(content)
        candidate_provenance_digest = hashlib.sha256(f"source-{candidate_id}".encode("utf-8")).hexdigest()
        provenance = {
            "schema_version": 1,
            "candidate_id": candidate_id,
            "status": "approved",
            "candidate_digest": digest,
            "asset_key": asset_key,
            "candidate_provenance_digest": candidate_provenance_digest,
            "evaluation_id": evaluation_id,
            "evaluation_digest": hashlib.sha256(evaluation_id.encode("utf-8")).hexdigest(),
        }
        provenance["provenance_digest"] = provenance_digest(provenance)
        (candidate / "provenance.json").write_text(
            json.dumps(provenance, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "candidate_id": candidate_id,
            "candidate_digest": digest,
            "candidate_provenance_digest": candidate_provenance_digest,
            "approved_provenance_digest": provenance["provenance_digest"],
            "evaluation_id": evaluation_id,
            "evaluation_digest": hashlib.sha256(evaluation_id.encode("utf-8")).hexdigest(),
            "asset_key": asset_key,
            "asset_path": f"{candidate_id}/SKILL.md",
            "provenance_path": f"{candidate_id}/provenance.json",
        }

    def _layout(self) -> tuple[Path, dict[str, dict[str, str]]]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        approved = root / "skills" / "approved"
        approved.mkdir(parents=True)
        current = self._asset(
            approved,
            "candidate-current",
            "target:reviewer/SKILL.md",
            """---
name: reviewer
description: review safety and deployment boundaries
---

# Review Safety
Use the safety boundary before approving a deployment.

## Deployment Checks
Check the deployment boundary, authorization, and rollback path.
""",
            "evaluation-current",
        )
        other = self._asset(
            approved,
            "candidate-other",
            "target:release/SKILL.md",
            """---
name: release
description: release planning
---

# Release Planning
Prepare a release checklist and record the change window.
""",
            "evaluation-other",
        )
        rolled_back = self._asset(
            approved,
            "candidate-rolled-back",
            "target:reviewer/SKILL.md",
            """---
name: reviewer
description: rollback-only historical version
---

# Rollback Only
This historical version must never be retrieved.
""",
            "evaluation-rolled-back",
        )
        candidates = root / "skills" / "candidates"
        candidates.mkdir(parents=True)
        (candidates / "candidate-unapproved").mkdir()
        (candidates / "candidate-unapproved" / "SKILL.md").write_text(
            "# Candidate Only\nCandidate-only retrieval content.\n",
            encoding="utf-8",
        )
        rejected = approved / "candidate-rejected"
        rejected.mkdir()
        (rejected / "SKILL.md").write_text(
            "# Rejected Only\nRejected-only retrieval content.\n",
            encoding="utf-8",
        )
        manifest = {
            "schema_version": 1,
            "revision": 7,
            "active": {
                current["asset_key"]: current,
                other["asset_key"]: other,
            },
            "history": [
                {"action": "promote", **rolled_back},
                {
                    "action": "rollback",
                    "candidate_id": rolled_back["candidate_id"],
                    "candidate_digest": rolled_back["candidate_digest"],
                },
            ],
        }
        (approved / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return approved, {"current": current, "other": other, "rolled_back": rolled_back}

    def test_retrieval_uses_only_current_manifest_entries(self) -> None:
        approved, assets = self._layout()

        result = retrieve_approved_assets(approved, query="rollback historical version", threshold=0.3)
        self.assertEqual(result.context, ())
        result = retrieve_approved_assets(approved, query="candidate-only rejected-only", threshold=0.3)
        self.assertEqual(result.context, ())

        result = retrieve_approved_assets(approved, query="safety boundary deployment")
        self.assertTrue(result.context)
        self.assertTrue(all(item.selection.candidate_id != assets["rolled_back"]["candidate_id"] for item in result.context))
        self.assertEqual(result.context[0].selection.asset_key, assets["current"]["asset_key"])

    def test_ranking_is_deterministic_and_attributed(self) -> None:
        approved, assets = self._layout()
        first = retrieve_approved_assets(
            approved,
            query="deployment boundary",
            task_id="task-123",
            threshold=0.1,
            max_items=3,
            max_tokens=100,
        )
        second = retrieve_approved_assets(
            approved,
            query="deployment boundary",
            task_id="task-123",
            threshold=0.1,
            max_items=3,
            max_tokens=100,
        )
        self.assertEqual(
            [item.selection.to_dict() for item in first.context],
            [item.selection.to_dict() for item in second.context],
        )
        selection = first.record.selections[0]
        self.assertEqual(selection.candidate_digest, assets["current"]["candidate_digest"])
        self.assertEqual(selection.evaluation_id, "evaluation-current")
        self.assertEqual(len(selection.text_digest), 64)
        self.assertEqual(first.record.task_id, "task-123")

    def test_threshold_and_item_token_budgets_are_fail_closed(self) -> None:
        approved, _ = self._layout()
        self.assertEqual(
            retrieve_approved_assets(approved, query="deployment boundary", threshold=0.99).context,
            (),
        )
        limited = retrieve_approved_assets(
            approved,
            query="deployment boundary",
            threshold=0.1,
            max_items=1,
            max_tokens=3,
        )
        self.assertEqual(limited.context, ())
        bounded = retrieve_approved_assets(
            approved,
            query="deployment boundary",
            threshold=0.1,
            max_items=1,
            max_tokens=100,
        )
        self.assertLessEqual(len(bounded.context), 1)
        self.assertLessEqual(sum(item.selection.token_count for item in bounded.context), 100)

    def test_optional_embedding_provider_is_bounded_and_attributed(self) -> None:
        approved, _ = self._layout()

        class Provider:
            def score(self, query: str, text: str) -> float:
                return 0.8

        result = retrieve_approved_assets(
            approved,
            query="unmatched concept",
            threshold=0.19,
            max_items=1,
            max_tokens=100,
            embedding_provider=Provider(),
        )
        self.assertEqual(len(result.context), 1)
        self.assertEqual(result.context[0].selection.embedding_score, 0.8)

    def test_record_omits_raw_query_and_context_output_cannot_overwrite_assets(self) -> None:
        approved, _ = self._layout()
        result = retrieve_approved_assets(approved, query="secret-token-123 safety")
        record_path = approved.parent.parent / "data" / "retrieval.json"
        record_path.parent.mkdir(parents=True)
        write_retrieval_record(record_path, result.record, protected_roots=(approved.parent / "active", approved.parent / "candidates", approved))
        self.assertNotIn("secret-token-123", record_path.read_text(encoding="utf-8"))
        with self.assertRaises(RetrievalError):
            write_retrieval_context(approved / "manifest.json", result)

    def test_cli_exports_context_and_attribution_record(self) -> None:
        approved, _ = self._layout()
        root = approved.parent.parent
        output = root / "context.json"
        result = self._run_cli(
            root,
            "--query",
            "authorization",
            "--task-id",
            "task-cli",
            "--output",
            str(output),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["selected"], 1)
        self.assertTrue(output.is_file())
        self.assertTrue(Path(summary["record_path"]).is_file())
        exported = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(exported["record"]["task_id"], "task-cli")
        self.assertEqual(len(exported["context"]), 1)

    def test_cli_rejects_record_and_context_path_collision(self) -> None:
        approved, _ = self._layout()
        root = approved.parent.parent
        same_path = root / "same.json"
        result = self._run_cli(
            root,
            "--query",
            "authorization",
            "--record",
            str(same_path),
            "--output",
            str(same_path),
        )
        self.assertEqual(result.returncode, 1)
        self.assertFalse(same_path.exists())

    def test_cli_rollback_excludes_the_rolled_back_version(self) -> None:
        from tests.test_promotion import PromotionTests

        helper = PromotionTests()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper._git_init(root)
            first_path, candidate_root, first_digest, active_root, approved_root, _ = helper._prepare_candidate(root)
            first_evaluation = helper._evaluation(root, first_path, first_path.name, first_digest)
            helper._git(root, "add", "--all")
            helper._git(root, "commit", "-m", "initial candidate")
            first_approval = helper._approve(first_path, candidate_root, first_digest, first_evaluation)
            helper._git(
                root,
                "add",
                "--all",
            )
            helper._git(root, "commit", "-m", "approve first")
            from axel_improve.promotion import promote_candidate

            promote_candidate(
                candidate_root,
                first_path.name,
                first_evaluation,
                approved_root,
                root,
                expected_approval_digest=first_approval.approval_digest,
                expected_candidate_digest=first_digest,
                active_root=active_root,
            )
            second_diagnosis = helper._fixture()
            second_diagnosis["id"] = "diagnosis-005"
            second_diagnosis["proposed_content"] = second_diagnosis["proposed_content"].replace("Always check", "First check")
            second_path, _, second_digest, _, _, _ = helper._prepare_candidate(root, second_diagnosis)
            second_evaluation = helper._evaluation(root, second_path, second_path.name, second_digest)
            second_approval = helper._approve(second_path, candidate_root, second_digest, second_evaluation)
            promote_candidate(
                candidate_root,
                second_path.name,
                second_evaluation,
                approved_root,
                root,
                expected_approval_digest=second_approval.approval_digest,
                expected_candidate_digest=second_digest,
                active_root=active_root,
            )

            rolled_back = self._run_cli(
                root,
                "--candidate-id",
                second_path.name,
                "--repo-root",
                str(root),
                "--operator",
                "operator@example.invalid",
                "--reason",
                "retrieval rollback test",
                command="rollback",
            )
            self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
            result = retrieve_approved_assets(approved_root, query="safety boundary", threshold=0.1)
            self.assertTrue(result.context)
            self.assertTrue(all(item.selection.candidate_id == first_path.name for item in result.context))


if __name__ == "__main__":
    unittest.main()
