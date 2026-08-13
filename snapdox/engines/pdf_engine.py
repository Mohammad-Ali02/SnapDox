"""PDF rendering and extraction, via PyMuPDF.

Covers the three directions that don't need a full document model: PDF out to
images, PDF out to raw text/HTML, and images in to a PDF.
"""

from __future__ import annotations

import html as html_mod
import io
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

from ..errors import ConversionFailed
from ..formats import Kind, normalize
from ..options import Options, parse_pages
from ..registry import converter
from . import raster
from ._pdfutil import open_pdf

#: Rasterizing every page of a huge document at high DPI fills a disk quickly.
MAX_RENDERED_PAGES = 500


def _page_paths(dst: Path, count: int) -> list[Path]:
    """One output path per page — bare ``dst`` when there is only one."""
    if count == 1:
        return [dst]
    width = len(str(count))
    return [dst.with_name(f"{dst.stem}_p{i + 1:0{width}d}{dst.suffix}") for i in range(count)]


@converter("pdf", Kind.RASTER, engine="pymupdf", weight=5, note="One image per page", fan_out=True)
def pdf_to_images(src: Path, dst: Path, opts: Options) -> list[Path]:
    with open_pdf(src, opts.password) as doc:
        pages = parse_pages(opts.pages, doc.page_count)
        if len(pages) > MAX_RENDERED_PAGES:
            raise ConversionFailed(
                f"{src.name} would render {len(pages)} images at once",
                hint=f"Narrow it down with --pages, e.g. --pages 1-{MAX_RENDERED_PAGES}.",
            )

        targets = _page_paths(dst, len(pages))
        zoom = opts.dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        written: list[Path] = []

        for index, target in zip(pages, targets):
            pix = doc.load_page(index).get_pixmap(matrix=matrix, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            written.append(raster.save(img, target, opts))

    return written


@converter("pdf", "txt", engine="pymupdf", weight=5, note="Text only, layout dropped", needs_text=True)
def pdf_to_text(src: Path, dst: Path, opts: Options) -> list[Path]:
    with open_pdf(src, opts.password) as doc:
        pages = parse_pages(opts.pages, doc.page_count)
        chunks = [doc.load_page(i).get_text("text") for i in pages]

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n\n".join(chunks).strip() + "\n", encoding="utf-8")
    return [dst]


@converter(
    "pdf", "html", engine="pymupdf", weight=5, note="Positioned HTML, keeps layout", needs_text=True
)
def pdf_to_html(src: Path, dst: Path, opts: Options) -> list[Path]:
    with open_pdf(src, opts.password) as doc:
        pages = parse_pages(opts.pages, doc.page_count)
        body = "\n".join(doc.load_page(i).get_text("html") for i in pages)

    title = html_mod.escape(src.stem)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(
        f"<!doctype html>\n<html><head><meta charset=\"utf-8\">\n"
        f"<title>{title}</title></head>\n<body>\n{body}\n</body></html>\n",
        encoding="utf-8",
    )
    return [dst]


@converter(Kind.RASTER, "pdf", engine="pymupdf", weight=5, note="One page sized to the image")
def image_to_pdf(src: Path, dst: Path, opts: Options) -> list[Path]:
    # Round-trip through Pillow so formats PyMuPDF won't open (ICO, exotic
    # TIFFs) and alpha channels are both handled by one code path.
    try:
        return _build_pdf([src], dst)
    except ConversionFailed:
        raise
    except Exception as exc:
        raise ConversionFailed(f"could not build a PDF from {src.name}: {exc}") from exc


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _build_pdf(sources: list[Path], dst: Path) -> list[Path]:
    """Write one page per source image, each page sized to its image."""
    doc = fitz.open()
    try:
        for path in sources:
            img = raster.flatten(raster.load(Path(path)))
            rect = fitz.Rect(0, 0, img.width, img.height)
            page = doc.new_page(width=rect.width, height=rect.height)
            page.insert_image(rect, stream=_png_bytes(img))
        dst.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(dst), garbage=3, deflate=True)
    finally:
        doc.close()
    return [dst]


def merge_images_to_pdf(sources: list[Path], dst: Path) -> list[Path]:
    """Combine several images into a single multi-page PDF, one page each."""
    if not sources:
        raise ConversionFailed("no images given to merge")
    return _build_pdf(list(sources), dst)
