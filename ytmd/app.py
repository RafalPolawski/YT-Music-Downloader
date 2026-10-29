"""Process entry point: wires settings, database, sync engine, scheduler and web server."""

from __future__ import annotations

import logging
import os
import threading

import uvicorn

from ytmd import __version__
from ytmd.config import Environment, SettingsStore
from ytmd.db import Database
from ytmd.downloader import ytdlp_version
from ytmd.logs import purge_logs, setup_logging
from ytmd.scheduler import Scheduler
from ytmd.sync import SyncEngine
from ytmd.updater import Updater
from ytmd.ytmusic import AuthError, YTMusicClient, auth_mode, make_client

log = logging.getLogger("ytmd")


class AppContext:
    """Everything the web layer needs, bundled together."""

    def __init__(self, env: Environment, engine_kwargs: dict | None = None):
        self.env = env
        env.ensure_dirs()
        self.settings = SettingsStore(env.settings_file)
        if not env.settings_file.exists():
            self.settings.save()
        self.db = Database(env.db_file)
        self.engine = SyncEngine(env, self.settings, self.db, **(engine_kwargs or {}))
        self.updater = Updater(self.db)
        self.scheduler = Scheduler(self.engine, self.settings, self.db, self.updater,
                                   auth_ready=lambda: auth_mode(self.env) is not None)
        self.device_login = None  # pending OAuth sign-in started from the web UI

    def client(self) -> YTMusicClient:
        return make_client(self.env, self.settings.get().brand_account_id)

    def verify_account(self) -> dict:
        """Check the saved credentials and remember who we are logged in as."""
        try:
            account = self.client().account_info()
        except AuthError as exc:
            self.db.set_kv("auth_error", str(exc))
            raise
        self.db.set_kv("account", account)
        self.db.set_kv("auth_error", None)
        return account

    def start(self) -> None:
        if not os.access(self.env.music_dir, os.W_OK):
            log.error("The music folder %s is not writable by uid %s - set PUID/PGID to the owner of that folder",
                      self.env.music_dir, os.getuid())
        self.db.set_kv("ytdlp_version", ytdlp_version())
        purge_logs(self.env.log_dir, self.settings.get().log_retention_days)
        if auth_mode(self.env):
            threading.Thread(target=self._verify_quietly, daemon=True).start()
        self.scheduler.start()

    def _verify_quietly(self) -> None:
        try:
            account = self.verify_account()
            log.info("Connected to YouTube Music as %s", account.get("name"))
        except AuthError as exc:
            log.error("%s", exc)

    def stop(self) -> None:
        self.scheduler.stop()
        self.db.close()


def main() -> None:
    env = Environment.from_env()
    env.ensure_dirs()
    setup_logging(env.log_dir, env.log_level)
    log.info("YT Music Downloader %s starting (music: %s, config: %s)", __version__, env.music_dir, env.config_dir)

    from ytmd.web.api import create_app

    ctx = AppContext(env)
    ctx.start()
    app = create_app(ctx)
    config = uvicorn.Config(app, host=env.host, port=env.port, log_config=None, access_log=False)
    server = uvicorn.Server(config)  # handles SIGINT/SIGTERM itself
    try:
        server.run()
    finally:
        log.info("Shutting down")
        ctx.stop()
