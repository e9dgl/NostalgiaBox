"""Menu > Settings > How to use the remote."""

from nostalgiabox.actions import Action
from nostalgiabox.menu import REMOTE_KEYS, help_pages
from nostalgiabox.overlay import _help_ass, _remote_ass
from nostalgiabox.config import UiConfig

from tests.test_menu import _app, send
from tests.test_power_and_sync import _labels


def _open_help(app):
    send(app, Action.BACK)
    app.menu.screen.cursor = _labels(app).index("Settings")
    send(app, Action.ENTER)
    assert _labels(app) == ["How to use the remote", "Sound sync", "Pair a remote"]
    send(app, Action.ENTER)


def test_help_pages_only_light_real_buttons():
    pages = help_pages()
    assert pages[0].title == "Your remote"
    assert pages[-1].title == "Using the menu"
    for page in pages:
        assert set(page.keys) <= set(REMOTE_KEYS), page.title
    lit = {key for page in pages for key in page.keys}
    assert lit == set(REMOTE_KEYS)  # every button on the remote is explained


def test_seek_times_come_from_the_config():
    words = {p.title: p.watching for p in help_pages(5, 60)}
    assert words["Rewind and fast forward"] == "Back 5 seconds, or on 60 seconds"


def test_help_screen_steps_through_buttons_and_backs_out(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    _open_help(app)
    assert app.menu.screen.kind == "help"
    assert "Your remote" in player.overlays[5]
    assert "1 of 13" in player.overlays[5]

    send(app, Action.CHANNEL_DOWN)
    assert "Hold OK for a moment to shuffle." in player.overlays[5]
    assert "2 of 13" in player.overlays[5]

    send(app, Action.ENTER)  # OK on a help page does nothing
    assert app.menu.is_open and app.menu.screen.kind == "help"

    send(app, Action.BACK)
    assert app.menu.screen.title == "Settings"


def test_lit_buttons_are_drawn_yellow():
    ui = UiConfig()
    yellow = "&H003FD2FF"  # #FFD23F as ASS
    assert yellow not in _remote_ass((), ui)
    assert yellow in _remote_ass(("pointer",), ui)


def test_turn_off_from_the_menu_opens_the_power_screen(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    send(app, Action.BACK)
    app.menu.screen.cursor = _labels(app).index("Turn off")
    send(app, Action.ENTER)
    assert app.menu.is_open and app.menu.screen.kind == "power"
    assert _labels(app) == ["Sleep", "Shut down", "Keep watching"]
