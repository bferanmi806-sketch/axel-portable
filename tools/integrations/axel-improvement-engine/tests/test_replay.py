"""Tests for replay-suite construction and deterministic fixture execution."""

from __future__ import annotations

from dataclasses import replace
import copy
import json
from pathlib import Path
import tempfile
import unittest

from axel_improve.redaction import Redactor
from axel_improve.replay import (
    DeterministicFixtureRunner,
    ReplayError,
    build_replay_suite,
    default_replay_path,
    load_replay_suite,
    run_replay_suite,
    write_replay_suite,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "fixtures" / "reconstructed.jsonl"


def fixture_records() -> list[dict]:
    return [json.loads(line) for line in FIXTURE_PATH.read_text(encoding="utf-8").splitlines()]


class ReplayTests(unittest.TestCase):
    def test_builder_assigns_stable_splits_and_excludes_unsafe_evidence(self) -> None:
        records = fixture_records()
        first = build_replay_suite(records, seed=17, redactor=Redactor())
        second = build_replay_suite(list(reversed(records)), seed=17, redactor=Redactor())

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(len(first.cases), 18)
        self.assertTrue(first.development_cases)
        self.assertTrue(first.held_out_cases)
        exclusions = {item.source: item.reason for item in first.exclusions}
        self.assertIn("fixture-007", exclusions)
        self.assertIn("secret-bearing", exclusions["fixture-007"])
        self.assertIn("fixture-017", exclusions)
        self.assertIn("incomplete evaluation", exclusions["fixture-017"])

    def test_same_task_group_cannot_split_across_development_and_held_out(self) -> None:
        records = fixture_records()[:2]
        records[0]["id"] = "same-task-001"
        records[1]["id"] = "same-task-002"
        records[0]["session_id"] = "same-task-session-001"
        records[1]["session_id"] = "same-task-session-002"
        records[1]["task"] = records[0]["task"]

        suite = build_replay_suite(records, seed=3, held_out_fraction=0.5, redactor=Redactor())
        self.assertEqual(len({case.split for case in suite.cases}), 1)

    def test_same_session_group_cannot_split_across_development_and_held_out(self) -> None:
        records = fixture_records()[:2]
        records[0]["id"] = "same-session-001"
        records[1]["id"] = "same-session-002"
        records[0]["session_id"] = records[1]["session_id"] = "same-session"

        suite = build_replay_suite(records, seed=3, held_out_fraction=0.5, redactor=Redactor())
        self.assertEqual(len({case.split for case in suite.cases}), 1)

    def test_same_task_group_cannot_split_across_projects(self) -> None:
        records = fixture_records()[:2]
        records[0]["id"] = "cross-project-001"
        records[1]["id"] = "cross-project-002"
        records[0]["task"] = records[1]["task"] = "Identical task prompt"
        records[0]["project_id"] = "project-a"
        records[1]["project_id"] = "project-b"

        suite = build_replay_suite(records, seed=3, held_out_fraction=0.5, redactor=Redactor())
        self.assertEqual(len({case.split for case in suite.cases}), 1)

    def test_deterministic_runner_reproduces_baseline_fixture_cases(self) -> None:
        suite = build_replay_suite(fixture_records(), seed=9, redactor=Redactor())
        first = run_replay_suite(suite)
        second = run_replay_suite(suite)

        self.assertEqual(first, second)
        self.assertTrue(first)
        self.assertTrue(all(result.status == "passed" for result in first))
        self.assertTrue(all(result.result_digest for result in first))

    def test_timeout_process_and_network_budgets_are_incomplete(self) -> None:
        timeout_suite = build_replay_suite(
            fixture_records()[:1],
            runner_config={"timeout_ms": 0},
            redactor=Redactor(),
        )
        timeout_result = run_replay_suite(timeout_suite)[0]
        self.assertEqual(timeout_result.status, "incomplete")
        self.assertIn("timeout", timeout_result.reason)

        process_suite = build_replay_suite(
            fixture_records()[3:4],
            runner_config={"process_budget": 0},
            redactor=Redactor(),
        )
        process_result = run_replay_suite(process_suite)[0]
        self.assertEqual(process_result.status, "incomplete")
        self.assertIn("process budget", process_result.reason)

        unknown_record = copy.deepcopy(fixture_records()[0])
        unknown_record["id"] = "unknown-tool-fixture"
        unknown_record["events"][1]["tool_name"] = "exec"
        unknown_suite = build_replay_suite(
            [unknown_record],
            runner_config={"process_budget": 0},
            redactor=Redactor(),
        )
        unknown_result = run_replay_suite(unknown_suite)[0]
        self.assertEqual(unknown_result.status, "incomplete")
        self.assertIn("process budget", unknown_result.reason)

        network_record = fixture_records()[0]
        network_record["id"] = "network-fixture"
        network_record["events"][1] = {
            "id": "network-fixture-e2",
            "type": "tool_call",
            "tool_name": "http_request",
            "input": {"url": "https://example.invalid/data"},
            "status": "ok",
            "duration_ms": 1,
        }
        network_suite = build_replay_suite([network_record], redactor=Redactor())
        network_result = run_replay_suite(network_suite)[0]
        self.assertEqual(network_result.status, "incomplete")
        self.assertIn("network", network_result.reason)

    def test_fixture_mismatch_is_failure_and_never_a_pass(self) -> None:
        suite = build_replay_suite(fixture_records()[:1], redactor=Redactor())
        case = suite.cases[0]
        expected = dict(case.expected_evidence)
        expected["score"] = 0.1
        result = DeterministicFixtureRunner().run(replace(case, expected_evidence=expected))

        self.assertEqual(result.status, "failed")
        self.assertIn("did not match", result.reason)

    def test_fixture_mutation_cannot_reuse_recorded_evidence(self) -> None:
        suite = build_replay_suite(fixture_records()[:1], redactor=Redactor())
        case = suite.cases[0]
        mutated_fixture = list(case.fixture)
        mutated_fixture[0] = dict(mutated_fixture[0], input={"prompt": "different task"})
        result = DeterministicFixtureRunner().run(replace(case, fixture=tuple(mutated_fixture)))

        self.assertEqual(result.status, "failed")
        self.assertIn("fixture changed", result.reason)

    def test_held_out_prompts_are_not_mutation_inputs(self) -> None:
        suite = build_replay_suite(fixture_records(), seed=21, redactor=Redactor())
        held_out_tasks = {case.task_input for case in suite.held_out_cases}
        mutation_inputs = suite.mutation_inputs()

        self.assertTrue(held_out_tasks)
        self.assertTrue(mutation_inputs)
        self.assertTrue(all(item["case_id"] in {case.id for case in suite.development_cases} for item in mutation_inputs))
        self.assertTrue(held_out_tasks.isdisjoint({item["task_input"] for item in mutation_inputs}))

    def test_runner_exception_is_recorded_as_incomplete(self) -> None:
        suite = build_replay_suite(fixture_records()[:1], redactor=Redactor())

        class BrokenRunner:
            def run(self, case):
                raise RuntimeError("untrusted runner detail")

        result = run_replay_suite(suite, runner=BrokenRunner())[0]
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.reason, "runner failure")
        self.assertNotIn("untrusted", result.reason)

    def test_raw_secret_id_is_not_persisted_as_an_exclusion_source(self) -> None:
        record = fixture_records()[0]
        record["id"] = "sk-test-secret-123456789"
        suite = build_replay_suite([record], redactor=Redactor())

        self.assertEqual(len(suite.cases), 0)
        serialized = json.dumps(suite.to_dict(), ensure_ascii=True)
        self.assertNotIn("sk-test-secret-123456789", serialized)

    def test_nested_secret_evidence_is_excluded(self) -> None:
        record = fixture_records()[0]
        record["id"] = "nested-secret-fixture"
        record["events"][1]["input"] = {"api_key": {"value": "nested-secret"}}

        suite = build_replay_suite([record], redactor=Redactor())

        self.assertFalse(suite.cases)
        self.assertIn("secret-bearing", suite.exclusions[0].reason)

    def test_truncated_historical_evidence_is_excluded(self) -> None:
        record = fixture_records()[0]
        record["id"] = "truncated-fixture"
        record["events"] = [
            {
                "id": f"truncated-event-{index}",
                "type": "assistant_message",
                "output": f"step-{index}",
            }
            for index in range(101)
        ]

        suite = build_replay_suite([record], redactor=Redactor())

        self.assertFalse(suite.cases)
        self.assertIn("truncated", suite.exclusions[0].reason)

    def test_suite_with_more_than_one_hundred_cases_round_trips(self) -> None:
        records = []
        for index in range(101):
            record = copy.deepcopy(fixture_records()[0])
            record["id"] = f"large-{index}"
            record["session_id"] = f"large-session-{index}"
            records.append(record)
        suite = build_replay_suite(records, seed=4, redactor=Redactor())

        with tempfile.TemporaryDirectory() as temporary:
            path = write_replay_suite(default_replay_path(temporary, suite), suite)
            loaded = load_replay_suite(path)

        self.assertEqual(len(loaded.cases), 101)

    def test_loaded_suite_rejects_unredacted_content(self) -> None:
        suite = build_replay_suite(fixture_records()[:1], redactor=Redactor())
        payload = suite.to_dict()
        payload["cases"][0]["task_input"] = "api_key=sk-test-secret-123456789"
        payload["id"] = suite.id

        with self.assertRaises(ReplayError):
            type(suite).from_mapping(payload)

    def test_loaded_suite_rejects_redaction_markers(self) -> None:
        suite = build_replay_suite(fixture_records()[:1], redactor=Redactor())
        payload = suite.to_dict()
        payload["cases"][0]["expected_evidence"]["outcome_summary"] = "[TRUNCATED:historical output]"

        with self.assertRaises(ReplayError):
            type(suite).from_mapping(payload)

    def test_suite_round_trip_preserves_identity(self) -> None:
        suite = build_replay_suite(fixture_records(), seed=5, redactor=Redactor())
        with tempfile.TemporaryDirectory() as temporary:
            path = write_replay_suite(Path(temporary) / "suite.json", suite)
            loaded = load_replay_suite(path)

        self.assertEqual(loaded.to_dict(), suite.to_dict())
        self.assertEqual(loaded.digest(), suite.digest())


if __name__ == "__main__":
    unittest.main()
