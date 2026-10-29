from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeClient, FakeDownloader, track
from ytmd.app import AppContext
from ytmd.config import Environment
from ytmd.downloader import TRANSIENT, UNAVAILABLE, DownloadError
from ytmd.web.api import SECRET_MASK, create_app


@pytest.fixture
def api(tmp_path, audio_samples, jpeg_bytes):
    def make(password=""):
        env = Environment(config_dir=tmp_path / "config", music_dir=tmp_path / "music", host="127.0.0.1",
                          port=0, web_user="admin", web_password=password, log_level="INFO")
        fake = FakeClient()
        fake.add_playlist("PL1", "Road Trip", [track("a1", "Song", ["Artist"])])
        ctx = AppContext(env, engine_kwargs={"client_factory": lambda s: fake,
                                             "download_fn": FakeDownloader(audio_samples),
                                             "image_fetcher": lambda u, s: jpeg_bytes})
        ctx.settings.update({"download_delay_seconds": 0})
        return ctx, TestClient(create_app(ctx), headers={"X-YTMD": "1"})

    return make


def test_status_and_static(api):
    ctx, c = api()
    assert c.get("/api/health").json() == {"ok": True}
    status = c.get("/api/status").json()
    assert status["auth"]["connected"] is False
    assert status["state"]["phase"] == "idle"
    assert "YT Music Downloader" in c.get("/").text


def test_settings_roundtrip_masks_secrets(api):
    ctx, c = api()
    r = c.put("/api/settings", json={"jellyfin_url": "http://jf:8096", "jellyfin_api_key": "secret",
                                     "audio_format": "m4a", "unknown": 1})
    assert r.status_code == 200
    assert r.json()["jellyfin_api_key"] == SECRET_MASK
    c.put("/api/settings", json={"jellyfin_api_key": SECRET_MASK, "layout": "flat"})
    assert ctx.settings.get().jellyfin_api_key == "secret"
    assert ctx.settings.get().layout == "flat"
    assert c.put("/api/settings", json={"audio_format": "flac"}).status_code == 400


def test_state_changes_need_the_csrf_header(api):
    ctx, c = api()
    assert c.post("/api/pause", headers={"X-YTMD": ""}).status_code == 403
    assert c.get("/api/status", headers={"X-YTMD": ""}).status_code == 200


def test_dangerous_ytdlp_args_are_rejected(api):
    ctx, c = api()
    assert c.put("/api/settings", json={"ytdlp_extra_args": "--exec 'rm -rf /'"}).status_code == 400
    assert c.put("/api/settings", json={"ytdlp_extra_args": "-o /tmp/x"}).status_code == 400
    ok = c.put("/api/settings", json={"ytdlp_extra_args": "--extractor-args youtube:player_client=tv --limit-rate=5M"})
    assert ok.status_code == 200


def test_sync_requires_account(api):
    _, c = api()
    assert c.post("/api/sync").status_code == 400


def test_playlists_and_tracks_flow(api):
    ctx, c = api()
    ctx.env.auth_file.write_text("{}")
    assert [p["id"] for p in c.post("/api/playlists/refresh").json()] == ["PL1"]
    ctx.engine.run("test")
    tracks = c.get("/api/tracks", params={"status": "downloaded"}).json()
    assert tracks["total"] == 1 and tracks["items"][0]["playlists"] == "Road Trip"
    assert tracks["items"][0]["playlist_list"] == [{"id": "PL1", "title": "Road Trip"}]
    assert c.patch("/api/playlists/PL1", json={"enabled": False}).json()["enabled"] == 0
    ctx.engine.run("test")
    trash = c.get("/api/trash").json()
    assert len(trash["items"]) == 1
    assert trash["items"][0]["video_id"] == "a1" and trash["items"][0]["title"] == "Song"
    assert c.delete("/api/trash").json()["removed"] == 1


def test_basic_auth_with_non_ascii_password(api):
    _, c = api(password="zażółć")
    token = base64.b64encode("admin:zażółć".encode()).decode()
    assert c.get("/api/status", headers={"Authorization": f"Basic {token}"}).status_code == 200
    wrong = base64.b64encode(b"admin:zazolc").decode()
    assert c.get("/api/status", headers={"Authorization": f"Basic {wrong}"}).status_code == 401


def test_static_assets_are_versioned(api):
    from ytmd import __version__
    _, c = api()
    html = c.get("/").text
    assert f"/static/app.js?v={__version__}" in html and "__VERSION__" not in html
    assert c.get(f"/static/app.js?v={__version__}").status_code == 200


def test_library_removal_approval(api):
    ctx, c = api()
    ctx.db.set_kv("held_library_removal", ["Old list"])
    assert c.get("/api/status").json()["held_library_removal"] == ["Old list"]
    assert c.post("/api/library/approve-removal").json() == {"ok": True}
    assert ctx.db.get_kv("approve_library_removal") is True


def test_basic_auth(api):
    _, c = api(password="pw")
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/health").status_code == 200
    token = base64.b64encode(b"admin:pw").decode()
    assert c.get("/api/status", headers={"Authorization": f"Basic {token}"}).status_code == 200


def test_connect_with_headers(api):
    ctx, c = api()
    ctx.client = lambda: FakeClient()
    r = c.post("/api/auth", json={"headers": "cookie: __Secure-3PAPISID=a; SAPISID=a\nx-goog-authuser: 0"})
    assert r.status_code == 200, r.text
    assert r.json()["account"]["name"] == "Tester"
    auth = c.get("/api/auth").json()
    assert auth["connected"] and auth["mode"] == "browser"
    assert c.delete("/api/auth").status_code == 200
    assert c.get("/api/auth").json()["connected"] is False


def test_oauth_device_flow(api, monkeypatch):
    from ytmd.web import api as api_module

    class FakeDeviceLogin:
        polls = 0

        def __init__(self, client_id, client_secret):
            if client_id != "id":
                raise ValueError("Google rejected the OAuth client")
            self.client = {"client_id": client_id, "client_secret": client_secret}
            self.user_code, self.verification_url, self.interval, self.expires_at = "ABC-DEF-GHI", "https://www.google.com/device", 5, 0

        def poll(self):
            FakeDeviceLogin.polls += 1
            if FakeDeviceLogin.polls < 2:
                return None
            return {"scope": "s", "token_type": "Bearer", "access_token": "a", "refresh_token": "r",
                    "expires_at": 0, "expires_in": 3600}

    monkeypatch.setattr(api_module, "DeviceLogin", FakeDeviceLogin)
    ctx, c = api()
    ctx.client = lambda: FakeClient()
    ctx.env.auth_file.write_text("{}")
    assert c.post("/api/auth/oauth/start", json={"client_id": "bad", "client_secret": "x"}).status_code == 400
    start = c.post("/api/auth/oauth/start", json={"client_id": "id", "client_secret": "secret"}).json()
    assert start["user_code"] == "ABC-DEF-GHI"
    assert c.post("/api/auth/oauth/poll").json() == {"status": "pending"}
    done = c.post("/api/auth/oauth/poll").json()
    assert done["status"] == "connected" and done["account"]["name"] == "Tester"
    assert c.get("/api/auth").json()["mode"] == "oauth"
    assert not ctx.env.auth_file.exists()  # switching methods replaces the old credentials


def test_duplicate_is_removed_on_youtube_music(api):
    ctx, c = api()
    ctx.env.auth_file.write_text("{}")
    fake = FakeClient()
    fake.add_playlist("PL1", "Road Trip", [track("a1", "Song", ["Artist"]), track("a1", "Song", ["Artist"])])
    fake.add_playlist("SAVED", "Someone's mix", [track("b1", "Other", ["X"]), track("b1", "Other", ["X"])],
                      owned=False)
    ctx.engine.client_factory = lambda s: fake
    ctx.client = lambda: fake
    ctx.engine.run("test")
    groups = c.get("/api/duplicates").json()
    road = next(g for g in groups if g["playlist"] == "Road Trip")
    saved = next(g for g in groups if g["playlist"] == "Someone's mix")
    assert road["editable"] and not saved["editable"]
    dup = road["items"][1]
    r = c.post("/api/playlist-entries/remove", json={"playlist_id": "PL1", "entries": [
        {"video_id": "a1", "set_video_id": dup["set_video_id"]}]})
    assert r.status_code == 200, r.text
    assert fake.removed == [("PL1", "a1", "PL1:1")]
    assert all(g["playlist"] != "Road Trip" for g in c.get("/api/duplicates").json())
    blocked = c.post("/api/playlist-entries/remove", json={"playlist_id": "SAVED", "entries": [
        {"video_id": "b1", "set_video_id": "SAVED:1"}]})
    assert blocked.status_code == 400


def test_unavailable_songs_are_found_and_removed(api, audio_samples):
    ctx, c = api()
    ctx.env.auth_file.write_text("{}")
    fake = FakeClient()
    fake.add_playlist("PL1", "Road Trip", [
        track("ok1", "Fine", ["A"]), track("grey1", "Greyed out", ["B"], available=False),
        track("dead1", "Deleted video", ["C"]), track("slow1", "Server hiccup", ["D"])])
    fake.add_playlist("LM", "Liked Music", [track("ok1", "Fine", ["A"]), track("grey2", "Gone", ["E"], available=False)],
                      owned=False)
    fake.add_playlist("SAVED", "Someone's mix", [track("grey3", "Hidden", ["F"], available=False)], owned=False)
    dl = FakeDownloader(audio_samples)
    dl.errors["dead1"] = DownloadError(UNAVAILABLE, "[youtube] dead1: Video unavailable. This video has been removed")
    dl.errors["slow1"] = DownloadError(TRANSIENT, "HTTP Error 503: Service Unavailable")
    ctx.engine.client_factory = lambda s: fake
    ctx.engine.download_fn = dl
    ctx.client = lambda: fake
    ctx.engine.run("test")
    ctx.db.update_playlist("LM", enabled=1)
    fake.tracks["PL1"][0] = track("ok1", "Fine", ["A"], available=False)  # greyed out after it was downloaded
    ctx.engine.run("test")

    groups = {g["playlist"]: g for g in c.get("/api/unavailable").json()}
    road = groups["Road Trip"]
    assert [(i["title"], i["position"]) for i in road["items"]] == [("Greyed out", 2), ("Deleted video", 3)]
    assert road["items"][1]["reason"] == "Video unavailable. This video has been removed" and road["editable"]
    assert groups["Liked Music"]["editable"] and not groups["Someone's mix"]["editable"]

    r = c.post("/api/playlist-entries/remove", json={"playlist_id": "PL1", "entries": [
        {"video_id": i["video_id"], "set_video_id": i["set_video_id"]} for i in road["items"]]})
    assert r.status_code == 200 and r.json()["removed"] == 2, r.text
    assert fake.removed == [("PL1", "grey1", "PL1:1"), ("PL1", "dead1", "PL1:2")]
    # Liked Music: the like is taken back, matched by video id alone
    r = c.post("/api/playlist-entries/remove", json={"playlist_id": "LM", "entries": [{"video_id": "grey2"}]})
    assert r.status_code == 200, r.text
    assert fake.removed[-1][:2] == ("LM", "grey2")
    assert [g["playlist"] for g in c.get("/api/unavailable").json()] == ["Someone's mix"]
    assert c.post("/api/playlist-entries/remove", json={"playlist_id": "SAVED", "entries": [
        {"video_id": "grey3", "set_video_id": "SAVED:0"}]}).status_code == 400
    assert c.post("/api/playlist-entries/remove", json={"playlist_id": "PL1", "entries": [
        {"video_id": "grey1", "set_video_id": "PL1:1"}]}).status_code == 400  # already gone
    assert c.post("/api/playlist-entries/remove", json={"playlist_id": "PL1", "entries": []}).status_code == 400
    assert c.post("/api/playlist-entries/remove", json={"playlist_id": "PL1", "entries": ["x"]}).status_code == 400


def test_layout_change_starts_a_sync(api):
    ctx, c = api()
    ctx.env.auth_file.write_text("{}")
    triggered = []
    ctx.scheduler.trigger = lambda reason="manual": triggered.append(reason) or True
    c.put("/api/settings", json={"layout": "artist_album"})
    c.put("/api/settings", json={"sync_interval_minutes": 30})
    assert triggered == ["library layout changed"]


def test_connect_rejects_bad_headers(api):
    ctx, c = api()
    r = c.post("/api/auth", json={"headers": "accept: */*"})
    assert r.status_code == 400
    assert not ctx.env.auth_file.exists()


def test_thumbnails_are_proxied_and_cached(api, monkeypatch, jpeg_bytes):
    from ytmd.web import api as api_module
    calls = []

    class Resp:
        content = jpeg_bytes

        def raise_for_status(self):
            pass

    monkeypatch.setattr(api_module.requests, "get", lambda url, **kw: calls.append(url) or Resp())
    _, c = api()
    url = "https://lh3.googleusercontent.com/abc=w120-h120"
    for _ in range(2):
        r = c.get("/api/thumb", params={"url": url})
        assert r.status_code == 200 and r.content == jpeg_bytes
    assert calls == [url]  # second request served from the cache
    assert c.get("/api/thumb", params={"url": "http://169.254.169.254/latest"}).status_code == 400
    assert c.get("/api/thumb", params={"url": "https://evil.example/googleusercontent.com.png"}).status_code == 400
