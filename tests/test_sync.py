from __future__ import annotations

import os
import time

from mutagen.oggopus import OggOpus

from tests.conftest import track
from ytmd.downloader import RATE_LIMITED, TRANSIENT, DownloadError


def basic_library(h):
    h.client.add_playlist("PL1", "Road Trip", [
        track("a1", "First Song", ["Alpha"], "Album A", "MPREa"),
        track("b1", "Second Song", ["Beta"]),
        track("c1", "Third Song", ["Gamma", "Delta"]),
    ])
    h.client.add_playlist("PL2", "Chill", [
        track("b1", "Second Song", ["Beta"]),
        track("d1", "Fourth Song", ["Delta"]),
    ])
    h.client.albums["MPREa"] = {
        "title": "Album A", "artists": ["Alpha"], "year": "2021", "track_count": 9,
        "thumbnail": "https://lh3.googleusercontent.com/album=w60-h60",
        "tracks": [{"video_id": "a1", "title": "First Song", "track_number": 4}],
    }


def test_first_sync_downloads_everything_and_writes_ordered_playlists(harness):
    h = harness
    basic_library(h)
    h.client.add_playlist("LM", "Liked Music", [track("z1", "Liked Only")], owned=False)

    s = h.sync()

    assert s.error is None
    assert s.downloaded == 4
    assert sorted(h.downloader.calls) == ["a1", "b1", "c1", "d1"]  # b1 only once, LM disabled by default
    assert h.audio_files() == {
        "Alpha/Album A/04 First Song.opus",
        "Beta/Singles/Second Song.opus",
        "Gamma/Singles/Third Song.opus",
        "Delta/Singles/Fourth Song.opus",
    }
    assert h.m3u8("Road Trip") == [
        "../Alpha/Album A/04 First Song.opus",
        "../Beta/Singles/Second Song.opus",
        "../Gamma/Singles/Third Song.opus",
    ]
    assert h.m3u8("Chill") == ["../Beta/Singles/Second Song.opus", "../Delta/Singles/Fourth Song.opus"]
    assert not (h.music / "Playlists" / "Liked Music.m3u8").exists()
    assert (h.music / "Alpha/Album A/cover.jpg").exists()

    tags = OggOpus(h.music / "Alpha/Album A/04 First Song.opus")
    assert tags["title"] == ["First Song"]
    assert tags["albumartist"] == ["Alpha"]
    assert tags["date"] == ["2021"]
    assert tags["tracknumber"] == ["4"]
    assert tags["tracktotal"] == ["9"]
    assert tags["ytmusic_video_id"] == ["a1"]
    assert "metadata_block_picture" in tags
    multi = OggOpus(h.music / "Gamma/Singles/Third Song.opus")
    assert multi["artist"] == ["Gamma", "Delta"]


def test_second_sync_is_a_no_op(harness):
    h = harness
    basic_library(h)
    h.sync()
    h.downloader.calls.clear()
    s = h.sync()
    assert h.downloader.calls == []
    assert not s.changed


def test_order_changes_and_duplicates_follow_youtube_music(harness):
    h = harness
    basic_library(h)
    h.sync()
    t = {x["video_id"]: x for x in h.client.tracks["PL1"]}
    h.client.tracks["PL1"] = [t["c1"], t["a1"], t["c1"], t["b1"]]
    h.sync()
    assert h.m3u8("Road Trip") == [
        "../Gamma/Singles/Third Song.opus",
        "../Alpha/Album A/04 First Song.opus",
        "../Gamma/Singles/Third Song.opus",
        "../Beta/Singles/Second Song.opus",
    ]


def test_removed_track_goes_to_trash_and_comes_back_when_re_added(harness):
    h = harness
    basic_library(h)
    h.sync()
    original = list(h.client.tracks["PL1"])

    # c1 is only in PL1 -> trashed. b1 is still in PL2 -> stays.
    h.client.tracks["PL1"] = [original[0], original[1]]
    s = h.sync()
    assert s.removed == 1
    assert "Gamma/Singles/Third Song.opus" not in h.audio_files()
    assert "Beta/Singles/Second Song.opus" in h.audio_files()
    assert not (h.music / "Gamma").exists()  # empty folders are cleaned up
    assert (h.env.trash_dir / "Gamma/Singles/Third Song.opus").exists()
    assert h.db.track("c1")["status"] == "trashed"
    assert len(h.m3u8("Road Trip")) == 2

    h.downloader.calls.clear()
    h.client.tracks["PL1"] = original
    s = h.sync()
    assert s.restored == 1
    assert h.downloader.calls == []
    assert "Gamma/Singles/Third Song.opus" in h.audio_files()
    assert h.db.track("c1")["status"] == "downloaded"


def test_trash_retention_zero_deletes_immediately(harness):
    h = harness
    basic_library(h)
    h.sync()
    h.settings.update({"trash_retention_days": 0})
    h.client.tracks["PL2"] = [h.client.tracks["PL2"][0]]
    h.sync()
    assert "Delta/Singles/Fourth Song.opus" not in h.audio_files()
    assert h.engine.trash.items() == []
    assert h.db.track("d1") is None


def test_old_trash_is_purged(harness):
    h = harness
    basic_library(h)
    h.sync()
    h.client.tracks["PL2"] = [h.client.tracks["PL2"][0]]
    h.sync()
    trashed = h.env.trash_dir / "Delta/Singles/Fourth Song.opus"
    old = time.time() - 31 * 86400
    os.utime(trashed, (old, old))
    s = h.sync()
    assert s.trash_purged == 1
    assert not trashed.exists()
    assert h.db.track("d1") is None


def test_mass_removal_is_held_until_approved(harness):
    h = harness
    h.client.add_playlist("PL1", "Big", [track(f"v{i}", f"Song {i}", [f"Artist {i}"]) for i in range(30)])
    h.sync()
    h.client.tracks["PL1"] = h.client.tracks["PL1"][:5]

    s = h.sync()
    assert s.held_playlists == 1
    assert s.removed == 0
    assert len(h.audio_files()) == 30
    assert h.db.playlist("PL1")["held_removals"] == 25

    h.db.update_playlist("PL1", approve_removals=1)
    s = h.sync()
    assert s.removed == 25
    assert len(h.audio_files()) == 5
    assert h.db.playlist("PL1")["held_removals"] == 0


def test_small_removals_are_not_held(harness):
    h = harness
    h.client.add_playlist("PL1", "Small", [track(f"v{i}", f"Song {i}", [f"Artist {i}"]) for i in range(4)])
    h.sync()
    h.client.tracks["PL1"] = []
    s = h.sync()
    assert s.removed == 4


def test_empty_library_response_is_held_until_approved(harness):
    h = harness
    basic_library(h)
    h.sync()
    saved = list(h.client.playlists)
    h.client.playlists = []
    s = h.sync()
    assert s.held_library_removals == 2 and s.removed == 0
    assert len(h.audio_files()) == 4
    assert h.db.get_kv("held_library_removal") == ["Chill", "Road Trip"]

    h.client.playlists = saved  # glitch over: nothing happens, hold is cleared
    s = h.sync()
    assert s.held_library_removals == 0 and s.removed == 0
    assert h.db.get_kv("held_library_removal") is None

    h.client.playlists = []
    h.sync()
    h.engine.approve_library_removal()
    s = h.sync()
    assert s.removed == 4
    assert h.audio_files() == set()
    assert h.db.playlists() == []


def test_many_playlists_vanishing_at_once_are_held(harness):
    h = harness
    for i in range(4):
        h.client.add_playlist(f"P{i}", f"List {i}", [track(f"t{i}", f"Song {i}", [f"Artist {i}"])])
    h.sync()
    for i in range(3):
        h.client.remove_playlist(f"P{i}")
    s = h.sync()
    assert s.held_library_removals == 3
    assert len(h.audio_files()) == 4
    h.engine.approve_library_removal()
    s = h.sync()
    assert s.removed == 3


def test_long_download_queues_are_split_into_runs(harness, monkeypatch):
    from ytmd import sync as sync_module
    h = harness
    h.settings.update({"concurrent_downloads": 1})
    h.client.add_playlist("PL1", "Many", [track(f"m{i}", f"Song {i}", [f"A{i}"]) for i in range(6)])
    original = h.downloader.__call__

    def slow(*args, **kwargs):
        time.sleep(0.2)
        return original(*args, **kwargs)

    h.engine.download_fn = slow
    monkeypatch.setattr(sync_module, "DOWNLOAD_BUDGET_SECONDS", -1)
    s = h.sync()
    assert s.more_pending
    assert 1 <= s.downloaded < 6
    monkeypatch.setattr(sync_module, "DOWNLOAD_BUDGET_SECONDS", 3600)
    s = h.sync()
    assert not s.more_pending
    assert len(h.audio_files()) == 6


def test_unreadable_playlist_keeps_its_tracks(harness):
    h = harness
    basic_library(h)
    h.sync()
    h.client.failing_playlists.add("PL1")
    s = h.sync()
    assert s.playlist_errors == 1
    assert s.removed == 0
    assert len(h.audio_files()) == 4


def test_disabling_a_playlist_removes_its_exclusive_tracks_and_file(harness):
    h = harness
    basic_library(h)
    h.sync()
    h.db.update_playlist("PL2", enabled=0)
    s = h.sync()
    assert s.removed == 1  # d1; b1 is still in PL1
    assert not (h.music / "Playlists" / "Chill.m3u8").exists()
    assert "Beta/Singles/Second Song.opus" in h.audio_files()


def test_playlist_deleted_on_youtube_music(harness):
    h = harness
    basic_library(h)
    h.sync()
    h.client.remove_playlist("PL2")
    s = h.sync()
    assert s.removed == 1
    assert not (h.music / "Playlists" / "Chill.m3u8").exists()
    assert h.db.playlist("PL2") is None


def test_renamed_playlist_renames_its_file(harness):
    h = harness
    basic_library(h)
    h.sync()
    h.client.playlists[1]["title"] = "Chill: Evening"
    h.sync()
    assert not (h.music / "Playlists" / "Chill.m3u8").exists()
    assert (h.music / "Playlists" / "Chill： Evening.m3u8").exists()


def test_rate_limit_pauses_downloads_without_counting_attempts(harness):
    h = harness
    basic_library(h)
    h.settings.update({"concurrent_downloads": 1})
    h.downloader.errors["a1"] = DownloadError(RATE_LIMITED, "Sign in to confirm you're not a bot")
    s = h.sync()
    assert s.rate_limited
    assert h.db.track("a1")["attempts"] == 0
    assert h.engine.cooldown_until > time.time()

    h.downloader.errors.clear()
    h.downloader.calls.clear()
    h.sync()
    assert h.downloader.calls == []  # still cooling down
    h.engine.clear_cooldown()
    h.sync()
    assert "a1" in h.downloader.calls
    assert h.db.track("a1")["status"] == "downloaded"


def test_failures_are_retried_with_backoff(harness):
    h = harness
    basic_library(h)
    h.downloader.errors["c1"] = DownloadError(TRANSIENT, "network glitch")
    s = h.sync()
    assert s.failed == 1
    t = h.db.track("c1")
    assert t["status"] == "failed" and t["attempts"] == 1 and t["next_attempt_at"] > time.time()
    assert len(h.m3u8("Road Trip")) == 2

    h.downloader.errors.clear()
    h.downloader.calls.clear()
    h.sync()
    assert h.downloader.calls == []  # backoff not over yet
    h.db.update_track("c1", next_attempt_at=0)
    h.sync()
    assert h.db.track("c1")["status"] == "downloaded"
    assert len(h.m3u8("Road Trip")) == 3


def test_music_video_is_replaced_by_album_version(harness):
    h = harness
    h.client.add_playlist("PL1", "Videos", [track("omv1", "Hit (Official Video)", ["Star"],
                                                  video_type="MUSIC_VIDEO_TYPE_OMV")])
    h.client.watch["omv1"] = {"lyrics_id": "MPLYt1", "counterpart": track("atv1", "Hit", ["Star"], "Greatest",
                                                                          "MPREg")}
    h.client.albums["MPREg"] = {"title": "Greatest", "artists": ["Star"], "year": "2019", "track_count": 12,
                                "thumbnail": None, "tracks": [{"video_id": "atv1", "title": "Hit", "track_number": 7}]}
    h.client.lyrics_data["MPLYt1"] = {"text": "line one\nline two", "synced": [(0, "line one"), (1500, "line two")]}

    h.sync()
    assert h.downloader.calls == ["atv1"]
    path = h.music / "Star/Greatest/07 Hit.opus"
    assert path.exists()
    assert OggOpus(path)["ytmusic_video_id"] == ["omv1"]
    assert OggOpus(path)["lyrics"] == ["line one\nline two"]
    assert path.with_suffix(".lrc").read_text().endswith("[00:01.50]line two\n")
    assert h.m3u8("Videos") == ["../Star/Greatest/07 Hit.opus"]

    h.downloader.calls.clear()
    h.sync()  # metadata resolution must stick, no re-download / re-tag loop
    assert h.downloader.calls == []
    assert h.db.track("omv1")["meta_resolved"] == 1


def test_name_collisions_get_unique_files(harness):
    h = harness
    h.client.add_playlist("PL1", "Dupes", [track("x1", "Same", ["Band"]), track("x2", "Same", ["Band"])])
    h.sync()
    assert h.audio_files() == {"Band/Singles/Same.opus", "Band/Singles/Same [x2].opus"}


def test_changing_layout_moves_existing_files(harness):
    h = harness
    basic_library(h)
    h.sync()
    h.settings.update({"layout": "flat"})
    s = h.sync()
    assert s.relocated == 4
    assert h.audio_files() == {
        "Alpha - First Song.opus", "Beta - Second Song.opus", "Gamma, Delta - Third Song.opus",
        "Delta - Fourth Song.opus",
    }
    assert h.m3u8("Chill") == ["../Beta - Second Song.opus", "../Delta - Fourth Song.opus"]
    assert not (h.music / "Alpha").exists()


def test_file_deleted_by_hand_is_downloaded_again(harness):
    h = harness
    basic_library(h)
    h.sync()
    (h.music / "Delta/Singles/Fourth Song.opus").unlink()
    h.downloader.calls.clear()
    h.sync()
    assert h.downloader.calls == ["d1"]


def test_existing_file_is_adopted_instead_of_downloaded(harness, audio_samples):
    h = harness
    h.client.add_playlist("PL1", "One", [track("k1", "Kept", ["Keeper"])])
    target = h.music / "Keeper/Singles/Kept.opus"
    target.parent.mkdir(parents=True)
    target.write_bytes(audio_samples["opus"].read_bytes())
    h.sync()
    assert h.downloader.calls == []
    assert h.db.track("k1")["status"] == "downloaded"


def test_unavailable_tracks_are_skipped(harness):
    h = harness
    h.client.add_playlist("PL1", "Greyed", [track("u1", "Gone", available=False), track("u2", "Here")])
    h.sync()
    assert h.downloader.calls == ["u2"]
    assert h.db.track("u1")["status"] == "unavailable"


def test_title_change_on_youtube_music_renames_file(harness):
    h = harness
    basic_library(h)
    h.sync()
    h.client.tracks["PL2"][1] = track("d1", "Fourth Song (Remastered)", ["Delta"])
    h.sync()
    assert "Delta/Singles/Fourth Song (Remastered).opus" in h.audio_files()
    assert "Delta/Singles/Fourth Song.opus" not in h.audio_files()
    assert OggOpus(h.music / "Delta/Singles/Fourth Song (Remastered).opus")["title"] == ["Fourth Song (Remastered)"]


def test_relocation_keeps_the_album_cover(harness):
    h = harness
    basic_library(h)
    h.sync()
    t = h.db.track("a1")
    assert (h.music / "Alpha/Album A/cover.jpg").exists()
    moved = h.engine._relocate([(t, "Alpha/Album A (Deluxe)/04 First Song.opus")], h.settings.get())
    assert moved == 1
    assert (h.music / "Alpha/Album A (Deluxe)/04 First Song.opus").exists()
    assert (h.music / "Alpha/Album A (Deluxe)/cover.jpg").exists()
    assert not (h.music / "Alpha/Album A").exists()  # only the cover was left, so the folder is gone
    assert h.db.track("a1")["path"] == "Alpha/Album A (Deluxe)/04 First Song.opus"


def test_interrupted_reorganisation_is_recovered_on_start(harness, audio_samples):
    from ytmd.sync import SyncEngine
    h = harness
    h.client.add_playlist("PL1", "One", [track("r1", "Song", ["Band"])])
    h.client.lyrics_data["L1"] = {"text": "la", "synced": [(0, "la")]}
    h.client.watch["r1"] = {"lyrics_id": "L1", "counterpart": None}
    h.sync()
    path = h.music / "Band/Singles/Song.opus"
    assert path.with_suffix(".lrc").exists()
    staging = h.env.tmp_dir / "relocate"
    staging.mkdir(parents=True)
    path.rename(staging / "r1.opus")
    path.with_suffix(".lrc").rename(staging / "r1.lrc")  # crash between the two phases

    SyncEngine(h.env, h.settings, h.db, client_factory=lambda s: h.client, download_fn=h.downloader)
    assert path.exists() and path.with_suffix(".lrc").exists()
    assert not h.env.tmp_dir.exists()
