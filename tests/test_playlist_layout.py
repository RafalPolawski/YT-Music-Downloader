"""The default "playlist" layout: one folder per YouTube Music playlist."""

from __future__ import annotations

import os

import pytest

from tests.conftest import track
from ytmd.db import Database
from ytmd.duplicates import find_duplicates, song_key


@pytest.fixture
def h(harness):
    harness.settings.update({"layout": "playlist"})
    harness.client.add_playlist("PL1", "Road Trip", [
        track("a1", "Digital Love", ["Daft Punk"], "Discovery", "MPREd"),
        track("b1", "Kong", ["Bonobo"]),
    ])
    harness.client.add_playlist("PL2", "Chill", [
        track("b1", "Kong", ["Bonobo"]),
        track("c1", "Teardrop", ["Massive Attack"]),
    ])
    harness.client.albums["MPREd"] = {"title": "Discovery", "artists": ["Daft Punk"], "year": "2001",
                                      "track_count": 14, "thumbnail": None,
                                      "tracks": [{"video_id": "a1", "title": "Digital Love", "track_number": 3}]}
    return harness


def test_every_playlist_gets_its_own_folder(h):
    s = h.sync()
    assert s.downloaded == 3
    assert sorted(h.downloader.calls) == ["a1", "b1", "c1"]  # Kong is downloaded only once
    assert h.audio_files() == {"Road Trip/Digital Love.opus", "Road Trip/Kong.opus",
                               "Chill/Kong.opus", "Chill/Teardrop.opus"}
    assert h.m3u8("Road Trip") == ["../Road Trip/Digital Love.opus", "../Road Trip/Kong.opus"]
    assert h.m3u8("Chill") == ["../Chill/Kong.opus", "../Chill/Teardrop.opus"]
    assert (h.music / "Road Trip/cover.jpg").read_bytes()[:2] == b"\xff\xd8"  # generated playlist cover


def test_copy_shares_the_downloaded_file(h):
    h.sync()
    a, b = h.music / "Road Trip/Kong.opus", h.music / "Chill/Kong.opus"
    assert a.read_bytes() == b.read_bytes()
    assert os.stat(a).st_ino == os.stat(b).st_ino  # hard link where the file system allows it


def test_removing_a_song_from_one_playlist_only_removes_that_folder_copy(h):
    h.sync()
    h.client.tracks["PL2"] = [track("c1", "Teardrop", ["Massive Attack"])]
    s = h.sync()
    assert s.removed == 0  # the song is still in Road Trip
    assert "Chill/Kong.opus" not in h.audio_files()
    assert "Road Trip/Kong.opus" in h.audio_files()
    assert h.m3u8("Chill") == ["../Chill/Teardrop.opus"]


def test_removing_it_from_the_first_playlist_moves_the_file_to_the_next(h):
    h.sync()
    h.client.tracks["PL1"] = [track("a1", "Digital Love", ["Daft Punk"], "Discovery", "MPREd")]
    h.downloader.calls.clear()
    h.sync()
    assert h.downloader.calls == []
    assert h.audio_files() == {"Road Trip/Digital Love.opus", "Chill/Kong.opus", "Chill/Teardrop.opus"}
    assert h.db.track("b1")["path"] == "Chill/Kong.opus"
    assert h.db.copies() == []


def test_song_leaving_every_playlist_goes_to_the_trash_with_its_copies(h):
    h.sync()
    h.client.tracks["PL1"] = [track("a1", "Digital Love", ["Daft Punk"], "Discovery", "MPREd")]
    h.client.tracks["PL2"] = [track("c1", "Teardrop", ["Massive Attack"])]
    s = h.sync()
    assert s.removed == 1
    assert not any("Kong" in f for f in h.audio_files())
    assert (h.env.trash_dir / "Road Trip/Kong.opus").exists()


def test_same_title_in_one_folder_gets_the_artist_added(h):
    h.client.tracks["PL1"].append(track("x9", "Kong", ["Someone Else"]))
    h.sync()
    assert {"Road Trip/Kong.opus", "Road Trip/Kong (Someone Else).opus"} <= h.audio_files()


def test_renamed_playlist_renames_its_folder(h):
    h.sync()
    h.client.playlists[1]["title"] = "Evening"
    h.sync()
    assert h.audio_files() == {"Road Trip/Digital Love.opus", "Road Trip/Kong.opus",
                               "Evening/Kong.opus", "Evening/Teardrop.opus"}
    assert not (h.music / "Chill").exists()


def test_switching_layouts_rebuilds_the_library(h):
    h.sync()
    h.settings.update({"layout": "artist_album"})
    h.sync()
    assert h.audio_files() == {"Daft Punk/Discovery/03 Digital Love.opus", "Bonobo/Singles/Kong.opus",
                               "Massive Attack/Singles/Teardrop.opus"}
    assert h.db.copies() == []
    h.settings.update({"layout": "playlist"})
    h.sync()
    assert h.audio_files() == {"Road Trip/Digital Love.opus", "Road Trip/Kong.opus",
                               "Chill/Kong.opus", "Chill/Teardrop.opus"}
    assert h.downloader.calls.count("b1") == 1


def test_deleted_copy_is_recreated(h):
    h.sync()
    (h.music / "Chill/Kong.opus").unlink()
    h.sync()
    assert "Chill/Kong.opus" in h.audio_files()


# ---------------------------------------------------------------------- duplicates
def test_song_key_ignores_video_decorations():
    assert song_key("Kong (Official Video)", ["Bonobo"]) == song_key("Kong", ["Bonobo"])
    assert song_key("Kong [HD]", ["Bonobo"]) == song_key("kong", ["Bonobo", "X"])
    assert song_key("Kong (Live)", ["Bonobo"]) != song_key("Kong", ["Bonobo"])


def test_duplicates_are_found_within_a_playlist(h):
    h.client.tracks["PL1"] += [track("b1", "Kong", ["Bonobo"]),
                               track("v1", "Digital Love (Official Video)", ["Daft Punk"],
                                     video_type="MUSIC_VIDEO_TYPE_OMV")]
    h.settings.update({"prefer_album_version": False})
    h.sync()
    groups = {(g["playlist"], g["kind"]): g for g in find_duplicates(h.db)}
    assert set(groups) == {("Road Trip", "same"), ("Road Trip", "similar")}
    same = groups[("Road Trip", "same")]
    assert [i["position"] for i in same["items"]] == [2, 3] and same["items"][0]["keep"]
    similar = groups[("Road Trip", "similar")]
    assert [i["video_id"] for i in similar["items"] if i["keep"]] == ["a1"]  # the album version
    assert "Chill" not in {g["playlist"] for g in groups.values()}  # across playlists is fine


def test_old_databases_are_migrated(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE playlist_tracks (playlist_id TEXT, position INTEGER, video_id TEXT,"
                " PRIMARY KEY (playlist_id, position))")
    con.commit()
    con.close()
    db = Database(path)
    db.set_playlist_tracks("PL", [("v", "s1")])
    assert db._one("SELECT set_video_id FROM playlist_tracks")["set_video_id"] == "s1"
    db.close()


def test_playlist_cover_is_not_carried_into_album_folders(h):
    h.sync()
    h.settings.update({"layout": "artist_album"})
    h.sync()
    from PIL import Image
    cover = h.music / "Daft Punk/Discovery/cover.jpg"
    assert cover.exists()  # written from the album art, not the playlist cover
    assert Image.open(cover).size == (360, 360)  # the fake album art, cropped square
    assert not (h.music / "Road Trip").exists()


def test_no_generated_covers_when_disabled(h):
    h.settings.update({"write_album_covers": False})
    h.sync()
    assert not (h.music / "Road Trip/cover.jpg").exists()


def test_generated_cover_handles_polish_and_emoji():
    import io

    from PIL import Image

    from ytmd.covers import playlist_cover
    img = Image.open(io.BytesIO(playlist_cover("Ameryka Południowa ☕", size=300)))
    assert img.size == (300, 300) and img.format == "JPEG"


def test_music_video_with_an_unrelated_counterpart_is_kept(harness):
    h = harness
    h.client.add_playlist("PL1", "Videos", [track("omv1", "Hit (Official Video)", ["Star"],
                                                  video_type="MUSIC_VIDEO_TYPE_OMV")])
    h.client.watch["omv1"] = {"lyrics_id": None,
                              "counterpart": track("atv9", "Hit (Remix)", ["Someone"], "Club Hits 2020", "MPREc")}
    h.sync()
    assert h.downloader.calls == ["omv1"]
    assert h.db.track("omv1")["album"] is None
