"""
YT-Music-Downloader - A professional, high-fidelity YouTube Music library mirror.
Features: 
- Smart collision-resistant naming.
- Real-time mirroring (moves server-deleted tracks to .deleted/).
- Full metadata & optimized cover embedding (Ogg Opus + JPEG).
- Data integrity checks to auto-repair corrupted downloads.
- Modern, multi-threaded UI with Rich.
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
from pathlib import Path
from typing import Dict, List, Optional, Set

# External Dependencies
import yt_dlp
from ytmusicapi import YTMusic
from rich.console import Console
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    MofNCompleteColumn,
    SpinnerColumn
)
from rich.panel import Panel
from rich.logging import RichHandler
from mutagen.oggopus import OggOpus
from mutagen.flac import Picture
from PIL import Image

# --- Constants ---
CONFIG_FILE = 'config.json'
LOG_FILE = 'sync.log'

# --- Logger Setup ---
def setup_logging():
    """Configures a bifurcated logging system: Rich for console, standard for file."""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    console = Console()
    
    # 1. Console Handler (Rich UI)
    console_handler = RichHandler(
        console=console,
        rich_tracebacks=True,
        markup=True,
        show_time=True,
        log_time_format="%H:%M:%S"
    )
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(console_handler)

    # 2. File Handler (Persistent logs)
    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)-7.7s] %(message)s", 
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    return console, logging.getLogger("ytm_sync")

console, logger = setup_logging()

class YDLLogger:
    """Redirects yt-dlp internal messages to our application logger."""
    def debug(self, msg):
        if not msg.startswith('[debug] '):
            logger.debug(msg)
    def info(self, msg): logger.debug(msg)
    def warning(self, msg): logger.debug(msg)
    def error(self, msg): logger.error(msg)

class Config:
    """Manages application settings with sensible defaults."""
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.file_path):
            with open(self.file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {
            "auth_file": "headers.json",
            "download_dir": "downloads",
            "max_workers": 3,
            "mirror_mode": True,
            "audio_quality": "128",
            "yt_dlp": {
                "audio_format": "opus",
                "auto_update": True,
                "additional_opts": {
                    "add-metadata": True,
                    "embed-thumbnail": True,
                    "windows-filenames": True
                }
            }
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
            # Try to load the file with mutagen which performs a sanity check
            OggOpus(file_path)
            return True
        except Exception:
            return False

    @staticmethod
    def set_metadata(file_path: str, tags: dict):
        """Applies generic audio tags to the file."""
        if not os.path.exists(file_path): return
        try:
            audio = OggOpus(file_path)
            for key, value in tags.items():
                if value: audio[key] = str(value)
            audio.save()
        except Exception as e:
            logger.debug(f"Metadata write error: {e}")

    def optimize_image(self, img_path: str) -> Optional[bytes]:
        """Resizes and compresses image to standardized 600x600 JPEG."""
        try:
            with Image.open(img_path) as img:
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                    
                img.thumbnail((600, 600), Image.Resampling.LANCZOS)
                
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=80, optimize=True)
                return buf.getvalue()
        except Exception as e:
            logger.debug(f"Image optimization error: {e}")
            return None

    def embed_thumbnail(self, audio_path: str, thumb_path: str) -> bool:
        """Embeds optimized thumbnail into Ogg Opus file via base64 picture block."""
        if not os.path.exists(audio_path) or not os.path.exists(thumb_path):
            return False

        img_data = self.optimize_image(thumb_path)
        if not img_data: return False

        try:
            pic = Picture()
            pic.data = img_data
            pic.type = 3 # Front Cover
            pic.mime = "image/jpeg"
            
            encoded = base64.b64encode(pic.write()).decode("ascii")

            audio = OggOpus(audio_path)
            audio["metadata_block_picture"] = [encoded]
            audio.save()
            return True
        except Exception as e:
            logger.debug(f"Thumbnail embed error: {e}")
            return False

class Syncer:
    """Manages naming, mirror logic, and directory health."""
    def __init__(self, config: Config):
        self.config = config

    def get_safe_filename(self, text: str) -> str:
        """Converts strings to Windows-safe filenames using Unicode equivalents."""
        mapping = {':': '：', '*': '＊', '?': '？', '"': '＂', '<': '＜', '>': '＞', '|': '｜', '/': '／', '\\': '＼'}
        safe = "".join(mapping.get(c, c) for c in text).strip().rstrip('.')
        return safe[:150] or f"Track_{hash(text) % 10000}"

    def get_expected_filenames(self, tracks: List[dict]) -> Dict[str, str]:
        """Generates unique, deterministic filenames to prevent collisions."""
        seen = {} # lower_stem: video_id
        mapping = {} # video_id: final_stem
        
        for t in tracks:
            vid = t.get('videoId')
            if not vid or vid in mapping: continue

            title = t.get('title', 'Unknown')
            artist = ", ".join([a['name'] for a in t.get('artists', [])])
            duration = t.get('duration', '00')

            # Tentative stems ordered by preference
            candidates = [
                self.get_safe_filename(title),
                self.get_safe_filename(f"{title} ({artist})"),
                self.get_safe_filename(f"{title} ({artist}, {duration})")
            ]

            found = False
            for c in candidates:
                if c.lower() not in seen:
                    seen[c.lower()], mapping[vid], found = vid, c, True
                    break
            
            if not found:
                # Absolute fallback with counter
                idx = 2
                while True:
                    c = self.get_safe_filename(f"{title} ({artist}, {duration}) [{idx}]")
                    if c.lower() not in seen:
                        seen[c.lower()], mapping[vid] = vid, c
                        break
                    idx += 1
        return mapping

    def run_mirroring(self, playlist_title: str, tracks: List[dict]):
        """Moves orphaned local files (not in remote playlist) to .deleted/."""
        if not self.config.get('mirror_mode', True): return

        p_dir = Path(self.config.get('download_dir', 'downloads')) / self.get_safe_filename(playlist_title)
        if not p_dir.exists(): return

        # Expected stems vs actual files
        expected_map = self.get_expected_filenames(tracks)
        remote_stems = {s.lower() for s in expected_map.values()}
        ext = f".{self.config.get('yt_dlp', {}).get('audio_format', 'opus')}"
        
        local_files = [f for f in p_dir.iterdir() if f.is_file() and f.suffix.lower() == ext]
        deleted_dir = p_dir / ".deleted"

        # 1. Restore from .deleted if re-added
        if deleted_dir.exists():
            for df in deleted_dir.iterdir():
                if df.stem.lower() in remote_stems:
                    try: 
                        df.rename(p_dir / df.name)
                        logger.info(f"Auto-Heal: Restored '{df.name}' to {playlist_title}")
                    except: pass

        # 2. Move to .deleted if removed from server
        to_move = [f for f in local_files if f.stem.lower() not in remote_stems]
        
        # Safety: Don't wipe the folder if API returns empty/incomplete
        if len(to_move) > len(local_files) * 0.5 and len(local_files) > 5:
            logger.warning(f"Mirroring safety: Aborted mass-move in '{playlist_title}'. Check internet/API.")
            return

        for f in to_move:
            deleted_dir.mkdir(exist_ok=True)
            try: 
                f.rename(deleted_dir / f.name)
                logger.warning(f"Mirroring: Moved '{f.name}' to .deleted/ (playlist: {playlist_title})")
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
        self.issues = []
        self.summaries = []

    def cleanup_temp(self, p_dir: Path):
        """Wipes temporary files left by yt-dlp or interrupted runs."""
        exts = ['*.temp', '*.part', '*.ytdl', '*.jpg', '*.webp', '*.png']
        count = 0
        for e in exts:
            for f in p_dir.glob(e):
                try: f.unlink(); count += 1
                except: pass
        if count > 0: logger.debug(f"Cleanup: Removed {count} temp files.")

    def sync_playlist(self, playlist: dict):
        """Synchronizes a single playlist with the local filesystem."""
        p_title = playlist['title']
        p_id = playlist['playlistId']
        
        out_dir = Path(self.config.get('download_dir', 'downloads')) / self.syncer.get_safe_filename(p_title)
        out_dir.mkdir(parents=True, exist_ok=True)
        self.cleanup_temp(out_dir)

        try:
            full_data = self.auth.yt.get_playlist(p_id, limit=None)
            tracks = full_data['tracks']
        except Exception as e:
            logger.error(f"Sync error for '{p_title}': {e}")
            self.progress.update(self.overall_task, advance=1)
            return

        task_id = self.progress.add_task(f"[cyan]{p_title}", total=len(tracks))
        expected_names = self.syncer.get_expected_filenames(tracks)
        
        counts = {"new": 0, "skip": 0, "fail": 0}
        processed_ids = set()

        for t in tracks:
            vid = t.get('videoId')
            if not vid or vid in processed_ids:
                if vid: self.progress.update(task_id, advance=1)
                continue
            processed_ids.add(vid)

            stem = expected_names.get(vid)
            ext = self.config.get('yt_dlp', {}).get('audio_format', 'opus')
            f_path = out_dir / f"{stem}.{ext}"

            # 1. Skip if already exists and is healthy
            if f_path.exists():
                if self.processor.is_file_valid(str(f_path)):
                    counts["skip"] += 1
                    self.progress.update(task_id, advance=1)
                    continue
                else:
                    logger.warning(f"Corrupt file detected: '{stem}'. Re-downloading...")
                    try: f_path.unlink()
                    except: pass

            # 2. Download via yt-dlp
            def hook(d): 
                if d['status'] == 'finished': self.progress.update(task_id, advance=1)

            y_opts = {
                'format': 'bestaudio/best',
                'outtmpl': f'{out_dir}/{stem.replace("%", "%%")}.%(ext)s',
                'quiet': True, 'no_warnings': True, 'logger': YDLLogger(),
                'progress_hooks': [hook], 'writethumbnail': True,
                'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': ext, 'preferredquality': self.config.get('audio_quality', '128')}]
            }
            
            # Additional logic: cookies, fallbacks
            if os.path.exists("cookies.txt"): y_opts['cookiefile'] = "cookies.txt"
            
            success = False
            try:
                with yt_dlp.YoutubeDL(y_opts) as ydl:
                    ydl.download([f'https://music.youtube.com/watch?v={vid}'])
                success = f_path.exists()
            except Exception as e:
                err = str(e).split('\n')[0]
                msg = f"[[cyan]{p_title}[/cyan]] '{t['title']}' fail: {err}"
                logger.warning(msg)
                with self.stats_lock: self.issues.append(msg)

            # 3. Post-process (Embedding & Metadata)
            if success:
                # Thumbnails
                for img in out_dir.glob(f"{stem}.*"):
                    if img.suffix.lower() in ('.jpg', '.webp', '.png', '.jpeg'):
                        self.processor.embed_thumbnail(str(f_path), str(img))
                        break
                
                # Tags
                artist = ", ".join([a['name'] for a in t.get('artists', [])])
                album = t.get('album', {}).get('name') if t.get('album') else None
                self.processor.set_metadata(str(f_path), {"title": t['title'], "artist": artist, "album": album})
                counts["new"] += 1
            else:
                counts["fail"] += 1

            self.cleanup_temp(out_dir)

        # Mirroring & Conclusion
        self.syncer.run_mirroring(p_title, tracks)
        summary = f"Playlist '{p_title}': {len(tracks)} tracks. {counts['new']} New, {counts['skip']} Skipped."
        if counts['fail'] > 0: summary += f" [bold yellow]{counts['fail']} Failed.[/bold yellow]"
        
        logger.info(summary)
        with self.stats_lock: self.summaries.append(summary)
        self.progress.remove_task(task_id)
        self.progress.update(self.overall_task, advance=1)

    def run(self):
        """Main execution loop."""
        # Pre-flight checks
        try: # Check FFmpeg
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        except:
            logger.error("[bold red]FFmpeg not found in PATH![/bold red] Required for processing.")
            return

        if not self.auth.initialize(): return

        logger.info("Discovering library playlists...")
        try:
            raw = self.auth.yt.get_library_playlists(limit=None)
            playlists = [p for p in raw if p['playlistId'] != 'LM' and 'liked' not in p['title'].lower()]
        except Exception as e:
            logger.error(f"Library fetch failed: {e}"); return

        if not playlists:
            logger.warning("No syncable playlists found."); return

        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            BarColumn(), MofNCompleteColumn(), console=console, transient=True
        ) as self.progress:
            self.overall_task = self.progress.add_task("[bold yellow]Syncing Library", total=len(playlists))
            
            max_workers = self.config.get('max_workers', 3)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                try:
                    futures = [executor.submit(self.sync_playlist, p) for p in playlists]
                    concurrent.futures.wait(futures)
                except KeyboardInterrupt:
                    logger.warning("\n[bold red]Interrupted. Finishing current tasks and exiting...[/bold red]")
                    executor.shutdown(wait=False, cancel_futures=True); raise

        # Final Report
        console.print("\n")
        report = []
        if self.issues:
            report.append("[bold red]Warnings & Failures:[/bold red]")
            report.extend([f"• {i}" for i in sorted(list(set(self.issues)))])
            report.append("")
        
        report.append("[bold cyan]Sync Summary:[/bold cyan]")
        report.extend([f"• {s}" for s in sorted(self.summaries)])
        
        console.print(Panel("\n".join(report), title="Final Sync Status", border_style="green", expand=False))
        logger.info(f"Done. Detailed log: {LOG_FILE}")

if __name__ == "__main__":
    try:
        cfg = Config(CONFIG_FILE)
        at = Auth(cfg.get('auth_file', 'headers.json'))
        sc = Syncer(cfg)
        app = Downloader(cfg, at, sc)
        app.run()
    except KeyboardInterrupt:
        logger.warning("\n[bold red]Interrupted by user.[/bold red]")
    except Exception as e:
        logger.critical(f"Fatal crash: {e}", exc_info=True)
