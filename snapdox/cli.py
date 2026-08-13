"""Command-line front-end.

    snapdox report.docx report.pdf          # explicit output path
    snapdox report.docx --to pdf            # output beside the input
    snapdox *.docx --to pdf --outdir built  # batch
    snapdox scan.pdf --to png --dpi 300     # one PNG per page
    snapdox --list                          # what can become what
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .errors import SnapDoxError
from .formats import FORMATS, Kind, by_kind, normalize
from .options import PDF_LAYOUTS, Options
from .pipeline import convert, default_target
from .registry import targets_for

_KIND_TITLES = {
    Kind.PDF: "PDF",
    Kind.DOC: "Documents",
    Kind.SHEET: "Spreadsheets",
    Kind.SLIDE: "Presentations",
    Kind.TEXT: "Text",
    Kind.RASTER: "Images",
    Kind.VECTOR: "Vector",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snapdox",
        description="Convert documents and images locally — no uploads, no page limits.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n", 2)[2],
    )
    parser.add_argument("inputs", nargs="*", type=Path, help="file(s) to convert")
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        default=None,
        help="output path (only with a single input; target format read from its extension)",
    )
    parser.add_argument("--to", metavar="EXT", help="target format, e.g. pdf, docx, png, svg")
    parser.add_argument("--outdir", type=Path, help="write results into this directory")
    parser.add_argument("--list", action="store_true", help="print the full capability matrix and exit")
    parser.add_argument("--targets", metavar="EXT", help="show what a given format can convert into")

    render = parser.add_argument_group("rasterizing (pdf/svg -> image)")
    render.add_argument("--dpi", type=int, default=200, help="output resolution (default: 200)")
    render.add_argument("--pages", metavar="SPEC", help="page selection like 1-3,7 (default: all)")
    render.add_argument("--quality", type=int, default=92, help="JPEG/WebP quality 1-100 (default: 92)")

    trace = parser.add_argument_group("vector tracing (image -> svg)")
    trace.add_argument("--trace-mode", choices=("color", "bw"), default="color", help="colour or silhouette")
    trace.add_argument("--trace-speckle", type=int, default=4, help="drop shapes smaller than N px (default: 4)")
    trace.add_argument("--trace-precision", type=int, default=6, help="curve smoothing, higher is smoother")

    word = parser.add_argument_group("pdf -> word")
    word.add_argument(
        "--pdf-layout",
        choices=PDF_LAYOUTS,
        default="flow",
        help=(
            "flow: paragraphs and images, bordered tables kept (default); "
            "tables: also detect borderless tables, for invoices and datasheets; "
            "text: plain reading-order text, easiest to edit"
        ),
    )

    misc = parser.add_argument_group("other")
    misc.add_argument("--password", help="password for an encrypted PDF")
    misc.add_argument("--engine", help="force a specific engine instead of the preferred one")
    misc.add_argument("--timeout", type=int, default=180, help="seconds before an engine is killed")
    misc.add_argument("-q", "--quiet", action="store_true", help="only print errors")
    misc.add_argument("--version", action="version", version=f"snapdox {__version__}")
    return parser


def options_from(args: argparse.Namespace) -> Options:
    return Options(
        dpi=args.dpi,
        pages=args.pages,
        quality=args.quality,
        trace_mode=args.trace_mode,
        trace_speckle=args.trace_speckle,
        trace_precision=args.trace_precision,
        pdf_layout=args.pdf_layout,
        password=args.password,
        engine=args.engine,
        timeout=args.timeout,
    )


def print_matrix() -> None:
    for kind, formats in by_kind().items():
        print(f"\n{_KIND_TITLES.get(kind, kind.value)}")
        for fmt in formats:
            reachable = sorted(targets_for(fmt.ext))
            if reachable:
                print(f"  {fmt.ext:<5} -> {', '.join(reachable)}")
            else:
                print(f"  {fmt.ext:<5} -> (target only)")
    print()


def print_targets(ext: str) -> int:
    ext = normalize(ext)
    if ext not in FORMATS:
        print(f"snapdox: unknown format {ext!r}", file=sys.stderr)
        return 2
    routes = targets_for(ext)
    if not routes:
        print(f"{ext} can only be produced, not read")
        return 0
    print(f"{ext} can convert to:")
    for target, route in sorted(routes.items()):
        marker = "  " if route.is_direct else " *"
        note = route.edges[0].note if route.is_direct else "via " + route.edges[0].dst
        print(f"{marker} {target:<6} {note}")
    print("\n  * = two-step conversion")
    return 0


def _resolve_jobs(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[tuple[Path, Path]]:
    """Work out the (source, destination) pairs implied by the arguments."""
    inputs = list(args.inputs)

    # `snapdox a.docx b.pdf` parses as two inputs when --to is absent; the
    # trailing argument is the destination if it isn't an existing file.
    if args.output is not None:
        inputs.append(args.output)
    explicit_out: Path | None = None
    if args.to is None and len(inputs) >= 2 and not inputs[-1].exists():
        explicit_out = inputs.pop()

    if not inputs:
        parser.error("no input files given")

    missing = [p for p in inputs if not p.is_file()]
    if missing:
        parser.error("no such file: " + ", ".join(str(p) for p in missing))

    if explicit_out is not None:
        if len(inputs) > 1:
            parser.error("an explicit output path only works with a single input; use --to and --outdir")
        return [(inputs[0], explicit_out)]

    if args.to is None:
        parser.error("give a target with --to EXT, or an output path")

    jobs = []
    for src in inputs:
        dst = default_target(src, args.to)
        if args.outdir:
            dst = args.outdir / dst.name
        jobs.append((src, dst))
    return jobs


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list:
        print_matrix()
        return 0
    if args.targets:
        return print_targets(args.targets)

    jobs = _resolve_jobs(args, parser)
    opts = options_from(args)

    failures = 0
    for src, dst in jobs:
        try:
            result = convert(src, dst, opts=opts)
        except SnapDoxError as exc:
            failures += 1
            print(f"snapdox: {src.name}: {exc.message}", file=sys.stderr)
            if exc.hint:
                print(f"         {exc.hint}", file=sys.stderr)
        except ValueError as exc:
            failures += 1
            print(f"snapdox: {src.name}: {exc}", file=sys.stderr)
        else:
            if not args.quiet:
                if result.multiple:
                    print(f"{src.name} -> {len(result.outputs)} files in {dst.parent}  ({result.seconds:.1f}s)")
                else:
                    print(f"{src.name} -> {result.primary}  ({result.seconds:.1f}s)")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
