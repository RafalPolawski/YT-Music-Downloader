"""REST API for the web UI plus static file serving."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
import requests
from pydantic import ValidationError
from urllib.parse import urlparse

from ytmd import __version__
from ytmd import db as dbm
from ytmd import jellyfin
from ytmd.config import Settings
from ytmd.duplicates import find_duplicates
from ytmd.library import disk_usage
from ytmd.logs import memory_handler
from ytmd.updater import update_ytdlp
from ytmd.ytmusic import (AuthError, DeviceLogin, auth_mode, disconnect, save_browser_auth, save_oauth,
                          write_private)

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
SECRET_MASK = "********"
FIRST_SYNC_DELAY = 300
# Thumbnails are fetched by the server (and cached) so browsers with tracker blocking still show them.
THUMB_HOSTS = ("googleusercontent.com", "ggpht.com", "ytimg.com")


def create_app(ctx) -> FastAPI:
    app = FastAPI(title="YT Music Downloader", version=__version__, docs_url="/api/docs", redoc_url=None)
    env, db, engine, scheduler = ctx.env, ctx.db, ctx.engine, ctx.scheduler

    # ------------------------------------------------------------------ optional password
    if env.web_password:
        expected = f"{env.web_user}:{env.web_password}".encode("utf-8")

        @app.middleware("http")
        async def basic_auth(request: Request, call_next):
            if request.url.path == "/api/health":
                return await call_next(request)
            header = request.headers.get("authorization", "")
            if header.lower().startswith("basic "):
                try:
                    given = base64.b64decode(header[6:])
                except ValueError:
                    given = b""
                if secrets.compare_digest(given, expected):
                    return await call_next(request)
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="YT Music Downloader"'})

    # Every state-changing call must carry a custom header. Browsers only send custom headers
    # cross-origin after a CORS preflight (which this app never allows), so other websites
    # open in the same browser cannot trigger actions here.
    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        if (request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/")
                and request.headers.get("x-ytmd") != "1"):
            return JSONResponse({"detail": "Missing X-YTMD header"}, status_code=403)
        return await call_next(request)

    def bad_request(message: str) -> HTTPException:
        return HTTPException(status_code=400, detail=message)

    def auth_info() -> dict:
        mode = auth_mode(env)
        return {"connected": mode is not None, "mode": mode, "account": db.get_kv("account"),
                "error": db.get_kv("auth_error")}

    def after_connect(account: dict, how: str) -> dict:
        db.add_event("success", f"Connected YouTube Music account {account.get('name')} ({how})")
        first_time = db.get_kv("last_sync") is None
        try:
            engine.refresh_library()
        except Exception as exc:
            log.warning("Could not read playlists right after connecting: %s", exc)
        delay = FIRST_SYNC_DELAY if first_time else 5
        scheduler.postpone(delay)
        return {"account": account, "first_sync_in": delay}

    # ------------------------------------------------------------------ status
    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/status")
    def status():
        return {
            "version": __version__,
            "ytdlp_version": db.get_kv("ytdlp_version"),
            "state": engine.state.snapshot(),
            "paused": scheduler.paused,
            "next_run_at": scheduler.next_run_at,
            "cooldown_until": engine.cooldown_until,
            "last_sync": db.get_kv("last_sync"),
            "held_library_removal": db.get_kv("held_library_removal"),
            "stats": db.stats(),
            "disk": disk_usage(env.music_dir),
            "auth": auth_info(),
            "cookies_uploaded": env.cookies_file.exists(),
            "paths": {"music": str(env.music_dir), "config": str(env.config_dir)},
            "music_writable": os.access(env.music_dir, os.W_OK),
            "now": time.time(),
        }

    @app.get("/api/events")
    def events(limit: int = 50):
        return db.events(min(max(limit, 1), 500))

    @app.get("/api/logs")
    def logs(after: int = 0):
        return memory_handler.tail(after)

    # ------------------------------------------------------------------ sync control
    @app.post("/api/sync")
    def sync_now():
        if auth_mode(env) is None:
            raise bad_request("Connect your YouTube Music account first (Settings)")
        if not scheduler.trigger("manual"):
            raise HTTPException(status_code=409, detail="A sync is already running")
        return {"ok": True}

    @app.post("/api/sync/cancel")
    def cancel():
        engine.cancel()
        return {"ok": True}

    @app.post("/api/pause")
    def pause():
        scheduler.pause()
        return {"ok": True}

    @app.post("/api/resume")
    def resume():
        scheduler.resume()
        return {"ok": True}

    @app.post("/api/cooldown/clear")
    def clear_cooldown():
        engine.clear_cooldown()
        return {"ok": True}

    # ------------------------------------------------------------------ playlists
    @app.get("/api/playlists")
    def playlists():
        return db.playlists()

    @app.post("/api/playlists/refresh")
    def refresh_playlists():
        try:
            return engine.refresh_library()
        except AuthError as exc:
            raise bad_request(str(exc))

    @app.patch("/api/playlists/{playlist_id}")
    def set_playlist(playlist_id: str, payload: dict = Body(...)):
        if db.playlist(playlist_id) is None:
            raise HTTPException(status_code=404, detail="Unknown playlist")
        if "enabled" in payload:
            db.update_playlist(playlist_id, enabled=int(bool(payload["enabled"])))
        return db.playlist(playlist_id)

    @app.post("/api/playlists/bulk")
    def set_all_playlists(payload: dict = Body(...)):
        enabled = int(bool(payload.get("enabled")))
        for p in db.playlists():
            db.update_playlist(p["id"], enabled=enabled)
        return {"ok": True}

    @app.post("/api/library/approve-removal")
    def approve_library_removal():
        engine.approve_library_removal()
        scheduler.trigger("removal approved")
        return {"ok": True}

    @app.post("/api/playlists/{playlist_id}/approve")
    def approve_removals(playlist_id: str):
        if db.playlist(playlist_id) is None:
            raise HTTPException(status_code=404, detail="Unknown playlist")
        db.update_playlist(playlist_id, approve_removals=1)
        scheduler.trigger("removal approved")
        return {"ok": True}

    # ------------------------------------------------------------------ tracks
    @app.get("/api/tracks")
    def tracks(q: str = "", status: str = "", playlist: str = "", offset: int = 0, limit: int = 100):
        if status and status not in dbm.STATUSES:
            raise bad_request("Unknown status")
        total, rows = db.search_tracks(q.strip(), status, playlist, max(offset, 0), min(max(limit, 1), 500))
        return {"total": total, "items": rows}

    @app.post("/api/tracks/{video_id}/retry")
    def retry_track(video_id: str):
        t = db.track(video_id)
        if t is None:
            raise HTTPException(status_code=404, detail="Unknown track")
        if t["status"] in (dbm.FAILED, dbm.PENDING):
            db.update_track(video_id, status=dbm.PENDING, attempts=0, next_attempt_at=None, last_error=None)
        return {"ok": True}

    @app.post("/api/tracks/retry-failed")
    def retry_failed():
        for t in db.tracks_by_status(dbm.FAILED):
            db.update_track(t["video_id"], status=dbm.PENDING, attempts=0, next_attempt_at=None, last_error=None)
        return {"ok": True}

    # ------------------------------------------------------------------ thumbnails
    thumb_dir = env.config_dir / "cache" / "thumbs"

    @app.get("/api/thumb")
    def thumb(url: str):
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not any(host == h or host.endswith("." + h) for h in THUMB_HOSTS):
            raise HTTPException(status_code=400, detail="Not a YouTube image")
        path = thumb_dir / f"{hashlib.sha1(url.encode()).hexdigest()}.img"
        if not path.exists():
            try:
                r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
                r.raise_for_status()
            except requests.RequestException:
                raise HTTPException(status_code=404, detail="Image not available")
            thumb_dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(r.content)
            tmp.replace(path)
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=604800"})

    # ------------------------------------------------------------------ duplicates
    @app.get("/api/duplicates")
    def duplicates():
        return find_duplicates(db)

    @app.post("/api/duplicates/remove")
    def remove_duplicate(payload: dict = Body(...)):
        playlist_id = str(payload.get("playlist_id", ""))
        video_id = str(payload.get("video_id", ""))
        set_video_id = str(payload.get("set_video_id", ""))
        p = db.playlist(playlist_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Unknown playlist")
        if not p["owned"]:
            raise bad_request("Only playlists you own can be edited - remove it in the YouTube Music app")
        entry = next((e for e in db.playlist_entries(playlist_id)
                      if e["video_id"] == video_id and e["set_video_id"] == set_video_id), None)
        if entry is None or not set_video_id:
            raise bad_request("That entry is not in the playlist (any more) - run a sync and try again")
        try:
            ctx.client().remove_from_playlist(playlist_id, video_id, set_video_id)
        except Exception as exc:
            raise bad_request(f"YouTube Music refused the change: {exc}")
        db.remove_playlist_entry(playlist_id, set_video_id)
        db.add_event("info", f"Removed duplicate '{entry['title']}' from '{p['title']}' on YouTube Music")
        scheduler.trigger("duplicate removed")
        return {"ok": True}

    # ------------------------------------------------------------------ trash
    @app.get("/api/trash")
    def trash():
        items = engine.trash.items()
        retention = ctx.settings.get().trash_retention_days
        for i in items:
            i["expires_at"] = i["trashed_at"] + retention * 86400
        return {"items": items, "retention_days": retention}

    @app.delete("/api/trash")
    def empty_trash():
        return {"removed": engine.trash.empty()}

    @app.post("/api/trash/delete")
    def delete_from_trash(payload: dict = Body(...)):
        try:
            ok = engine.trash.delete(str(payload.get("path", "")))
        except ValueError as exc:
            raise bad_request(str(exc))
        return {"ok": ok}

    # ------------------------------------------------------------------ settings
    @app.get("/api/settings")
    def get_settings():
        data = ctx.settings.get().model_dump()
        if data["jellyfin_api_key"]:
            data["jellyfin_api_key"] = SECRET_MASK
        return data

    @app.put("/api/settings")
    def put_settings(payload: dict = Body(...)):
        if payload.get("jellyfin_api_key") == SECRET_MASK:
            payload.pop("jellyfin_api_key")
        known = set(Settings.model_fields)
        changes = {k: v for k, v in payload.items() if k in known}
        old = ctx.settings.get()
        try:
            new = ctx.settings.update(changes)
        except ValidationError as exc:
            first = exc.errors()[0]
            raise bad_request(f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}")
        if new.sync_interval_minutes != old.sync_interval_minutes:
            scheduler.reschedule()
        if (new.layout, new.playlists_dir) != (old.layout, old.playlists_dir) and auth_mode(env):
            scheduler.trigger("library layout changed")  # reorganize the files right away
        log.info("Settings saved")
        return get_settings()

    # ------------------------------------------------------------------ account
    @app.get("/api/auth")
    def get_auth():
        return auth_info()

    @app.post("/api/auth")
    def connect(payload: dict = Body(...)):
        backups = {f: f.read_bytes() for f in (env.auth_file,) if f.exists()}
        try:
            save_browser_auth(str(payload.get("headers", "")), env.auth_file)
            account = ctx.verify_account()
        except (ValueError, AuthError) as exc:
            env.auth_file.unlink(missing_ok=True)
            for f, data in backups.items():
                f.write_bytes(data)
            raise bad_request(str(exc))
        for f in (env.oauth_file, env.oauth_client_file):
            f.unlink(missing_ok=True)
        return after_connect(account, "browser session")

    @app.post("/api/auth/oauth/start")
    def oauth_start(payload: dict = Body(...)):
        try:
            ctx.device_login = DeviceLogin(str(payload.get("client_id", "")), str(payload.get("client_secret", "")))
        except ValueError as exc:
            raise bad_request(str(exc))
        dl = ctx.device_login
        return {"user_code": dl.user_code, "verification_url": dl.verification_url, "interval": dl.interval,
                "expires_at": dl.expires_at}

    @app.post("/api/auth/oauth/poll")
    def oauth_poll():
        dl = ctx.device_login
        if dl is None:
            raise bad_request("No sign-in in progress")
        try:
            token = dl.poll()
        except Exception as exc:
            ctx.device_login = None
            raise bad_request(str(exc))
        if token is None:
            return {"status": "pending"}
        ctx.device_login = None
        backups = {f: f.read_bytes() for f in (env.auth_file, env.oauth_file, env.oauth_client_file) if f.exists()}
        save_oauth(env, token, dl.client)
        try:
            account = ctx.verify_account()
        except AuthError as exc:
            disconnect(env)
            for f, data in backups.items():
                f.write_bytes(data)
            raise bad_request(str(exc))
        return {"status": "connected", **after_connect(account, "Google sign-in")}

    @app.delete("/api/auth")
    def disconnect_account():
        disconnect(env)
        db.set_kv("account", None)
        db.set_kv("auth_error", None)
        db.add_event("info", "YouTube Music account disconnected")
        return {"ok": True}

    @app.post("/api/cookies")
    def upload_cookies(payload: dict = Body(...)):
        text = str(payload.get("text", "")).strip()
        if "\t" not in text or ("youtube.com" not in text and "google.com" not in text):
            raise bad_request("This does not look like a Netscape cookies.txt export for youtube.com")
        if not text.startswith("# "):
            text = "# Netscape HTTP Cookie File\n" + text
        write_private(env.cookies_file, text + "\n")
        return {"ok": True}

    @app.delete("/api/cookies")
    def delete_cookies():
        env.cookies_file.unlink(missing_ok=True)
        return {"ok": True}

    # ------------------------------------------------------------------ integrations
    @app.post("/api/jellyfin/test")
    def jellyfin_test(payload: dict = Body(...)):
        url = str(payload.get("url") or ctx.settings.get().jellyfin_url).strip()
        key = str(payload.get("api_key") or "")
        if not key or key == SECRET_MASK:
            key = ctx.settings.get().jellyfin_api_key
        if not url or not key:
            raise bad_request("Enter the Jellyfin URL and an API key")
        try:
            return jellyfin.test_connection(url, key)
        except Exception as exc:
            raise bad_request(f"Could not reach Jellyfin: {exc}")

    @app.post("/api/ytdlp/update")
    def ytdlp_update():
        result = update_ytdlp()
        db.set_kv("ytdlp_version", result.get("after"))
        db.set_kv("ytdlp_last_check", time.time())
        if result["error"]:
            raise bad_request(result["error"])
        return result

    # ------------------------------------------------------------------ static UI
    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8").replace("__VERSION__", __version__)

    @app.get("/", include_in_schema=False)
    def index():
        # Asset URLs carry the version, so browsers fetch fresh JS/CSS after an update.
        return HTMLResponse(index_html, headers={"Cache-Control": "no-cache"})

    return app
