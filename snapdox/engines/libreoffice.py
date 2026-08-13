"""LibreOffice headless engine.

Covers every Office-to-Office and Office-to-PDF conversion.  Three details make
the difference between this being reliable and being maddening:

1. **Isolated but reused profiles.**  Each run gets a ``-env:UserInstallation``
   directory of its own, drawn from a small pool.  The isolation matters
   because a LibreOffice window already open on the desktop otherwise swallows
   the request and returns success having converted nothing.  The *reuse*
   matters just as much: building a profile from scratch costs ~15s, while a
   warm one converts in under 3s.
2. **Output is verified, not assumed.**  ``soffice`` exits 0 in plenty of
   failure cases, so success means "the expected file exists and is non-empty".
3. **A scrubbed environment.**  LibreOffice ships its own Python; an inherited
   ``PYTHONPATH`` or ``PYTHONHOME`` makes it emit interpreter warnings and can
   break its scripting provider outright.
4. **Explicit filters** where the extension alone is ambiguous (``txt`` and
   ``html`` mean different things coming out of Writer vs Calc).
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..errors import ConversionFailed, EngineMissing, EngineTimeout
from ..formats import normalize
from ..options import Options
from ..registry import converter

WRITER = ["docx", "doc", "odt", "rtf", "txt", "html"]
CALC = ["xlsx", "xls", "ods", "csv"]
IMPRESS = ["pptx", "ppt", "odp"]

_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
)

#: Filters keyed by (document family, target extension).  Anything not listed
#: is passed to LibreOffice as a bare extension, which it resolves fine.
_FILTERS = {
    ("writer", "txt"): "txt:Text",
    ("writer", "html"): "html:HTML (StarWriter)",
    ("writer", "docx"): "docx:MS Word 2007 XML",
    ("writer", "doc"): "doc:MS Word 97",
    ("calc", "csv"): "csv:Text - txt - csv (StarCalc)",
    ("calc", "html"): "html:HTML (StarCalc)",
    ("calc", "xlsx"): "xlsx:Calc MS Excel 2007 XML",
    ("impress", "pptx"): "pptx:Impress MS PowerPoint 2007 XML",
}


def _family(ext: str) -> str:
    ext = normalize(ext)
    if ext in CALC:
        return "calc"
    if ext in IMPRESS:
        return "impress"
    return "writer"


def find_soffice() -> Path:
    """Locate the LibreOffice binary, or explain how to get one."""
    override = os.environ.get("SNAPDOX_SOFFICE")
    if override:
        if Path(override).is_file():
            return Path(override)
        raise EngineMissing(f"SNAPDOX_SOFFICE points at {override!r}, which is not a file")

    on_path = shutil.which("soffice") or shutil.which("libreoffice")
    if on_path:
        return Path(on_path)

    for candidate in _CANDIDATES:
        if Path(candidate).is_file():
            return Path(candidate)

    raise EngineMissing(
        "LibreOffice is required for Office document conversions but was not found",
        hint="Install it from libreoffice.org, or set SNAPDOX_SOFFICE to soffice.exe.",
    )


def available() -> bool:
    try:
        find_soffice()
        return True
    except EngineMissing:
        return False


#: How many conversions may run at once.  Each needs its own profile, and
#: LibreOffice gets unhappy well before a large number of them.
MAX_CONCURRENT = 4

_pool: queue.Queue[int] | None = None
_pool_lock = threading.Lock()


def profile_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "snapdox" / "lo-profiles"


@contextmanager
def _profile() -> Iterator[Path]:
    """Borrow a warm profile directory, returning it to the pool afterwards."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = queue.Queue()
            for slot in range(MAX_CONCURRENT):
                _pool.put(slot)
    pool = _pool

    slot = pool.get()
    directory = profile_root() / f"p{slot}"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        yield directory
    finally:
        pool.put(slot)


def _child_env() -> dict[str, str]:
    """LibreOffice's bundled Python must not inherit ours."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env.pop("SAL_USE_VCLPLUGIN", None)
    return env


def run(src: Path, dst: Path, opts: Options) -> list[Path]:
    """Convert ``src`` to ``dst`` via headless LibreOffice."""
    soffice = find_soffice()
    target_ext = normalize(dst.suffix)
    convert_arg = _FILTERS.get((_family(src.suffix), target_ext), target_ext)

    dst.parent.mkdir(parents=True, exist_ok=True)

    with _profile() as profile, tempfile.TemporaryDirectory(prefix="snapdox-lo-") as tmp:
        outdir = Path(tmp)
        cmd = [
            str(soffice),
            f"-env:UserInstallation={profile.as_uri()}",
            "--headless",
            "--invisible",
            "--nodefault",
            "--nolockcheck",
            "--nologo",
            "--norestore",
            "--convert-to",
            convert_arg,
            "--outdir",
            str(outdir),
            str(src),
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=opts.timeout,
                check=False,
                env=_child_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise EngineTimeout(
                f"LibreOffice took longer than {opts.timeout}s converting {src.name}",
                hint="Raise the limit with --timeout, or check the file opens normally.",
            ) from exc

        produced = outdir / f"{src.stem}.{target_ext}"
        if not produced.is_file():
            # LibreOffice occasionally names the output after the chosen filter.
            matches = sorted(outdir.glob(f"*.{target_ext}"))
            produced = matches[0] if matches else produced

        if not produced.is_file() or produced.stat().st_size == 0:
            # A corrupted profile causes silent no-ops; drop it so the next
            # run rebuilds from scratch rather than failing forever.
            shutil.rmtree(profile, ignore_errors=True)
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            reason = detail[-1] if detail else f"exit code {proc.returncode}"
            raise ConversionFailed(
                f"LibreOffice produced no output for {src.name} -> {target_ext}: {reason}",
                hint="Close any open LibreOffice windows and try again, or check the file isn't corrupt.",
            )

        shutil.move(str(produced), str(dst))

    return [dst]


converter(WRITER, "pdf", engine="libreoffice", weight=10, note="Full layout fidelity")(run)
converter(CALC, "pdf", engine="libreoffice", weight=10, note="Full layout fidelity")(run)
converter(IMPRESS, "pdf", engine="libreoffice", weight=10, note="One PDF page per slide")(run)

# Plain text is registered apart from the rest so it can be flagged as a
# text-recovery target: chaining an image into it yields an empty file.
converter(WRITER, [w for w in WRITER if w != "txt"], engine="libreoffice", weight=10)(run)
converter(WRITER, "txt", engine="libreoffice", weight=10, needs_text=True)(run)
converter(CALC, CALC, engine="libreoffice", weight=10, note="CSV covers the first sheet only")(run)
converter(IMPRESS, IMPRESS, engine="libreoffice", weight=10)(run)
