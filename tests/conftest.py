from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from ytmd.config import Environment, SettingsStore
from ytmd.db import Database
from ytmd.downloader import DownloadError
from ytmd.sync import SyncEngine


@pytest.fixture(scope="session")
def audio_samples(tmp_path_factory) -> dict[str, Path]:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for these tests")
    out = tmp_path_factory.mktemp("samples")
    codecs = {"opus": ["-c:a", "libopus"], "m4a": ["-c:a", "aac"], "mp3": []}
    files = {}
    for ext, codec in codecs.items():
        path = out / f"sample.{ext}"
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
             *codec, str(path)],
            check=True,
        )
        files[ext] = path
    return files


@pytest.fixture(scope="session")
def jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (640, 360), (180, 40, 200)).save(buf, "JPEG")
    return buf.getvalue()


def track(vid, title, artists=("Artist",), album=None, album_id=None, video_type="MUSIC_VIDEO_TYPE_ATV",
          available=True):
    return {
        "video_id": vid, "title": title, "artists": list(artists), "album": album, "album_id": album_id,
        "duration": 180, "thumbnail": f"https://lh3.googleusercontent.com/{vid}=w60-h60",
        "video_type": video_type, "explicit": False, "available": available,
    }


class FakeClient:
    """In-memory stand-in for YTMusicClient."""

    def __init__(self):
        self.playlists: list[dict] = []
        self.tracks: dict[str, list[dict]] = {}
        self.albums: dict[str, dict] = {}
        self.watch: dict[str, dict] = {}
        self.lyrics_data: dict[str, dict] = {}
        self.failing_playlists: set[str] = set()
        self.removed: list[tuple[str, str, str]] = []

    def add_playlist(self, pid, title, tracks, owned=True):
        self.playlists.append({"id": pid, "title": title, "thumbnail": None, "count": len(tracks),
                               "owned": owned, "author": None})
        self.tracks[pid] = list(tracks)

    def remove_playlist(self, pid):
        self.playlists = [p for p in self.playlists if p["id"] != pid]
        self.tracks.pop(pid, None)

    def library_playlists(self):
        return [dict(p) for p in self.playlists]

    def playlist_tracks(self, pid):
        if pid in self.failing_playlists:
            raise RuntimeError("boom")
        return [{**t, "set_video_id": t.get("set_video_id") or f"{pid}:{i}"}
                for i, t in enumerate(self.tracks.get(pid, []))]

    def remove_from_playlist(self, pid, vid, set_vid):
        self.removed.append((pid, vid, set_vid))
        self.tracks[pid] = [t for i, t in enumerate(self.tracks[pid])
                            if (t.get("set_video_id") or f"{pid}:{i}") != set_vid]

    def album(self, album_id):
        return self.albums[album_id]

    def watch_info(self, vid):
        return self.watch.get(vid, {"lyrics_id": None, "counterpart": None})

    def lyrics(self, browse_id):
        return self.lyrics_data.get(browse_id)

    def account_info(self):
        return {"name": "Tester", "handle": "@tester", "photo": None}


class FakeDownloader:
    def __init__(self, samples: dict[str, Path]):
        self.samples = samples
        self.calls: list[str] = []
        self.errors: dict[str, DownloadError] = {}

    def __call__(self, video_id, work_dir, opts, on_progress=None, cancel=None):
        self.calls.append(video_id)
        if video_id in self.errors:
            raise self.errors[video_id]
        work_dir.mkdir(parents=True, exist_ok=True)
        dst = work_dir / f"{video_id}.{opts.audio_format}"
        shutil.copy(self.samples[opts.audio_format], dst)
        return dst


class Harness:
    def __init__(self, tmp_path: Path, samples, jpeg):
        self.env = Environment(config_dir=tmp_path / "config", music_dir=tmp_path / "music", host="127.0.0.1",
                               port=0, web_user="admin", web_password="", log_level="INFO")
        self.env.ensure_dirs()
        self.settings = SettingsStore(self.env.settings_file)
        self.settings.update({"download_delay_seconds": 0, "concurrent_downloads": 2, "layout": "artist_album"})
        self.db = Database(self.env.db_file)
        self.client = FakeClient()
        self.downloader = FakeDownloader(samples)
        self.engine = SyncEngine(self.env, self.settings, self.db, client_factory=lambda s: self.client,
                                 download_fn=self.downloader, image_fetcher=lambda url, size: jpeg)

    @property
    def music(self) -> Path:
        return self.env.music_dir

    def sync(self):
        summary = self.engine.run("test")
        assert summary is not None
        return summary

    def m3u8(self, name: str) -> list[str]:
        path = self.music / "Playlists" / f"{name}.m3u8"
        return [l for l in path.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")]

    def audio_files(self) -> set[str]:
        return {p.relative_to(self.music).as_posix() for p in self.music.rglob("*.opus")
                if not p.relative_to(self.music).as_posix().startswith(".ytmd")}


@pytest.fixture
def harness(tmp_path, audio_samples, jpeg_bytes) -> Harness:
    h = Harness(tmp_path, audio_samples, jpeg_bytes)
    yield h
    h.db.close()
