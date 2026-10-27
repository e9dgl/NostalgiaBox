from pathlib import Path

from nostalgiabox.actions import Action, InputEvent
from nostalgiabox.app import TVApp
from nostalgiabox.config import ChannelConfig, config_from_dict
from nostalgiabox.channel import Channel
from nostalgiabox.input.keymap import cec_key_to_event, evdev_key_to_event
from nostalgiabox.input.manager import InputManager
from nostalgiabox.library import build_library, build_show
from nostalgiabox.menu import PLAY_QUEUE, SHUFFLE_ALL, SHUFFLE_SHOW, Menu
from nostalgiabox.player import END_EOF, MockPlayer
from tests.helpers import FakeClock


def _touch(root: Path, *rels: str) -> None:
    for rel in rels:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")


def _library(tmp_path):
    """Three shows: one flat, one with season folders, one with SxxEyy names."""
    _touch(tmp_path, "Pingu/Pingu 01.mp4", "Pingu/Pingu 02.mp4", "Pingu/Pingu 03.mp4")
    _touch(
        tmp_path,
        "The Raccoons/Season 2/S02E01.mp4",
        "The Raccoons/Season 1/S01E02.mp4",
        "The Raccoons/Season 1/S01E01.mp4",
        "The Raccoons/Season 10/S10E01.mp4",
    )
    _touch(tmp_path, "Arthur/arthur_s02e01.mp4", "Arthur/arthur_s01e01.mp4")
    return tmp_path


def _channel(tmp_path, name, number):
    from nostalgiabox.channel import scan_episodes
    from nostalgiabox.config import DEFAULT_VIDEO_EXTENSIONS

    folder = tmp_path / name
    return Channel(
        ChannelConfig(number=number, name=name, path=folder),
        scan_episodes(folder, DEFAULT_VIDEO_EXTENSIONS),
    )


# -- library ---------------------------------------------------------------
def test_flat_show_has_one_series(tmp_path):
    _library(tmp_path)
    show = build_show(_channel(tmp_path, "Pingu", 2))
    assert not show.has_several_series
    assert [e.title for e in show.episodes] == ["01", "02", "03"]


def test_season_folders_become_series_in_number_order(tmp_path):
    _library(tmp_path)
    show = build_show(_channel(tmp_path, "The Raccoons", 3))
    assert [s.name for s in show.series] == ["Series 1", "Series 2", "Series 10"]
    assert [e.path.name for e in show.series[0].episodes] == ["S01E01.mp4", "S01E02.mp4"]
    assert show.episodes[0].path.name == "S01E01.mp4"
    assert show.episodes[-1].path.name == "S10E01.mp4"


def test_season_numbers_in_file_names_become_series(tmp_path):
    _library(tmp_path)
    show = build_show(_channel(tmp_path, "Arthur", 4))
    assert [s.name for s in show.series] == ["Series 1", "Series 2"]
    assert show.series[0].episodes[0].title == "s01e01"  # show name dropped


def test_episode_title_drops_the_show_name():
    from nostalgiabox.library import episode_title

    assert episode_title(Path("Arthur - S01E02 - Arthur's Eyes.mp4"), "Arthur") == (
        "S01E02 - Arthur's Eyes"
    )
    assert episode_title(Path("arthur.mp4"), "Arthur") == "arthur"  # nothing left: keep it
    assert episode_title(Path("Dragon_Tales.ep1.mkv")) == "Dragon Tales ep1"


def test_shows_are_a_to_z_ignoring_the(tmp_path):
    _library(tmp_path)
    channels = [
        _channel(tmp_path, "Pingu", 2),
        _channel(tmp_path, "The Raccoons", 3),
        _channel(tmp_path, "Arthur", 4),
    ]
    assert [s.name for s in build_library(channels)] == ["Arthur", "Pingu", "The Raccoons"]


# -- menu ------------------------------------------------------------------
def _menu(tmp_path):
    _library(tmp_path)
    return Menu(build_library([
        _channel(tmp_path, "Pingu", 2),
        _channel(tmp_path, "The Raccoons", 3),
        _channel(tmp_path, "Arthur", 4),
    ]))


def test_top_level_is_shuffle_then_shows(tmp_path):
    menu = _menu(tmp_path)
    menu.open()
    labels = [i.label for i in menu.screen.items]
    assert labels == [
        "Shuffle everything", "Arthur", "Pingu", "The Raccoons", "Sound sync", "Pair a remote"
    ]
    assert menu.select().kind == SHUFFLE_ALL


def test_open_highlights_current_show(tmp_path):
    menu = _menu(tmp_path)
    menu.open(current_show="Pingu")
    assert menu.screen.items[menu.screen.cursor].label == "Pingu"


def test_single_series_show_lists_episodes(tmp_path):
    menu = _menu(tmp_path)
    menu.open(current_show="Pingu")
    assert menu.select() is None  # opens the show
    labels = [i.label for i in menu.screen.items]
    assert labels == [
        "Play from the start", "Shuffle Pingu", "Episode 1", "Episode 2", "Episode 3",
    ]

    menu.move(3)  # "Pingu 02"
    choice = menu.select()
    assert choice.kind == PLAY_QUEUE
    assert choice.paths[choice.index].name == "Pingu 02.mp4"


def test_multi_series_show_lists_series_then_episodes(tmp_path):
    menu = _menu(tmp_path)
    menu.open(current_show="The Raccoons")
    menu.select()
    labels = [i.label for i in menu.screen.items]
    assert labels == [
        "Play from the start", "Shuffle The Raccoons", "Series 1", "Series 2", "Series 10",
    ]
    menu.move(1)
    assert menu.select().kind == SHUFFLE_SHOW

    menu.move(2)  # Series 2
    assert menu.select() is None
    assert menu.screen.title == "Series 2"
    play_series = menu.select()
    # Series 2 starts after both Series 1 episodes, and carries on to Series 10.
    assert play_series.index == 2
    assert [p.name for p in play_series.paths[play_series.index:]] == ["S02E01.mp4", "S10E01.mp4"]


def test_back_walks_up_and_closes(tmp_path):
    menu = _menu(tmp_path)
    menu.open(current_show="The Raccoons")
    menu.select()
    menu.move(4)
    menu.select()
    assert menu.depth == 3
    menu.back()
    menu.back()
    assert menu.depth == 1
    menu.back()
    assert not menu.is_open


def test_cursor_wraps(tmp_path):
    menu = _menu(tmp_path)
    menu.open()
    menu.move(-1)
    assert menu.screen.items[menu.screen.cursor].label == "Pair a remote"


def test_visible_window_keeps_cursor_on_screen(tmp_path):
    menu = _menu(tmp_path)
    menu.open()
    menu.screen.cursor = 3
    first, rows = menu.visible(2)
    assert first <= 3 < first + len(rows)


# -- keys ------------------------------------------------------------------
def test_back_and_menu_keys():
    assert evdev_key_to_event("KEY_BACKSPACE").action == Action.BACK
    assert evdev_key_to_event("KEY_ESC").action == Action.BACK  # not quit any more
    assert evdev_key_to_event("KEY_MENU").action == Action.MENU
    assert cec_key_to_event("exit").action == Action.BACK
    assert cec_key_to_event("electronic program guide").action == Action.MENU


# -- the app ---------------------------------------------------------------
def _app(tmp_path, **overrides):
    _library(tmp_path)
    data = {
        "media_root": str(tmp_path),
        "shuffle_seed": 3,
        "start_offset": 0,
        "power_off_command": [],
        "state_file": str(tmp_path / "state" / "playback.json"),
    }
    data.update(overrides)
    clock = FakeClock()
    player = MockPlayer()
    app = TVApp(config_from_dict(data), player, InputManager([]), clock=clock)
    return app, player, clock


def send(app, action):
    app.handle_event(InputEvent(action))


def test_powers_on_into_shuffle_everything(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    assert app.mode == "mix"
    assert player.current is not None
    # The "now playing" caption names the show.
    assert any(name in player.overlays[6] for name in ("Arthur", "Pingu", "The Raccoons"))


def test_shuffle_everything_reaches_every_show(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    seen = set()
    for _ in range(9):  # 9 episodes in the library: one full shuffle
        seen.add(player.current.relative_to(tmp_path).parts[0])
        player.finish_current(END_EOF)
        app.step()
    assert {"Arthur", "Pingu", "The Raccoons"} <= seen
    assert app.mode == "mix"


def test_back_opens_menu_over_playback(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    playing = player.current
    send(app, Action.BACK)
    assert app.menu.is_open
    assert "What do you want to watch?" in player.overlays[5]
    assert player.current == playing  # the show keeps playing behind the menu


def test_pick_an_episode_then_it_carries_on_in_order(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    send(app, Action.BACK)
    app.menu.screen.cursor = 3            # The Raccoons
    send(app, Action.ENTER)               # open show
    send(app, Action.CHANNEL_DOWN)        # Shuffle The Raccoons
    send(app, Action.CHANNEL_DOWN)        # Series 1
    send(app, Action.ENTER)               # open Series 1
    send(app, Action.CHANNEL_DOWN)        # S01E01
    send(app, Action.CHANNEL_DOWN)        # S01E02
    send(app, Action.ENTER)
    assert not app.menu.is_open
    assert 5 not in player.overlays
    assert app.mode == "queue"
    assert (player.current, player.time_pos) == (tmp_path / "The Raccoons/Season 1/S01E02.mp4", 0.0)

    player.finish_current(END_EOF)
    app.step()
    assert player.current.name == "S02E01.mp4"
    player.finish_current(END_EOF)
    app.step()
    assert player.current.name == "S10E01.mp4"
    # End of the show: back to shuffling everything.
    player.finish_current(END_EOF)
    app.step()
    assert app.mode == "mix"


def test_play_from_the_start(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    send(app, Action.MENU)
    app.menu.screen.cursor = 2            # Pingu
    send(app, Action.ENTER)
    send(app, Action.ENTER)               # Play from the start
    assert (player.current, player.time_pos) == (tmp_path / "Pingu/Pingu 01.mp4", 0.0)
    assert app.mode == "queue"


def test_shuffle_one_show_uses_its_channel(tmp_path):
    app, player, clock = _app(tmp_path)
    app.start()
    send(app, Action.BACK)
    app.menu.screen.cursor = 1            # Arthur
    send(app, Action.ENTER)
    send(app, Action.CHANNEL_DOWN)
    send(app, Action.ENTER)               # Shuffle Arthur
    assert app.mode == "channel"
    assert app.lineup.current.name == "Arthur"
    clock.advance(1)                      # the channel-change bridge cuts over
    app.step()
    assert player.current.parent.name == "Arthur"


def test_channel_buttons_still_flip_channels(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    send(app, Action.CHANNEL_UP)
    assert app.mode == "channel"
    assert player.current is not None


def test_menu_back_out_and_idle_timeout(tmp_path):
    app, player, clock = _app(tmp_path, menu_timeout=30)
    app.start()
    send(app, Action.BACK)
    send(app, Action.BACK)                # Back at the top closes it
    assert not app.menu.is_open
    send(app, Action.ENTER)               # OK also opens it
    assert app.menu.is_open
    clock.advance(31)
    app.step()
    assert not app.menu.is_open
    assert 5 not in player.overlays


def test_volume_works_with_menu_open(tmp_path):
    app, player, _ = _app(tmp_path, volume_control="pi", initial_volume=50)
    app.start()
    send(app, Action.BACK)
    send(app, Action.VOLUME_UP)
    assert app.volume == 55
    assert app.menu.is_open


def test_standby_resumes_a_queued_episode(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    app.play_queue([tmp_path / "Pingu/Pingu 01.mp4", tmp_path / "Pingu/Pingu 02.mp4"], 1)
    player.time_pos = 42.0
    send(app, Action.POWER)
    send(app, Action.POWER)  # power screen, then sleep
    send(app, Action.POWER)
    assert app.mode == "queue"
    assert (player.current, player.time_pos) == (tmp_path / "Pingu/Pingu 02.mp4", 42.0)


def test_start_mode_channel_keeps_the_old_behaviour(tmp_path):
    app, player, _ = _app(tmp_path, start_mode="channel", start_channel=3)
    app.start()
    assert app.mode == "channel"
    assert app.lineup.current.number == 3
