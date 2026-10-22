from __future__ import annotations

import json
from pathlib import Path

import pytest

from ytmd import downloader
from ytmd.downloader import (NEEDS_LOGIN, RATE_LIMITED, TRANSIENT, UNAVAILABLE, DownloadOptions, build_args,
                             classify_error)
from ytmd.library import PathAllocator, Trash, render_m3u8, sanitize, track_relpath
from ytmd.ytmusic import _headers_from_curl, netscape_cookies_from_auth, normalize_track, save_browser_auth


# ---------------------------------------------------------------------- library
@pytest.mark.parametrize("raw,expected", [
    ("AC/DC", "AC／DC"),
    ('What? "Why": <Now>|*', "What？ ＂Why＂： ＜Now＞｜＊"),
    ("  trailing dots...  ", "trailing dots"),
    ("", "Unknown"),
    ("CON", "_CON"),
    (".hidden", "_hidden"),
    ("𝐁𝐨𝐥𝐝 𝐓𝐢𝐭𝐥𝐞", "Bold Title"),
    ("tab\tand\nnewline", "tabandnewline"),
])
def test_sanitize(raw, expected):
    assert sanitize(raw) == expected


def test_sanitize_limits_bytes():
    assert len(sanitize("ż" * 300).encode()) <= 180


def test_track_relpath_layouts():
    t = {"title": "Song", "artists": ["A", "B"], "album": "LP", "album_artists": ["A"], "track_number": 3}
    assert track_relpath(t, "artist_album", ".opus") == "A/LP/03 Song.opus"
    assert track_relpath(t, "artist", "opus") == "A/Song.opus"
    assert track_relpath(t, "flat", "m4a") == "A, B - Song.m4a"
    single = {"title": "Song", "artists": [], "album": None}
    assert track_relpath(single, "artist_album", "mp3") == "Unknown Artist/Singles/Song.mp3"


def test_allocator_is_case_insensitive():
    a = PathAllocator()
    assert a.claim("v1", "A/Song.opus") == "A/Song.opus"
    assert a.claim("v1", "A/Song.opus") == "A/Song.opus"
    assert a.claim("v2", "a/song.opus") == "a/song [v2].opus"


def test_m3u8_uses_relative_paths(tmp_path):
    entries = [{"title": "T", "artists": ["A"], "duration": 61, "path": "A/Al/01 T.opus"}]
    text = render_m3u8("My List", entries, tmp_path / "Playlists", tmp_path)
    assert text == "#EXTM3U\n#PLAYLIST:My List\n#EXTINF:61,A - T\n../A/Al/01 T.opus\n"


def test_trash_put_restore_and_delete(tmp_path):
    music = tmp_path / "music"
    (music / "A").mkdir(parents=True)
    (music / "A/x.opus").write_text("audio")
    (music / "A/x.lrc").write_text("lyrics")
    trash = Trash(music / ".ytmd-trash")
    rel = trash.put(music, "A/x.opus")
    assert rel == "A/x.opus" and not (music / "A/x.opus").exists()
    assert (trash.root / "A/x.lrc").exists()
    assert [i["path"] for i in trash.items()] == ["A/x.opus"]
    with pytest.raises(ValueError):
        trash.delete("../../etc/passwd")
    assert trash.restore(rel, music / "B/x.opus")
    assert (music / "B/x.lrc").exists()


# ---------------------------------------------------------------------- downloader
@pytest.mark.parametrize("text,kind", [
    ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies", RATE_LIMITED),
    ("HTTP Error 429: Too Many Requests", RATE_LIMITED),
    ("This content isn't available, try again later.", RATE_LIMITED),
    ("Video unavailable. This video has been removed by the uploader", UNAVAILABLE),
    ("Private video. Sign in if you've been granted access", UNAVAILABLE),
    ("Sign in to confirm your age. This video may be inappropriate for some users.", NEEDS_LOGIN),
    ("This video is only available to Music Premium members", NEEDS_LOGIN),
    ("Unable to download webpage: <urlopen error timed out>", TRANSIENT),
])
def test_classify_error(text, kind):
    assert classify_error(text) == kind


def test_build_args(tmp_path):
    args = build_args("https://music.youtube.com/watch?v=x", tmp_path,
                      DownloadOptions(audio_format="opus", sponsorblock=True, extra_args="--limit-rate 2M"),
                      tmp_path / "c.txt")
    joined = " ".join(args)
    assert "bestaudio[acodec=opus]" in joined
    assert "--audio-format opus" in joined
    assert "--cookies" in args and "--sponsorblock-remove" in args
    assert args[-3:] == ["--limit-rate", "2M", "https://music.youtube.com/watch?v=x"]


def test_progress_parsing():
    p = downloader._parse_progress(f"{downloader.PROGRESS_PREFIX} 500/NA/1000/12.5")
    assert p.percent == 50.0 and p.speed == 12.5


# ---------------------------------------------------------------------- ytmusic helpers
def test_normalize_track():
    raw = {"videoId": "v", "title": " T ", "artists": [{"name": "A", "id": "1"}, {"name": ""}],
           "album": {"name": "Al", "id": "MPRE1"}, "duration_seconds": 10, "isAvailable": False,
           "thumbnails": [{"url": "s", "width": 60, "height": 60}, {"url": "b", "width": 120, "height": 120}],
           "videoType": "MUSIC_VIDEO_TYPE_ATV"}
    n = normalize_track(raw)
    assert n["title"] == "T" and n["artists"] == ["A"] and n["album_id"] == "MPRE1"
    assert n["thumbnail"] == "b" and n["available"] is False
    assert normalize_track({"title": "no id"}) is None


def test_headers_from_curl():
    cmd = ("curl 'https://music.youtube.com/youtubei/v1/browse?prettyPrint=false' \\\n"
           "  -H 'x-goog-authuser: 0' \\\n  -b 'SAPISID=abc; SID=def' \\\n  --data-raw '{}'")
    text = _headers_from_curl(cmd)
    assert "x-goog-authuser: 0" in text and "cookie: SAPISID=abc; SID=def" in text


COOKIE = "SAPISID=abc; __Secure-3PAPISID=abc; SID=def"


def test_save_browser_auth_from_headers_and_json(tmp_path):
    target = tmp_path / "browser.json"
    save_browser_auth(f"cookie: {COOKIE}\nx-goog-authuser: 0\nuser-agent: test", target)
    data = json.loads(target.read_text())
    assert data["cookie"] == COOKIE
    assert "SAPISIDHASH" in data["authorization"]  # placeholder so ytmusicapi detects browser auth
    assert oct(target.stat().st_mode & 0o777) == "0o600"

    save_browser_auth(json.dumps({"Cookie": COOKIE, "X-Goog-AuthUser": "0",
                                  "Authorization": "SAPISIDHASH 123_abc"}), target)
    data = json.loads(target.read_text())
    assert data["Authorization"] == "SAPISIDHASH 123_abc" and "authorization" not in data

    with pytest.raises(ValueError):
        save_browser_auth("accept: */*", target)
    with pytest.raises(ValueError, match="__Secure-3PAPISID"):
        save_browser_auth("cookie: SID=1\nx-goog-authuser: 0", target)


def test_saved_browser_auth_is_accepted_by_ytmusicapi(tmp_path):
    from ytmusicapi.auth.types import AuthType

    from ytmd.ytmusic import YTMusicClient
    target = tmp_path / "browser.json"
    # with a visitor id ytmusicapi does not need the network during construction
    save_browser_auth(f"cookie: {COOKIE}\nx-goog-authuser: 0\nX-Goog-Visitor-Id: abc", target)
    client = YTMusicClient(target)
    assert client.yt.auth_type == AuthType.BROWSER


def test_netscape_cookies(tmp_path):
    auth = tmp_path / "browser.json"
    auth.write_text(json.dumps({"cookie": "SAPISID=abc; __Secure-3PSID=x=y"}))
    text = netscape_cookies_from_auth(auth)
    lines = [l.split("\t") for l in text.splitlines() if l and not l.startswith("#")]
    assert [(l[0], l[5], l[6]) for l in lines] == [(".youtube.com", "SAPISID", "abc"),
                                                   (".youtube.com", "__Secure-3PSID", "x=y")]
    assert netscape_cookies_from_auth(Path(tmp_path / "missing.json")) is None


def test_oauth_credentials_are_loaded_as_oauth(tmp_path):
    from ytmusicapi.auth.types import AuthType

    from ytmd.config import Environment
    from ytmd.ytmusic import auth_mode, make_client, save_oauth
    env = Environment(config_dir=tmp_path, music_dir=tmp_path / "m", host="", port=0, web_user="",
                      web_password="", log_level="INFO")
    assert auth_mode(env) is None
    save_oauth(env, {"scope": "https://www.googleapis.com/auth/youtube", "token_type": "Bearer",
                     "access_token": "a", "refresh_token": "r", "expires_at": 9999999999, "expires_in": 3600},
               {"client_id": "id", "client_secret": "secret"})
    assert auth_mode(env) == "oauth"
    assert make_client(env).yt.auth_type == AuthType.OAUTH_CUSTOM_CLIENT
