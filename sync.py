"""
YT-Music-Downloader - A professional, high-fidelity YouTube Music library mirror.
Features:
- Smart collision-resistant naming.
- Real-time mirroring (moves server-deleted tracks to .deleted/).
- Full metadata & optimized cover embedding (Ogg Opus + JPEG 800x800).
- Data integrity checks to auto-repair corrupted downloads.
- Modern, multi-threaded UI with Rich.
- VBR best-quality audio with ytmusicapi thumbnail fetch (no cover misses).
- Automatic fallback from music.youtube.com → youtube.com on failure.
"""

import os
import json
import threading
import logging
import concurrent.futures
import base64
import io
import re
import subprocess
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

# External Dependencies
import yt_dlp
from ytmusicapi import YTMusic
from rich.console import Console
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    MofNCompleteColumn,
    SpinnerColumn,
)
from rich.panel import Panel
from rich.logging import RichHandler
from mutagen.oggopus import OggOpus
from mutagen.flac import Picture
from PIL import Image

# --- Constants ---
CONFIG_FILE = "config.json"
LOG_FILE = "sync.log"

# --- Logger Setup ---
def setup_logging():
    """Configures a bifurcated logging system: Rich for console, standard for file."""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    console = Console()

    console_handler = RichHandler(
        console=console,
        rich_tracebacks=True,
        markup=True,
        show_time=True,
        log_time_format="%H:%M:%S",
    )
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(console_handler)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)-7.7s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    return console, logging.getLogger("ytm_sync")


console, logger = setup_logging()


class YDLLogger:
    """Redirects yt-dlp internal messages to our application logger."""

    def debug(self, msg):
        if not msg.startswith("[debug] "):
            logger.debug(msg)

    def info(self, msg):
        logger.debug(msg)

    def warning(self, msg):
        logger.debug(msg)

    def error(self, msg):
        logger.debug(msg)


class Config:
    """Manages application settings with sensible defaults."""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.file_path):
            with open(self.file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "auth_file": "headers.json",
            "download_dir": "downloads",
            "max_workers": 3,
            "mirror_mode": True,
            "audio_quality": "0",
            "yt_dlp": {
                "audio_format": "opus",
                "auto_update": True,
                "cookies_from_browser": None,
            },
        }

    def get(self, key, default=None):
        return self.data.get(key, default)


class Auth:
    """Handles connection to the YT Music API."""

    def __init__(self, auth_file: str):
        self.auth_file = auth_file
        self.yt = None

    def initialize(self) -> bool:
        if not os.path.exists(self.auth_file):
            logger.error(f"[bold red]{self.auth_file} not found![/bold red]")
            logger.info("Run [bold cyan]'python setup_auth.py'[/bold cyan] first.")
            return False
        try:
            self.yt = YTMusic(self.auth_file)
            return True
        except Exception as e:
            logger.error(f"YT Music Auth Failure: {e}")
            return False


class Processor:
    """Handles file integrity, metadata, and thumbnail processing."""

    @staticmethod
    def is_file_valid(file_path: str) -> bool:
        """Verifies if an Ogg Opus file is readable and not corrupted."""
        if not os.path.exists(file_path) or os.path.getsize(file_path) < 1024:
            return False
        try:
            OggOpus(file_path)
            return True
        except Exception:
            return False

    @staticmethod
    def has_thumbnail(file_path: str) -> bool:
        """Returns True if the Ogg Opus file has an embedded cover image."""
        try:
            audio = OggOpus(file_path)
            return bool(audio.get("metadata_block_picture"))
        except Exception:
            return False

    @staticmethod
    def set_metadata(file_path: str, tags: dict):
        """Applies audio tags to the file."""
        if not os.path.exists(file_path):
            return
        try:
            audio = OggOpus(file_path)
            for key, value in tags.items():
                if value:
                    audio[key] = str(value)
            audio.save()
        except Exception as e:
            logger.debug(f"Metadata write error: {e}")

    def optimize_image(self, img_data: bytes) -> Optional[bytes]:
        """Resizes and compresses image to 800x800 JPEG."""
        try:
            with Image.open(io.BytesIO(img_data)) as img:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img.thumbnail((800, 800), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85, optimize=True)
                return buf.getvalue()
        except Exception as e:
            logger.debug(f"Image optimization error: {e}")
            return None

    def embed_thumbnail(self, audio_path: str, img_data: bytes) -> bool:
        """Optimizes and embeds thumbnail bytes into Ogg Opus file."""
        if not os.path.exists(audio_path):
            return False

        optimized = self.optimize_image(img_data)
        if not optimized:
            return False

        try:
            pic = Picture()
            pic.data = optimized
            pic.type = 3  # Front Cover
            pic.mime = "image/jpeg"

            encoded = base64.b64encode(pic.write()).decode("ascii")

            audio = OggOpus(audio_path)
            audio["metadata_block_picture"] = [encoded]
            audio.save()
            return True
        except Exception as e:
            logger.debug(f"Thumbnail embed error: {e}")
            return False

    def fetch_thumbnail_from_api(self, thumbnails: list) -> Optional[bytes]:
        """Downloads the highest quality thumbnail from YTMusic API data.

        Modifies thumbnail URL to request 800x800 instead of the default small size.
        """
        if not thumbnails:
            return None
        # Pick thumbnail with the highest declared resolution
        best = max(thumbnails, key=lambda t: t.get("width", 0) * t.get("height", 0))
        url = best.get("url", "")
        if not url:
            return None
        # YTMusic thumbnails use Google image URLs like:
        #   https://lh3.googleusercontent.com/...=w226-h226-l90-rj
        # Replace size parameters to request 800x800
        url = re.sub(r"=w\d+-h\d+", "=w800-h800", url)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read()
        except Exception as e:
            logger.debug(f"API thumbnail download error: {e}")
            return None

    @staticmethod
    def read_thumbnail_file(file_path: str) -> Optional[bytes]:
        """Reads thumbnail bytes from a local file (yt-dlp fallback)."""
        try:
            with open(file_path, "rb") as f:
                return f.read()
        except Exception:
            return None


class Syncer:
    """Manages naming, mirror logic, and directory health."""

    def __init__(self, config: Config):
        self.config = config

    def get_safe_filename(self, text: str) -> str:
        """Converts strings to Windows-safe filenames using Unicode equivalents."""
        mapping = {
            ":": "：", "*": "＊", "?": "？", '"': "＂",
            "<": "＜", ">": "＞", "|": "｜", "/": "／", "\\": "＼",
        }
        safe = "".join(mapping.get(c, c) for c in text).strip().rstrip(".")
        return safe[:150] or f"Track_{hash(text) % 10000}"

    def get_expected_filenames(self, tracks: List[dict]) -> Dict[str, str]:
        """Generates unique, deterministic filenames to prevent collisions."""
        seen: Dict[str, str] = {}
        mapping: Dict[str, str] = {}

        for t in tracks:
            vid = t.get("videoId")
            if not vid or vid in mapping:
                continue

            title = t.get("title", "Unknown")
            artist = ", ".join([a["name"] for a in t.get("artists", [])])
            duration = t.get("duration", "00")

            candidates = [
                self.get_safe_filename(title),
                self.get_safe_filename(f"{title} ({artist})"),
                self.get_safe_filename(f"{title} ({artist}, {duration})"),
            ]

            found = False
            for c in candidates:
                if c.lower() not in seen:
                    seen[c.lower()] = vid
                    mapping[vid] = c
                    found = True
                    break

            if not found:
                idx = 2
                while True:
                    c = self.get_safe_filename(f"{title} ({artist}, {duration}) [{idx}]")
                    if c.lower() not in seen:
                        seen[c.lower()] = vid
                        mapping[vid] = c
                        break
                    idx += 1

        return mapping

    def run_mirroring(self, playlist_title: str, tracks: List[dict]):
        """Moves orphaned local files (not in remote playlist) to .deleted/."""
        if not self.config.get("mirror_mode", True):
            return

        p_dir = (
            Path(self.config.get("download_dir", "downloads"))
            / self.get_safe_filename(playlist_title)
        )
        if not p_dir.exists():
            return

        expected_map = self.get_expected_filenames(tracks)
        remote_stems = {s.lower() for s in expected_map.values()}
        ext = f".{self.config.get('yt_dlp', {}).get('audio_format', 'opus')}"

        local_files = [f for f in p_dir.iterdir() if f.is_file() and f.suffix.lower() == ext]
        deleted_dir = p_dir / ".deleted"

        # 1. Restore from .deleted if re-added to playlist
        if deleted_dir.exists():
            for df in deleted_dir.iterdir():
                if df.stem.lower() in remote_stems:
                    try:
                        df.rename(p_dir / df.name)
                        logger.info(f"Auto-Heal: Restored '{df.name}' to {playlist_title}")
                    except Exception:
                        pass

        # 2. Move to .deleted if removed from server
        to_move = [f for f in local_files if f.stem.lower() not in remote_stems]

        # Safety: don't wipe folder if API returns empty/incomplete data
        if len(to_move) > len(local_files) * 0.5 and len(local_files) > 5:
            logger.warning(
                f"Mirroring safety: Aborted mass-move in '{playlist_title}'. Check internet/API."
            )
            return

        for f in to_move:
            deleted_dir.mkdir(exist_ok=True)
            try:
                f.rename(deleted_dir / f.name)
                logger.warning(
                    f"Mirroring: Moved '{f.name}' to .deleted/ (playlist: {playlist_title})"
                )
            except Exception as e:
                logger.debug(f"Move failed: {e}")


class Downloader:
    """Core synchronization engine driving yt-dlp and track processing."""

    def __init__(self, config: Config, auth: Auth, syncer: Syncer):
        self.config = config
        self.auth = auth
        self.syncer = syncer
        self.processor = Processor()
        self.progress = None
        self.overall_task = None
        self.stats_lock = threading.Lock()
        self.issues: List[str] = []
        self.summaries: List[str] = []
        self._cookies_valid: Optional[bool] = None  # cached once at startup

    def validate_cookies(self) -> bool:
        """Checks once whether cookies.txt is a valid Netscape-format file."""
        if self._cookies_valid is not None:
            return self._cookies_valid

        path = "cookies.txt"
        if not os.path.exists(path):
            self._cookies_valid = False
            return False

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                first_line = f.readline()
            valid = "Netscape HTTP Cookie File" in first_line or "HTTP Cookie File" in first_line
            if not valid:
                logger.warning(
                    "cookies.txt is not in Netscape format – ignoring it. "
                    "Export via 'Get cookies.txt LOCALLY' browser extension."
                )
            self._cookies_valid = valid
            return valid
        except Exception:
            self._cookies_valid = False
            return False

    def cleanup_initial(self, p_dir: Path):
        """Full cleanup of leftover temp/image files at playlist start."""
        exts = ["*.temp", "*.part", "*.ytdl", "*.jpg", "*.webp", "*.png", "*.jpeg"]
        count = 0
        for pattern in exts:
            for f in p_dir.glob(pattern):
                try:
                    f.unlink()
                    count += 1
                except Exception:
                    pass
        if count > 0:
            logger.debug(f"Cleanup: Removed {count} leftover temp files in {p_dir.name}.")

    def cleanup_track(self, p_dir: Path, stem: str):
        """Removes only the temp/thumbnail files for a specific track stem."""
        IMAGE_EXTS = (".jpg", ".webp", ".png", ".jpeg")
        TEMP_EXTS = (".temp", ".part", ".ytdl")
        count = 0
        for ext in IMAGE_EXTS + TEMP_EXTS:
            f = p_dir / f"{stem}{ext}"
            if f.exists():
                try:
                    f.unlink()
                    count += 1
                except Exception:
                    pass
        # Also handle double-extension leftovers like "Song.webp.part"
        for f in p_dir.glob(f"{stem}.*.*"):
            try:
                f.unlink()
                count += 1
            except Exception:
                pass
        if count > 0:
            logger.debug(f"Cleanup: Removed {count} temp files for '{stem}'.")

    def _build_ydl_opts(self, out_dir: Path, stem: str, task_id, thumb_capture: list) -> dict:
        """Builds the yt-dlp options dict for a single track download."""
        ext = self.config.get("yt_dlp", {}).get("audio_format", "opus")
        quality = self.config.get("audio_quality", "0")

        def hook(d):
            status = d.get("status")
            if status == "finished":
                fname = d.get("filename", "")
                fext = Path(fname).suffix.lower()
                if fext in (".jpg", ".webp", ".png", ".jpeg"):
                    # Capture thumbnail path written by yt-dlp (fallback)
                    thumb_capture[0] = fname
                # Do NOT advance progress here – we advance once after all retries
            # 'error' status is also handled – no double-advance risk

        opts = {
            "format": "bestaudio[ext=webm]/bestaudio/best",
            "outtmpl": f"{out_dir}/{stem.replace('%', '%%')}.%(ext)s",
            "quiet": True,
            "no_warnings": True,
            "color": "no_color",
            "logger": YDLLogger(),
            "progress_hooks": [hook],
            "writethumbnail": True,      # kept as thumbnail fallback
            "socket_timeout": 30,
            "retries": 10,
            "sleep_requests": self.config.get("yt_dlp", {}).get("sleep_requests", 1), # Adds a small 1s delay to prevent IP bans
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": ext,
                    "preferredquality": quality,  # "0" = VBR best
                }
            ],
        }

        if self.validate_cookies():
            opts["cookiefile"] = "cookies.txt"

        return opts

    def sync_playlist(self, playlist: dict):
        """Synchronizes a single playlist with the local filesystem."""
        p_title = playlist["title"]
        p_id = playlist["playlistId"]

        out_dir = (
            Path(self.config.get("download_dir", "downloads"))
            / self.syncer.get_safe_filename(p_title)
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        self.cleanup_initial(out_dir)

        try:
            full_data = self.auth.yt.get_playlist(p_id, limit=None)
            tracks = full_data["tracks"]
        except Exception as e:
            logger.error(f"Sync error for '{p_title}': {e}")
            self.progress.update(self.overall_task, advance=1)
            return

        task_id = self.progress.add_task(f"[cyan]{p_title}", total=len(tracks))
        expected_names = self.syncer.get_expected_filenames(tracks)

        counts = {"new": 0, "skip": 0, "fixed": 0, "fail": 0}
        processed_ids: set = set()

        for t in tracks:
            vid = t.get("videoId")
            if not vid or vid in processed_ids:
                if vid:
                    self.progress.update(task_id, advance=1)
                continue
            processed_ids.add(vid)

            stem = expected_names.get(vid)
            ext = self.config.get("yt_dlp", {}).get("audio_format", "opus")
            f_path = out_dir / f"{stem}.{ext}"

            # 1. File already exists – check health and cover
            if f_path.exists():
                if self.processor.is_file_valid(str(f_path)):
                    # Repair missing cover without re-downloading audio
                    if not self.processor.has_thumbnail(str(f_path)):
                        thumbnails = t.get("thumbnails", [])
                        thumb_data = self.processor.fetch_thumbnail_from_api(thumbnails)
                        if thumb_data and self.processor.embed_thumbnail(str(f_path), thumb_data):
                            logger.info(f"Cover fixed: '{stem}'")
                            counts["fixed"] += 1
                        else:
                            logger.debug(f"Could not fix cover for '{stem}'")
                    else:
                        counts["skip"] += 1
                    self.progress.update(task_id, advance=1)
                    continue
                else:
                    logger.warning(f"Corrupt file detected: '{stem}'. Re-downloading...")
                    try:
                        f_path.unlink()
                    except Exception:
                        pass

            # 2. Download via yt-dlp with fallback URLs
            thumb_capture = [None]  # thumbnail path written by yt-dlp hook
            ydl_opts = self._build_ydl_opts(out_dir, stem, task_id, thumb_capture)

            urls_to_try = [
                f"https://music.youtube.com/watch?v={vid}",
                f"https://www.youtube.com/watch?v={vid}",
            ]

            success = False
            last_err = ""

            # Prevent multithreading write collisions on the cookie file
            thread_cookie = None
            if ydl_opts.get("cookiefile") == "cookies.txt":
                import tempfile
                import shutil
                import os
                fd, thread_cookie = tempfile.mkstemp(suffix=".txt")
                os.close(fd)
                try:
                    shutil.copy("cookies.txt", thread_cookie)
                    ydl_opts["cookiefile"] = thread_cookie
                except Exception:
                    pass

            try:
                for url in urls_to_try:
                    try:
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            ydl.download([url])
                        if f_path.exists():
                            success = True
                            break
                    except Exception as e:
                        raw_err = str(e).split("\n")[0]
                        last_err = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', raw_err).strip()
                        if url != urls_to_try[-1]:
                            logger.debug(
                                f"Primary URL failed for '{t['title']}', trying fallback..."
                            )
            finally:
                if thread_cookie:
                    try:
                        import os
                        os.remove(thread_cookie)
                    except Exception:
                        pass

            if not success and last_err:
                msg = f"[[cyan]{p_title}[/cyan]] '{t['title']}' fail: {last_err}"
                logger.warning(msg)
                with self.stats_lock:
                    self.issues.append(msg)

            # Advance progress bar once per track (success or fail)
            self.progress.update(task_id, advance=1)

            # 3. Post-process: thumbnail + metadata
            if success:
                thumb_data: Optional[bytes] = None

                # Primary: fetch from ytmusicapi (highest quality, most reliable)
                thumbnails = t.get("thumbnails", [])
                if thumbnails:
                    thumb_data = self.processor.fetch_thumbnail_from_api(thumbnails)

                # Fallback: use file written by yt-dlp during download
                if not thumb_data:
                    cap = thumb_capture[0]
                    if cap and os.path.exists(cap):
                        thumb_data = Processor.read_thumbnail_file(cap)

                if thumb_data:
                    if not self.processor.embed_thumbnail(str(f_path), thumb_data):
                        logger.debug(f"Thumbnail embed failed for '{stem}'")
                else:
                    logger.debug(f"No thumbnail source found for '{stem}'")

                # Tags (title, artist, album only – YTMusic year data is unreliable)
                artist = ", ".join([a["name"] for a in t.get("artists", [])])
                album = t.get("album", {}).get("name") if t.get("album") else None
                self.processor.set_metadata(
                    str(f_path),
                    {"title": t["title"], "artist": artist, "album": album},
                )
                counts["new"] += 1
            else:
                counts["fail"] += 1

            # Clean up only this track's temp/thumbnail files
            self.cleanup_track(out_dir, stem)

        # Generate .m3u8 playlist file to preserve track order
        try:
            m3u8_path = out_dir / f"{self.syncer.get_safe_filename(p_title)}.m3u8"
            ext = self.config.get("yt_dlp", {}).get("audio_format", "opus")
            
            with open(m3u8_path, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for t in tracks:
                    vid = t["videoId"]
                    if vid in expected_names:
                        f_name = f"{expected_names[vid]}.{ext}"
                        if (out_dir / f_name).exists():
                            f.write(f"{f_name}\n")
            logger.debug(f"Generated playlist file: '{m3u8_path.name}'")
        except Exception as e:
            logger.error(f"Failed to generate .m3u8 for '{p_title}': {e}")

        # Mirroring & summary
        self.syncer.run_mirroring(p_title, tracks)
        summary = (
            f"Playlist '{p_title}': {len(tracks)} tracks. "
            f"{counts['new']} New, {counts['skip']} Skipped."
        )
        if counts["fixed"] > 0:
            summary += f" [bold green]{counts['fixed']} Covers fixed.[/bold green]"
        if counts["fail"] > 0:
            summary += f" [bold yellow]{counts['fail']} Failed.[/bold yellow]"

        logger.info(summary)
        with self.stats_lock:
            self.summaries.append(summary)
        self.progress.remove_task(task_id)
        self.progress.update(self.overall_task, advance=1)

    def run(self):
        """Main execution loop."""
        # Pre-flight: FFmpeg
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        except Exception:
            logger.error(
                "[bold red]FFmpeg not found in PATH![/bold red] Required for audio processing."
            )
            return

        # Pre-flight: cookies (validate once, cache result)
        browser = self.config.get("yt_dlp", {}).get("cookies_from_browser")
        if browser:
            logger.info(f"Extracting cookies from {browser} (one-time)...")
            while True:
                try:
                    ydl_opts = {
                        "cookiesfrombrowser": (browser, ),
                        "cookiefile": "cookies.txt",
                        "quiet": True,
                        "no_warnings": True,
                    }
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.cookiejar.save()
                    logger.info(f"Successfully extracted cookies from {browser}!")
                    break
                except Exception as e:
                    err_msg = str(e)
                    if "Could not copy" in err_msg or "Permission denied" in err_msg or "locked" in err_msg.lower():
                        console.print(f"\n[bold red]File Lock Error:[/bold red] {browser.capitalize()} is currently running and locking its cookie database.")
                        console.print(f"[bold yellow]Please close {browser.capitalize()} completely (including system tray), then press Enter to try again...[/bold yellow]")
                        try:
                            input()
                        except KeyboardInterrupt:
                            return
                    else:
                        logger.error(f"Failed to extract cookies from {browser}: {e}")
                        break

        self.validate_cookies()

        if not self.auth.initialize():
            return

        logger.info("Discovering library playlists...")
        try:
            raw = self.auth.yt.get_library_playlists(limit=None)
            playlists = [
                p for p in raw
                if p["playlistId"] != "LM" and "liked" not in p["title"].lower()
            ]
        except Exception as e:
            logger.error(f"Library fetch failed: {e}")
            return

        if not playlists:
            logger.warning("No syncable playlists found.")
            return

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            console=console,
            transient=True,
        ) as self.progress:
            self.overall_task = self.progress.add_task(
                "[bold yellow]Syncing Library", total=len(playlists)
            )
            max_workers = self.config.get("max_workers", 3)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                try:
                    futures = [executor.submit(self.sync_playlist, p) for p in playlists]
                    concurrent.futures.wait(futures)
                except KeyboardInterrupt:
                    logger.warning(
                        "\n[bold red]Interrupted. Finishing current tasks and exiting...[/bold red]"
                    )
                    executor.shutdown(wait=False, cancel_futures=True)
                    import os
                    os._exit(1)

        # Final report
        console.print("\n")
        report = []
        if self.issues:
            report.append("[bold red]Warnings & Failures:[/bold red]")
            report.extend([f"• {i}" for i in sorted(set(self.issues))])
            report.append("")

        report.append("[bold cyan]Sync Summary:[/bold cyan]")
        report.extend([f"• {s}" for s in sorted(self.summaries)])

        console.print(
            Panel("\n".join(report), title="Final Sync Status", border_style="green", expand=False)
        )
        logger.info(f"Done. Detailed log: {LOG_FILE}")


if __name__ == "__main__":
    try:
        cfg = Config(CONFIG_FILE)
        at = Auth(cfg.get("auth_file", "headers.json"))
        sc = Syncer(cfg)
        app = Downloader(cfg, at, sc)
        app.run()
    except KeyboardInterrupt:
        logger.warning("\n[bold red]Interrupted by user.[/bold red]")
        import os
        os._exit(1)
    except Exception as e:
        logger.critical(f"Fatal crash: {e}", exc_info=True)
