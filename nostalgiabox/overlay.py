"""On-screen display: the green digital channel banner, volume bar, and messages.

These are drawn to look like a late-90s/early-2000s TV's on-screen display: a
chunky phosphor-green readout in a retro terminal font. Two signature elements:

* the **channel banner** ("CH 03" + the show name) that flashes top-right when
  you change channels, and
* the **volume bar** - a row of solid green bars for the current level followed
  by green dots for the rest, with a "Volume" label - matching a classic TV OSD.

On top of that sits the modern half: a **show menu** (a dark panel listing
shows, series and episodes, with the highlighted row in a solid accent bar) and
a **now playing** caption naming the show and episode.

Everything is rendered as ASS overlays on a fixed 1280x720 virtual canvas (mpv
scales it to the TV) and cleared automatically after a few seconds by
:meth:`OverlayManager.tick`, which the main loop calls every iteration.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable, Dict, Optional

from .config import Config, UiConfig
from .player import Player

if TYPE_CHECKING:  # pragma: no cover
    from .menu import Menu

# Virtual canvas the overlays are laid out on. This maps to the WHOLE display
# (a 16:9 TV), so mpv scales it to whatever the screen is.
CANVAS_W = 1280
CANVAS_H = 720

# Overlays sit inside a title-safe area of the full 16:9 picture, so nothing
# is lost on a TV that overscans the edges.
_FRAME_W = CANVAS_W
_FRAME_X0 = 0
_FRAME_X1 = CANVAS_W
_FRAME_CX = CANVAS_W // 2
_SAFE = 0.05
_IX0 = int(CANVAS_W * _SAFE)                     # 64   (left safe edge)
_IX1 = CANVAS_W - int(CANVAS_W * _SAFE)          # 1216 (right safe edge)
_IY0 = int(CANVAS_H * _SAFE)                     # 36   (top safe edge)
_IY1 = CANVAS_H - int(CANVAS_H * _SAFE)          # 684  (bottom safe edge)

# Overlay slots (ids). Each kind of overlay owns one id so it can be replaced
# or cleared independently.
_ID_CHANNEL = 1
_ID_VOLUME = 2
_ID_STANDBY = 3
_ID_MESSAGE = 4
_ID_MENU = 5
_ID_NOW_PLAYING = 6

# How many menu rows fit on screen at once.
MENU_ROWS = 8

_BLACK = "&H00000000"


class OverlayManager:
    """Draws and expires the TV's on-screen overlays."""

    def __init__(
        self,
        player: Player,
        config: Config,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._player = player
        self._config = config
        self._ui = config.ui
        self._clock = clock
        # overlay id -> wall time (monotonic) at which it should disappear.
        self._expiry: Dict[int, float] = {}

    # -- public API ---------------------------------------------------------
    def show_channel_bug(
        self, number: int, name: str, *, duration: Optional[float] = None
    ) -> None:
        """Flash the channel number + name, like changing channels on a cable box."""
        dur = self._config.channel_bug_seconds if duration is None else duration
        ass = _channel_bug_ass(number, name, self._ui)
        self._player.set_overlay(_ID_CHANNEL, ass, CANVAS_W, CANVAS_H)
        self._arm(_ID_CHANNEL, dur)

    def show_volume(
        self, level: int, muted: bool, *, duration: Optional[float] = None
    ) -> None:
        dur = self._config.osd_duration if duration is None else duration
        ass = _volume_ass(level, muted, self._ui)
        self._player.set_overlay(_ID_VOLUME, ass, CANVAS_W, CANVAS_H)
        self._arm(_ID_VOLUME, dur)

    def show_message(self, text: str, *, duration: Optional[float] = None) -> None:
        dur = self._config.osd_duration if duration is None else duration
        ass = _message_ass(text, self._ui)
        self._player.set_overlay(_ID_MESSAGE, ass, CANVAS_W, CANVAS_H)
        self._arm(_ID_MESSAGE, dur)

    def show_now_playing(
        self, show: str, episode: str, *, duration: Optional[float] = None
    ) -> None:
        """Show name and episode, bottom-left, when an episode starts."""
        dur = self._config.channel_bug_seconds if duration is None else duration
        ass = _now_playing_ass(show, episode, self._ui)
        self._player.set_overlay(_ID_NOW_PLAYING, ass, CANVAS_W, CANVAS_H)
        self._arm(_ID_NOW_PLAYING, dur)

    def show_menu(self, menu: "Menu") -> None:
        """Draw (or redraw) the show menu. It stays until :meth:`clear_menu`."""
        self._player.clear_overlay(_ID_CHANNEL)
        self._player.clear_overlay(_ID_NOW_PLAYING)
        self._expiry.pop(_ID_CHANNEL, None)
        self._expiry.pop(_ID_NOW_PLAYING, None)
        self._player.set_overlay(_ID_MENU, _menu_ass(menu, self._ui), CANVAS_W, CANVAS_H)
        self._expiry.pop(_ID_MENU, None)

    def clear_menu(self) -> None:
        self._player.clear_overlay(_ID_MENU)
        self._expiry.pop(_ID_MENU, None)

    def show_standby(self) -> None:
        """Persistent 'standby' notice for when the box is 'off'."""
        ass = _standby_ass(self._ui)
        self._player.set_overlay(_ID_STANDBY, ass, CANVAS_W, CANVAS_H)
        self._expiry.pop(_ID_STANDBY, None)

    def clear_standby(self) -> None:
        self._player.clear_overlay(_ID_STANDBY)
        self._expiry.pop(_ID_STANDBY, None)

    def tick(self) -> None:
        """Clear any overlays whose time is up. Call this every loop iteration."""
        now = self._clock()
        for overlay_id, when in list(self._expiry.items()):
            if now >= when:
                self._player.clear_overlay(overlay_id)
                self._expiry.pop(overlay_id, None)

    def clear_all(self) -> None:
        for overlay_id in (
            _ID_CHANNEL, _ID_VOLUME, _ID_STANDBY, _ID_MESSAGE, _ID_MENU, _ID_NOW_PLAYING,
        ):
            self._player.clear_overlay(overlay_id)
        self._expiry.clear()

    # -- internals ----------------------------------------------------------
    def _arm(self, overlay_id: int, duration: float) -> None:
        if duration <= 0:
            # duration 0 means "leave it until explicitly cleared"
            self._expiry.pop(overlay_id, None)
        else:
            self._expiry[overlay_id] = self._clock() + duration


# --------------------------------------------------------------------------
# Colour + style helpers
# --------------------------------------------------------------------------
def _hex_to_ass(hex_color: str, alpha: int = 0) -> str:
    """Convert ``#RRGGBB`` to an ASS ``&HAABBGGRR`` colour string."""
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _style(ui: UiConfig, *, size: int, alpha: int = 0) -> str:
    """Common ASS override tags: retro font, green fill, and a soft CRT glow."""
    color = _hex_to_ass(ui.color, alpha)
    tags = rf"\fn{ui.font}\b1\fs{size}\c{color}\1a&H{alpha:02X}&"
    if ui.glow:
        # A blurred green border reads as phosphor bloom; a faint dark edge keeps
        # it legible over bright video.
        tags += rf"\bord2\blur4\3c{color}\4c{_BLACK}\shad0"
    else:
        tags += rf"\bord2\3c{_BLACK}\shad0"
    return tags


# --------------------------------------------------------------------------
# ASS builders (free functions so they are easy to unit test)
# --------------------------------------------------------------------------
def _channel_bug_ass(number: int, name: str, ui: UiConfig) -> str:
    """Green digital 'CH 03' + show name, flashed inside the top-right of the frame."""
    num = f"{number:02d}"
    number_line = (
        rf"{{\an9\pos({_IX1},{_IY0}){_style(ui, size=88)}}}CH {num}"
    )
    name_line = (
        rf"{{\an9\pos({_IX1},{_IY0 + 104}){_style(ui, size=40)}}}{_escape(name)}"
    )
    return "\n".join([number_line, name_line])


def _volume_ass(level: int, muted: bool, ui: UiConfig) -> str:
    """A 'Volume' label with solid green bars (level) then green dots (remainder)."""
    level = max(0, min(100, int(level)))
    segments = 20
    filled = 0 if muted else round(level / 100 * segments)

    bar_w = 16
    pitch = 38
    bar_h = 48
    total_w = (segments - 1) * pitch + bar_w
    x0 = _FRAME_CX - total_w // 2          # centre the bar on the screen
    row_top = _IY1 - bar_h                  # sit just above the bottom safe edge
    dot_r = 6
    green = _hex_to_ass(ui.color)

    label = "Mute" if muted else "Volume"
    parts = [
        rf"{{\an7\pos({x0},{row_top - 62}){_style(ui, size=48)}}}{label}"
    ]

    for i in range(segments):
        cx = x0 + i * pitch + bar_w / 2
        if i < filled:
            parts.append(
                _filled_rect(x=x0 + i * pitch, y=row_top, w=bar_w, h=bar_h, fill=green)
            )
        else:
            parts.append(_dot(cx=cx, cy=row_top + bar_h / 2, r=dot_r, fill=green))
    return "\n".join(parts)


def _message_ass(text: str, ui: UiConfig) -> str:
    """A centred green digital message (channel entry, 'NO SIGNAL', etc.)."""
    return rf"{{\an8\pos({_FRAME_CX},{_IY0}){_style(ui, size=60)}}}{_escape(text)}"


def _standby_ass(ui: UiConfig) -> str:
    return rf"{{\an5\pos({_FRAME_CX},{CANVAS_H // 2}){_style(ui, size=72)}}}STANDBY"


def _plain(ui: UiConfig, *, size: int, color: str, bold: bool = False) -> str:
    """Menu text: the retro font, no glow, a thin dark edge for legibility."""
    return (
        rf"\fn{ui.font}\b{1 if bold else 0}\fs{size}\c{_hex_to_ass(color)}"
        rf"\1a&H00&\bord1\3c{_BLACK}\shad0"
    )


def _now_playing_ass(show: str, episode: str, ui: UiConfig) -> str:
    """'Now playing' caption: show name over episode title, bottom-left."""
    x = _IX0
    show, episode = _clip(show, 40), _clip(episode, 60)
    # A dark band behind the text keeps it readable over bright cartoons.
    # VT323 is narrow: about a third of its point size per character.
    width = max(len(show) * 17, len(episode) * 11) + 56
    band = (
        rf"{{\an7\pos({x - 24},{_IY1 - 112})\p1\c{_BLACK}\1a&H40&\bord0\shad0}}"
        rf"m 0 0 l {width} 0 l {width} 124 l 0 124{{\p0}}"
    )
    title = rf"{{\an1\pos({x},{_IY1 - 44}){_style(ui, size=52)}}}{_escape(show)}"
    sub = rf"{{\an1\pos({x},{_IY1}){_plain(ui, size=34, color='#FFFFFF')}}}{_escape(episode)}"
    return "\n".join([band, title, sub])


def _menu_ass(menu: "Menu", ui: UiConfig) -> str:
    """The show menu: a dark panel on the left, one highlighted row."""
    screen = menu.screen
    panel_w = 720
    x = _IX0
    row_h = 54
    top = 176
    accent = _hex_to_ass(ui.menu_accent)
    parts = [
        # Panel: mostly opaque black, so the show keeps playing faintly behind.
        rf"{{\an7\pos(0,0)\p1\c{_BLACK}\1a&H18&\bord0\shad0}}"
        rf"m 0 0 l {panel_w} 0 l {panel_w} {CANVAS_H} l 0 {CANVAS_H}{{\p0}}",
    ]
    if screen.breadcrumb:
        parts.append(
            rf"{{\an7\pos({x},{_IY0 + 8}){_plain(ui, size=30, color='#9A9A9A')}}}"
            rf"{_escape(_clip(screen.breadcrumb, 40))}  >"
        )
    parts.append(
        rf"{{\an7\pos({x},{_IY0 + 44}){_style(ui, size=60)}}}{_escape(_clip(screen.title, 26))}"
    )

    first, rows = menu.visible(MENU_ROWS)
    for offset, item in enumerate(rows):
        index = first + offset
        y = top + offset * row_h
        selected = index == screen.cursor
        if selected:
            parts.append(
                _filled_rect(x=x - 16, y=y - 4, w=panel_w - x - 24, h=row_h - 6, fill=accent)
            )
        color = "#FFFFFF" if selected else "#C8C8C8"
        arrow = "  >" if item.opens is not None else ""
        parts.append(
            rf"{{\an7\pos({x},{y}){_plain(ui, size=40, color=color, bold=selected)}}}"
            rf"{_escape(_clip(item.label, 34))}{arrow}"
        )
        if item.detail:
            parts.append(
                rf"{{\an9\pos({panel_w - 56},{y + 8}){_plain(ui, size=28, color='#9A9A9A')}}}"
                rf"{_escape(item.detail)}"
            )

    count = len(screen.items)
    if first > 0:
        parts.append(_triangle(cx=panel_w // 2, cy=top - 22, up=True, fill=accent))
    if first + len(rows) < count:
        parts.append(
            _triangle(cx=panel_w // 2, cy=top + MENU_ROWS * row_h + 6, up=False, fill=accent)
        )
    hint = "OK  choose      BACK  go back" if menu.depth > 1 else "OK  choose      BACK  close"
    parts.append(
        rf"{{\an1\pos({x},{_IY1}){_plain(ui, size=28, color='#9A9A9A')}}}{hint}"
    )
    return "\n".join(parts)


def _triangle(*, cx: float, cy: float, up: bool, fill: str, size: int = 14) -> str:
    """A small arrow showing the list scrolls further that way."""
    x, y = round(cx), round(cy)
    if up:
        draw = f"m 0 {-size} l {size} {size // 2} l {-size} {size // 2}"
    else:
        draw = f"m 0 {size} l {size} {-size // 2} l {-size} {-size // 2}"
    return rf"{{\an7\pos({x},{y})\p1\c{fill}\1a&H00&\bord0\shad0}}{draw}{{\p0}}"


def _clip(text: str, limit: int) -> str:
    """Shorten long names so a row never runs off the panel."""
    return text if len(text) <= limit else text[: limit - 2].rstrip() + ".."


def _filled_rect(*, x: float, y: float, w: float, h: float, fill: str) -> str:
    """An ASS drawing (\\p1) filled rectangle at absolute canvas coordinates."""
    x, y = round(x), round(y)
    w, h = round(w), round(h)
    draw = f"m 0 0 l {w} 0 l {w} {h} l 0 {h}"
    return rf"{{\an7\pos({x},{y})\p1\c{fill}\1a&H00&\bord0\shad0}}{draw}{{\p0}}"


def _dot(*, cx: float, cy: float, r: float, fill: str) -> str:
    """A small filled circle centred at (cx, cy) using 4 bezier arcs."""
    c = 0.5523 * r  # magic constant to approximate a circle with cubic beziers
    x, y = round(cx), round(cy)
    r = round(r, 2)
    c = round(c, 2)
    path = (
        f"m 0 {-r} "
        f"b {c} {-r} {r} {-c} {r} 0 "
        f"b {r} {c} {c} {r} 0 {r} "
        f"b {-c} {r} {-r} {c} {-r} 0 "
        f"b {-r} {-c} {-c} {-r} 0 {-r}"
    )
    return rf"{{\an5\pos({x},{y})\p1\c{fill}\1a&H00&\bord0\shad0}}{path}{{\p0}}"


def _escape(text: str) -> str:
    """Escape characters that are meaningful inside an ASS override block."""
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


__all__ = ["OverlayManager", "CANVAS_W", "CANVAS_H", "MENU_ROWS"]
