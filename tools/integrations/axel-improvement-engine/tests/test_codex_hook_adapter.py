"""Black-box tests for the documented Codex JSON command-hook adapter."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "integrations" / "codex-hooks" / "capture.py"
FIXTURES = ROOT / "integrations" / "codex-hooks" / "fixtures"


class CodexHookAdapterTests(unittest.TestCase):
    def invoke(self, root: Path, fixture_name: str, command: str | None = None) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["AXEL_IMPROVE_ROOT"] = str(root)
        environment["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + environment.get("PYTHONPATH", "")
        if command is not None:
            environment["AXEL_IMPROVE_COMMAND"] = command
        return subprocess.run(
            [sys.executable, str(HOOK)],
            input=(FIXTURES / fixture_name).read_text(encoding="utf-8"),
            text=True,
            capture_output=True,
            env=environment,
            check=False,
        )

    def test_supported_payloads_flush_a_sanitized_common_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            command = f"{sys.executable} -m axel_improve"
            for fixture in ("session-start.json", "user-prompt.json", "pre-tool.json", "post-tool.json", "stop.json"):
                result = self.invoke(runtime, fixture, command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "{}")

            source_path = str(ROOT / "src")
            environment = os.environ.copy()
            environment["PYTHONPATH"] = source_path + os.pathsep + environment.get("PYTHONPATH", "")
            exported = subprocess.run(
                [sys.executable, "-m", "axel_improve", "export", "--root", str(runtime)],
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertEqual(exported.returncode, 0, exported.stderr)
            records = [json.loads(line) for line in exported.stdout.splitlines()]
            self.assertEqual(len(records), 1)
            record = records[0]
            self.assertEqual(record["schema_version"], 1)
            self.assertEqual(record["host"], "codex")
            self.assertEqual(record["host_version"], "unknown")
            self.assertEqual(record["outcome"]["status"], "unevaluated")
            self.assertEqual(len(record["events"]), 4)
            self.assertEqual(record["events"][-1]["output"]["message"], "Tests passed")
            rendered = json.dumps(record)
            self.assertNotIn("super-secret-token", rendered)
            self.assertNotIn("sk-test-secret-123456789", rendered)
            self.assertLess(len(rendered), 9000)

    def test_duplicate_events_do_not_duplicate_the_trajectory_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            command = f"{sys.executable} -m axel_improve"
            for fixture in ("user-prompt.json", "pre-tool.json", "pre-tool.json", "stop.json"):
                self.assertEqual(self.invoke(runtime, fixture, command).returncode, 0)

            source_path = str(ROOT / "src")
            environment = os.environ.copy()
            environment["PYTHONPATH"] = source_path + os.pathsep + environment.get("PYTHONPATH", "")
            exported = subprocess.run(
                [sys.executable, "-m", "axel_improve", "export", "--root", str(runtime)],
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertEqual(len(exported.stdout.splitlines()), 1)
            self.assertEqual(len(json.loads(exported.stdout)["events"]), 3)

    def test_malformed_payload_is_reported_without_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            environment = os.environ.copy()
            environment["AXEL_IMPROVE_ROOT"] = str(runtime)
            result = subprocess.run(
                [sys.executable, str(HOOK)],
                input='{ "token": "super-secret-token"',
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "{}")
            diagnostics = (runtime / "spool" / "diagnostics.jsonl").read_text(encoding="utf-8")
            self.assertIn("malformed hook payload", diagnostics)
            self.assertNotIn("super-secret-token", diagnostics)

    def test_unavailable_engine_leaves_a_local_outbox_and_does_not_fail_hook(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            for fixture in ("user-prompt.json", "stop.json"):
                result = self.invoke(runtime, fixture, "definitely-not-an-engine-command")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "{}")
            outbox = list((runtime / "spool" / "outbox").glob("*.jsonl"))
            self.assertEqual(len(outbox), 1)
            self.assertEqual(len(json.loads(outbox[0].read_text(encoding="utf-8"))["events"]), 2)
            self.assertIn("engine delivery was unavailable", (runtime / "spool" / "diagnostics.jsonl").read_text(encoding="utf-8"))

    def test_unavailable_spool_does_not_fail_hook(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            unavailable_root = Path(temporary) / "not-a-directory"
            unavailable_root.write_text("occupied", encoding="utf-8")
            for fixture in ("user-prompt.json", "stop.json"):
                result = self.invoke(unavailable_root, fixture, "definitely-not-an-engine-command")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "{}")

    def test_session_end_flushes_a_segment_when_stop_is_not_seen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            command = f"{sys.executable} -m axel_improve"
            prompt = json.loads((FIXTURES / "user-prompt.json").read_text(encoding="utf-8"))
            prompt["session_id"] = "thr-fixture-02"
            environment = os.environ.copy()
            environment["AXEL_IMPROVE_ROOT"] = str(runtime)
            environment["AXEL_IMPROVE_COMMAND"] = command
            environment["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + environment.get("PYTHONPATH", "")
            for payload in (json.dumps(prompt), (FIXTURES / "session-end.json").read_text(encoding="utf-8")):
                result = subprocess.run([sys.executable, str(HOOK)], input=payload, text=True, capture_output=True, env=environment, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
            exported = subprocess.run([sys.executable, "-m", "axel_improve", "export", "--root", str(runtime)], text=True, capture_output=True, env=environment, check=False)
            self.assertEqual(exported.returncode, 0, exported.stderr)
            self.assertEqual(json.loads(exported.stdout)["outcome"]["summary"], "Codex capture segment ended at SessionEnd.")

    def test_concurrent_hook_processes_do_not_overwrite_session_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            command = f"{sys.executable} -m axel_improve"
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(
                    executor.map(
                        lambda fixture: self.invoke(runtime, fixture, command),
                        ("user-prompt.json", "pre-tool.json"),
                    )
                )
            for result in results:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "{}")

            stop = self.invoke(runtime, "stop.json", command)
            self.assertEqual(stop.returncode, 0, stop.stderr)
            self.assertEqual(stop.stdout.strip(), "{}")

            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + environment.get("PYTHONPATH", "")
            exported = subprocess.run(
                [sys.executable, "-m", "axel_improve", "export", "--root", str(runtime)],
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertEqual(exported.returncode, 0, exported.stderr)
            self.assertEqual(len(json.loads(exported.stdout)["events"]), 3)
            self.assertEqual(list((runtime / "spool" / "state").glob("*.lock")), [])

    def test_lock_timeout_queues_a_sanitized_payload_for_the_next_hook(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            session_id = "thr-fixture-01"
            digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]
            lock = runtime / "spool" / "state" / f"codex-{digest}.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text("test", encoding="ascii")

            queued = self.invoke(runtime, "user-prompt.json", "definitely-not-an-engine-command")
            self.assertEqual(queued.returncode, 0, queued.stderr)
            self.assertEqual(len(list((runtime / "spool" / "pending").rglob("*.json"))), 1)
            lock.unlink()

            stop = self.invoke(runtime, "stop.json", f"{sys.executable} -m axel_improve")
            self.assertEqual(stop.returncode, 0, stop.stderr)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + environment.get("PYTHONPATH", "")
            exported = subprocess.run(
                [sys.executable, "-m", "axel_improve", "export", "--root", str(runtime)],
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertEqual(exported.returncode, 0, exported.stderr)
            self.assertEqual(len(json.loads(exported.stdout)["events"]), 2)
            self.assertEqual(list((runtime / "spool" / "pending").rglob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
