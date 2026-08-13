"""SnapDox — local file conversion, no uploads.

    from snapdox import convert
    convert("report.docx", "report.pdf")
"""

from .errors import (
    ConversionFailed,
    EncryptedPdf,
    EngineMissing,
    EngineTimeout,
    ScannedPdf,
    SnapDoxError,
    SourceMissing,
    UnknownFormat,
    UnsupportedPair,
)
from .formats import FORMATS, Kind, of_path
from .options import Options
from .pipeline import Result, convert, convert_to_dir
from .registry import capability_matrix, resolve, targets_for

__version__ = "1.0.0"

__all__ = [
    "convert",
    "convert_to_dir",
    "Result",
    "Options",
    "FORMATS",
    "Kind",
    "of_path",
    "resolve",
    "targets_for",
    "capability_matrix",
    "SnapDoxError",
    "UnknownFormat",
    "UnsupportedPair",
    "ConversionFailed",
    "EngineMissing",
    "EngineTimeout",
    "EncryptedPdf",
    "ScannedPdf",
    "SourceMissing",
]
