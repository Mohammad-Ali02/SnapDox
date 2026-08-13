"""Raster image conversions, via Pillow.

Most of the work here is handling the ways formats disagree with each other:
JPEG and BMP have no alpha channel, ICO has a size ceiling, GIF is limited to a
256-colour palette.  Converting without accounting for that either crashes or
silently produces a black rectangle where the transparency used to be.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageSequence

from ..errors import ConversionFailed
from ..formats import Kind, normalize
from ..options import Options
from ..registry import converter

#: Formats that cannot store an alpha channel.
OPAQUE_ONLY = {"jpg", "bmp"}

#: Sizes written into a multi-resolution .ico, largest first.
ICO_SIZES = (256, 128, 64, 48, 32, 16)

Image.MAX_IMAGE_PIXELS = 500_000_000  # allow big scans, still guard against decompression bombs


def load(path: Path) -> Image.Image:
    """Open an image, taking the first frame of anything animated."""
    try:
        img = Image.open(path)
        img.load()
    except Exception as exc:
        raise ConversionFailed(f"could not read {path.name} as an image: {exc}") from exc

    if getattr(img, "n_frames", 1) > 1:
        img = next(ImageSequence.Iterator(img)).copy()
    return img


def flatten(img: Image.Image, background: tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    """Composite transparency onto a solid background for formats without alpha."""
    if img.mode == "P" and "transparency" in img.info:
        img = img.convert("RGBA")
    if img.mode in ("RGBA", "LA"):
        canvas = Image.new("RGB", img.size, background)
        alpha = img.getchannel("A")
        canvas.paste(img.convert("RGB"), mask=alpha)
        return canvas
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def save(img: Image.Image, dst: Path, opts: Options) -> Path:
    """Write ``img`` to ``dst``, honouring the quirks of the target format."""
    ext = normalize(dst.suffix)
    dst.parent.mkdir(parents=True, exist_ok=True)
    params: dict[str, object] = {}

    if ext in OPAQUE_ONLY:
        img = flatten(img)

    if ext == "jpg":
        fmt, params = "JPEG", {"quality": opts.quality, "optimize": True, "progressive": True}
    elif ext == "webp":
        fmt, params = "WEBP", {"quality": opts.quality, "method": 6}
    elif ext == "png":
        fmt, params = "PNG", {"optimize": True}
    elif ext == "tiff":
        fmt, params = "TIFF", {"compression": "tiff_lzw"}
    elif ext == "bmp":
        fmt = "BMP"
    elif ext == "gif":
        img = flatten(img).convert("P", palette=Image.Palette.ADAPTIVE, colors=256)
        fmt = "GIF"
    elif ext == "ico":
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        largest = min(max(img.size), ICO_SIZES[0])
        sizes = [(s, s) for s in ICO_SIZES if s <= largest] or [(16, 16)]
        fmt, params = "ICO", {"sizes": sizes}
    else:
        raise ConversionFailed(f"no image writer for .{ext}")

    try:
        img.save(dst, fmt, **params)
    except Exception as exc:
        raise ConversionFailed(f"could not write {dst.name}: {exc}") from exc
    return dst


@converter(Kind.RASTER, Kind.RASTER, engine="pillow", weight=5)
def raster_to_raster(src: Path, dst: Path, opts: Options) -> list[Path]:
    return [save(load(src), dst, opts)]
