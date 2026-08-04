import argparse
import logging
import os
import shutil
from typing import Dict, Optional


LOGGER = logging.getLogger(__name__)


PROJECT_FOLDERS = [
    os.path.join("Videos", "A-Roll"),
    os.path.join("Videos", "B-Roll"),
    os.path.join("Audio", "Trilha"),
    os.path.join("Efeitos", "SFX"),
    "Output",
    "Legendas",
]


def configure_logging() -> None:
    if logging.getLogger().handlers:
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def validate_input_file(file_path: str, file_label: str) -> str:
    normalized_path = os.path.abspath(file_path)

    if not os.path.isfile(normalized_path):
        raise FileNotFoundError(f"{file_label} not found: {normalized_path}")

    return normalized_path


def ensure_project_structure(project_root: str) -> Dict[str, str]:
    created_paths: Dict[str, str] = {}

    for relative_folder in PROJECT_FOLDERS:
        absolute_folder = os.path.join(project_root, relative_folder)
        os.makedirs(absolute_folder, exist_ok=True)
        created_paths[relative_folder] = absolute_folder
        LOGGER.info("Ensured folder exists: %s", absolute_folder)

    return created_paths


def copy_with_overwrite(source_path: str, destination_folder: str) -> str:
    os.makedirs(destination_folder, exist_ok=True)

    destination_path = os.path.join(destination_folder, os.path.basename(source_path))
    shutil.copy2(source_path, destination_path)

    LOGGER.info("Copied file from %s to %s", source_path, destination_path)
    return destination_path


def setup_manager(
    raw_video_path: str,
    subtitle_path: str,
    project_root: Optional[str] = None,
) -> Dict[str, str]:
    configure_logging()

    resolved_project_root = os.path.abspath(project_root or os.getcwd())
    LOGGER.info("Starting setup in project root: %s", resolved_project_root)

    validated_video_path = validate_input_file(raw_video_path, "Raw video")
    validated_subtitle_path = validate_input_file(subtitle_path, "Subtitle SRT")

    folders = ensure_project_structure(resolved_project_root)

    video_backup_path = copy_with_overwrite(
        validated_video_path,
        folders[os.path.join("Videos", "A-Roll")],
    )
    subtitle_backup_path = copy_with_overwrite(
        validated_subtitle_path,
        folders["Legendas"],
    )

    if not os.path.isfile(video_backup_path):
        raise RuntimeError(f"Video backup was not created: {video_backup_path}")

    if not os.path.isfile(subtitle_backup_path):
        raise RuntimeError(f"Subtitle backup was not created: {subtitle_backup_path}")

    LOGGER.info("Setup completed successfully")

    return {
        "project_root": resolved_project_root,
        "video_backup_path": video_backup_path,
        "subtitle_backup_path": subtitle_backup_path,
        "video_path": validated_video_path,
        "subtitle_path": validated_subtitle_path,
    }


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare the project folders and back up the raw video and subtitle SRT.",
    )
    parser.add_argument("video", help="Path to the raw video file")
    parser.add_argument("srt", help="Path to the subtitle SRT file")
    parser.add_argument(
        "--project-root",
        default=None,
        help="Project root where the folder structure will be created. Defaults to the current working directory.",
    )
    return parser


def main() -> int:
    configure_logging()

    parser = build_argument_parser()
    args = parser.parse_args()

    try:
        setup_manager(args.video, args.srt, args.project_root)
    except Exception as exc:
        LOGGER.exception("Setup failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())