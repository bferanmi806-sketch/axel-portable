"""Focused tests for isolated, provenance-bearing skill candidates."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from axel_improve.candidates import CandidateError, propose_skill_candidate, transition_candidate
from axel_improve.skills import SkillBank, content_digest, file_digest, parse_frontmatter


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


class CandidateTests(unittest.TestCase):
    def _active_bank(self, root: Path) -> tuple[SkillBank, Path, str]:
        active = root / "active"
        candidate = root / "candidates"
        path = active / "reviewer" / "SKILL.md"
        path.parent.mkdir(parents=True)
        active_content = "---\nname: reviewer\ndescription: Review code changes\n---\n\n# Reviewer\n\nCheck the diff.\n"
        path.write_text(active_content, encoding="utf-8")
        return SkillBank(active, candidate), candidate, active_content

    def _transition_args(self, candidate_root: Path, candidate_id: str) -> dict[str, str | None]:
        record = json.loads((candidate_root / candidate_id / "provenance.json").read_text(encoding="utf-8"))
        return {
            "expected_status": record["status"],
            "expected_candidate_digest": record["candidate_digest"],
            "expected_parent_digest": record.get("parent_digest"),
            "expected_diff_digest": record["diff_digest"],
            "expected_provenance_digest": record["provenance_digest"],
            "configured_candidate_root": candidate_root,
            "active_root": candidate_root.parent / "active",
            "approved_root": candidate_root.parent / "approved",
        }

    def _diagnosis(self) -> dict:
        return json.loads((FIXTURES / "skill_diagnosis.json").read_text(encoding="utf-8"))

    def test_existing_skill_gets_readable_provenance_candidate_without_mutating_active_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bank, candidate_root, active_content = self._active_bank(root)
            result = propose_skill_candidate(self._diagnosis(), bank, candidate_root)

            self.assertEqual(result.action, "created")
            self.assertEqual((root / "active" / "reviewer" / "SKILL.md").read_text(encoding="utf-8"), active_content)
            candidate_path = candidate_root / "candidate-diagnosis-004"
            self.assertEqual((candidate_path / "SKILL.md").read_text(encoding="utf-8"), (FIXTURES / "candidate_skill.golden.md").read_text(encoding="utf-8"))
            provenance = json.loads((candidate_path / "provenance.json").read_text(encoding="utf-8"))
            provenance.pop("created_at", None)
            self.assertEqual(provenance["parent_digest"], file_digest(root / "active" / "reviewer" / "SKILL.md"))
            self.assertEqual(provenance["trajectory_ids"], ["trajectory-01", "trajectory-02"])
            self.assertEqual(
                provenance,
                json.loads((FIXTURES / "candidate_provenance.golden.json").read_text(encoding="utf-8")),
            )
            self.assertIn("Always check the safety boundary.", (candidate_path / "change.diff").read_text(encoding="utf-8"))

    def test_duplicate_is_suppressed_and_similar_target_is_explicit_merge_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bank, candidate_root, _ = self._active_bank(Path(temporary))
            first = propose_skill_candidate(self._diagnosis(), bank, candidate_root)
            duplicate = propose_skill_candidate(self._diagnosis(), bank, candidate_root)
            second = self._diagnosis()
            second["id"] = "diagnosis-005"
            second["proposed_content"] = second["proposed_content"].replace("Always check", "First check")
            merge = propose_skill_candidate(second, bank, candidate_root)

            self.assertEqual(first.action, "created")
            self.assertEqual(duplicate.action, "suppressed")
            self.assertEqual(merge.reason, "merge candidate")
            provenance = json.loads((candidate_root / "candidate-diagnosis-005" / "provenance.json").read_text(encoding="utf-8"))
            self.assertEqual(provenance["relation"], "merge_candidate")
            self.assertEqual(provenance["merge_candidate_for"], ["candidate-diagnosis-004"])

    def test_tested_transition_requires_evaluation_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bank, candidate_root, _ = self._active_bank(Path(temporary))
            without_rules = self._diagnosis()
            without_rules.pop("evaluation_rules")
            propose_skill_candidate(without_rules, bank, candidate_root)
            with self.assertRaisesRegex(CandidateError, "requires evaluation rules"):
                transition_candidate(candidate_root, "candidate-diagnosis-004", "tested", **self._transition_args(candidate_root, "candidate-diagnosis-004"))

            root2 = Path(temporary) / "second"
            bank2, candidate_root2, _ = self._active_bank(root2)
            propose_skill_candidate(self._diagnosis(), bank2, candidate_root2)
            self.assertEqual(transition_candidate(candidate_root2, "candidate-diagnosis-004", "tested", **self._transition_args(candidate_root2, "candidate-diagnosis-004"))["status"], "tested")

    def test_untrusted_paths_names_and_oversized_model_bodies_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bank, candidate_root, _ = self._active_bank(root)
            unsafe_id = self._diagnosis()
            unsafe_id["id"] = "../../escape"
            with self.assertRaises(CandidateError):
                propose_skill_candidate(unsafe_id, bank, candidate_root)
            unsafe_target = self._diagnosis()
            unsafe_target["target"] = "../reviewer"
            with self.assertRaises(CandidateError):
                propose_skill_candidate(unsafe_target, bank, candidate_root)
            oversized = self._diagnosis()
            oversized["proposed_content"] = "x" * 131_073
            with self.assertRaises(CandidateError):
                propose_skill_candidate(oversized, bank, candidate_root)

            candidate_root.mkdir(parents=True, exist_ok=True)
            escaped = root / "escaped"
            escaped.mkdir()
            try:
                (candidate_root / "candidate-diagnosis-004").symlink_to(escaped, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlink creation is unavailable: {exc}")
            with self.assertRaises(CandidateError):
                propose_skill_candidate(self._diagnosis(), bank, candidate_root)

    def test_frontmatter_parser_does_not_evaluate_model_text(self) -> None:
        frontmatter, body = parse_frontmatter("---\nname: safe\ntriggers: [review, test]\n---\n$(never-run)\n")
        self.assertEqual(frontmatter["triggers"], ["review", "test"])
        self.assertEqual(body, "$(never-run)\n")

    def test_protected_roots_and_ineligible_diagnoses_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bank, candidate_root, _ = self._active_bank(root)
            with self.assertRaises(CandidateError):
                propose_skill_candidate(self._diagnosis(), bank, root / "active")

            ineligible = self._diagnosis()
            ineligible.update({"status": "one_off", "promotable": False})
            with self.assertRaisesRegex(CandidateError, "not eligible"):
                propose_skill_candidate(ineligible, bank, candidate_root)

    def test_tampered_candidate_cannot_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bank, candidate_root, _ = self._active_bank(root)
            propose_skill_candidate(self._diagnosis(), bank, candidate_root)
            transition_args = self._transition_args(candidate_root, "candidate-diagnosis-004")
            candidate_file = candidate_root / "candidate-diagnosis-004" / "SKILL.md"
            candidate_file.write_text(candidate_file.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")

            with self.assertRaisesRegex(CandidateError, "digest"):
                transition_candidate(candidate_root, "candidate-diagnosis-004", "tested", **transition_args)
