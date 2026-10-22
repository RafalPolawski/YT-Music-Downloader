"""Thin, thread-safe wrapper around ytmusicapi that returns normalized dictionaries."""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import shlex
import threading
import time
from pathlib import Path
from typing import Any, Callable

import requests
from ytmusicapi import OAuthCredentials, YTMusic, setup as ytm_setup

from ytmd.config import Environment

log = logging.getLogger(__name__)

LIKED_MUSIC_ID = "LM"
EXCLUDED_PLAYLISTS = {"SE"}  # "Episodes for later" (podcasts)
VIDEO_TYPE_SONG = "MUSIC_VIDEO_TYPE_ATV"


class AuthError(Exception):
    """The account is not connected or the saved credentials stopped working."""


def best_thumbnail(thumbs: list[dict] | None) -> str | None:
    if not thumbs:
        return None
    best = max(thumbs, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))
    return best.get("url")


def _artist_names(artists: list[dict] | None) -> list[str]:
    return [a["name"].strip() for a in artists or [] if a and a.get("name") and a["name"].strip()]


def _parse_count(value: Any) -> int | None:
    if value is None:
        return None
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


def normalize_playlist(p: dict) -> dict:
    return {
        "id": p["playlistId"],
        "title": (p.get("title") or "Untitled playlist").strip(),
        "thumbnail": best_thumbnail(p.get("thumbnails")),
        "count": _parse_count(p.get("count")),
        "owned": bool(p.get("owned")),
        "author": ", ".join(_artist_names(p.get("author"))) or None,
    }


def normalize_track(t: dict) -> dict | None:
    video_id = t.get("videoId")
    if not video_id:
        return None
    album = t.get("album") or {}
    if isinstance(album, str):  # album tracks carry the album title as a plain string
        album = {"name": album}
    return {
        "video_id": video_id,
        "title": (t.get("title") or "Unknown Title").strip(),
        "artists": _artist_names(t.get("artists")),
        "album": album.get("name"),
        "album_id": album.get("id"),
        "duration": t.get("duration_seconds"),
        "thumbnail": best_thumbnail(t.get("thumbnails") or t.get("thumbnail")),
        "video_type": t.get("videoType"),
        "explicit": bool(t.get("isExplicit")),
        "available": t.get("isAvailable", True) is not False,
    }


def _retry(fn: Callable, attempts: int = 3, delay: float = 2.0):
    for i in range(attempts):
        try:
            return fn()
        except (requests.ConnectionError, requests.Timeout) as exc:
            if i == attempts - 1:
                raise
            log.debug("YT Music request failed (%s), retrying", exc)
            time.sleep(delay * (i + 1))


def auth_mode(env: Environment) -> str | None:
    """How the account is connected: ``"browser"`` (copied headers), ``"oauth"`` or None."""
    if env.auth_file.exists():
        return "browser"
    if env.oauth_file.exists() and env.oauth_client_file.exists():
        return "oauth"
    return None


def make_client(env: Environment, brand_account_id: str = "") -> "YTMusicClient":
    mode = auth_mode(env)
    if mode == "oauth":
        client = json.loads(env.oauth_client_file.read_text(encoding="utf-8"))
        return YTMusicClient(env.oauth_file, brand_account_id, oauth_client=client)
    return YTMusicClient(env.auth_file, brand_account_id)


class YTMusicClient:
    def __init__(self, auth_file: Path, brand_account_id: str = "", oauth_client: dict | None = None):
        if not auth_file.exists():
            raise AuthError("YouTube Music account is not connected")
        session = requests.Session()
        session.request = functools.partial(session.request, timeout=30)  # type: ignore[method-assign]
        credentials = None
        if oauth_client:
            credentials = OAuthCredentials(oauth_client["client_id"], oauth_client["client_secret"])
        try:
            self.yt = YTMusic(str(auth_file), user=brand_account_id or None, requests_session=session,
                              oauth_credentials=credentials)
        except Exception as exc:
            raise AuthError(f"Saved credentials are invalid: {exc}") from exc
        self._lock = threading.Lock()

    def _call(self, fn: Callable, *args, **kwargs):
        with self._lock:
            return _retry(lambda: fn(*args, **kwargs))

    # ------------------------------------------------------------------ account
    def account_info(self) -> dict:
        try:
            info = self._call(self.yt.get_account_info)
        except Exception as exc:
            raise AuthError(f"YouTube Music rejected the credentials: {exc}") from exc
        if not info or not info.get("accountName"):
            raise AuthError("YouTube Music did not return account details - credentials may have expired")
        return {
            "name": info.get("accountName"),
            "handle": info.get("channelHandle"),
            "photo": info.get("accountPhotoUrl"),
        }

    # ------------------------------------------------------------------ library
    def library_playlists(self) -> list[dict]:
        raw = self._call(self.yt.get_library_playlists, limit=None) or []
        out, seen = [], set()
        for p in raw:
            if not p.get("playlistId") or p["playlistId"] in EXCLUDED_PLAYLISTS or p["playlistId"] in seen:
                continue
            seen.add(p["playlistId"])
            out.append(normalize_playlist(p))
        return out

    def playlist_tracks(self, playlist_id: str) -> list[dict]:
        data = self._call(self.yt.get_playlist, playlist_id, limit=None)
        tracks = []
        for t in data.get("tracks") or []:
            n = normalize_track(t)
            if n:
                tracks.append(n)
        return tracks

    def album(self, album_id: str) -> dict:
        data = self._call(self.yt.get_album, album_id)
        return {
            "title": data.get("title"),
            "artists": _artist_names(data.get("artists")),
            "year": str(data["year"]) if data.get("year") else None,
            "track_count": data.get("trackCount") or len(data.get("tracks") or []),
            "thumbnail": best_thumbnail(data.get("thumbnails")),
            "tracks": [
                {"video_id": t.get("videoId"), "title": t.get("title"), "track_number": t.get("trackNumber")}
                for t in data.get("tracks") or []
            ],
        }

    def watch_info(self, video_id: str) -> dict:
        """Lyrics id and (for music videos) the matching album track."""
        data = self._call(self.yt.get_watch_playlist, videoId=video_id, limit=1)
        info: dict[str, Any] = {"lyrics_id": data.get("lyrics"), "counterpart": None}
        tracks = data.get("tracks") or []
        if tracks and tracks[0].get("videoId") == video_id and tracks[0].get("counterpart"):
            info["counterpart"] = normalize_track(tracks[0]["counterpart"])
        return info

    def lyrics(self, browse_id: str) -> dict | None:
        try:
            data = self._call(self.yt.get_lyrics, browse_id, timestamps=True)
        except Exception as exc:
            log.debug("Timed lyrics unavailable (%s), falling back to plain text", exc)
            data = self._call(self.yt.get_lyrics, browse_id)
        if not data or not data.get("lyrics"):
            return None
        if data.get("hasTimestamps"):
            lines = [(line.start_time, line.text) for line in data["lyrics"]]
            return {"text": "\n".join(text for _, text in lines), "synced": lines}
        return {"text": data["lyrics"], "synced": None}


# ---------------------------------------------------------------------- credentials
def _headers_from_curl(command: str) -> str:
    command = command.replace("\\\r\n", " ").replace("\\\n", " ")
    tokens = shlex.split(command)
    headers = []
    for i, tok in enumerate(tokens[:-1]):
        if tok in ("-H", "--header"):
            headers.append(tokens[i + 1])
        elif tok in ("-b", "--cookie"):
            headers.append(f"cookie: {tokens[i + 1]}")
    return "\n".join(headers)


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".new")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def save_browser_auth(raw: str, auth_file: Path) -> None:
    """Accepts copied request headers, a "Copy as cURL" command or an existing headers JSON file."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("Nothing was pasted")

    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ValueError(f"That looks like JSON but could not be parsed: {exc}") from exc
        if not isinstance(data, dict) or "cookie" not in {k.lower() for k in data}:
            raise ValueError("The JSON does not contain a 'cookie' entry")
    else:
        if text.lower().startswith("curl "):
            text = _headers_from_curl(text)
        try:
            data = json.loads(ytm_setup(headers_raw=text))
        except Exception as exc:
            raise ValueError(str(exc)) from exc

    cookie = next(v for k, v in data.items() if k.lower() == "cookie")
    if "__Secure-3PAPISID=" not in cookie:
        raise ValueError("The cookie has no __Secure-3PAPISID value - make sure you are signed in to "
                         "music.youtube.com and copied the headers of a music.youtube.com request")
    # ytmusicapi recognises browser credentials by a SAPISIDHASH authorization header; its value
    # is recomputed for every request, so a placeholder is enough when the browser did not send one.
    auth_key = next((k for k in data if k.lower() == "authorization"), "authorization")
    if "SAPISIDHASH" not in str(data.get(auth_key, "")):
        data[auth_key] = "SAPISIDHASH 0_placeholder"
    _write_private(auth_file, json.dumps(data, indent=2))


class DeviceLogin:
    """OAuth "sign in on another device" flow (google.com/device), driven from the web UI."""

    def __init__(self, client_id: str, client_secret: str):
        client_id, client_secret = (client_id or "").strip(), (client_secret or "").strip()
        if not client_id or not client_secret:
            raise ValueError("Enter both the OAuth client ID and client secret")
        self.client = {"client_id": client_id, "client_secret": client_secret}
        self.credentials = OAuthCredentials(client_id, client_secret)
        try:
            code = self.credentials.get_code()
        except requests.RequestException as exc:
            raise ValueError(f"Could not reach Google: {exc}") from exc
        except Exception as exc:
            raise ValueError(f"Google rejected the OAuth client: {exc}") from exc
        if "device_code" not in code:
            raise ValueError(str(code.get("error_description") or code.get("error") or code))
        self.device_code = code["device_code"]
        self.user_code = code["user_code"]
        self.verification_url = code.get("verification_url") or "https://www.google.com/device"
        self.interval = int(code.get("interval") or 5)
        self.expires_at = time.time() + int(code.get("expires_in") or 1800)

    def poll(self) -> dict | None:
        """The token once the user approved the sign-in, None while still waiting."""
        if time.time() > self.expires_at:
            raise ValueError("The sign-in code expired - please start again")
        raw = self.credentials.token_from_code(self.device_code)
        error = raw.get("error")
        if error in ("authorization_pending", "slow_down"):
            return None
        if error or "refresh_token" not in raw:
            raise ValueError(str(raw.get("error_description") or error or "Google did not return a token"))
        return {
            "scope": raw.get("scope"),
            "token_type": raw.get("token_type", "Bearer"),
            "access_token": raw["access_token"],
            "refresh_token": raw["refresh_token"],
            "expires_at": int(time.time()) + int(raw.get("expires_in", 3600)),
            "expires_in": int(raw.get("expires_in", 3600)),
        }


def save_oauth(env: Environment, token: dict, client: dict) -> None:
    _write_private(env.oauth_client_file, json.dumps(client, indent=2))
    _write_private(env.oauth_file, json.dumps(token, indent=2))
    env.auth_file.unlink(missing_ok=True)


def disconnect(env: Environment) -> None:
    for f in (env.auth_file, env.oauth_file, env.oauth_client_file):
        f.unlink(missing_ok=True)


def netscape_cookies_from_auth(auth_file: Path) -> str | None:
    """Turn the Cookie header saved for ytmusicapi into a cookies.txt that yt-dlp understands."""
    try:
        data = json.loads(auth_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cookie = next((v for k, v in data.items() if k.lower() == "cookie"), None)
    if not cookie:
        return None
    expires = int(time.time()) + 365 * 86400
    lines = ["# Netscape HTTP Cookie File", "# Generated by yt-music-downloader from the connected account", ""]
    for part in cookie.split(";"):
        name, sep, value = part.strip().partition("=")
        if not sep or not name:
            continue
        lines.append("\t".join([".youtube.com", "TRUE", "/", "TRUE", str(expires), name, value]))
    return "\n".join(lines) + "\n"
