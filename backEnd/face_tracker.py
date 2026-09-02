import argparse
import logging
import os
from pathlib import Path
from typing import List

import cv2


LOGGER = logging.getLogger(__name__)


def configure_logging() -> None:
    if logging.getLogger().handlers:
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _compute_vertical_crop_width(input_width: int, input_height: int) -> int:
    target_width = int(round(input_height * 9 / 16))
    return max(1, min(target_width, input_width))


def _compute_center_crop_x(input_width: int, crop_width: int) -> int:
    return max(0, int(round(input_width / 2 - crop_width / 2)))


def _clamp_crop_x(crop_x: float, input_width: int, crop_width: int) -> int:
    max_x = max(0, input_width - crop_width)
    return max(0, min(int(round(crop_x)), max_x))


DEFAULT_YUNET_MODEL_PATH = str(
    Path(__file__).resolve().parent
    / "assets"
    / "models"
    / "face_detection_yunet_2023mar.onnx"
)
YUNET_SCORE_THRESHOLD = 0.6


def _get_yunet_model_path() -> str:
    # Permite apontar para um modelo alternativo sem alterar código.
    configured = os.getenv("TAPA_NA_LATA_YUNET_MODEL")
    model_path = configured if configured else DEFAULT_YUNET_MODEL_PATH
    if not os.path.isfile(model_path):
        raise RuntimeError(f"YuNet model file not found: {model_path}")
    return model_path


def _create_face_detector(input_width: int, input_height: int):
    detector_ctor = getattr(cv2, "FaceDetectorYN", None)
    if detector_ctor is None or not hasattr(detector_ctor, "create"):
        raise RuntimeError("OpenCV FaceDetectorYN (YuNet) is unavailable in this environment")

    model_path = _get_yunet_model_path()
    return detector_ctor.create(
        model_path,
        "",
        (input_width, input_height),
        score_threshold=YUNET_SCORE_THRESHOLD,
    )


def detect_face_crop_x(video_path: str) -> int:
    resolved_video_path = os.path.abspath(video_path)
    if not os.path.isfile(resolved_video_path):
        raise FileNotFoundError(f"Video not found: {resolved_video_path}")

    capture = cv2.VideoCapture(resolved_video_path)
    if not capture.isOpened():
        raise RuntimeError(f"Unable to open video: {resolved_video_path}")

    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            raise RuntimeError("Invalid FPS detected in video metadata")

        input_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        input_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if input_width <= 0 or input_height <= 0:
            raise RuntimeError("Invalid video dimensions detected")

        crop_width = _compute_vertical_crop_width(input_width, input_height)
        center_fallback_x = _compute_center_crop_x(input_width, crop_width)

        face_detector = _create_face_detector(input_width, input_height)

        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        duration_seconds = frame_count / fps if frame_count > 0 else 5.0
        sample_count = min(10, max(3, int(duration_seconds)))
        sample_seconds = [
            duration_seconds * index / max(1, sample_count - 1)
            for index in range(sample_count)
        ]
        if not sample_seconds:
            sample_seconds = [0]

        face_centers_x: List[float] = []

        for second in sample_seconds:
            frame_index = int(round(second * fps))
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            has_frame, frame = capture.read()
            if not has_frame:
                continue

            _, faces = face_detector.detect(frame)
            if faces is None or len(faces) == 0:
                continue

            # Colunas 0-3 são x, y, w, h; usa o rosto de maior área quando há vários na amostra.
            x, _, w, _ = max(faces, key=lambda face: face[2] * face[3])[:4]
            face_centers_x.append(float(x) + float(w) / 2.0)

        if not face_centers_x:
            return center_fallback_x

        average_face_center_x = sum(face_centers_x) / len(face_centers_x)
        target_crop_x = average_face_center_x - (crop_width / 2.0)
        resolved_crop_x = _clamp_crop_x(target_crop_x, input_width, crop_width)

        LOGGER.info(
            "Detected faces in %d sampled frames; crop_x=%d",
            len(face_centers_x),
            resolved_crop_x,
        )
        return resolved_crop_x
    finally:
        capture.release()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect face position and return crop X for a 9:16 vertical crop.",
    )
    parser.add_argument("video", help="Path to input video")
    return parser


def main() -> int:
    configure_logging()

    parser = build_argument_parser()
    args = parser.parse_args()

    try:
        crop_x = detect_face_crop_x(args.video)
        print(crop_x)
    except Exception as exc:
        LOGGER.exception("Face tracking failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())