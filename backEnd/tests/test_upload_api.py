from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import upload_api
from pipeline_contracts import VideoMetadata


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("TAPA_NA_LATA_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("TAPA_NA_LATA_MAX_VIDEO_BYTES", "100")
    monkeypatch.setenv("TAPA_NA_LATA_MAX_SRT_BYTES", "200")
    monkeypatch.setattr(
        upload_api,
        "_validate_saved_files",
        lambda video, subtitle: VideoMetadata(str(video), 10.0, 1280, 720, "h264", video.stat().st_size),
    )
    upload_api.EDIT_STATES.clear()
    return TestClient(upload_api.app)


def _upload_project(client: TestClient) -> str:
    response = client.post(
        "/api/uploads",
        data={"title": "Minha live"},
        files={
            "video": ("entrada.mp4", b"1234567890", "video/mp4"),
            "subtitle": ("legenda.srt", b"1\n00:00:00,000 --> 00:00:01,000\nOla\n", "text/plain"),
        },
    )
    assert response.status_code == 201
    return response.json()["project_id"]


def test_health(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_upload_accepts_valid_files_and_does_not_expose_path(client: TestClient) -> None:
    response = client.post(
        "/api/uploads",
        data={"title": "Minha live especial"},
        files={
            "video": ("entrada.mp4", b"1234567890", "video/mp4"),
            "subtitle": ("legenda.srt", b"1\n00:00:00,000 --> 00:00:01,000\nOla\n", "text/plain"),
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "uploaded"
    assert body["title"] == "Minha live especial"
    assert body["files"]["video"]["filename"] == "entrada.mp4"
    assert "path" not in body["files"]["video"]


def test_upload_rejects_empty_title(client: TestClient) -> None:
    response = client.post(
        "/api/uploads",
        data={"title": "   "},
        files={
            "video": ("entrada.mp4", b"video", "video/mp4"),
            "subtitle": ("legenda.srt", b"srt", "text/plain"),
        },
    )

    assert response.status_code == 400
    assert "título" in response.json()["detail"]


def test_upload_rejects_invalid_extensions(client: TestClient) -> None:
    response = client.post(
        "/api/uploads",
        data={"title": "Teste"},
        files={
            "video": ("entrada.mov", b"video", "video/quicktime"),
            "subtitle": ("legenda.txt", b"srt", "text/plain"),
        },
    )

    assert response.status_code == 415
    assert list((Path(upload_api.upload_root())).iterdir()) == []


def test_upload_rejects_video_above_configured_limit(client: TestClient) -> None:
    response = client.post(
        "/api/uploads",
        data={"title": "Teste"},
        files={
            "video": ("entrada.mp4", b"x" * 101, "video/mp4"),
            "subtitle": ("legenda.srt", b"srt", "text/plain"),
        },
    )

    assert response.status_code == 413
    assert list(Path(upload_api.upload_root()).glob("*/")) == []


def test_upload_rejects_missing_title(client: TestClient) -> None:
    response = client.post(
        "/api/uploads",
        files={
            "video": ("entrada.mp4", b"video", "video/mp4"),
            "subtitle": ("legenda.srt", b"srt", "text/plain"),
        },
    )

    assert response.status_code == 422


def test_project_metadata_returns_public_media_metadata(client: TestClient) -> None:
    project_id = _upload_project(client)

    response = client.get(f"/api/projects/{project_id}/metadata")

    assert response.status_code == 200
    assert response.json()["metadata"] == {
        "duration_seconds": 10.0,
        "resolution": {"width": 1280, "height": 720},
        "resolution_label": "1280x720",
        "codec": "h264",
        "size_bytes": 10,
    }
    assert str(Path(upload_api.upload_root())) not in response.text


def test_start_edit_returns_edit_id_and_progress(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    project_id = _upload_project(client)

    class DeferredExecutor:
        def submit(self, function, *args):
            return None

    monkeypatch.setattr(upload_api, "EDIT_EXECUTOR", DeferredExecutor())
    response = client.post(f"/api/projects/{project_id}/edits", json={"name": "Minha edição"})

    assert response.status_code == 202
    edit_id = response.json()["edit_id"]
    progress = client.get(f"/api/projects/{project_id}/edits/{edit_id}")
    assert progress.status_code == 200
    assert progress.json()["status"] == "queued"
    assert progress.json()["progress_percent"] == 0


def test_completed_edit_supports_file_and_zip_downloads(client: TestClient) -> None:
    project_id = _upload_project(client)
    edit_id = "8b3f3f6e-4d2f-4b43-b7d2-4c1f2b8a2c10"
    output_directory = upload_api.upload_root() / project_id / "edits" / edit_id / "output"
    output_directory.mkdir(parents=True)
    (output_directory / "resultado.mp4").write_bytes(b"resultado")
    upload_api.EDIT_STATES[edit_id] = {
        "project_id": project_id,
        "edit_id": edit_id,
        "status": "completed",
        "progress_percent": 100,
        "stage": "completed",
        "outputs": [{"output_id": "resultado.mp4", "filename": "resultado.mp4", "size_bytes": 9}],
    }

    file_response = client.get(
        f"/api/projects/{project_id}/edits/{edit_id}/outputs/resultado.mp4/download"
    )
    zip_response = client.get(f"/api/projects/{project_id}/edits/{edit_id}/download.zip")

    assert file_response.status_code == 200
    assert file_response.content == b"resultado"
    assert zip_response.status_code == 200
    assert zip_response.headers["content-type"] == "application/zip"


def test_download_is_blocked_before_edit_completion(client: TestClient) -> None:
    project_id = _upload_project(client)
    edit_id = "8b3f3f6e-4d2f-4b43-b7d2-4c1f2b8a2c10"
    upload_api.EDIT_STATES[edit_id] = {
        "project_id": project_id,
        "edit_id": edit_id,
        "status": "running",
        "progress_percent": 50,
        "stage": "processing",
        "outputs": [],
    }

    response = client.get(
        f"/api/projects/{project_id}/edits/{edit_id}/outputs/resultado.mp4/download"
    )

    assert response.status_code == 409
