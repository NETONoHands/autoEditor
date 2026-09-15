import json
import math
import os
import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ValidationError

MAX_PART_DURATION_SECONDS = 3600.0
MIN_INPUT_WIDTH = 1280
MIN_INPUT_HEIGHT = 720


class InputValidationError(ValueError):
    """Indicates that a user-provided media or subtitle file is invalid."""


@dataclass(frozen=True)
class VideoMetadata:
    path: str
    duration_seconds: float
    width: int
    height: int
    codec_name: str
    size_bytes: int

    @property
    def classification(self) -> str:
        return "short" if self.duration_seconds <= MAX_PART_DURATION_SECONDS else "long"


def normalize_existing_path(path: str, label: str) -> str:
    resolved_path = os.path.abspath(os.fspath(path))
    if not os.path.isfile(resolved_path):
        raise InputValidationError(f"{label} não encontrado: {resolved_path}")
    return resolved_path


def validate_captions_json(captions_path: str) -> str:
    resolved_path = normalize_existing_path(captions_path, "Arquivo de legendas")
    if Path(resolved_path).suffix.lower() != ".json":
        raise InputValidationError("A legenda deve estar no formato JSON (.json).")

    try:
        content = Path(resolved_path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputValidationError("O arquivo JSON de legendas deve usar texto UTF-8.") from exc

    try:
        captions = json.loads(content)
    except json.JSONDecodeError as exc:
        raise InputValidationError("O arquivo de legendas não é um JSON válido.") from exc

    _validate_captions_payload(normalize_captions_payload(captions))
    return resolved_path


def _validate_captions_payload(captions: Any) -> None:
    if not isinstance(captions, list) or not captions:
        raise InputValidationError("O JSON de legendas deve ser uma lista não vazia de palavras.")

    previous_start = -1.0
    for index, item in enumerate(captions, start=1):
        if not isinstance(item, dict):
            raise InputValidationError(f"Item {index} do JSON de legendas deve ser um objeto.")
        word = item.get("word")
        start = item.get("start")
        end = item.get("end")
        if not isinstance(word, str) or not word.strip():
            raise InputValidationError(f"Item {index} do JSON de legendas deve ter 'word' não vazio.")
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            raise InputValidationError(f"Item {index} do JSON de legendas deve ter 'start' e 'end' numéricos.")
        start_value = float(start)
        end_value = float(end)
        if not math.isfinite(start_value) or not math.isfinite(end_value) or start_value < 0 or end_value <= start_value:
            raise InputValidationError(f"Intervalo inválido no item {index} do JSON de legendas.")
        if start_value < previous_start:
            raise InputValidationError("As palavras do JSON de legendas devem estar em ordem de tempo.")
        previous_start = start_value


def load_captions_json(captions_path: str) -> list[dict[str, Any]]:
    resolved_path = validate_captions_json(captions_path)
    raw_payload = json.loads(Path(resolved_path).read_text(encoding="utf-8-sig"))
    return serialize_captions(normalize_captions_payload(raw_payload))


class RawCaptionItem(BaseModel):
    texto: str
    start: float
    end: float


class RawCaptionsPayload(BaseModel):
    legendas: list[RawCaptionItem]


def normalize_captions_payload(payload: Any) -> list[dict[str, Any]]:
    # Aceita o formato interno legado (lista de {word,start,end}) e o novo ({"legendas": [{"texto","start","end"}]}).
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and "legendas" in payload:
        try:
            parsed_payload = RawCaptionsPayload.model_validate(payload)
        except ValidationError as exc:
            raise InputValidationError(f"JSON de legendas inválido: {exc}") from exc
        return [
            {"word": item.texto, "start": item.start, "end": item.end}
            for item in parsed_payload.legendas
        ]
    raise InputValidationError("O JSON de legendas deve ser uma lista ou conter a chave 'legendas'.")


def serialize_captions(captions: "list[dict[str, Any]]") -> list[dict[str, Any]]:
    return [
        {"word": str(item["word"]), "start": float(item["start"]), "end": float(item["end"])}
        for item in captions
    ]


def validate_srt_file(srt_path: str) -> str:
    resolved_path = normalize_existing_path(srt_path, "Arquivo SRT")
    if Path(resolved_path).suffix.lower() != ".srt":
        raise InputValidationError("A legenda deve estar no formato SRT (.srt).")

    try:
        content = Path(resolved_path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputValidationError("O arquivo SRT deve usar texto UTF-8.") from exc

    blocks = re.split(r"\r?\n\s*\r?\n", content.strip())
    if not blocks or not content.strip():
        raise InputValidationError("O arquivo SRT está vazio.")

    timestamp_pattern = re.compile(
        r"^\d{2}:\d{2}:\d{2},\d{3}\s+-->\s+\d{2}:\d{2}:\d{2},\d{3}$"
    )
    previous_start = -1.0
    for block_number, block in enumerate(blocks, start=1):
        lines = [line.strip() for line in block.splitlines()]
        if len(lines) < 3 or not lines[0].isdigit() or not timestamp_pattern.match(lines[1]):
            raise InputValidationError(
                f"Bloco {block_number} do SRT é inválido; esperados índice, timestamps e texto."
            )
        start_text, end_text = [part.strip() for part in lines[1].split("-->", 1)]
        start = _srt_timestamp_to_seconds(start_text)
        end = _srt_timestamp_to_seconds(end_text)
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise InputValidationError(f"Intervalo inválido no bloco {block_number} do SRT.")
        if start < previous_start:
            raise InputValidationError("Os timestamps do SRT devem estar em ordem.")
        previous_start = start
    return resolved_path


def _srt_timestamp_to_seconds(value: str) -> float:
    hours, minutes, seconds_milliseconds = value.split(":", 2)
    seconds, milliseconds = seconds_milliseconds.split(",", 1)
    hours_value = int(hours)
    minutes_value = int(minutes)
    seconds_value = int(seconds)
    if minutes_value >= 60 or seconds_value >= 60 or len(milliseconds) != 3:
        raise InputValidationError("Timestamp SRT fora do formato válido.")
    return hours_value * 3600 + minutes_value * 60 + seconds_value + int(milliseconds) / 1000


def _probe_video(video_path: str) -> dict[str, Any]:
    if Path(video_path).suffix.lower() != ".mp4":
        raise InputValidationError("O vídeo de entrada deve estar no formato MP4 (.mp4).")

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,width,height,codec_name",
        "-of",
        "json",
        video_path,
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffprobe não foi encontrado no PATH.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        raise InputValidationError(f"O MP4 não pôde ser lido pelo ffprobe: {detail}") from exc

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise InputValidationError("O ffprobe retornou metadata de vídeo inválida.") from exc


def validate_video_file(video_path: str) -> VideoMetadata:
    resolved_path = normalize_existing_path(video_path, "Vídeo")
    probe = _probe_video(resolved_path)
    streams = [stream for stream in probe.get("streams", []) if stream.get("codec_type") == "video"]
    if not streams:
        raise InputValidationError("O MP4 não contém um stream de vídeo.")

    stream = streams[0]
    try:
        duration = float(probe["format"]["duration"])
        width = int(stream["width"])
        height = int(stream["height"])
        codec_name = str(stream["codec_name"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InputValidationError("Não foi possível obter duração e resolução do MP4.") from exc

    if duration <= 0:
        raise InputValidationError("O vídeo deve ter duração maior que zero.")
    if width < MIN_INPUT_WIDTH or height < MIN_INPUT_HEIGHT:
        raise InputValidationError(
            f"A resolução mínima aceita é {MIN_INPUT_WIDTH}x{MIN_INPUT_HEIGHT}; encontrada {width}x{height}."
        )

    return VideoMetadata(
        resolved_path,
        duration,
        width,
        height,
        codec_name,
        os.path.getsize(resolved_path),
    )


def serialize_video_metadata(metadata: VideoMetadata) -> dict[str, object]:
    return {
        "duration_seconds": metadata.duration_seconds,
        "resolution": {"width": metadata.width, "height": metadata.height},
        "resolution_label": f"{metadata.width}x{metadata.height}",
        "codec": metadata.codec_name,
        "size_bytes": metadata.size_bytes,
    }


def validate_output_video(video_path: str) -> dict[str, object]:
    resolved_path = os.path.abspath(video_path)
    if not os.path.isfile(resolved_path) or os.path.getsize(resolved_path) == 0:
        raise RuntimeError(f"O vídeo final não existe ou está vazio: {resolved_path}")

    probe = _probe_video(resolved_path)
    try:
        duration = float(probe["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Não foi possível validar a duração do vídeo final: {resolved_path}") from exc
    if not duration > 0:
        raise RuntimeError(f"A duração do vídeo final deve ser maior que zero: {resolved_path}")

    return {
        "duration_seconds": duration,
        "size_bytes": os.path.getsize(resolved_path),
    }


def validate_vertical_output(video_path: str) -> dict[str, object]:
    resolved_path = os.path.abspath(video_path)
    if not os.path.isfile(resolved_path) or os.path.getsize(resolved_path) == 0:
        raise RuntimeError(f"O vídeo vertical não existe ou está vazio: {resolved_path}")

    probe = _probe_video(resolved_path)
    video_streams = [
        stream for stream in probe.get("streams", [])
        if stream.get("codec_type") == "video"
    ]
    audio_streams = [
        stream for stream in probe.get("streams", [])
        if stream.get("codec_type") == "audio"
    ]
    if not video_streams or not audio_streams:
        raise RuntimeError("O vídeo vertical deve conter streams de vídeo e áudio.")

    video_stream = video_streams[0]
    audio_stream = audio_streams[0]
    try:
        duration = float(probe["format"]["duration"])
        width = int(video_stream["width"])
        height = int(video_stream["height"])
        video_codec = str(video_stream["codec_name"])
        audio_codec = str(audio_stream["codec_name"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Metadata incompleta no vídeo vertical final.") from exc

    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("A duração do vídeo vertical deve ser finita e maior que zero.")
    if (width, height) != (1080, 1920):
        raise RuntimeError(f"O vídeo vertical deve ter 1080x1920; encontrado {width}x{height}.")
    if video_codec not in {"h264", "avc1"}:
        raise RuntimeError(f"O vídeo vertical deve usar H.264; encontrado {video_codec}.")
    if audio_codec != "aac":
        raise RuntimeError(f"O áudio final deve usar AAC; encontrado {audio_codec}.")

    return {
        "duration_seconds": duration,
        "resolution": {"width": width, "height": height},
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "size_bytes": os.path.getsize(resolved_path),
    }


def validate_dependencies() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise RuntimeError("Dependências não encontradas no PATH: " + ", ".join(missing))


def normalize_output_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", normalized).strip("-").lower()
    if not normalized:
        raise InputValidationError("O nome da edição deve conter pelo menos uma letra ou número.")
    return normalized


def build_output_stem(name: str, part_number: Optional[int] = None) -> str:
    stem = normalize_output_name(name)
    if part_number is not None:
        stem = f"{stem}-parte-{part_number:02d}"
    return stem
