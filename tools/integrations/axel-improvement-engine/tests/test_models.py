"""Tests for trajectory schema validation and canonical digests."""

from __future__ import annotations

import unittest

from axel_improve.errors import RecordValidationError
from axel_improve.models import Trajectory


def valid_record() -> dict:
    return {
        "schema_version": 1,
        "id": "model-001",
        "host": "test",
        "project_id": "project",
        "session_id": "session",
        "task": "Run a safe test",
        "events": [
            {
                "id": "model-001-e1",
                "type": "tool_call",
                "tool_name": "run_tests",
                "input": {"command": "python -m unittest"},
                "status": "ok",
                "duration_ms": 10,
            }
        ],
        "outcome": {
            "status": "success",
            "summary": "The test passed",
            "evaluation": {
                "status": "evaluated",
                "evaluator": "unit-test",
                "score": 1.0,
                "validators": [],
            },
        },
    }


class ModelTests(unittest.TestCase):
    def test_valid_record_is_sanitized_and_digest_is_stable(self) -> None:
        first = Trajectory.from_mapping(valid_record())
        second = Trajectory.from_mapping(valid_record())

        self.assertEqual(first.digest(), second.digest())
        self.assertEqual(first.events[0].event_type, "tool_call")
        self.assertEqual(first.evaluation.status, "evaluated")
        self.assertEqual(first.metadata["redaction"], {"redacted": 0, "truncated": 0, "hashed_paths": 0})

    def test_event_ids_must_be_unique(self) -> None:
        record = valid_record()
        record["events"].append(dict(record["events"][0]))

        with self.assertRaises(RecordValidationError):
            Trajectory.from_mapping(record)

    def test_evaluated_outcome_requires_evidence(self) -> None:
        record = valid_record()
        record["outcome"]["evaluation"] = {"status": "evaluated"}

        with self.assertRaises(RecordValidationError):
            Trajectory.from_mapping(record)

    def test_actions_alias_is_accepted(self) -> None:
        record = valid_record()
        record["actions"] = record.pop("events")

        trajectory = Trajectory.from_mapping(record)

        self.assertEqual(len(trajectory.events), 1)
