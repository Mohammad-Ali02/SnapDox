"""Web layer: the API contract, and the guarantees the browser relies on."""

from __future__ import annotations

import io
import time
import zipfile

import pytest

from web.app import create_app


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def wait_for(client, job_id, timeout=120.0):
    """Poll a job until it reaches a terminal state, as the browser does."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/job/{job_id}").get_json()
        if body["status"] in ("done", "error"):
            return body
        time.sleep(0.2)
    pytest.fail(f"job {job_id} never finished — it is stuck at {body['status']!r}")


def submit(client, path, target, **form):
    data = {"file": (io.BytesIO(path.read_bytes()), path.name), "target": target, **form}
    response = client.post("/api/convert", data=data, content_type="multipart/form-data")
    return response


def test_index_renders(client):
    page = client.get("/")
    assert page.status_code == 200
    assert b"SnapDox" in page.data


def test_targets_are_grouped_for_the_dropdown(client):
    body = client.get("/api/targets/png").get_json()
    assert body["source"] == "png"
    kinds = [group["kind"] for group in body["groups"]]
    assert "Images" in kinds and "Vector" in kinds
    svg = next(o for g in body["groups"] for o in g["options"] if o["ext"] == "svg")
    assert svg["direct"] is True


def test_targets_rejects_unknown_extension(client):
    assert client.get("/api/targets/xyz").status_code == 404


def test_convert_and_download(client, fixtures):
    response = submit(client, fixtures / "sample.docx", "pdf")
    assert response.status_code == 202

    job = wait_for(client, response.get_json()["id"])
    assert job["status"] == "done", job["message"]

    download = client.get(job["download"])
    assert download.status_code == 200
    assert download.data.startswith(b"%PDF")
    assert "sample.pdf" in download.headers["Content-Disposition"]


def test_multi_page_output_is_zipped(client, fixtures):
    response = submit(client, fixtures / "sample.pdf", "png", dpi="72")
    job = wait_for(client, response.get_json()["id"])
    assert job["status"] == "done"
    assert job["bundled"] is True

    download = client.get(job["download"])
    with zipfile.ZipFile(io.BytesIO(download.data)) as bundle:
        assert len(bundle.namelist()) == 3


def test_pdf_layout_choice_reaches_the_engine(client, fixtures):
    from docx import Document

    response = submit(client, fixtures / "columns.pdf", "docx", pdf_layout="text")
    job = wait_for(client, response.get_json()["id"])
    assert job["status"] == "done", job["message"]

    doc = Document(io.BytesIO(client.get(job["download"]).data))
    text = " ".join(p.text for p in doc.paragraphs)
    assert text.index("item 1") < text.index("item 7")


def test_failed_conversion_reports_the_reason(client, fixtures):
    response = submit(client, fixtures / "scanned.pdf", "docx")
    job = wait_for(client, response.get_json()["id"])
    assert job["status"] == "error"
    assert "text layer" in job["message"]
    assert job["hint"]


def test_a_job_never_stays_running_when_the_engine_dies(client, fixtures, monkeypatch):
    """A BaseException from a compiled engine must still end the job.

    vtracer is a Rust extension whose panics derive from BaseException, so an
    `except Exception` here would leave the job at "running" and the browser
    polling forever.
    """
    import web.jobs as jobs

    def explode(*args, **kwargs):
        raise BaseException("simulated engine panic")

    monkeypatch.setattr(jobs, "convert", explode)

    response = submit(client, fixtures / "sample.png", "svg")
    job = wait_for(client, response.get_json()["id"], timeout=20)
    assert job["status"] == "error"
    assert "panic" in job["message"]


def test_unsupported_pair_is_refused_before_queueing(client, fixtures):
    response = submit(client, fixtures / "sample.png", "xlsx")
    assert response.status_code == 400
    assert "can't convert" in response.get_json()["error"]


def test_upload_without_a_file_is_refused(client):
    response = client.post("/api/convert", data={"target": "pdf"})
    assert response.status_code == 400


def test_download_is_unavailable_for_an_unknown_job(client):
    assert client.get("/api/download/deadbeef").status_code == 404


def test_uploaded_filename_cannot_escape_the_workspace(client, fixtures):
    """A hostile name must not steer where anything is written."""
    data = {
        "file": (io.BytesIO((fixtures / "sample.png").read_bytes()), r"..\..\evil.png"),
        "target": "jpg",
    }
    response = client.post("/api/convert", data=data, content_type="multipart/form-data")
    job = wait_for(client, response.get_json()["id"])
    assert job["status"] == "done"

    served = client.get(job["download"])
    assert ".." not in served.headers["Content-Disposition"]
