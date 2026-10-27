from pathlib import Path

from nostalgiabox.actions import Action
from nostalgiabox.input.keymap import cec_key_to_event, evdev_key_to_event
from nostalgiabox.library import parse_episode
from nostalgiabox.player import END_EOF
from tests.test_menu import _app, send


# -- reading episode names ---------------------------------------------------
def test_parse_sxxeyy_and_name():
    ep = parse_episode(Path("Arthur - S01E02 - Arthur's Eyes.mp4"), "Arthur")
    assert (ep.season, ep.number, ep.name) == (1, 2, "Arthur's Eyes")
    assert ep.code == "Series 1  Episode 2"
    assert ep.label == "2. Arthur's Eyes"


def test_parse_uses_the_folder_series():
    ep = parse_episode(Path("05 Lost.mp4"), "Show", folder_season=3)
    assert (ep.season, ep.number, ep.name) == (3, 5, "Lost")


def test_parse_other_forms():
    assert parse_episode(Path("1x07 The Day.mp4")).number == 7
    assert parse_episode(Path("Episode 4 - Picnic.mkv")).name == "Picnic"
    assert (parse_episode(Path("101.mp4")).season, parse_episode(Path("101.mp4")).number) == (1, 1)
    plain = parse_episode(Path("Noddy Goes to Toyland.mp4"), "Noddy")
    assert (plain.season, plain.number, plain.name) == (None, None, "Noddy Goes to Toyland")
    assert plain.label == "Noddy Goes to Toyland"


# -- keys --------------------------------------------------------------------
def test_media_keys_map():
    assert evdev_key_to_event("KEY_PLAYPAUSE").action == Action.PLAY_PAUSE
    assert evdev_key_to_event("KEY_FASTFORWARD").action == Action.FAST_FORWARD
    assert evdev_key_to_event("KEY_NEXTSONG").action == Action.FAST_FORWARD
    assert evdev_key_to_event("KEY_REWIND").action == Action.REWIND
    assert evdev_key_to_event("KEY_PREVIOUSSONG").action == Action.REWIND
    assert cec_key_to_event("pause").action == Action.PLAY_PAUSE


# -- the app -----------------------------------------------------------------
def test_pause_shows_the_card_and_play_carries_on(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    app.play_queue([tmp_path / "The Raccoons/Season 2/S02E01.mp4"])
    send(app, Action.PLAY_PAUSE)
    assert app.paused and player.paused
    card = player.overlays[7]
    assert "PAUSED" in card
    assert "The Raccoons" in card
    assert "Series 2  Episode 1" in card
    assert app._stall.path is None  # a paused picture is not a frozen one

    send(app, Action.PLAY_PAUSE)
    assert not app.paused and not player.paused
    assert 7 not in player.overlays
    assert app._stall.path is not None


def test_ok_carries_on_when_paused(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    send(app, Action.PLAY_PAUSE)
    send(app, Action.ENTER)
    assert not app.paused
    assert not app.menu.is_open


def test_menu_over_pause_then_back_shows_the_card_again(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    send(app, Action.PLAY_PAUSE)
    send(app, Action.BACK)
    assert app.menu.is_open
    send(app, Action.BACK)
    assert app.paused and 7 in player.overlays


def test_new_episode_is_never_paused(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    send(app, Action.PLAY_PAUSE)
    send(app, Action.CHANNEL_UP)
    assert not app.paused
    assert 7 not in player.overlays


def test_fast_forward_and_rewind(tmp_path):
    app, player, _ = _app(tmp_path, seek_forward_seconds=30, seek_back_seconds=10)
    app.start()
    player.time_pos = 100.0
    send(app, Action.FAST_FORWARD)
    assert player.time_pos == 130.0
    send(app, Action.REWIND)
    assert player.time_pos == 120.0
    assert "10 seconds" in player.overlays[4]
    player.time_pos = 4.0
    send(app, Action.REWIND)
    assert player.time_pos == 0.0  # never before the start


def test_fast_forward_past_the_end_rolls_on(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    first = player.current
    send(app, Action.FAST_FORWARD)
    player.finish_current(END_EOF)
    app.step()
    assert player.current != first
