"""SnapDox web UI.

    python -m web.app

Then open http://127.0.0.1:5000.  Files never leave the machine — the browser
is just a front-end for the same pipeline the CLI uses.
"""

from __future__ import annotations

import argparse
import errno
import sys
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file

from snapdox import __version__
from snapdox.errors import SnapDoxError
from snapdox.formats import FORMATS, Kind, normalize, of_path
from snapdox.options import Options
from snapdox.registry import sources, targets_for

from .jobs import JobStore

#: Refuse anything larger than this up front rather than after a long upload.
MAX_UPLOAD_MB = 200

KIND_ORDER = [Kind.PDF, Kind.DOC, Kind.SHEET, Kind.SLIDE, Kind.TEXT, Kind.RASTER, Kind.VECTOR]
KIND_LABELS = {
    Kind.PDF: "PDF",
    Kind.DOC: "Documents",
    Kind.SHEET: "Spreadsheets",
    Kind.SLIDE: "Presentations",
    Kind.TEXT: "Text",
    Kind.RASTER: "Images",
    Kind.VECTOR: "Vector",
}

store = JobStore()


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
    # Without this, Flask caches templates outside debug mode and an edited
    # page keeps serving the old markup until the server is restarted.
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            version=__version__,
            readable=sorted(sources()),
            max_mb=MAX_UPLOAD_MB,
        )

    @app.get("/api/targets/<ext>")
    def api_targets(ext: str):
        """What a given source format can become — drives the target picker."""
        ext = normalize(ext)
        if ext not in FORMATS:
            return jsonify({"error": f"SnapDox doesn't handle .{ext} files"}), 404

        routes = targets_for(ext)
        groups: list[dict] = []
        for kind in KIND_ORDER:
            options = [
                {
                    "ext": target,
                    "label": FORMATS[target].label,
                    "direct": route.is_direct,
                    "note": route.edges[0].note if route.is_direct else f"via {route.edges[0].dst}",
                    "fanOut": route.fan_out,
                }
                for target, route in sorted(routes.items())
                if FORMATS[target].kind == kind
            ]
            if options:
                groups.append({"kind": KIND_LABELS[kind], "options": options})

        return jsonify({"source": ext, "groups": groups})

    @app.post("/api/convert")
    def api_convert():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify({"error": "no file was uploaded"}), 400

        target = normalize(request.form.get("target", ""))
        if target not in FORMATS:
            return jsonify({"error": "pick a target format"}), 400

        src_fmt = of_path(upload.filename)
        if src_fmt is None:
            return jsonify({"error": f"SnapDox doesn't handle {Path(upload.filename).suffix} files"}), 400
        if target not in targets_for(src_fmt.ext):
            return jsonify({"error": f"can't convert {src_fmt.ext} to {target}"}), 400

        try:
            opts = _options_from_form(request.form)
        except (ValueError, SnapDoxError) as exc:
            return jsonify({"error": str(exc)}), 400

        job = store.submit(upload, target, opts)
        return jsonify(job.as_dict()), 202

    @app.get("/api/job/<job_id>")
    def api_job(job_id: str):
        job = store.get(job_id)
        if job is None:
            abort(404)
        return jsonify(job.as_dict())

    @app.get("/api/download/<job_id>")
    def api_download(job_id: str):
        job = store.get(job_id)
        if job is None or job.status != "done" or not job.outputs:
            abort(404)
        path, name = store.payload_for(job)
        return send_file(path, as_attachment=True, download_name=name)

    @app.errorhandler(413)
    def too_large(_exc):
        return jsonify({"error": f"file is larger than the {MAX_UPLOAD_MB} MB limit"}), 413

    return app


def _options_from_form(form) -> Options:
    """Build Options from form fields, rejecting nonsense before queueing."""

    def number(name: str, default: int) -> int:
        raw = (form.get(name) or "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            raise ValueError(f"{name} must be a whole number") from None

    opts = Options(
        dpi=number("dpi", 200),
        quality=number("quality", 92),
        pages=(form.get("pages") or "").strip() or None,
        trace_mode=(form.get("trace_mode") or "color").strip(),
        trace_speckle=number("trace_speckle", 4),
        pdf_layout=(form.get("pdf_layout") or "flow").strip(),
        password=(form.get("password") or "").strip() or None,
    )
    opts.validate()
    return opts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the SnapDox web UI.")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: localhost only)")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    if args.host not in ("127.0.0.1", "localhost"):
        print(
            f"! Serving on {args.host} exposes SnapDox to your network. "
            "It has no authentication — only do this on a network you trust."
        )

    print(f"SnapDox {__version__}  ->  http://{args.host}:{args.port}")
    try:
        create_app().run(host=args.host, port=args.port, debug=args.debug)
    except OSError as exc:
        if exc.errno not in (errno.EADDRINUSE, errno.EACCES) and "in use" not in str(exc).lower():
            raise
        # The default message for this is a bare socket error, which reads
        # like SnapDox is broken rather than already running.
        print(
            f"\nPort {args.port} is already taken — SnapDox may be running in another window.\n"
            f"  Open http://{args.host}:{args.port} to check,\n"
            f"  or start a second copy on a different port:  python serve.py --port {args.port + 1}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
