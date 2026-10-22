# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-10-22

First release of the rewritten app: a self-hosted service with a web UI that keeps your
YouTube Music playlists mirrored to a folder on your disk.

### Added

- Continuous sync with YouTube Music as the source of truth: additions, removals and
  re-ordering are applied on a schedule (hourly by default).
- One `.m3u8` per playlist in YouTube Music order with relative paths — works with Jellyfin,
  Navidrome and apps such as Symfonium through your server.
- A real music library: every track stored once as `Artist/Album/NN Title`, tagged with album
  artist, year and track number, embedded cover art, `cover.jpg` per album and lyrics
  (synced `.lrc` when available).
- Album versions instead of music videos; Opus or M4A without re-encoding, MP3 optional.
- Trash with retention and automatic restore when a track comes back; safety nets that hold
  mass removals (half a playlist, or several playlists at once) until approved.
- Web UI: dashboard with live progress, playlist picker, track browser with retry, trash,
  logs and all settings; dark and light theme, works on phones.
- Account connection from the UI: browser session (copied headers, cURL or an old
  `headers.json`) or Google sign-in with a device code (OAuth).
- Resilient downloads: retries with back-off, automatic pause when YouTube rate-limits,
  long queues split into runs, daily yt-dlp self-update.
- Docker image for amd64 and arm64 (Apple Silicon) with `PUID`/`PGID`, health check and an
  optional password for the web UI; optional Jellyfin library refresh after each sync.

### Changed

- Replaces the previous one-shot `sync.py` script. Files are organised by artist and album
  instead of one folder per playlist, so start with an empty music folder. The old
  `headers.json` can be pasted into the new UI to connect your account.

[1.0.0]: https://github.com/RafalPolawski/YT-Music-Downloader/releases/tag/v1.0.0
