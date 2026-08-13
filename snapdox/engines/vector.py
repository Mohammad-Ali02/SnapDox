"""Vector conversions.

**Raster to SVG** uses vtracer, which fits real Bezier curves to colour regions
— the output is editable paths, not a bitmap wrapped in an ``<image>`` tag.
Tracing is a lossy interpretation, so the knobs matter: ``trace_speckle``
discards JPEG noise that would otherwise become thousands of tiny shapes.

**SVG onward** goes through svglib to a ReportLab drawing and out as PDF.  From
there the registry chains to PNG/JPEG through the PDF engine, which keeps the
whole path vector until the final rasterize step.  svglib is pure Python, which
avoids the Cairo DLL hunt that ``cairosvg`` demands on Windows.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Iterator

from ..errors import ConversionFailed
from ..formats import Kind, normalize
from ..options import Options
from ..registry import converter
from . import raster

#: vtracer reads these directly; anything else is converted to PNG first.
_NATIVE_INPUTS = {"png", "jpg", "bmp", "gif"}


@contextlib.contextmanager
def _muffled_stderr() -> Iterator[None]:
    """Silence writes to fd 2 for the duration of the block.

    A Rust panic prints its own message straight to the file descriptor before
    unwinding, so Python-level redirection can't catch it.  We only use this
    around an attempt we know how to recover from — an alarming panic dump
    followed by a successful conversion is worse than no message at all.
    """
    sys.stderr.flush()
    saved = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(devnull)
        os.close(saved)


def _trace(source: Path, dst: Path, opts: Options) -> None:
    """Call vtracer, translating a Rust panic into a normal SnapDox error.

    vtracer is a compiled extension, so a failure inside it arrives as
    ``pyo3_runtime.PanicException`` — which derives from ``BaseException``,
    not ``Exception``.  Catching only ``Exception`` lets it escape and kill
    the calling thread outright, so this has to be deliberately broad.
    """
    import vtracer

    try:
        vtracer.convert_image_to_svg_py(
            str(source),
            str(dst),
            colormode="color" if opts.trace_mode == "color" else "binary",
            filter_speckle=max(0, opts.trace_speckle),
            path_precision=max(1, opts.trace_precision),
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        raise ConversionFailed(f"tracing failed: {exc}") from exc


@converter(
    Kind.RASTER,
    "svg",
    engine="vtracer",
    weight=5,
    note="Traces real vector paths, not an embedded bitmap",
)
def raster_to_svg(src: Path, dst: Path, opts: Options) -> list[Path]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    direct = normalize(src.suffix) in _NATIVE_INPUTS

    with tempfile.TemporaryDirectory(prefix="snapdox-trace-") as tmp:
        if direct:
            try:
                with _muffled_stderr():
                    _trace(src, dst, opts)
            except ConversionFailed:
                # vtracer's decoder is stricter than Pillow's — it rejects
                # files with bad chunk checksums that every viewer opens
                # happily. Re-encoding through Pillow rescues those.
                direct = False

        if not direct:
            staged = Path(tmp) / "input.png"
            try:
                raster.save(raster.load(src), staged, opts)
            except ConversionFailed as exc:
                raise ConversionFailed(
                    f"could not read {src.name} as an image to trace: {exc.message}"
                ) from exc
            _trace(staged, dst, opts)

    if not dst.is_file() or dst.stat().st_size == 0:
        raise ConversionFailed(f"tracing {src.name} produced an empty SVG")

    # A trace with no paths is a blank file, which is never what anyone wanted.
    # It usually means the speckle filter swallowed everything.
    if "<path" not in dst.read_text(encoding="utf-8", errors="ignore"):
        dst.unlink(missing_ok=True)
        hint = (
            f"The detail threshold ({opts.trace_speckle}) may be discarding everything — "
            "try a lower value, or a larger source image."
            if opts.trace_speckle > 0
            else "The image may be blank, or too small for any shape to be found."
        )
        raise ConversionFailed(f"tracing {src.name} found no shapes to draw", hint=hint)
    return [dst]


@converter("svg", "pdf", engine="svglib", weight=5, note="Stays vector end to end")
def svg_to_pdf(src: Path, dst: Path, opts: Options) -> list[Path]:
    from reportlab.graphics import renderPDF
    from svglib.svglib import svg2rlg

    try:
        drawing = svg2rlg(str(src))
    except Exception as exc:
        raise ConversionFailed(f"could not parse {src.name} as SVG: {exc}") from exc

    if drawing is None:
        raise ConversionFailed(
            f"{src.name} contains no drawable content",
            hint="SnapDox reads standard SVG; files that rely on scripts or external images may not render.",
        )

    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        renderPDF.drawToFile(drawing, str(dst))
    except Exception as exc:
        raise ConversionFailed(f"could not render {src.name} to PDF: {exc}") from exc
    return [dst]
