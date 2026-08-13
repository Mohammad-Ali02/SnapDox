"""Error types.

Every failure a user can plausibly cause gets its own class with a message
written for a human, so both the CLI and the web UI can show something better
than a stack trace.
"""

from __future__ import annotations


class SnapDoxError(Exception):
    """Base class for everything SnapDox raises deliberately."""

    #: Short, actionable hint shown under the error message in the UI.
    hint: str = ""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.message = message
        if hint:
            self.hint = hint


class UnknownFormat(SnapDoxError):
    """The extension isn't in the format table at all."""


class UnsupportedPair(SnapDoxError):
    """Both formats are known, but there's no route between them."""


class ConversionFailed(SnapDoxError):
    """An engine ran and did not produce usable output."""


class EngineMissing(SnapDoxError):
    """A required external program (LibreOffice, Pandoc) isn't installed."""


class EngineTimeout(ConversionFailed):
    """An external engine took too long and was killed."""


class EncryptedPdf(SnapDoxError):
    """The PDF is password-protected and no usable password was supplied."""

    hint = "Supply the open password with --password, or remove it in your PDF reader first."


class ScannedPdf(SnapDoxError):
    """The PDF has no text layer, so there is nothing to turn into a document."""

    hint = (
        "This PDF is a scan or export with no selectable text. Convert it to images "
        "instead (pdf -> png), or run it through OCR first."
    )


class SourceMissing(SnapDoxError):
    """The input file doesn't exist or isn't readable."""
