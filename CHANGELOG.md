# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- A song whose cover image failed to download stayed without embedded art for good, and one failure
  left every later song of the same album without art in that sync. Cover downloads are now retried, and
  files without embedded art get it at a later sync (up to 50 files per sync, nothing is downloaded again).
- Covers of music videos could have black bars above and below the picture when YouTube has no
  high-resolution thumbnail for the video. The bars are cut off now.

## [1.3.3] - 2026-11-03

### Fixed

- Artist/Album layout: songs without an album on YouTube Music (singles, music videos, uploads) were
  collected in one `Artist/Singles` folder per artist, which Jellyfin showed as an album called "Singles"
  without a cover. Each such song now gets a folder of its own (`Artist/Title/Title.opus`) with its cover,
  and its album tag is the song title, the way a single release is tagged. Existing files are moved and
  re-tagged at the next sync, nothing is downloaded again.
- Album folders that are missing their `cover.jpg` (e.g. after a failed image download) get it at the next
  sync.

## [1.3.2] - 2026-11-03

### Changed

- Updated FastAPI to 0.162.0 and Deno, the JavaScript runtime yt-dlp uses for YouTube, to 2.9.8.
- CI now runs the tests on the same Python version as the Docker image, so a base image update is tested
  before it is released.

## [1.3.1] - 2026-11-03

### Fixed

- Every song in Jellyfin (and Symfonium) showed its playlist's generated cover instead of its own: Jellyfin
  gives a folder's `cover.jpg` to every song in it. Playlist folders no longer get a generated cover, and
  the ones 1.2.0–1.3.0 wrote are deleted at the next sync (a `cover.jpg` of your own is kept). Afterwards
  refresh the music library in Jellyfin with *Replace existing images* to drop the cached pictures.

### Changed

- Settings and README recommend the Artist/Album/Track layout for Jellyfin and Plex: they build albums
  from folders, so playlist folders show up there as albums named after one of their songs.

## [1.3.0] - 2026-10-29

### Added

- **Cleanup** page (replaces *Duplicates*): besides duplicates it lists songs that can no longer be
  downloaded — deleted, private, region-blocked or greyed out on YouTube Music — per playlist. Remove them
  one by one or all at once with a click; nothing is removed automatically. Works for your own playlists
  and Liked Music (the like is taken back).
- Links to YouTube Music everywhere: every playlist name on the Tracks page opens the song inside that
  playlist, trash entries link to the song, and Cleanup entries open in their playlist.
- Logs survive restarts and updates: the Logs page loads its history from disk, log files are kept one
  per day and can be downloaded from the Logs page.
- Setting *Keep log files for* (Settings → Advanced, default: forever) with the space logs currently use.

### Fixed

- The account row in Settings no longer overflows the screen on phones.

## [1.2.1] - 2026-10-29

### Fixed

- Tracks, Duplicates and Trash pages no longer overflow the screen on phones; wide tables scroll inside
  their card and duplicate entries stack vertically.

### Changed

- README: a tour of every page with new screenshots, a highlights overview and a short FAQ.

## [1.2.0] - 2026-10-29

### Added

- Generated `cover.jpg` with the playlist name in every playlist folder (playlist layout), so Jellyfin shows
  a meaningful picture for the folder instead of a random song's cover.
- Thumbnails are loaded through the app and cached, so browsers with tracker blocking (e.g. Brave
  Shields) show playlist and track images.

### Changed

- Playlists page is a compact list instead of large cards.
- Duplicates: every entry can be removed; the suggestion (keep the first entry or the album version) is
  shown as a label instead of being the only option.
- A music video is only swapped for its album version when YouTube Music's counterpart is really the
  same song (title and artist match), which avoids remixes and compilation covers.
- Switching to the Artist/Album layout now writes the album `cover.jpg` for files that were moved there.

## [1.1.0] - 2026-10-23

### Added

- New default folder layout "Playlist / Track": one folder per YouTube Music playlist with files named
  after the song title. A song in several playlists is downloaded once and hard-linked (or copied) into
  every playlist folder; removing it from one playlist removes only that folder's file.
- Duplicates page listing songs that appear twice in the same playlist (same video, or video and album
  version), with a clearly marked button to remove an entry on YouTube Music.

### Changed

- Saving a different folder layout reorganizes the library right away instead of at the next scheduled
  sync. Existing installations keep their current layout until it is changed in Settings.

## [1.0.1] - 2026-10-23

### Changed

- The Docker image now ships ffmpeg 9.0.2 (was 8.1.2) and newer GitHub Actions are used to
  build it.
- CI tests run against the same static ffmpeg as the image and no longer hang on apt mirrors.

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

[1.3.3]: https://github.com/RafalPolawski/YT-Music-Downloader/releases/tag/v1.3.3
[1.3.2]: https://github.com/RafalPolawski/YT-Music-Downloader/releases/tag/v1.3.2
[1.3.1]: https://github.com/RafalPolawski/YT-Music-Downloader/releases/tag/v1.3.1
[1.3.0]: https://github.com/RafalPolawski/YT-Music-Downloader/releases/tag/v1.3.0
[1.2.1]: https://github.com/RafalPolawski/YT-Music-Downloader/releases/tag/v1.2.1
[1.2.0]: https://github.com/RafalPolawski/YT-Music-Downloader/releases/tag/v1.2.0
[1.1.0]: https://github.com/RafalPolawski/YT-Music-Downloader/releases/tag/v1.1.0
[1.0.1]: https://github.com/RafalPolawski/YT-Music-Downloader/releases/tag/v1.0.1
[1.0.0]: https://github.com/RafalPolawski/YT-Music-Downloader/releases/tag/v1.0.0
