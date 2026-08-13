"""Pandoc engine — Markdown and EPUB, which LibreOffice doesn't really speak.

Pandoc owns only the pairs it is genuinely best at.  Anything LibreOffice
handles well (docx -> odt, say) is registered at a heavier weight there, and
the registry prefers the lighter edge, so these two engines don't fight.

Note there is no Markdown-to-PDF edge here: Pandoc needs a LaTeX installation
for that.  The registry instead chains ``md -> docx -> pdf``, which needs
nothing extra and preserves styling better anyway.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..errors import ConversionFailed, EngineMissing, EngineTimeout
from ..formats import normalize
from ..options import Options
from ..registry import converter

#: Extension -> the name Pandoc knows the format by.
PANDOC_NAMES = {
    "md": "markdown",
    "html": "html",
    "docx": "docx",
    "epub": "epub",
    "txt": "plain",
    "odt": "odt",
    "rtf": "rtf",
}

#: Formats Pandoc can read *and* write here.  DOCX leads deliberately: on a
#: tie the registry keeps the first route it finds, and routing onward through
#: DOCX preserves more styling than HTML does.
_ROUND_TRIP = ["docx", "html", "txt", "odt", "rtf"]

_CANDIDATES = (
    r"C:\Users\%USERNAME%\AppData\Local\Pandoc\pandoc.exe",
    "/usr/bin/pandoc",
    "/usr/local/bin/pandoc",
    "/opt/homebrew/bin/pandoc",
)


def find_pandoc() -> Path:
    override = os.environ.get("SNAPDOX_PANDOC")
    if override:
        if Path(override).is_file():
            return Path(override)
        raise EngineMissing(f"SNAPDOX_PANDOC points at {override!r}, which is not a file")

    on_path = shutil.which("pandoc")
    if on_path:
        return Path(on_path)

    local = Path(os.path.expandvars(r"%LOCALAPPDATA%\Pandoc\pandoc.exe"))
    if local.is_file():
        return local

    for candidate in _CANDIDATES:
        expanded = Path(os.path.expandvars(candidate))
        if expanded.is_file():
            return expanded

    raise EngineMissing(
        "Pandoc is required for Markdown and EPUB conversions but was not found",
        hint="Install it from pandoc.org, or set SNAPDOX_PANDOC to pandoc.exe.",
    )


def available() -> bool:
    try:
        find_pandoc()
        return True
    except EngineMissing:
        return False


def run(src: Path, dst: Path, opts: Options) -> list[Path]:
    pandoc = find_pandoc()
    src_fmt = PANDOC_NAMES.get(normalize(src.suffix))
    dst_ext = normalize(dst.suffix)
    dst_fmt = PANDOC_NAMES.get(dst_ext)
    if src_fmt is None or dst_fmt is None:
        raise ConversionFailed(f"Pandoc cannot convert {src.suffix} to {dst.suffix}")

    cmd = [str(pandoc), "--from", src_fmt, "--to", dst_fmt, "--output", str(dst)]

    if dst_ext in ("html", "epub"):
        # Produce a complete document rather than a fragment, and inline
        # images so the result is a single portable file.
        cmd += ["--standalone", "--embed-resources"]
        cmd += ["--metadata", f"title={src.stem}"]
    # Resolve relative image paths against the source file's own folder.
    cmd += ["--resource-path", str(src.parent)]
    cmd.append(str(src))

    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=opts.timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise EngineTimeout(f"Pandoc took longer than {opts.timeout}s converting {src.name}") from exc

    if proc.returncode != 0 or not dst.is_file() or dst.stat().st_size == 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        reason = detail[-1] if detail else f"exit code {proc.returncode}"
        raise ConversionFailed(f"Pandoc failed on {src.name}: {reason}")

    return [dst]


# Markdown is Pandoc's alone; DOCX is the preferred landing point because the
# chain onward to PDF through LibreOffice keeps styling that HTML would lose.
converter("md", "docx", engine="pandoc", weight=4, note="Headings, lists and tables preserved")(run)
converter("md", ["html", "txt", "epub", "odt", "rtf"], engine="pandoc", weight=6)(run)
# Markdown out is text recovery — an image-only document has nothing to give it.
converter([*_ROUND_TRIP, "epub"], "md", engine="pandoc", weight=6, needs_text=True)(run)

# EPUB likewise.
converter("epub", _ROUND_TRIP, engine="pandoc", weight=6)(run)
converter(_ROUND_TRIP, "epub", engine="pandoc", weight=6)(run)
