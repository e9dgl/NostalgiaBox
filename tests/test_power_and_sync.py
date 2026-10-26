import json

from nostalgiabox.actions import Action
from tests.test_menu import _app, send


def _labels(app):
    return [i.label for i in app.menu.screen.items]


# -- the power button --------------------------------------------------------
def test_power_asks_sleep_or_shut_down(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    send(app, Action.POWER)
    assert app.menu.is_open and not app.standby
    assert app.menu.screen.title == "Turn off"
    assert _labels(app) == ["Sleep", "Shut down", "Keep watching"]
    assert "Turn off" in player.overlays[5]
    assert player.current is not None  # still playing behind it


def test_sleep_then_power_wakes(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    send(app, Action.POWER)
    send(app, Action.ENTER)  # Sleep
    assert app.standby and not app.menu.is_open
    send(app, Action.POWER)
    assert not app.standby and player.current is not None
    assert not app.menu.is_open  # waking never opens the power screen


def test_shut_down_has_to_be_confirmed(tmp_path):
    app, player, _ = _app(tmp_path, power_off_command=["true"])
    ran = []
    app._run_power_off_command = lambda: ran.append(True)
    app.start()
    app._running = True
    send(app, Action.POWER)
    send(app, Action.CHANNEL_DOWN)
    send(app, Action.ENTER)  # Shut down -> are you sure?
    assert app.menu.screen.title == "Shut down the box?"
    assert app.menu.screen.cursor == 0  # "No" is highlighted first
    assert not ran
    send(app, Action.ENTER)  # No, keep watching
    assert not app.menu.is_open and not ran and app._running

    send(app, Action.POWER)
    send(app, Action.CHANNEL_DOWN)
    send(app, Action.ENTER)
    send(app, Action.CHANNEL_DOWN)
    send(app, Action.ENTER)  # Yes, shut down
    assert ran and app.powered_off and not app._running
    assert "GOODBYE" in player.overlays[4]


def test_back_from_confirm_goes_back_then_closes(tmp_path):
    app, _, _ = _app(tmp_path)
    app.start()
    send(app, Action.POWER)
    send(app, Action.CHANNEL_DOWN)
    send(app, Action.ENTER)
    send(app, Action.BACK)
    assert app.menu.screen.title == "Turn off"
    send(app, Action.BACK)
    assert not app.menu.is_open and not app.standby


def test_keep_watching(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    playing = player.current
    send(app, Action.POWER)
    send(app, Action.CHANNEL_UP)  # wraps to "Keep watching"
    send(app, Action.ENTER)
    assert not app.menu.is_open and not app.standby and player.current == playing


# -- sound sync --------------------------------------------------------------
def _open_sync(app):
    send(app, Action.BACK)
    app.menu.screen.cursor = _labels(app).index("Sound sync")
    send(app, Action.ENTER)


def test_sound_sync_from_the_menu(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    assert player.audio_delay == 0.0
    _open_sync(app)
    assert not app.menu.is_open  # the picture stays in view
    assert "In sync" in player.overlays[8]
    send(app, Action.VOLUME_UP)
    send(app, Action.VOLUME_UP)
    assert player.audio_delay == 0.1
    assert "Sound 100 ms later" in player.overlays[8]
    send(app, Action.VOLUME_DOWN)
    send(app, Action.VOLUME_DOWN)
    send(app, Action.VOLUME_DOWN)
    assert player.audio_delay == -0.05
    assert "Sound 50 ms earlier" in player.overlays[8]
    send(app, Action.ENTER)
    assert 8 not in player.overlays
    saved = json.loads((tmp_path / "state" / "settings.json").read_text())
    assert saved == {"audio_delay_ms": -50}


def test_sound_sync_keys_do_not_change_volume_or_channel(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    volume, channel = app.volume, app.lineup.current.number
    _open_sync(app)
    send(app, Action.VOLUME_UP)
    send(app, Action.CHANNEL_UP)
    assert (app.volume, app.lineup.current.number) == (volume, channel)


def test_saved_sound_sync_wins_over_the_config(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "settings.json").write_text('{"audio_delay_ms": 150}')
    app, player, _ = _app(tmp_path, audio_delay_ms=-200)
    app.start()
    assert player.audio_delay == 0.15


def test_config_sound_sync(tmp_path):
    app, player, _ = _app(tmp_path, audio_delay_ms=-200)
    app.start()
    assert player.audio_delay == -0.2


def test_sound_sync_closes_by_itself(tmp_path):
    app, player, clock = _app(tmp_path)
    app.start()
    _open_sync(app)
    clock.advance(31)
    app.step()
    assert 8 not in player.overlays
    send(app, Action.VOLUME_UP)
    assert player.audio_delay == 0.0  # back to normal volume keys
