import os
from pathlib import Path

import cv2
import numpy as np
import pytest

import face_tracker


def _write_test_video(path: str, width: int = 640, height: int = 360, frame_count: int = 30, fps: float = 10.0) -> None:
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(path, fourcc, fps, (width, height))
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    for _ in range(frame_count):
        writer.write(frame)
    writer.release()


def test_detect_face_crop_x_missing_video_raises(tmp_path: Path) -> None:
    missing_path = str(tmp_path / "missing.mp4")

    with pytest.raises(FileNotFoundError):
        face_tracker.detect_face_crop_x(missing_path)


def test_detect_face_crop_x_falls_back_to_center_without_faces(tmp_path: Path) -> None:
    video_path = str(tmp_path / "no_face.mp4")
    _write_test_video(video_path)

    crop_x = face_tracker.detect_face_crop_x(video_path)

    input_width, input_height = 640, 360
    crop_width = face_tracker._compute_vertical_crop_width(input_width, input_height)
    expected_center = face_tracker._compute_center_crop_x(input_width, crop_width)
    assert crop_x == expected_center


def test_detect_face_crop_x_uses_detected_face_position(tmp_path: Path, monkeypatch) -> None:
    video_path = str(tmp_path / "with_face.mp4")
    _write_test_video(video_path)

    class FakeDetector:
        def detect(self, frame):
            return None, np.array([[100.0, 50.0, 80.0, 80.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.95]])

    monkeypatch.setattr(face_tracker, "_create_face_detector", lambda width, height: FakeDetector())

    crop_x = face_tracker.detect_face_crop_x(video_path)

    input_width, input_height = 640, 360
    crop_width = face_tracker._compute_vertical_crop_width(input_width, input_height)
    expected = face_tracker._clamp_crop_x(100.0 + 80.0 / 2.0 - crop_width / 2.0, input_width, crop_width)
    assert crop_x == expected


def test_get_yunet_model_path_missing_file_raises(monkeypatch) -> None:
    monkeypatch.setenv("TAPA_NA_LATA_YUNET_MODEL", "does-not-exist.onnx")

    with pytest.raises(RuntimeError, match="YuNet model file not found"):
        face_tracker._get_yunet_model_path()


def test_create_face_detector_missing_model_raises(monkeypatch) -> None:
    monkeypatch.setenv("TAPA_NA_LATA_YUNET_MODEL", "does-not-exist.onnx")

    with pytest.raises(RuntimeError, match="YuNet model file not found"):
        face_tracker._create_face_detector(640, 360)


def test_default_model_path_exists() -> None:
    assert os.path.isfile(face_tracker.DEFAULT_YUNET_MODEL_PATH)
