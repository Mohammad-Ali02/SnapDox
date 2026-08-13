"""Per-conversion knobs.

One flat options object is passed to every converter.  Engines ignore the
fields that don't apply to them, which keeps the converter signature uniform
and the registry simple.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: PDF -> Word layout strategies, in the order they're offered to users.
PDF_LAYOUTS = ("flow", "tables", "text")


def parse_pages(spec: str | None, page_count: int) -> list[int]:
    """Turn ``"1-3,5"`` into zero-based page indices, clamped to the document.

    ``None`` or an empty string means every page.  Raises ``ValueError`` on
    syntax the user is likely to have typo'd, so the CLI can complain clearly.
    """
    if not spec or not spec.strip():
        return list(range(page_count))

    pages: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk.lstrip("-"):
            start_s, _, end_s = chunk.partition("-")
            try:
                start, end = int(start_s), int(end_s)
            except ValueError as exc:
                raise ValueError(f"bad page range {chunk!r}, expected e.g. 2-5") from exc
            if start > end:
                start, end = end, start
            pages.extend(range(start - 1, end))
        else:
            try:
                pages.append(int(chunk) - 1)
            except ValueError as exc:
                raise ValueError(f"bad page number {chunk!r}") from exc

    seen: set[int] = set()
    ordered = [p for p in pages if 0 <= p < page_count and not (p in seen or seen.add(p))]
    if not ordered:
        raise ValueError(f"page selection {spec!r} matches no pages in a {page_count}-page document")
    return ordered


@dataclass
class Options:
    """Everything a converter might want to know beyond the two file paths."""

    # --- rasterizing (pdf -> image, svg -> image) ---
    dpi: int = 200
    #: Page selection like "1-3,5".  None means all pages.
    pages: str | None = None

    # --- lossy image output ---
    quality: int = 92

    # --- raster -> svg tracing ---
    #: "color" keeps the palette; "bw" produces a single-colour silhouette.
    trace_mode: str = "color"
    #: Discard traced shapes smaller than this many pixels.  Kills JPEG noise.
    trace_speckle: int = 4
    #: Higher = fewer, smoother curves.
    trace_precision: int = 6

    # --- pdf ---
    password: str | None = None

    #: How PDF -> Word should treat the page.
    #:   "flow"   paragraphs and images, bordered tables kept (default)
    #:   "tables" also guess at borderless tables — for invoices and datasheets
    #:   "text"   clean reading-order text, layout discarded, easiest to edit
    pdf_layout: str = "flow"

    # --- routing ---
    #: Force a specific engine name instead of the registry's preferred one.
    engine: str | None = None
    #: Seconds before an external engine is killed.
    timeout: int = 180

    extra: dict[str, object] = field(default_factory=dict)

    def validate(self) -> None:
        if not 12 <= self.dpi <= 1200:
            raise ValueError(f"dpi must be between 12 and 1200, got {self.dpi}")
        if not 1 <= self.quality <= 100:
            raise ValueError(f"quality must be between 1 and 100, got {self.quality}")
        if self.trace_mode not in ("color", "bw"):
            raise ValueError(f"trace_mode must be 'color' or 'bw', got {self.trace_mode!r}")
        if self.pdf_layout not in PDF_LAYOUTS:
            raise ValueError(
                f"pdf_layout must be one of {', '.join(PDF_LAYOUTS)}, got {self.pdf_layout!r}"
            )
