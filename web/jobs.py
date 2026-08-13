"""Job store for the web UI.

Conversions run on a small thread pool so the browser gets an immediate
response and can poll for progress.  Everything lives under one workspace
directory that is swept on a timer, so uploads and results don't accumulate.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from snapdox.engines.libreoffice import MAX_CONCURRENT
from snapdox.errors import SnapDoxError
from snapdox.options import Options
from snapdox.pipeline import convert

#: How long a finished job's files stick around before being deleted.
TTL_SECONDS = 30 * 60

#: How often the sweeper looks for expired jobs.
SWEEP_SECONDS = 120


@dataclass
class Job:
    id: str
    source_name: str
    target: str
    status: str = "queued"  # queued | running | done | error
    message: str = ""
    hint: str = ""
    outputs: list[Path] = field(default_factory=list)
    route: str = ""
    seconds: float = 0.0
    created: float = field(default_factory=time.time)
    workdir: Path | None = None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source_name,
            "target": self.target,
            "status": self.status,
            "message": self.message,
            "hint": self.hint,
            "route": self.route,
            "seconds": round(self.seconds, 1),
            "files": [p.name for p in self.outputs],
            "download": f"/api/download/{self.id}" if self.status == "done" else None,
            "bundled": len(self.outputs) > 1,
        }


class JobStore:
    """Thread-safe registry of conversion jobs and their working directories."""

    def __init__(self, root: Path | None = None, workers: int = MAX_CONCURRENT):
        self.root = root or Path(tempfile.gettempdir()) / "snapdox-web"
        self.root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="snapdox")
        self._start_sweeper()

    # --- lifecycle ---

    def submit(self, upload, target: str, opts: Options) -> Job:
        """Save an uploaded file and queue its conversion."""
        job_id = uuid.uuid4().hex
        workdir = self.root / job_id
        (workdir / "in").mkdir(parents=True)
        (workdir / "out").mkdir(parents=True)

        # The uploaded name is used only for its extension and for display;
        # the path on disk is ours, so a hostile filename can't escape.
        source_name = Path(upload.filename or "upload").name
        suffix = Path(source_name).suffix or ""
        src = workdir / "in" / f"source{suffix}"
        upload.save(src)

        job = Job(id=job_id, source_name=source_name, target=target, workdir=workdir)
        with self._lock:
            self._jobs[job_id] = job

        self._pool.submit(self._run, job, src, opts)
        return job

    def _run(self, job: Job, src: Path, opts: Options) -> None:
        job.status = "running"
        assert job.workdir is not None
        stem = Path(job.source_name).stem or "converted"
        dst = job.workdir / "out" / f"{stem}.{job.target}"
        try:
            result = convert(src, dst, opts=opts)
        except SnapDoxError as exc:
            job.status = "error"
            job.message = exc.message
            job.hint = exc.hint
        except ValueError as exc:
            job.status = "error"
            job.message = str(exc)
        except (KeyboardInterrupt, SystemExit):
            job.status = "error"
            job.message = "conversion was interrupted"
            raise
        except BaseException as exc:
            # Deliberately broader than Exception: compiled engines can raise
            # BaseException-derived errors (a Rust panic from vtracer, say).
            # If one escaped here the job would sit at "running" forever and
            # the browser would poll it until the tab was closed.
            job.status = "error"
            job.message = f"unexpected failure: {type(exc).__name__}: {exc}"
        else:
            job.outputs = result.outputs
            job.route = result.route.describe()
            job.seconds = result.seconds
            job.status = "done"
        finally:
            if job.status not in ("done", "error"):  # belt and braces
                job.status = "error"
                job.message = "conversion ended without producing a result"

    # --- reads ---

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def payload_for(self, job: Job) -> tuple[Path, str]:
        """The file to send for a finished job, plus its download name.

        Multiple outputs are zipped once and cached beside them.
        """
        if len(job.outputs) == 1:
            return job.outputs[0], job.outputs[0].name

        assert job.workdir is not None
        stem = Path(job.source_name).stem or "converted"
        bundle = job.workdir / f"{stem}-{job.target}.zip"
        if not bundle.exists():
            with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in job.outputs:
                    zf.write(path, path.name)
        return bundle, bundle.name

    # --- cleanup ---

    def sweep(self, now: float | None = None) -> int:
        """Delete jobs past their TTL.  Returns how many were removed."""
        now = now or time.time()
        expired = []
        with self._lock:
            for job_id, job in list(self._jobs.items()):
                if now - job.created > TTL_SECONDS:
                    expired.append(self._jobs.pop(job_id))
        for job in expired:
            if job.workdir:
                shutil.rmtree(job.workdir, ignore_errors=True)
        return len(expired)

    def _start_sweeper(self) -> None:
        def loop() -> None:
            while True:
                time.sleep(SWEEP_SECONDS)
                try:
                    self.sweep()
                except Exception:  # a sweeper crash must not kill the server
                    pass

        threading.Thread(target=loop, daemon=True, name="snapdox-sweeper").start()

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False)
        shutil.rmtree(self.root, ignore_errors=True)
