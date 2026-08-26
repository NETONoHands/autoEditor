import logging
import os
import shutil
import tempfile
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from analyzer import analyze_video
from main import DEFAULT_LUT_PATH, run_pipeline
from pipeline_contracts import (
    InputValidationError,
    build_output_stem,
    serialize_video_metadata,
    validate_srt_file,
    validate_video_file,
)

LOGGER = logging.getLogger(__name__)

BYTES_PER_MIB = 1024 * 1024
BYTES_PER_GIB = 1024 * BYTES_PER_MIB
DEFAULT_MAX_VIDEO_BYTES = 2 * BYTES_PER_GIB
DEFAULT_MAX_SRT_BYTES = 16 * BYTES_PER_MIB


def _positive_int_from_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} deve ser um número inteiro positivo.") from exc
    if value <= 0:
        raise RuntimeError(f"{name} deve ser um número inteiro positivo.")
    return value


def max_video_bytes() -> int:
    return _positive_int_from_env("TAPA_NA_LATA_MAX_VIDEO_BYTES", DEFAULT_MAX_VIDEO_BYTES)


def max_srt_bytes() -> int:
    return _positive_int_from_env("TAPA_NA_LATA_MAX_SRT_BYTES", DEFAULT_MAX_SRT_BYTES)


def upload_root() -> Path:
    root = Path(
        os.getenv(
            "TAPA_NA_LATA_UPLOAD_DIR",
            str(Path(tempfile.gettempdir()) / "autoEditor" / "uploads"),
        )
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


app = FastAPI(title="Tapa na Lata", version="0.1.0")
cors_origins = [origin.strip() for origin in os.getenv(
    "TAPA_NA_LATA_CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
).split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["*"], allow_headers=["*"], allow_credentials=False)
EDIT_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tapa-edit")
EDIT_STATES: dict[str, dict[str, Any]] = {}
EDIT_STATES_LOCK = threading.Lock()


class EditRequest(BaseModel):
    name: str | None = None
    lut_path: str | None = None
    remove_silence: bool = False
    silence_threshold: float = 0.3
    face_tracking: bool = True
    display_title: str | None = None


def _project_directory(project_id: str) -> Path:
    try:
        uuid.UUID(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Projeto não encontrado.") from exc
    directory = upload_root() / project_id
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")
    return directory


def _read_manifest(project_id: str) -> dict[str, Any]:
    manifest_path = _project_directory(project_id) / "manifest.json"
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")
    import json

    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Manifesto do projeto inválido.") from exc


def _write_manifest(project_id: str, manifest: dict[str, Any]) -> None:
    import json

    path = _project_directory(project_id) / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _extension_for(upload: UploadFile, expected: str) -> None:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix != expected:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"O arquivo {upload.filename or '<sem nome>'} deve usar a extensão {expected}.",
        )


async def _save_upload(upload: UploadFile, destination: Path, maximum_bytes: int) -> int:
    total_bytes = 0
    try:
        with destination.open("wb") as output_file:
            while chunk := await upload.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > maximum_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"O arquivo {upload.filename or '<sem nome>'} excede o limite permitido.",
                    )
                output_file.write(chunk)
    finally:
        await upload.close()
    return total_bytes


def _validate_saved_files(video_path: Path, subtitle_path: Path) -> Any:
    try:
        metadata = validate_video_file(str(video_path))
        validate_srt_file(str(subtitle_path))
        return metadata
    except InputValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


def _run_edit(project_id: str, edit_id: str, request: EditRequest) -> None:
    project_directory = upload_root() / project_id
    input_directory = project_directory / "input"
    output_directory = project_directory / "edits" / edit_id / "output"
    output_directory.mkdir(parents=True, exist_ok=True)
    state = EDIT_STATES[edit_id]
    try:
        state.update({"status": "running", "progress_percent": 10, "stage": "processing"})
        result = run_pipeline(
            str(input_directory / "video.mp4"),
            str(input_directory / "subtitle.srt"),
            project_root=str(project_directory),
            lut_path=request.lut_path or DEFAULT_LUT_PATH,
            output_directory=str(output_directory),
            output_name=request.name or _read_manifest(project_id)["title"],
            remove_silence=request.remove_silence,
            silence_threshold=request.silence_threshold,
            face_tracking=request.face_tracking,
            display_title=request.display_title or "",
        )
        files = [
            {"output_id": path.name, "filename": path.name, "size_bytes": path.stat().st_size}
            for path in output_directory.rglob("*")
            if path.is_file()
        ]
        state.update(
            {
                "status": "completed",
                "progress_percent": 100,
                "stage": "completed",
                "outputs": files,
                "silence_removal": result.get("silence_removal", {}),
                "result": result,
            }
        )
    except Exception as exc:
        LOGGER.exception("Edição %s falhou", edit_id)
        state.update({"status": "failed", "progress_percent": 100, "stage": "failed", "error": str(exc)})


def _public_edit_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in state.items()
        if key not in {"result"}
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/uploads", status_code=status.HTTP_201_CREATED)
async def create_upload(
    video: Annotated[UploadFile, File(...)],
    subtitle: Annotated[UploadFile, File(...)],
    title: Annotated[str, Form(...)],
) -> dict[str, object]:
    normalized_title = title.strip()
    if not normalized_title:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O título é obrigatório e não pode ficar vazio.",
        )

    _extension_for(video, ".mp4")
    _extension_for(subtitle, ".srt")

    upload_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    directory = upload_root() / project_id
    input_directory = directory / "input"
    input_directory.mkdir(parents=True, exist_ok=False)
    video_path = input_directory / "video.mp4"
    subtitle_path = input_directory / "subtitle.srt"

    try:
        video_size = await _save_upload(video, video_path, max_video_bytes())
        subtitle_size = await _save_upload(subtitle, subtitle_path, max_srt_bytes())
        metadata_object = _validate_saved_files(video_path, subtitle_path)
    except HTTPException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except OSError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        LOGGER.exception("Falha ao armazenar upload %s", upload_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Não foi possível armazenar o upload.",
        ) from exc
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        LOGGER.exception("Falha inesperada ao validar upload %s", upload_id)
        raise

    metadata = serialize_video_metadata(metadata_object)
    manifest = {
        "project_id": project_id,
        "upload_id": upload_id,
        "title": normalized_title,
        "input": {"video": "input/video.mp4", "subtitle": "input/subtitle.srt"},
        "metadata": metadata,
    }
    _write_manifest(project_id, manifest)

    return {
        "project_id": project_id,
        "upload_id": upload_id,
        "status": "uploaded",
        "title": normalized_title,
        "metadata": metadata,
        "files": {
            "video": {"filename": video.filename, "size_bytes": video_size},
            "subtitle": {"filename": subtitle.filename, "size_bytes": subtitle_size},
        },
    }


@app.get("/api/projects/{project_id}/metadata")
def project_metadata(project_id: str) -> dict[str, Any]:
    manifest = _read_manifest(project_id)
    return {
        "project_id": project_id,
        "title": manifest["title"],
        "metadata": manifest["metadata"],
    }


@app.post("/api/projects/{project_id}/edits", status_code=status.HTTP_202_ACCEPTED)
def start_edit(project_id: str, request: EditRequest) -> dict[str, Any]:
    manifest = _read_manifest(project_id)
    name = (request.name or manifest["title"]).strip()
    if not name:
        raise HTTPException(status_code=400, detail="O nome da edição é obrigatório.")
    try:
        build_output_stem(name)
    except InputValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    edit_id = str(uuid.uuid4())
    state = {
        "project_id": project_id,
        "edit_id": edit_id,
        "status": "queued",
        "progress_percent": 0,
        "stage": "queued",
        "outputs": [],
    }
    with EDIT_STATES_LOCK:
        EDIT_STATES[edit_id] = state
    (upload_root() / project_id / "edits" / edit_id / "output").mkdir(parents=True, exist_ok=True)
    EDIT_EXECUTOR.submit(_run_edit, project_id, edit_id, request.model_copy(update={"name": name}))
    return _public_edit_state(state)


@app.get("/api/projects/{project_id}/edits/{edit_id}")
def edit_progress(project_id: str, edit_id: str) -> dict[str, Any]:
    _project_directory(project_id)
    with EDIT_STATES_LOCK:
        state = EDIT_STATES.get(edit_id)
    if state is None or state["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="Edição não encontrada.")
    return _public_edit_state(state)


@app.get("/api/projects/{project_id}/edits/{edit_id}/outputs")
def edit_outputs(project_id: str, edit_id: str) -> dict[str, Any]:
    state = edit_progress(project_id, edit_id)
    if state["status"] != "completed":
        return {"edit_id": edit_id, "status": state["status"], "outputs": []}
    return {"edit_id": edit_id, "status": state["status"], "outputs": state["outputs"]}


def _output_path(project_id: str, edit_id: str, output_id: str) -> Path:
    state = edit_progress(project_id, edit_id)
    if state["status"] != "completed":
        raise HTTPException(status_code=409, detail="Os resultados ainda não estão disponíveis.")
    if Path(output_id).name != output_id or output_id in {".", ".."}:
        raise HTTPException(status_code=404, detail="Resultado não encontrado.")
    allowed_outputs = {item["output_id"] for item in state["outputs"]}
    if output_id not in allowed_outputs:
        raise HTTPException(status_code=404, detail="Resultado não encontrado.")
    output_directory = _project_directory(project_id) / "edits" / edit_id / "output"
    path = next((candidate for candidate in output_directory.rglob(output_id) if candidate.is_file()), None)
    if path is None:
        raise HTTPException(status_code=404, detail="Resultado não encontrado.")
    return path


@app.get("/api/projects/{project_id}/edits/{edit_id}/outputs/{output_id}/download")
def download_output(project_id: str, edit_id: str, output_id: str) -> FileResponse:
    path = _output_path(project_id, edit_id, output_id)
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@app.get("/api/projects/{project_id}/edits/{edit_id}/download.zip")
def download_outputs_zip(project_id: str, edit_id: str) -> FileResponse:
    edit_progress(project_id, edit_id)
    output_directory = _project_directory(project_id) / "edits" / edit_id / "output"
    if not output_directory.is_dir():
        raise HTTPException(status_code=404, detail="Resultados não encontrados.")
    zip_path = output_directory.parent / f"{edit_id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        files = [path for path in output_directory.rglob("*") if path.is_file()]
        if not files:
            raise HTTPException(status_code=404, detail="Resultados não encontrados.")
        for path in files:
            archive.write(path, arcname=path.relative_to(output_directory))
    return FileResponse(zip_path, filename=f"{edit_id}.zip", media_type="application/zip")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "upload_api:app",
        host=os.getenv("TAPA_NA_LATA_HOST", "127.0.0.1"),
        port=_positive_int_from_env("TAPA_NA_LATA_PORT", 8000),
        reload=False,
    )
