"""Runs yt-dlp in a subprocess (so it can be updated and cancelled at runtime)."""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

PROGRESS_PREFIX = "[ytmd-progress]"
FILE_PREFIX = "[ytmd-file]"
TIMEOUT_SECONDS = 20 * 60

FORMATS = {
    # target -> (format selector, extra postprocessor args)
    "opus": ("bestaudio[acodec=opus]/bestaudio/best", ["--audio-format", "opus"]),
    "m4a": ("bestaudio[ext=m4a]/bestaudio/best", ["--audio-format", "m4a"]),
    "mp3": ("bestaudio/best", ["--audio-format", "mp3", "--audio-quality", "0"]),
}

# Error kinds
RATE_LIMITED = "rate_limited"
UNAVAILABLE = "unavailable"
NEEDS_LOGIN = "needs_login"
CANCELLED = "cancelled"
TRANSIENT = "transient"

_PATTERNS = [
    (RATE_LIMITED, re.compile(
        r"http error 429|too many requests|confirm you[’']?re not a bot|rate.?limit|"
        r"try again later|your ip is likely being blocked", re.I)),
    (NEEDS_LOGIN, re.compile(
        r"confirm your age|age.?restricted|inappropriate for some users|premium members|"
        r"members.?only|join this channel|sign in to view|requires payment", re.I)),
    (UNAVAILABLE, re.compile(
        r"video unavailable|private video|has been removed|no longer available|not available in your country|"
        r"account associated with this video has been terminated|copyright claim|"
        r"this video is not available|blocked it in your country", re.I)),
]


class DownloadError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def classify_error(text: str) -> str:
    for kind, pattern in _PATTERNS:
        if pattern.search(text or ""):
            return kind
    return TRANSIENT


def _last_error_line(stderr: str) -> str:
    lines = [l.strip() for l in (stderr or "").splitlines() if l.strip()]
    errors = [l for l in lines if l.startswith("ERROR:")]
    msg = (errors or lines or ["yt-dlp failed without output"])[-1]
    msg = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", msg)
    return msg.removeprefix("ERROR:").strip()[:500]


@dataclass
class Progress:
    downloaded: int = 0
    total: int = 0
    speed: float = 0.0

    @property
    def percent(self) -> float:
        return round(100 * self.downloaded / self.total, 1) if self.total else 0.0


def _parse_progress(line: str) -> Progress | None:
    parts = line[len(PROGRESS_PREFIX):].strip().split("/")
    if len(parts) != 4:
        return None

    def num(v: str) -> float:
        try:
            return float(v)
        except ValueError:
            return 0.0

    downloaded, total, estimate, speed = (num(p) for p in parts)
    return Progress(int(downloaded), int(total or estimate), speed)


def ytdlp_command() -> list[str]:
    # -P: never import modules from the working directory; -X utf8: stable output encoding
    return [sys.executable, "-P", "-X", "utf8", "-m", "yt_dlp"]


def ytdlp_version() -> str | None:
    try:
        out = subprocess.run([*ytdlp_command(), "--version"], capture_output=True, text=True, timeout=60)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


@dataclass
class DownloadOptions:
    audio_format: str = "opus"
    cookies_text: str | None = None
    sponsorblock: bool = False
    extra_args: str = ""


def build_args(url: str, out_dir: Path, opts: DownloadOptions, cookie_file: Path | None) -> list[str]:
    fmt, pp_args = FORMATS[opts.audio_format]
    args = [
        *ytdlp_command(),
        "--ignore-config",
        "--no-playlist",
        "--format", fmt,
        "--extract-audio", *pp_args,
        "--output", str(out_dir / "%(id)s.%(ext)s"),
        "--no-mtime",
        "--no-overwrites",
        "--retries", "5",
        "--fragment-retries", "5",
        "--socket-timeout", "30",
        "--sleep-requests", "1",
        "--newline", "--progress",
        "--progress-template",
        f"download:{PROGRESS_PREFIX} %(progress.downloaded_bytes)s/%(progress.total_bytes)s/"
        f"%(progress.total_bytes_estimate)s/%(progress.speed)s",
        "--print", f"after_move:{FILE_PREFIX} %(filepath)s",
    ]
    if cookie_file:
        args += ["--cookies", str(cookie_file)]
    if opts.sponsorblock:
        args += ["--sponsorblock-remove", "music_offtopic"]
    if opts.extra_args.strip():
        args += shlex.split(opts.extra_args)
    args.append(url)
    return args


def download_audio(
    video_id: str,
    work_dir: Path,
    opts: DownloadOptions,
    on_progress: Callable[[Progress], None] | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Download one track as audio into ``work_dir``. Returns the produced file."""
    work_dir.mkdir(parents=True, exist_ok=True)
    urls = [f"https://music.youtube.com/watch?v={video_id}", f"https://www.youtube.com/watch?v={video_id}"]
    last: DownloadError | None = None
    for url in urls:
        try:
            return _run_once(url, work_dir, opts, on_progress, cancel)
        except DownloadError as exc:
            last = exc
            if exc.kind in (RATE_LIMITED, CANCELLED, UNAVAILABLE):
                break
            log.debug("Download via %s failed (%s), trying fallback", url, exc)
    assert last is not None
    raise last


def _run_once(url, work_dir, opts, on_progress, cancel) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    cookie_file = None
    if opts.cookies_text:
        # yt-dlp writes the cookie jar back on exit, so every process gets its own copy.
        fd, name = tempfile.mkstemp(prefix="cookies-", suffix=".txt", dir=work_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(opts.cookies_text)
        cookie_file = Path(name)

    args = build_args(url, work_dir, opts, cookie_file)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    log.debug("Running: %s", " ".join(shlex.quote(a) for a in args))
    try:
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", env=env, cwd=work_dir,
        )
    except OSError as exc:
        raise DownloadError(TRANSIENT, f"Could not start yt-dlp: {exc}") from exc

    stderr_chunks: list[str] = []
    reader = threading.Thread(target=lambda: stderr_chunks.append(proc.stderr.read()), daemon=True)
    reader.start()
    killer_done = threading.Event()
    timed_out = threading.Event()

    def watchdog():
        started = time.monotonic()
        while not killer_done.wait(1.0):
            if time.monotonic() - started > TIMEOUT_SECONDS:
                timed_out.set()
            if (cancel is not None and cancel.is_set()) or timed_out.is_set():
                proc.kill()
                return

    threading.Thread(target=watchdog, daemon=True).start()

    produced: Path | None = None
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            if line.startswith(PROGRESS_PREFIX):
                p = _parse_progress(line)
                if p and on_progress:
                    on_progress(p)
            elif line.startswith(FILE_PREFIX):
                produced = Path(line[len(FILE_PREFIX):].strip())
        proc.wait()
    finally:
        killer_done.set()
        reader.join(timeout=5)
        if cookie_file:
            cookie_file.unlink(missing_ok=True)

    if cancel is not None and cancel.is_set():
        raise DownloadError(CANCELLED, "Cancelled")
    if timed_out.is_set():
        raise DownloadError(TRANSIENT, f"yt-dlp did not finish within {TIMEOUT_SECONDS // 60} minutes")
    stderr = "".join(stderr_chunks)
    if proc.returncode != 0 or produced is None or not produced.exists():
        message = _last_error_line(stderr)
        kind = classify_error(message)
        if kind == TRANSIENT and classify_error(stderr) == RATE_LIMITED:
            kind = RATE_LIMITED
        raise DownloadError(kind, message)
    return produced


def clean_work_dir(work_dir: Path) -> None:
    shutil.rmtree(work_dir, ignore_errors=True)
