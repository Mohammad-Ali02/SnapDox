"""The one entry point both front-ends call.

``convert()`` resolves a route through the registry, runs each hop, and cleans
up whatever intermediates it created along the way.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .errors import ConversionFailed, SnapDoxError, SourceMissing
from .formats import normalize, of_path
from .options import Options
from .registry import Route, resolve


@dataclass
class Result:
    source: Path
    outputs: list[Path]
    route: Route
    seconds: float

    @property
    def primary(self) -> Path:
        return self.outputs[0]

    @property
    def multiple(self) -> bool:
        return len(self.outputs) > 1

    def summary(self) -> str:
        if self.multiple:
            what = f"{len(self.outputs)} files"
        else:
            what = self.primary.name
        return f"{self.source.name} -> {what} in {self.seconds:.1f}s"


def default_target(src: Path, to: str) -> Path:
    return src.with_suffix("." + normalize(to))


def convert(
    src: str | Path,
    dst: str | Path | None = None,
    *,
    to: str | None = None,
    opts: Options | None = None,
) -> Result:
    """Convert ``src`` into ``dst`` (or into ``to`` beside the source).

    Returns every file produced — more than one when the conversion fans out,
    such as a multi-page PDF becoming one PNG per page.
    """
    src = Path(src).expanduser().resolve()
    opts = opts or Options()
    opts.validate()

    if not src.is_file():
        raise SourceMissing(f"no such file: {src}")

    if dst is None:
        if to is None:
            raise ValueError("convert() needs either a destination path or a target format")
        dst = default_target(src, to)
    dst = Path(dst).expanduser()
    if not dst.is_absolute():
        dst = (Path.cwd() / dst).resolve()

    src_fmt = of_path(src)
    dst_fmt = of_path(dst)
    if src_fmt is None:
        raise SnapDoxError(
            f"don't recognise the extension on {src.name}",
            hint="Run `snapdox --list` to see supported formats.",
        )
    if dst_fmt is None:
        raise SnapDoxError(f"don't recognise the target extension on {dst.name}")

    if src.resolve() == dst.resolve():
        raise SnapDoxError("source and destination are the same file")

    route = resolve(src_fmt.ext, dst_fmt.ext, engine=opts.engine)

    started = time.perf_counter()
    dst.parent.mkdir(parents=True, exist_ok=True)

    if route.is_direct:
        outputs = route.edges[0].fn(src, dst, opts)
    else:
        outputs = _run_chain(route, src, dst, opts)

    elapsed = time.perf_counter() - started

    missing = [p for p in outputs if not Path(p).is_file()]
    if not outputs or missing:
        raise ConversionFailed(
            f"conversion reported success but produced no file for {src.name}"
        )

    return Result(source=src, outputs=[Path(p) for p in outputs], route=route, seconds=elapsed)


def _run_chain(route: Route, src: Path, dst: Path, opts: Options) -> list[Path]:
    """Run a multi-hop route, keeping intermediates in a scratch directory."""
    with tempfile.TemporaryDirectory(prefix="snapdox-chain-") as tmp:
        tmpdir = Path(tmp)
        current = src
        last = len(route.edges) - 1

        for i, edge in enumerate(route.edges):
            if i == last:
                return edge.fn(current, dst, opts)

            step_out = tmpdir / f"step{i}_{src.stem}.{edge.dst}"
            produced = edge.fn(current, step_out, opts)
            if len(produced) != 1:
                raise ConversionFailed(
                    f"intermediate step {edge.src} -> {edge.dst} produced "
                    f"{len(produced)} files; SnapDox can only chain single-file steps",
                    hint="Convert in two explicit steps instead.",
                )
            current = Path(produced[0])

    raise AssertionError("unreachable: routes always have at least one edge")


def convert_to_dir(
    src: str | Path,
    outdir: str | Path,
    to: str,
    *,
    opts: Options | None = None,
) -> Result:
    """Convenience wrapper: convert into ``outdir`` keeping the source stem."""
    src = Path(src)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    return convert(src, outdir / f"{src.stem}.{normalize(to)}", opts=opts)


def copy_result(result: Result, outdir: Path) -> list[Path]:
    """Copy every produced file into ``outdir``, returning the new paths."""
    outdir.mkdir(parents=True, exist_ok=True)
    moved = []
    for path in result.outputs:
        target = outdir / path.name
        shutil.copy2(path, target)
        moved.append(target)
    return moved
