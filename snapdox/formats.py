"""Canonical table of every file format SnapDox knows about.

Everything else in the package refers to formats by their canonical extension
(lowercase, no dot).  Aliases like ``jpeg`` or ``htm`` are folded onto their
canonical spelling here so the rest of the code never has to think about them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Kind(str, Enum):
    """Broad family a format belongs to.  Drives grouping in the UI."""

    PDF = "pdf"
    DOC = "doc"
    SHEET = "sheet"
    SLIDE = "slide"
    RASTER = "raster"
    VECTOR = "vector"
    TEXT = "text"


@dataclass(frozen=True)
class Format:
    ext: str
    kind: Kind
    mime: str
    label: str


def _f(ext: str, kind: Kind, mime: str, label: str) -> Format:
    return Format(ext=ext, kind=kind, mime=mime, label=label)


FORMATS: dict[str, Format] = {
    f.ext: f
    for f in (
        _f("pdf", Kind.PDF, "application/pdf", "PDF document"),
        # Word processing
        _f("docx", Kind.DOC, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "Word document"),
        _f("doc", Kind.DOC, "application/msword", "Word 97-2003"),
        _f("odt", Kind.DOC, "application/vnd.oasis.opendocument.text", "OpenDocument text"),
        _f("rtf", Kind.DOC, "application/rtf", "Rich Text Format"),
        # Spreadsheets
        _f("xlsx", Kind.SHEET, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "Excel workbook"),
        _f("xls", Kind.SHEET, "application/vnd.ms-excel", "Excel 97-2003"),
        _f("ods", Kind.SHEET, "application/vnd.oasis.opendocument.spreadsheet", "OpenDocument sheet"),
        _f("csv", Kind.SHEET, "text/csv", "CSV"),
        # Presentations
        _f("pptx", Kind.SLIDE, "application/vnd.openxmlformats-officedocument.presentationml.presentation", "PowerPoint"),
        _f("ppt", Kind.SLIDE, "application/vnd.ms-powerpoint", "PowerPoint 97-2003"),
        _f("odp", Kind.SLIDE, "application/vnd.oasis.opendocument.presentation", "OpenDocument slides"),
        # Text-ish
        _f("txt", Kind.TEXT, "text/plain", "Plain text"),
        _f("md", Kind.TEXT, "text/markdown", "Markdown"),
        _f("html", Kind.TEXT, "text/html", "HTML"),
        _f("epub", Kind.TEXT, "application/epub+zip", "EPUB e-book"),
        # Raster images
        _f("png", Kind.RASTER, "image/png", "PNG image"),
        _f("jpg", Kind.RASTER, "image/jpeg", "JPEG image"),
        _f("webp", Kind.RASTER, "image/webp", "WebP image"),
        _f("bmp", Kind.RASTER, "image/bmp", "Bitmap"),
        _f("tiff", Kind.RASTER, "image/tiff", "TIFF image"),
        _f("gif", Kind.RASTER, "image/gif", "GIF image"),
        _f("ico", Kind.RASTER, "image/x-icon", "Icon"),
        # Vector
        _f("svg", Kind.VECTOR, "image/svg+xml", "SVG vector"),
    )
}

#: Spellings that mean the same thing as a canonical extension.
ALIASES: dict[str, str] = {
    "jpeg": "jpg",
    "jpe": "jpg",
    "tif": "tiff",
    "htm": "html",
    "markdown": "md",
    "text": "txt",
}


def normalize(ext: str) -> str:
    """``'.JPEG'`` -> ``'jpg'``.  Accepts an extension with or without a dot."""
    ext = ext.strip().lstrip(".").lower()
    return ALIASES.get(ext, ext)


def lookup(ext: str) -> Format | None:
    return FORMATS.get(normalize(ext))


def of_path(path: str | Path) -> Format | None:
    return lookup(Path(path).suffix)


def is_known(ext: str) -> bool:
    return normalize(ext) in FORMATS


def by_kind() -> dict[Kind, list[Format]]:
    """Formats grouped by family, each group sorted by label."""
    grouped: dict[Kind, list[Format]] = {}
    for fmt in FORMATS.values():
        grouped.setdefault(fmt.kind, []).append(fmt)
    for group in grouped.values():
        group.sort(key=lambda f: f.label)
    return grouped
