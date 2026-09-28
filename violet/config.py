from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


APP_NAME = "Violet"
# A local benchmark on this workstation selected Turbo for the quickest
# short-dictation turnaround. It remains a plain string so a future settings
# screen can switch models without changing the app architecture.
DEFAULT_MODEL = "large-v3-turbo"
MAX_CPU_THREADS = 32


@dataclass(slots=True)
class Settings:
    model_id: str = DEFAULT_MODEL
    language: str = "en"
    cpu_threads: int = 8
    input_device: int | None = None


def app_data_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def model_cache_dir() -> Path:
    path = app_data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return app_data_dir() / "config.json"


def settings_exist() -> bool:
    """Whether this user already has a persisted Violet choice."""

    return settings_path().is_file()


def load_settings() -> Settings:
    """Load only known settings; never write recordings or transcripts."""
    path = settings_path()
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Settings()
    if not isinstance(raw, dict):
        return Settings()
    # Settings are local input too. Strictly constrain them so a corrupted or
    # hand-edited JSON file cannot point the model runtime at an arbitrary Hub
    # identifier or consume an unbounded number of CPU threads.
    settings = Settings()
    candidate_model = raw.get("model_id")
    if isinstance(candidate_model, str):
        try:
            from .models import model_spec

            model_spec(candidate_model)
            settings.model_id = candidate_model
        except (ImportError, ValueError):
            pass
    candidate_threads = raw.get("cpu_threads")
    if isinstance(candidate_threads, int) and not isinstance(candidate_threads, bool):
        settings.cpu_threads = min(MAX_CPU_THREADS, max(1, candidate_threads))
    # Current public model policy is English-only. Keep this setting narrow
    # until a future multilingual policy explicitly allowlists a model.
    if raw.get("language") == "en":
        settings.language = "en"
    candidate_device = raw.get("input_device")
    if isinstance(candidate_device, int) and not isinstance(candidate_device, bool) and candidate_device >= 0:
        settings.input_device = candidate_device
    return settings


def save_settings(settings: Settings) -> None:
    path = settings_path()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    temporary.replace(path)
