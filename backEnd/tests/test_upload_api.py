import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import upload_api
from pipeline_contracts import VideoMetadata


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("TAPA_NA_LATA_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("TAPA_NA_LATA_MAX_VIDEO_BYTES", "100")
    monkeypatch.setenv("TAPA_NA_LATA_MAX_CAPTIONS_BYTES", "200")
    monkeypatch.setattr(
        upload_api, "validate_video_file",
        lambda path: VideoMetadata(str(path), 10.0, 1280, 720, "h264", Path(path).stat().st_size)
    )
    upload_api.EDIT_STATES.clear()
    return TestClient(upload_api.app)


CAPTIONS_JSON = b'[{"word": "Ola", "start": 0.0, "end": 1.0}]'


def _upload_project(client: TestClient) -> str:
    response = client.post(
        "/api/uploads",
        data={"title": "Minha live"},
        files={
            "video": ("entrada.mp4", b"1234567890", "video/mp4"),
            "captions": ("legenda.json", CAPTIONS_JSON, "application/json"),
        },
    )
    assert response.status_code == 201
    return response.json()["project_id"]


def test_health(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_upload_normalizes_legendas_texto_format_to_internal_captions(client: TestClient) -> None:
    legendas_payload = json.dumps(
        {"legendas": [{"texto": "Ola", "start": 0.0, "end": 0.5}, {"texto": "mundo", "start": 0.6, "end": 1.2}]}
    ).encode("utf-8")

    response = client.post(
        "/api/uploads",
        data={"title": "Minha live"},
        files={
            "video": ("entrada.mp4", b"1234567890", "video/mp4"),
            "captions": ("legenda.json", legendas_payload, "application/json"),
        },
    )

    assert response.status_code == 201
    project_id = response.json()["project_id"]
    captions_path = upload_api.upload_root() / project_id / "input" / "captions.json"
    assert json.loads(captions_path.read_text(encoding="utf-8")) == [
        {"word": "Ola", "start": 0.0, "end": 0.5},
        {"word": "mundo", "start": 0.6, "end": 1.2},
    ]


def test_upload_accepts_valid_files_and_does_not_expose_path(client: TestClient) -> None:
    response = client.post(
        "/api/uploads",
        data={"title": "Minha live especial"},
        files={
            "video": ("entrada.mp4", b"1234567890", "video/mp4"),
            "captions": ("legenda.json", CAPTIONS_JSON, "application/json"),
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
            "captions": ("legenda.json", CAPTIONS_JSON, "application/json"),
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
            "captions": ("legenda.txt", b"srt", "text/plain"),
        },
    )

    assert response.status_code == 415
    assert list((Path(upload_api.upload_root())).iterdir()) == []


def test_upload_rejects_invalid_captions_root_type(client: TestClient) -> None:
    invalid_captions_json = json.dumps({}).encode("utf-8")
    response = client.post(
        "/api/uploads",
        data={"title": "Título"},
        files={
            "video": ("entrada.mp4", b"video_content", "video/mp4"),
            "captions": ("legenda.json", invalid_captions_json, "application/json"),
        },
    )
    assert response.status_code == 400
    assert "O JSON de legendas deve ser uma lista ou conter a chave 'legendas'." in response.json()["detail"]


def test_upload_rejects_empty_captions_list(client: TestClient) -> None:
    empty_captions_json = json.dumps([]).encode("utf-8")
    response = client.post(
        "/api/uploads",
        data={"title": "Título"},
        files={
            "video": ("entrada.mp4", b"video_content", "video/mp4"),
            "captions": ("legenda.json", empty_captions_json, "application/json"),
        },
    )
    assert response.status_code == 400
    assert "A lista está vazia" in response.json()["detail"]


def test_upload_rejects_invalid_captions_item_type(client: TestClient) -> None:
    invalid_item_captions_json = json.dumps(["palavra1", "palavra2"]).encode("utf-8")
    response = client.post(
        "/api/uploads",
        data={"title": "Título"},
        files={
            "video": ("entrada.mp4", b"video_content", "video/mp4"),
            "captions": ("legenda.json", invalid_item_captions_json, "application/json"),
        },
    )
    assert response.status_code == 400
    assert "Formato inesperado" in response.json()["detail"]


def test_upload_rejects_captions_missing_or_empty_word(client: TestClient) -> None:
    missing_word_captions_json = json.dumps([{"start": 0.0, "end": 1.0}]).encode("utf-8")
    empty_word_captions_json = json.dumps([{"word": "", "start": 0.0, "end": 1.0}]).encode("utf-8")

    # Teste com 'word' ausente
    response_missing = client.post(
        "/api/uploads",
        data={"title": "Título"},
        files={
            "video": ("entrada.mp4", b"video_content", "video/mp4"),
            "captions": ("legenda.json", missing_word_captions_json, "application/json"),
        },
    )
    assert response_missing.status_code == 400
    assert "deve ter a chave 'word' com um valor de texto não vazio" in response_missing.json()["detail"]

    # Teste com 'word' vazio
    response_empty = client.post(
        "/api/uploads",
        data={"title": "Título"},
        files={
            "video": ("entrada.mp4", b"video_content", "video/mp4"),
            "captions": ("legenda.json", empty_word_captions_json, "application/json"),
        },
    )
    assert response_empty.status_code == 400
    assert "deve ter a chave 'word' com um valor de texto não vazio" in response_empty.json()["detail"]


def test_upload_accepts_valid_captions_format(client: TestClient) -> None:
    valid_captions_json = json.dumps([{"word": "Teste", "start": 0.0, "end": 1.0}]).encode("utf-8")
    response = client.post(
        "/api/uploads",
        data={"title": "Título"},
        files={
            "video": ("entrada.mp4", b"video_content", "video/mp4"),
            "captions": ("legenda.json", valid_captions_json, "application/json"),
        },
    )
    assert response.status_code == 201


def test_upload_rejects_video_above_configured_limit(client: TestClient) -> None:
    response = client.post(
        "/api/uploads",
        data={"title": "Teste"},
        files={
            "video": ("entrada.mp4", b"x" * 101, "video/mp4"),
            "captions": ("legenda.json", CAPTIONS_JSON, "application/json"),
        },
    )

    assert response.status_code == 413
    assert list(Path(upload_api.upload_root()).glob("*/")) == []


def test_upload_rejects_missing_title(client: TestClient) -> None:
    response = client.post(
        "/api/uploads",
        files={
            "video": ("entrada.mp4", b"video", "video/mp4"),
            "captions": ("legenda.json", CAPTIONS_JSON, "application/json"),
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


def test_create_cut_removes_interval_and_rebases_captions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = _upload_project(client)
    input_directory = upload_api.upload_root() / project_id / "input"
    (input_directory / "captions.json").write_text(
        json.dumps(
            [
                {"word": "Ola", "start": 0.0, "end": 1.0},
                {"word": "Mundo", "start": 4.0, "end": 5.0},
            ]
        ),
        encoding="utf-8",
    )
    manifest = upload_api._read_manifest(project_id)
    manifest["metadata"]["duration_seconds"] = 5.0
    upload_api._write_manifest(project_id, manifest)

    def fake_cut(video_path, remove_intervals, total_duration, output_path=None, work_dir=None):
        from cutter import compute_keep_intervals

        resolved_output_path = output_path or str(Path(video_path).with_suffix(".cut.mp4"))
        Path(resolved_output_path).write_bytes(b"cutvideo")
        keep_intervals = compute_keep_intervals(remove_intervals, total_duration)
        return {
            "output_path": resolved_output_path,
            "keep_intervals": keep_intervals,
            "preserved_intervals": [],
        }

    monkeypatch.setattr(upload_api, "cut_by_removed_intervals", fake_cut)
    monkeypatch.setattr(
        upload_api,
        "validate_video_file",
        lambda path: VideoMetadata(str(path), 3.0, 1280, 720, "h264", Path(path).stat().st_size),
    )

    response = client.post(
        f"/api/projects/{project_id}/cuts",
        json={"remove_intervals": [{"start": 1.0, "end": 3.0}]},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["metadata"]["duration_seconds"] == 3.0
    captions = body["captions"]
    assert captions[0] == {"word": "Ola", "start": 0.0, "end": 1.0}
    assert captions[1]["word"] == "Mundo"
    assert captions[1]["start"] == pytest.approx(2.0)
    assert captions[1]["end"] == pytest.approx(3.0)

def test_thumbnail_generates_once_and_returns_jpeg(client, monkeypatch):
    import upload_api
    project_id = _upload_project(client)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b"jpg")

    monkeypatch.setattr(upload_api.subprocess, "run", fake_run)
    first = client.get(f"/api/projects/{project_id}/thumbnail")
    second = client.get(f"/api/projects/{project_id}/thumbnail")
    assert first.status_code == 200 and first.headers["content-type"] == "image/jpeg"
    assert second.status_code == 200
    assert len(calls) == 1
    assert "-ss" in calls[0] and "00:00:05" in calls[0] and "-vframes" in calls[0]
