import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Optional

from filelock import FileLock
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from analyzer import analyze_video
from cutter import compute_auto_remove_intervals, cut_by_removed_intervals
from disfluency_detector import detect_disfluencies
from silence_detector import detect_silences
from main import DEFAULT_LUT_PATH, run_pipeline
from pipeline_contracts import (
    InputValidationError,
    build_output_stem,
    load_captions_json,
    normalize_captions_payload,
    serialize_captions,
    serialize_video_metadata,
    validate_captions_srt,
    validate_video_file,
)
from segmentation import rebase_captions_to_intervals
from setup_manager import ensure_project_structure

LOGGER = logging.getLogger(__name__)

BYTES_PER_MIB = 1024 * 1024
BYTES_PER_GIB = 1024 * BYTES_PER_MIB
DEFAULT_MAX_VIDEO_BYTES = 2 * BYTES_PER_GIB
DEFAULT_MAX_CAPTIONS_BYTES = 16 * BYTES_PER_MIB


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


def max_captions_bytes() -> int:
    return _positive_int_from_env("TAPA_NA_LATA_MAX_CAPTIONS_BYTES", DEFAULT_MAX_CAPTIONS_BYTES)


def upload_root() -> Path:
    root = Path(
        os.getenv(
            "TAPA_NA_LATA_UPLOAD_DIR",
            str(Path(tempfile.gettempdir()) / "autoEditor" / "uploads"),
        )
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


CLEANUP_INTERVAL_SECONDS = 60 * 60
CLEANUP_MAX_AGE_SECONDS = 24 * 60 * 60


def _latest_mtime(directory: Path) -> float:
    # O mtime da pasta não muda quando ficheiros aninhados são escritos; usa o mais recente da árvore.
    latest = os.path.getmtime(directory)
    for current, _dirs, files in os.walk(directory):
        for name in files:
            try:
                latest = max(latest, os.path.getmtime(os.path.join(current, name)))
            except OSError:
                continue
    return latest


def _directory_size(directory: Path) -> int:
    total = 0
    for current, _dirs, files in os.walk(directory):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(current, name))
            except OSError:
                continue
    return total


def _cleanup_old_projects() -> tuple[int, int]:
    removed = 0
    freed = 0
    cutoff = time.time() - CLEANUP_MAX_AGE_SECONDS
    with _ACTIVE_EDITS_LOCK:
        active_projects = {project_id for project_id, _ in _ACTIVE_EDITS}
    for directory in upload_root().iterdir():
        try:
            if not directory.is_dir() or directory.name in active_projects:
                continue
            if _latest_mtime(directory) >= cutoff:
                continue
            size = _directory_size(directory)
            shutil.rmtree(directory)
            removed += 1
            freed += size
        except OSError:
            LOGGER.exception("Falha ao limpar a pasta de upload %s", directory)
    return removed, freed


async def _background_cleanup_task() -> None:
    while True:
        try:
            removed, freed = await asyncio.to_thread(_cleanup_old_projects)
            LOGGER.info(
                "Limpeza de uploads: %d pasta(s) removida(s), %d bytes libertados (%s)",
                removed,
                freed,
                datetime.now().isoformat(timespec="seconds"),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Falha na limpeza periódica de uploads")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cleanup_task = asyncio.create_task(_background_cleanup_task())
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task


app = FastAPI(title="Tapa na Lata", version="0.1.0", lifespan=lifespan)
cors_origins = [origin.strip() for origin in os.getenv(
    "TAPA_NA_LATA_CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
).split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["*"], allow_headers=["*"], allow_credentials=False)
EDIT_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tapa-edit")
ACTIVE_EDIT_STATUSES = frozenset({"queued", "running"})
# Apenas liveness deste processo (não é estado de negócio): edições que este processo está a tratar.
_ACTIVE_EDITS: set[tuple[str, str]] = set()
_ACTIVE_EDITS_LOCK = threading.Lock()
_HEARTBEAT_THREAD: threading.Thread | None = None


def edit_stale_seconds() -> int:
    return _positive_int_from_env("TAPA_NA_LATA_EDIT_STALE_SECONDS", 30)


def edit_heartbeat_interval_seconds() -> float:
    return max(edit_stale_seconds() / 6, 0.1)


class EditRequest(BaseModel):
    name: str | None = None
    lut_path: str | None = None
    remove_silence: bool = False
    silence_threshold: float = Field(default=0.3, ge=0.0, le=2.0)
    face_tracking: bool = True
    display_title: str | None = None
    safe_area: float = Field(default=0.0, ge=0.0, le=0.2)
    crop_x: int | None = None
    crop_y: int | None = None
    crop_w: int | None = None
    crop_h: int | None = None
    content_crop_x: Optional[int] = None
    content_crop_y: Optional[int] = None
    content_crop_w: Optional[int] = None
    content_crop_h: Optional[int] = None
    subtitle_font: Literal["Arial", "Roboto", "Anton", "Sansation", "DejaVu Sans"] = "Arial"
    subtitle_color_preset: Literal[
        "white_black_outline", "yellow_shadow", "white_black_box", "cyan_black_outline"
    ] = "white_black_outline"
    subtitle_position_y: Literal["top", "center", "bottom"] = "bottom"
    subtitle_scale: float = Field(default=1.0, ge=0.5, le=2.0)


class RemovedInterval(BaseModel):
    start: float = Field(ge=0.0)
    end: float


class CutRequest(BaseModel):
    remove_intervals: list[RemovedInterval] | None = None


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

    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Manifesto do projeto inválido.") from exc


def _write_manifest(project_id: str, manifest: dict[str, Any]) -> None:
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


def _validate_saved_files(video_path: Path, captions_path: Path) -> Any:
    try:
        metadata = validate_video_file(str(video_path))
        validate_captions_srt(str(captions_path))
        return metadata
    except InputValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


def _normalize_captions_file(captions_path: Path) -> None:
    # Reescreve o JSON recebido (legado ou {"legendas":[{"texto",...}]}) no formato interno {word,start,end}.
    try:
        normalized_captions = load_captions_json(str(captions_path))
        captions_path.write_text(json.dumps(normalized_captions, ensure_ascii=False, indent=2), encoding="utf-8")
    except InputValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


def _edit_state_paths(project_id: str, edit_id: str) -> tuple[Path, Path]:
    try:
        uuid.UUID(project_id)
        uuid.UUID(edit_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Edição não encontrada.") from exc
    edit_directory = upload_root() / project_id / "edits" / edit_id
    return edit_directory / "state.json", edit_directory / "state.json.lock"


def _atomic_write_state(state_path: Path, state: dict[str, Any]) -> None:
    temporary_path = state_path.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(jsonable_encoder(state), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary_path, state_path)


def _mark_active(project_id: str, edit_id: str) -> None:
    global _HEARTBEAT_THREAD
    with _ACTIVE_EDITS_LOCK:
        _ACTIVE_EDITS.add((project_id, edit_id))
        if _HEARTBEAT_THREAD is None or not _HEARTBEAT_THREAD.is_alive():
            _HEARTBEAT_THREAD = threading.Thread(
                target=_heartbeat_loop, name="tapa-edit-heartbeat", daemon=True
            )
            _HEARTBEAT_THREAD.start()


def _mark_inactive(project_id: str, edit_id: str) -> None:
    with _ACTIVE_EDITS_LOCK:
        _ACTIVE_EDITS.discard((project_id, edit_id))


def _is_active_here(project_id: str, edit_id: str) -> bool:
    with _ACTIVE_EDITS_LOCK:
        return (project_id, edit_id) in _ACTIVE_EDITS


def _heartbeat_loop() -> None:
    # Renova o carimbo das edições deste processo; se o processo morrer, o carimbo expira.
    while True:
        time.sleep(edit_heartbeat_interval_seconds())
        with _ACTIVE_EDITS_LOCK:
            active = list(_ACTIVE_EDITS)
        for project_id, edit_id in active:
            try:
                state_path, lock_path = _edit_state_paths(project_id, edit_id)
                with FileLock(str(lock_path)):
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    if state.get("status") in ACTIVE_EDIT_STATUSES:
                        state["heartbeat_at"] = time.time()
                        _atomic_write_state(state_path, state)
            except Exception:
                LOGGER.debug("Falha ao renovar heartbeat da edição %s", edit_id, exc_info=True)


def _read_edit_state(project_id: str, edit_id: str) -> dict[str, Any] | None:
    state_path, lock_path = _edit_state_paths(project_id, edit_id)
    if not state_path.parent.is_dir():
        return None
    with FileLock(str(lock_path)):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if (
            state.get("status") in ACTIVE_EDIT_STATUSES
            and not _is_active_here(project_id, edit_id)
            and time.time() - float(state.get("heartbeat_at", 0)) > edit_stale_seconds()
        ):
            state.update(
                {
                    "status": "failed",
                    "progress_percent": 100,
                    "stage": "failed",
                    "current_phase": "Interrompido",
                    "error": "A edição foi interrompida (o servidor foi reiniciado ou parou). Inicie-a novamente.",
                }
            )
            state.setdefault("logs", []).append("Erro: A edição foi interrompida inesperadamente.")
            _atomic_write_state(state_path, state)
        return state


def _write_edit_state(project_id: str, edit_id: str, state: dict[str, Any]) -> None:
    state_path, lock_path = _edit_state_paths(project_id, edit_id)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state["heartbeat_at"] = time.time()
    with FileLock(str(lock_path)):
        _atomic_write_state(state_path, state)


def _update_edit_state(project_id: str, edit_id: str, updates: dict[str, Any], log: str | None = None) -> dict[str, Any]:
    # Lê-modifica-grava sob lock para não perder logs escritos por outras chamadas.
    state_path, lock_path = _edit_state_paths(project_id, edit_id)
    with FileLock(str(lock_path)):
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.update(updates)
        if log is not None:
            state.setdefault("logs", []).append(log)
        state["heartbeat_at"] = time.time()
        _atomic_write_state(state_path, state)
    return state


def _append_log(edit_id: str, message: str, project_id: str | None = None) -> None:
    if project_id is None:
        project_id = _project_id_for_edit(edit_id)
    if project_id is None:
        return
    try:
        _update_edit_state(project_id, edit_id, {}, log=message)
    except Exception:
        LOGGER.debug("Falha ao gravar log da edição %s", edit_id, exc_info=True)


def _project_id_for_edit(edit_id: str) -> str | None:
    with _ACTIVE_EDITS_LOCK:
        for active_project_id, active_edit_id in _ACTIVE_EDITS:
            if active_edit_id == edit_id:
                return active_project_id
    return None


def _set_phase(project_id: str, edit_id: str, phase: str, progress_percent: int | None = None) -> None:
    updates: dict[str, Any] = {"current_phase": phase}
    if progress_percent is not None:
        updates["progress_percent"] = progress_percent
    try:
        _update_edit_state(project_id, edit_id, updates, log=phase)
    except Exception:
        LOGGER.debug("Falha ao gravar fase da edição %s", edit_id, exc_info=True)


PHASE_PROGRESS = {
    "A extrair áudio e metadados": 10,
    "A analisar silêncios e disfluências": 30,
    "A aplicar parâmetros de câmara": 50,
    "A renderizar vídeo final": 75,
}


def _run_edit(project_id: str, edit_id: str, request: EditRequest) -> None:
    project_directory = upload_root() / project_id
    input_directory = project_directory / "input"
    output_directory = project_directory / "edits" / edit_id / "output"
    output_directory.mkdir(parents=True, exist_ok=True)
    state = _read_edit_state(project_id, edit_id) or {
        "project_id": project_id,
        "edit_id": edit_id,
        "outputs": [],
    }
    state.setdefault("current_phase", "")
    state.setdefault("logs", [])

    def on_phase(phase: str) -> None:
        _set_phase(project_id, edit_id, phase, PHASE_PROGRESS.get(phase))

    def on_log(message: str) -> None:
        _append_log(edit_id, message, project_id)

    try:
        state = _update_edit_state(
            project_id, edit_id, {"status": "running", "progress_percent": 10, "stage": "processing"}, log="Edição iniciada"
        )
        result = run_pipeline(
            str(input_directory / "video.mp4"),
            str(input_directory / "captions.srt"),
            project_root=str(project_directory),
            lut_path=request.lut_path or DEFAULT_LUT_PATH,
            output_directory=str(output_directory),
            output_name=request.name or _read_manifest(project_id)["title"],
            remove_silence=request.remove_silence,
            silence_threshold=request.silence_threshold,
            face_tracking=request.face_tracking,
            display_title=request.display_title or "",
            safe_area=request.safe_area,
            crop_x=request.crop_x,
            crop_y=request.crop_y,
            crop_w=request.crop_w,
            crop_h=request.crop_h,
            content_crop_x=request.content_crop_x,
            content_crop_y=request.content_crop_y,
            content_crop_w=request.content_crop_w,
            content_crop_h=request.content_crop_h,
            subtitle_font=request.subtitle_font,
            subtitle_color_preset=request.subtitle_color_preset,
            subtitle_position_y=request.subtitle_position_y,
            subtitle_scale=request.subtitle_scale,
            on_phase=on_phase,
            on_log=on_log,
        )
        files = [
            {"output_id": path.name, "filename": path.name, "size_bytes": path.stat().st_size}
            for path in output_directory.rglob("*")
            if path.is_file()
        ]
        _update_edit_state(
            project_id,
            edit_id,
            {
                "status": "completed",
                "progress_percent": 100,
                "stage": "completed",
                "current_phase": "Concluído",
                "outputs": files,
                "silence_removal": result.get("silence_removal", {}),
                "result": result,
            },
            log="Edição concluída",
        )
    except Exception as exc:
        LOGGER.exception("Edição %s falhou", edit_id)
        try:
            _update_edit_state(
                project_id,
                edit_id,
                {
                    "status": "failed",
                    "progress_percent": 100,
                    "stage": "failed",
                    "current_phase": "Falhou",
                    "error": str(exc),
                },
                log=f"Erro: {exc}",
            )
        except Exception:
            LOGGER.exception("Não foi possível guardar o estado falhado da edição %s", edit_id)
    finally:
        _mark_inactive(project_id, edit_id)


def _public_edit_state(state: dict[str, Any]) -> dict[str, Any]:
    public = {
        key: value
        for key, value in state.items()
        if key not in {"result", "heartbeat_at"}
    }
    public.setdefault("current_phase", "")
    public.setdefault("logs", [])
    return public


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/uploads", status_code=status.HTTP_201_CREATED)
async def create_upload(
    video: Annotated[UploadFile, File(...)],
    captions: Annotated[UploadFile, File(...)],
    title: Annotated[str, Form(...)],
) -> dict[str, object]:
    normalized_title = title.strip()
    if not normalized_title:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O título é obrigatório e não pode ficar vazio.",
        )

    _extension_for(video, ".mp4")
    _extension_for(captions, ".srt")

    upload_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    directory = upload_root() / project_id
    input_directory = directory / "input"
    input_directory.mkdir(parents=True, exist_ok=False)
    ensure_project_structure(str(directory))
    video_path = input_directory / "video.mp4"
    captions_path = input_directory / "captions.srt"

    try:
        video_size = await _save_upload(video, video_path, max_video_bytes())
        captions_size = await _save_upload(captions, captions_path, max_captions_bytes())
        metadata_object = _validate_saved_files(video_path, captions_path)
    except InputValidationError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
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
        "input": {"video": "input/video.mp4", "captions": "input/captions.srt"},
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
            "captions": {"filename": captions.filename, "size_bytes": captions_size},
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


@app.get("/api/projects/{project_id}/video")
def project_video(project_id: str) -> FileResponse:
    video_path = _project_directory(project_id) / "input" / "video.mp4"
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail="Vídeo não encontrado.")
    return FileResponse(video_path, media_type="video/mp4")


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}


def _find_aroll_video(project_dir: Path) -> Path | None:
    aroll_dir = project_dir / "Videos" / "A-Roll"
    if aroll_dir.is_dir():
        for candidate in sorted(aroll_dir.iterdir()):
            if candidate.is_file() and candidate.suffix.lower() in VIDEO_EXTENSIONS:
                return candidate
    # O upload grava o vídeo em input/ e não popula A-Roll.
    fallback = project_dir / "input" / "video.mp4"
    return fallback if fallback.is_file() else None


@app.get("/api/projects/{project_id}/thumbnail")
def project_thumbnail(project_id: str) -> FileResponse:
    project_dir = _project_directory(project_id)
    thumbnail_path = project_dir / "thumbnail.jpg"

    if not thumbnail_path.is_file():
        video_path = _find_aroll_video(project_dir)
        if video_path is None:
            raise HTTPException(status_code=404, detail="Vídeo original não encontrado.")

        command = [
            "ffmpeg", "-y",
            "-ss", "00:00:05",
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            str(thumbnail_path),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="FFmpeg não encontrado.")
        except subprocess.CalledProcessError as error:
            LOGGER.error("Falha ao gerar thumbnail: %s", error.stderr)
            thumbnail_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="Falha ao gerar thumbnail.")

        # Vídeos com menos de 5s não geram frame com -ss 5.
        if not thumbnail_path.is_file() or thumbnail_path.stat().st_size == 0:
            thumbnail_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="Thumbnail não foi gerada.")

    return FileResponse(thumbnail_path, media_type="image/jpeg")


@app.get("/api/projects/{project_id}/captions")
def project_captions(project_id: str) -> dict[str, Any]:
    captions_path = _project_directory(project_id) / "input" / "captions.json"
    if not captions_path.is_file():
        raise HTTPException(status_code=404, detail="Legendas não encontradas.")
    return {"project_id": project_id, "captions": load_captions_json(str(captions_path))}


@app.get("/api/projects/{project_id}/suggested-cuts")
def project_suggested_cuts(project_id: str) -> dict[str, Any]:
    input_directory = _project_directory(project_id) / "input"
    video_path = input_directory / "video.mp4"
    captions_path = input_directory / "captions.json"
    if not video_path.is_file() or not captions_path.is_file():
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")

    captions = load_captions_json(str(captions_path))
    try:
        silences = detect_silences(str(video_path))
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="FFmpeg não encontrado.")
    except RuntimeError as exc:
        LOGGER.error("Falha ao detectar silêncios: %s", exc)
        raise HTTPException(status_code=500, detail="Falha ao detectar silêncios.")

    return {
        "project_id": project_id,
        "silences": silences,
        "disfluencies": detect_disfluencies(captions),
    }


@app.post("/api/projects/{project_id}/cuts", status_code=status.HTTP_201_CREATED)
def create_cut(project_id: str, request: CutRequest) -> dict[str, Any]:
    manifest = _read_manifest(project_id)
    project_directory = _project_directory(project_id)
    input_directory = project_directory / "input"
    video_path = input_directory / "video.mp4"
    captions_path = input_directory / "captions.json"

    total_duration = float(manifest["metadata"]["duration_seconds"])
    captions = load_captions_json(str(captions_path))
    if request.remove_intervals:
        remove_intervals = [(interval.start, interval.end) for interval in request.remove_intervals]
    else:
        # Sem exclusões manuais: corte automático (silêncios de áudio + repetições).
        remove_intervals = compute_auto_remove_intervals(str(video_path), captions, total_duration)
        if not remove_intervals:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Nenhum silêncio ou repetição foi detectado.",
            )

    cut_id = str(uuid.uuid4())
    cut_directory = project_directory / "cuts" / cut_id
    cut_directory.mkdir(parents=True, exist_ok=True)
    cut_video_path = cut_directory / "video.mp4"

    try:
        cut_result = cut_by_removed_intervals(
            str(video_path),
            remove_intervals,
            total_duration,
            output_path=str(cut_video_path),
            work_dir=str(cut_directory),
        )
    except ValueError as exc:
        shutil.rmtree(cut_directory, ignore_errors=True)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    rebased_captions = rebase_captions_to_intervals(captions, cut_result["keep_intervals"])
    new_metadata = serialize_video_metadata(validate_video_file(cut_result["output_path"]))

    shutil.copy2(cut_result["output_path"], video_path)
    captions_path.write_text(
        json.dumps(rebased_captions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    shutil.rmtree(cut_directory, ignore_errors=True)

    manifest["metadata"] = new_metadata
    _write_manifest(project_id, manifest)

    return {
        "project_id": project_id,
        "cut_id": cut_id,
        "metadata": new_metadata,
        "video_url": f"/api/projects/{project_id}/video?v={cut_id}",
        "captions": rebased_captions,
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
        "current_phase": "Na fila",
        "logs": [],
        "outputs": [],
    }
    (upload_root() / project_id / "edits" / edit_id / "output").mkdir(parents=True, exist_ok=True)
    _mark_active(project_id, edit_id)
    _write_edit_state(project_id, edit_id, state)
    EDIT_EXECUTOR.submit(_run_edit, project_id, edit_id, request.model_copy(update={"name": name}))
    return _public_edit_state(state)


@app.get("/api/projects/{project_id}/edits/{edit_id}")
def edit_progress(project_id: str, edit_id: str) -> dict[str, Any]:
    _project_directory(project_id)
    state = _read_edit_state(project_id, edit_id)
    if state is None or state.get("project_id") != project_id:
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
