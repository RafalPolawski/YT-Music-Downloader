"""yt-dlp subprocess handling: a scripted fake for edge cases plus one run of the real thing."""

from __future__ import annotations

import functools
import http.server
import importlib.util
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from ytmd import downloader
from ytmd.downloader import (CANCELLED, RATE_LIMITED, TRANSIENT, UNAVAILABLE, DownloadError, DownloadOptions,
                             download_audio)
from ytmd.tagger import audio_info

FAKE_YTDLP = textwrap.dedent('''
    import pathlib, sys, time
    args = sys.argv[1:]
    url = args[-1]
    out = args[args.index("--output") + 1]
    log = pathlib.Path(out).parent / "calls.log"
    with log.open("a") as f:
        f.write(url + "\\n")
    if "--cookies" in args:
        cookies = pathlib.Path(args[args.index("--cookies") + 1])
        assert cookies.read_text().startswith("# Netscape"), "cookie file missing"
    if url.endswith("/hang"):
        time.sleep(30)
    if "music.youtube.com" in url and "fallback" in url:
        print("ERROR: [youtube] x: Unable to download webpage: timed out", file=sys.stderr)
        sys.exit(1)
    if url.endswith("/bot"):
        print("WARNING: something harmless", file=sys.stderr)
        print("ERROR: [youtube] x: Sign in to confirm you\\u2019re not a bot", file=sys.stderr)
        sys.exit(1)
    if url.endswith("/gone"):
        print("ERROR: [youtube] x: Video unavailable. This video has been removed", file=sys.stderr)
        sys.exit(1)
    path = pathlib.Path(out.replace("%(id)s", "vid").replace("%(ext)s", "opus"))
    for done in (0, 512, 1024):
        print(f"[ytmd-progress] {done}/1024/NA/2048.0", flush=True)
    path.write_bytes(b"audio")
    print(f"[ytmd-file] {path}")
''')


@pytest.fixture
def fake_ytdlp(tmp_path, monkeypatch):
    script = tmp_path / "fake_ytdlp.py"
    script.write_text(FAKE_YTDLP)
    monkeypatch.setattr(downloader, "ytdlp_command", lambda: [sys.executable, str(script)])
    return tmp_path / "work"


def calls(work: Path) -> list[str]:
    return (work / "calls.log").read_text().splitlines()


def test_success_reports_progress_and_file(fake_ytdlp):
    seen = []
    path = downloader._run_once("https://x/ok", fake_ytdlp, DownloadOptions(), seen.append, None)
    assert path == fake_ytdlp / "vid.opus" and path.read_bytes() == b"audio"
    assert [p.percent for p in seen] == [0.0, 50.0, 100.0]


def test_rate_limit_is_recognised_and_not_retried(fake_ytdlp, monkeypatch):
    monkeypatch.setattr(downloader, "_run_once", functools.partial(_route, "bot"))
    with pytest.raises(DownloadError) as err:
        download_audio("x", fake_ytdlp, DownloadOptions())
    assert err.value.kind == RATE_LIMITED
    assert "not a bot" in str(err.value)
    assert len(calls(fake_ytdlp)) == 1


def test_unavailable_video_is_not_retried_on_youtube_com(fake_ytdlp, monkeypatch):
    monkeypatch.setattr(downloader, "_run_once", functools.partial(_route, "gone"))
    with pytest.raises(DownloadError) as err:
        download_audio("x", fake_ytdlp, DownloadOptions())
    assert err.value.kind == UNAVAILABLE
    assert len(calls(fake_ytdlp)) == 1


def test_transient_failure_falls_back_to_youtube_com(fake_ytdlp, monkeypatch):
    monkeypatch.setattr(downloader, "_run_once", functools.partial(_route, "fallback"))
    path = download_audio("x", fake_ytdlp, DownloadOptions())
    assert path.exists()
    assert [u.split("/")[2] for u in calls(fake_ytdlp)] == ["music.youtube.com", "www.youtube.com"]


_real_run_once = downloader._run_once


def _route(kind, url, *args):
    """Turn the real watch URLs into URLs the fake script understands."""
    return _real_run_once(f"{url}/{kind}", *args)


def test_cancel_kills_the_process(fake_ytdlp):
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(DownloadError) as err:
        downloader._run_once("https://x/hang", fake_ytdlp, DownloadOptions(), None, cancel)
    assert err.value.kind == CANCELLED
    assert time.monotonic() - started < 10


def test_cookie_copy_is_removed_afterwards(fake_ytdlp):
    opts = DownloadOptions(cookies_text="# Netscape HTTP Cookie File\n")
    downloader._run_once("https://x/ok", fake_ytdlp, opts, None, None)
    assert not list(fake_ytdlp.glob("cookies-*"))


def test_missing_ytdlp_is_a_transient_error(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "ytdlp_command", lambda: [str(tmp_path / "does-not-exist")])
    with pytest.raises(DownloadError) as err:
        downloader._run_once("https://x/ok", tmp_path / "w", DownloadOptions(), None, None)
    assert err.value.kind == TRANSIENT


# ---------------------------------------------------------------------- the real yt-dlp
@pytest.mark.skipif(shutil.which("ffmpeg") is None or importlib.util.find_spec("yt_dlp") is None,
                    reason="needs ffmpeg and yt-dlp")
@pytest.mark.parametrize("fmt", ["opus", "m4a", "mp3"])
def test_real_ytdlp_extracts_audio(tmp_path, fmt):
    """Runs the actual yt-dlp CLI against a local HTTP server (no YouTube involved)."""
    site = tmp_path / "site"
    site.mkdir()
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-c:a", "libopus", str(site / "song.webm")], check=True)
    handler = functools.partial(QuietHandler, directory=str(site))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        seen = []
        url = f"http://127.0.0.1:{server.server_address[1]}/song.webm"
        opts = DownloadOptions(audio_format=fmt, extra_args='--proxy ""')
        path = downloader._run_once(url, tmp_path / "work", opts, seen.append, threading.Event())
    finally:
        server.shutdown()
    assert path.suffix == f".{fmt}"
    assert abs(audio_info(path)["length"] - 2.0) < 0.2
    assert seen and seen[-1].percent == 100.0


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass
