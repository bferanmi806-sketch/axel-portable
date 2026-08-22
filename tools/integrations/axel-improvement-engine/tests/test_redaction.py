"""Tests for deterministic trajectory redaction and bounds."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from axel_improve.errors import UnsafePathError
from axel_improve.redaction import RedactionConfig, Redactor, reject_unsafe_paths


class RedactionTests(unittest.TestCase):
    def test_sensitive_fields_and_known_secret_values_are_redacted(self) -> None:
        redactor = Redactor(secret_values=("local-secret-value",))
        value = {
            "api_key": "sk-test-secret-123456789",
            "message": "Bearer abc.def.ghi and local-secret-value",
            "nested": {"password": "password-value"},
        }

        sanitized = redactor.sanitize(value)

        self.assertEqual(sanitized["api_key"], "[REDACTED:sensitive field]")
        self.assertNotIn("abc.def.ghi", sanitized["message"])
        self.assertNotIn("local-secret-value", sanitized["message"])
        self.assertEqual(sanitized["nested"]["password"], "[REDACTED:sensitive field]")
        self.assertGreaterEqual(redactor.stats.redacted, 3)

    def test_short_sensitive_environment_values_are_redacted(self) -> None:
        with patch.dict("os.environ", {"AXEL_CAPTURE_TOKEN": "Qz7"}, clear=False):
            redactor = Redactor.from_environment()
            sanitized = redactor.sanitize({"message": "value=Qz7"})

        self.assertNotIn("Qz7", sanitized["message"])

    def test_sensitive_mapping_and_scalar_values_are_redacted(self) -> None:
        redactor = Redactor()

        sanitized = redactor.sanitize(
            {
                "api_key": {"value": "nested-secret"},
                "password": 123456,
                "credentials": ["secret", {"token": "nested-token"}],
            }
        )

        self.assertEqual(sanitized["api_key"], "[REDACTED:sensitive field]")
        self.assertEqual(sanitized["password"], "[REDACTED:sensitive field]")
        self.assertIn("[REDACTED:sensitive field]", sanitized.values())

    def test_authorization_headers_are_redacted_from_free_text(self) -> None:
        redactor = Redactor()

        sanitized = redactor.sanitize(
            {
                "message": "Authorization: Basic dXNlcjpzdXBlci1zZWNyZXQ=",
                "headers": [{"name": "Authorization", "value": "Basic structured-secret"}],
            }
        )

        self.assertNotIn("dXNlcjpzdXBlci1zZWNyZXQ=", sanitized["message"])
        self.assertEqual(sanitized["headers"][0]["value"], "[REDACTED:sensitive field]")

    def test_compound_secret_assignments_are_redacted(self) -> None:
        redactor = Redactor()

        sanitized = redactor.sanitize(
            {
                "message": "client_secret=fixture-secret-123 AWS_SECRET_ACCESS_KEY=fixture-key-456 OPENAI_API_KEY=fixture-key-789 clientSecret=\"fixture quoted secret\" {\"clientSecret\":\"escaped \\\"secret\\\"\"}",
                "passwd": "direct-passwd",
            }
        )

        self.assertNotIn("fixture-secret-123", sanitized["message"])
        self.assertNotIn("fixture-key-456", sanitized["message"])
        self.assertNotIn("fixture-key-789", sanitized["message"])
        self.assertNotIn("fixture quoted secret", sanitized["message"])
        self.assertNotIn("escaped", sanitized["message"])
        self.assertEqual(sanitized["passwd"], "[REDACTED:sensitive field]")

    def test_absolute_path_is_hashed_only_in_path_fields(self) -> None:
        redactor = Redactor()
        sanitized = redactor.sanitize(
            {
                "file_path": r"C:\Users\example\project\src\main.py",
                "message": r"C:\Users\example\project\src\main.py",
            }
        )

        self.assertTrue(sanitized["file_path"].startswith("[PATH:"))
        self.assertEqual(sanitized["message"], r"C:\Users\example\project\src\main.py")
        self.assertEqual(redactor.stats.hashed_paths, 1)

    def test_output_and_collections_are_bounded(self) -> None:
        redactor = Redactor(
            RedactionConfig(max_string_chars=10, max_output_chars=5, max_collection_items=2)
        )
        sanitized = redactor.sanitize({"output": "0123456789", "items": [1, 2, 3]})
        text = redactor.sanitize("01234567890", "text")

        self.assertTrue(sanitized["output"].startswith("01234 [TRUNCATED:"))
        self.assertEqual(len(sanitized["items"]), 2)
        self.assertTrue(text.startswith("0123456789 [TRUNCATED:"))
        self.assertEqual(redactor.stats.truncated, 3)

    def test_path_traversal_is_rejected_before_storage(self) -> None:
        with self.assertRaises(UnsafePathError):
            reject_unsafe_paths({"path": "../../outside.txt"})
        with self.assertRaises(UnsafePathError):
            reject_unsafe_paths({"path": {"value": "../../outside.txt"}})

    def test_non_finite_numbers_are_not_persistable(self) -> None:
        redactor = Redactor()

        sanitized = redactor.sanitize({"score": float("nan")})

        self.assertEqual(sanitized["score"], "[REDACTED:non-finite number]")

    def test_cookie_headers_and_standalone_tokens_are_redacted(self) -> None:
        redactor = Redactor()

        sanitized = redactor.sanitize(
            {
                "token": "super-secret-token",
                "message": "Cookie: sessionid=super-secret-cookie",
                "working_dir": r"C:\Users\example\project",
            }
        )

        self.assertEqual(sanitized["token"], "[REDACTED:sensitive field]")
        self.assertNotIn("super-secret-cookie", sanitized["message"])
        self.assertTrue(sanitized["working_dir"].startswith("[PATH:"))

    def test_camel_case_sensitive_keys_and_inline_credential_paths_are_redacted(self) -> None:
        redactor = Redactor()

        sanitized = redactor.sanitize(
            {
                "tokenValue": "super-secret-token",
                "message": r"Read C:\Users\example\.env before continuing",
            }
        )

        self.assertEqual(sanitized["tokenValue"], "[REDACTED:sensitive field]")
        self.assertNotIn(r"C:\Users\example\.env", sanitized["message"])

    def test_credential_path_with_following_text_and_secret_key_is_redacted(self) -> None:
        redactor = Redactor()

        sanitized = redactor.sanitize(
            {
                "token=super-secret": "value",
                "message": "/home/example/.aws/credentials before continuing",
            }
        )

        self.assertEqual(sanitized["[REDACTED:key]"], "[REDACTED:sensitive field]")
        self.assertNotIn(".aws/credentials", sanitized["message"])
