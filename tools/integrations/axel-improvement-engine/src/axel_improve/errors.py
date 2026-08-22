"""Exceptions raised by the Axel improvement ledger."""


class AxelImproveError(Exception):
    """Base class for expected engine errors."""


class RecordValidationError(AxelImproveError):
    """Raised when an imported trajectory does not satisfy the event schema."""


class UnsafePathError(RecordValidationError):
    """Raised when an imported path could escape its intended scope."""


class LedgerError(AxelImproveError):
    """Raised when the local ledger cannot be opened or updated safely."""
