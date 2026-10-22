"""Audio tags, cover art and lyrics for opus / m4a / mp3 files (via mutagen)."""

from __future__ import annotations

import base64
import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests
from mutagen import File as MutagenFile
from mutagen.flac import Picture
from mutagen.id3 import APIC, ID3, TALB, TDRC, TIT2, TPE1, TPE2, TRCK, TXXX, USLT, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.oggopus import OggOpus
from PIL import Image

log = logging.getLogger(__name__)

VIDEO_ID_TAG = "YTMUSIC_VIDEO_ID"
_MP4_VIDEO_ID = f"----:com.apple.iTunes:{VIDEO_ID_TAG}"


@dataclass
class TrackTags:
    title: str
    artists: list[str] = field(default_factory=list)
    album: str | None = None
    album_artists: list[str] = field(default_factory=list)
    year: str | None = None
    track_number: int | None = None
    track_total: int | None = None
    video_id: str | None = None
    lyrics: str | None = None


# ---------------------------------------------------------------------- images
def image_urls(url: str | None, size: int) -> list[str]:
    """Candidate URLs for the largest version of a YT Music thumbnail."""
    if not url:
        return []
    if "googleusercontent.com" in url or "ggpht.com" in url:
        base = re.sub(r"=[^/=]*$", "", url)
        return [f"{base}=w{size}-h{size}-l90-rj", url]
    m = re.search(r"i\.ytimg\.com/vi(?:_webp)?/([\w-]+)/", url)
    if m:
        vid = m.group(1)
        return [f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"]
    return [url]


def fetch_image(url: str | None, size: int) -> bytes | None:
    for candidate in image_urls(url, size):
        try:
            r = requests.get(candidate, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200 and len(r.content) > 1000:
                return r.content
        except requests.RequestException as exc:
            log.debug("Cover download failed for %s: %s", candidate, exc)
    return None


def square_jpeg(data: bytes, size: int) -> bytes | None:
    """Center-crop video thumbnails to a square and re-encode as JPEG."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
            w, h = img.size
            if abs(w - h) > min(w, h) * 0.05:
                side = min(w, h)
                left, top = (w - side) // 2, (h - side) // 2
                img = img.crop((left, top, left + side, top + side))
            if img.width > size:
                img = img.resize((size, size), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90, optimize=True)
            return buf.getvalue()
    except Exception as exc:
        log.debug("Could not process cover image: %s", exc)
        return None


# ---------------------------------------------------------------------- lyrics
def to_lrc(lines: list[tuple[int, str]], tags: TrackTags | None = None) -> str:
    out = []
    if tags:
        out.append(f"[ti:{tags.title}]")
        if tags.artists:
            out.append(f"[ar:{', '.join(tags.artists)}]")
        if tags.album:
            out.append(f"[al:{tags.album}]")
    for ms, text in lines:
        minutes, rest = divmod(max(ms, 0), 60000)
        out.append(f"[{minutes:02d}:{rest / 1000:05.2f}]{text}")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------- tagging
def write_tags(path: Path, tags: TrackTags, cover_jpeg: bytes | None) -> None:
    suffix = path.suffix.lower()
    if suffix in (".opus", ".ogg"):
        _tag_vorbis(path, tags, cover_jpeg)
    elif suffix in (".m4a", ".mp4", ".aac"):
        _tag_mp4(path, tags, cover_jpeg)
    elif suffix == ".mp3":
        _tag_id3(path, tags, cover_jpeg)
    else:
        raise ValueError(f"Unsupported audio format: {suffix}")


def _track_str(tags: TrackTags) -> str | None:
    if not tags.track_number:
        return None
    return f"{tags.track_number}/{tags.track_total}" if tags.track_total else str(tags.track_number)


def _tag_vorbis(path: Path, tags: TrackTags, cover: bytes | None) -> None:
    audio = OggOpus(path)
    audio.delete()
    audio["title"] = tags.title
    if tags.artists:
        audio["artist"] = tags.artists
    if tags.album:
        audio["album"] = tags.album
    if tags.album_artists:
        audio["albumartist"] = tags.album_artists
    if tags.year:
        audio["date"] = tags.year
    if tags.track_number:
        audio["tracknumber"] = str(tags.track_number)
        if tags.track_total:
            audio["tracktotal"] = str(tags.track_total)
    if tags.lyrics:
        audio["lyrics"] = tags.lyrics
    if tags.video_id:
        audio[VIDEO_ID_TAG.lower()] = tags.video_id
    if cover:
        pic = Picture()
        pic.type = 3
        pic.mime = "image/jpeg"
        pic.desc = "Cover"
        pic.data = cover
        with Image.open(io.BytesIO(cover)) as img:
            pic.width, pic.height = img.size
        pic.depth = 24
        audio["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
    audio.save()


def _tag_mp4(path: Path, tags: TrackTags, cover: bytes | None) -> None:
    audio = MP4(path)
    audio.delete()
    audio["\xa9nam"] = [tags.title]
    if tags.artists:
        audio["\xa9ART"] = ["; ".join(tags.artists)]
    if tags.album:
        audio["\xa9alb"] = [tags.album]
    if tags.album_artists:
        audio["aART"] = ["; ".join(tags.album_artists)]
    if tags.year:
        audio["\xa9day"] = [tags.year]
    if tags.track_number:
        audio["trkn"] = [(tags.track_number, tags.track_total or 0)]
    if tags.lyrics:
        audio["\xa9lyr"] = [tags.lyrics]
    if tags.video_id:
        audio[_MP4_VIDEO_ID] = [MP4FreeForm(tags.video_id.encode("utf-8"))]
    if cover:
        audio["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
    audio.save()


def _tag_id3(path: Path, tags: TrackTags, cover: bytes | None) -> None:
    try:
        audio = ID3(path)
        audio.delete(path)
    except ID3NoHeaderError:
        pass
    audio = ID3()
    audio.add(TIT2(encoding=3, text=tags.title))
    if tags.artists:
        audio.add(TPE1(encoding=3, text=tags.artists))
    if tags.album:
        audio.add(TALB(encoding=3, text=tags.album))
    if tags.album_artists:
        audio.add(TPE2(encoding=3, text=tags.album_artists))
    if tags.year:
        audio.add(TDRC(encoding=3, text=tags.year))
    if (trck := _track_str(tags)):
        audio.add(TRCK(encoding=3, text=trck))
    if tags.lyrics:
        audio.add(USLT(encoding=3, lang="eng", desc="", text=tags.lyrics))
    if tags.video_id:
        audio.add(TXXX(encoding=3, desc=VIDEO_ID_TAG, text=tags.video_id))
    if cover:
        audio.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))
    audio.save(path, v2_version=4)


# ---------------------------------------------------------------------- inspection
def audio_info(path: Path) -> dict | None:
    """Duration and embedded video id of a file, or None if it is not readable audio."""
    try:
        f = MutagenFile(path)
    except Exception:
        return None
    if f is None or not getattr(f, "info", None) or not getattr(f.info, "length", 0):
        return None
    video_id = None
    try:
        tags = f.tags or {}
        if isinstance(f, MP4):
            raw = tags.get(_MP4_VIDEO_ID)
            video_id = bytes(raw[0]).decode() if raw else None
        elif hasattr(tags, "getall"):
            frames = tags.getall(f"TXXX:{VIDEO_ID_TAG}")
            video_id = str(frames[0].text[0]) if frames else None
        else:
            values = tags.get(VIDEO_ID_TAG.lower()) or tags.get(VIDEO_ID_TAG)
            video_id = values[0] if values else None
    except Exception:
        pass
    return {"length": f.info.length, "video_id": video_id}
