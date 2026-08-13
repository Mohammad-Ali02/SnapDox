"""Shared PDF probing used by more than one engine."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import fitz  # PyMuPDF

from ..errors import ConversionFailed, EncryptedPdf

#: A page with fewer than this many characters is treated as having no real
#: text.  Scanned pages usually yield 0, but a stray ligature or a page-number
#: stamp burned into the image can produce a handful.
_TEXT_FLOOR = 12


@contextmanager
def open_pdf(path: Path, password: str | None = None) -> Iterator[fitz.Document]:
    """Open a PDF, unlocking it if a password was supplied."""
    try:
        doc = fitz.open(path)
    except Exception as exc:  # PyMuPDF raises a grab-bag of types
        raise ConversionFailed(f"could not open {path.name} as a PDF: {exc}") from exc

    try:
        if doc.needs_pass:
            if not password or not doc.authenticate(password):
                raise EncryptedPdf(
                    f"{path.name} is password-protected"
                    + (" and the supplied password was rejected" if password else "")
                )
        yield doc
    finally:
        doc.close()


def text_stats(doc: fitz.Document, sample: int = 8) -> tuple[int, int]:
    """Return (pages sampled, total characters found) across the document.

    Samples pages spread evenly rather than reading everything, so a 500-page
    scan is diagnosed in milliseconds.
    """
    count = doc.page_count
    if count == 0:
        return 0, 0
    step = max(1, count // sample)
    indices = list(range(0, count, step))[:sample]
    chars = 0
    for i in indices:
        chars += len(doc.load_page(i).get_text("text").strip())
    return len(indices), chars


def has_text_layer(doc: fitz.Document) -> bool:
    """True when the PDF carries selectable text rather than page images."""
    sampled, chars = text_stats(doc)
    if sampled == 0:
        return False
    return chars >= _TEXT_FLOOR * sampled or chars >= 200


#: A block must clear the split line by this many points to count as one-sided.
_GUTTER_TOLERANCE = 6.0

#: Each column needs at least this many blocks before we believe in it.
_MIN_COLUMN_BLOCKS = 3


def ordered_text_blocks(page: fitz.Page) -> list[str]:
    """Return a page's text blocks in human reading order.

    PyMuPDF's own ``sort=True`` orders blocks by position, which on a
    two-column page interleaves the columns — you get question 1, then
    question 11, then question 2.  This detects a clean vertical gutter and
    reads the left column all the way down before starting the right one,
    treating anything spanning the gutter (titles, footers) as a full-width
    band that stays in place.
    """
    blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
    if not blocks:
        return []

    split = (page.rect.x0 + page.rect.x1) / 2
    left, right, spanning = [], [], []
    for block in blocks:
        x0, x1 = block[0], block[2]
        if x1 < split + _GUTTER_TOLERANCE:
            left.append(block)
        elif x0 > split - _GUTTER_TOLERANCE:
            right.append(block)
        else:
            spanning.append(block)

    by_position = lambda b: (round(b[1], 1), round(b[0], 1))  # noqa: E731

    if len(left) < _MIN_COLUMN_BLOCKS or len(right) < _MIN_COLUMN_BLOCKS:
        return [b[4].strip() for b in sorted(blocks, key=by_position)]

    column_top = min(b[1] for b in left + right)
    column_bottom = max(b[3] for b in left + right)

    header = sorted((b for b in spanning if b[3] <= column_top), key=by_position)
    footer = sorted((b for b in spanning if b[1] >= column_bottom), key=by_position)
    # Anything spanning the gutter mid-page can't be placed by column; keep it
    # in vertical order after the columns rather than dropping it.
    middle = sorted(
        (b for b in spanning if b[3] > column_top and b[1] < column_bottom), key=by_position
    )

    ordered = header + sorted(left, key=by_position) + sorted(right, key=by_position)
    ordered += middle + footer
    return [b[4].strip() for b in ordered]
