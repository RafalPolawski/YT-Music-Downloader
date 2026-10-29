"""Runtime paths (from environment variables) and user settings (persisted as JSON)."""

from __future__ import annotations

import json
import logging
import os
import shlex
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

log = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Environment:
    """Settings that only make sense at the process/container level."""

    config_dir: Path
    music_dir: Path
    host: str
    port: int
    web_user: str
    web_password: str
    log_level: str

    @classmethod
    def from_env(cls) -> "Environment":
        return cls(
            config_dir=Path(os.environ.get("YTMD_CONFIG_DIR", "/config")).expanduser(),
            music_dir=Path(os.environ.get("YTMD_MUSIC_DIR", "/music")).expanduser(),
            host=os.environ.get("YTMD_HOST", "0.0.0.0"),
            port=int(os.environ.get("YTMD_PORT", "8080")),
            web_user=os.environ.get("YTMD_WEB_USER", "admin"),
            web_password=os.environ.get("YTMD_WEB_PASSWORD", ""),
            log_level=os.environ.get("YTMD_LOG_LEVEL", "INFO").upper(),
        )

    # --- files inside the config dir -------------------------------------------------
    @property
    def settings_file(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def auth_file(self) -> Path:
        return self.config_dir / "browser.json"

    @property
    def oauth_file(self) -> Path:
        return self.config_dir / "oauth.json"

    @property
    def oauth_client_file(self) -> Path:
        return self.config_dir / "oauth_client.json"

    @property
    def cookies_file(self) -> Path:
        """Optional Netscape cookies.txt uploaded by the user (used by yt-dlp only)."""
        return self.config_dir / "cookies.txt"

    @property
    def db_file(self) -> Path:
        return self.config_dir / "state.db"

    @property
    def log_dir(self) -> Path:
        return self.config_dir / "logs"

    # --- folders inside the music dir ------------------------------------------------
    @property
    def trash_dir(self) -> Path:
        return self.music_dir / ".ytmd-trash"

    @property
    def tmp_dir(self) -> Path:
        return self.music_dir / ".ytmd-tmp"

    def ensure_dirs(self) -> None:
        for d in (self.config_dir, self.log_dir, self.music_dir):
            d.mkdir(parents=True, exist_ok=True)


# Options that may be passed to yt-dlp from the UI. Anything able to run commands or write
# files elsewhere (--exec, --output, --plugin-dirs, ...) is deliberately not on this list.
ALLOWED_YTDLP_OPTIONS = {
    "--extractor-args", "--extractor-retries", "--limit-rate", "-r", "--throttled-rate", "--proxy",
    "--source-address", "--force-ipv4", "-4", "--force-ipv6", "-6", "--geo-bypass", "--xff",
    "--geo-verification-proxy", "--sleep-requests", "--sleep-interval", "--min-sleep-interval",
    "--max-sleep-interval", "--retries", "-R", "--fragment-retries", "--retry-sleep", "--socket-timeout",
    "--http-chunk-size", "--concurrent-fragments", "-N", "--impersonate", "--user-agent", "--referer",
    "--add-headers", "--add-header", "--format", "-f", "--format-sort", "-S", "--audio-quality",
    "--no-check-certificates", "--prefer-insecure", "--legacy-server-connect", "--verbose", "-v",
    "--sponsorblock-api",
}


def validate_ytdlp_args(value: str) -> str:
    try:
        tokens = shlex.split(value or "")
    except ValueError as exc:
        raise ValueError(f"cannot parse arguments: {exc}") from exc
    for tok in tokens:
        if tok.startswith("-") and tok.split("=", 1)[0] not in ALLOWED_YTDLP_OPTIONS:
            raise ValueError(f"option {tok.split('=', 1)[0]} is not allowed here")
    return value


AudioFormat = Literal["opus", "m4a", "mp3"]
Layout = Literal["playlist", "artist_album", "artist", "flat"]


class Settings(BaseModel):
    """Everything the user can change from the web UI."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    # Sync
    sync_interval_minutes: int = Field(60, ge=5, le=7 * 24 * 60)
    auto_enable_new_playlists: bool = True

    # Downloads
    audio_format: AudioFormat = "opus"
    concurrent_downloads: int = Field(2, ge=1, le=8)
    download_delay_seconds: float = Field(3.0, ge=0, le=120)
    prefer_album_version: bool = True
    use_account_cookies: bool = False
    sponsorblock_remove_offtopic: bool = False
    max_attempts: int = Field(5, ge=1, le=50)

    # Library
    layout: Layout = "playlist"
    playlists_dir: str = "Playlists"
    write_lyrics: bool = True
    write_album_covers: bool = True
    cover_size: int = Field(1200, ge=300, le=3000)

    # Removal & trash
    trash_retention_days: int = Field(30, ge=0, le=3650)
    removal_guard_percent: int = Field(50, ge=1, le=100)
    removal_guard_min_tracks: int = Field(10, ge=0, le=100000)

    # Integrations
    jellyfin_url: str = ""
    jellyfin_api_key: str = ""

    # Advanced
    ytdlp_auto_update: bool = True
    ytdlp_extra_args: str = ""
    brand_account_id: str = ""
    log_retention_days: int = Field(0, ge=0, le=3650)  # 0 keeps log files forever

    @field_validator("ytdlp_extra_args")
    @classmethod
    def _check_ytdlp_args(cls, value: str) -> str:
        return validate_ytdlp_args(value)


class SettingsStore:
    """Thread-safe load/save of :class:`Settings` to a JSON file."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._settings = self._load()

    def _load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return Settings.model_validate(data)
        except (OSError, ValueError, ValidationError) as exc:
            log.error("Could not read %s (%s) - using defaults", self.path, exc)
            return Settings()

    def get(self) -> Settings:
        with self._lock:
            return self._settings.model_copy()

    def update(self, changes: dict) -> Settings:
        with self._lock:
            merged = self._settings.model_dump() | changes
            new = Settings.model_validate(merged)
            self._write(new)
            self._settings = new
            return new.model_copy()

    def save(self) -> None:
        with self._lock:
            self._write(self._settings)

    def _write(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(settings.model_dump(), indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
