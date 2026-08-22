"""Controlled diagnosis classes used by the improvement engine."""

from __future__ import annotations

from enum import Enum


class DiagnosisClass(str, Enum):
    MEMORY = "memory"
    SKILL = "skill"
    ROUTING = "routing"
    VALIDATOR = "validator"
    PLAYBOOK = "playbook"
    TOOL_FAILURE = "tool-failure"
    RECOVERY = "recovery-procedure"
    TEMPLATE = "template"
    ONE_OFF = "one-off-incident"


DIAGNOSIS_CLASSES = frozenset(item.value for item in DiagnosisClass)
