"""On-screen display: the channel banner, volume bar, messages, and show menu.

These are drawn like a late-90s kids' TV on-screen display, but built to be
easy for young children to read: a big rounded font (Fredoka), bright yellow
on a thick deep-blue outline, and solid backgrounds behind anything long.

* the **channel banner** ("CH 03" + the show name) flashes top-right when you
  change channels;
* the **volume bar** is a row of chunky bars for the current level followed by
  dots for the rest, with a "Volume" label - matching a classic TV OSD;
* the **show menu** fills the screen: a deep-blue board with 90s colour stripes
  along the top and big rounded tiles, the chosen one in yellow;
* the **now playing** card names the show and episode when one starts.

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
_ID_PAUSE = 7
_ID_SOUND_SYNC = 8

# How many menu rows fit on screen at once.
MENU_ROWS = 5

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

    def show_paused(self, show: str, code: str, episode: str) -> None:
        """The pause card: show, series and episode numbers, episode name."""
        self._player.clear_overlay(_ID_NOW_PLAYING)
        self._expiry.pop(_ID_NOW_PLAYING, None)
        self._player.set_overlay(
            _ID_PAUSE, _pause_ass(show, code, episode, self._ui), CANVAS_W, CANVAS_H
        )
        self._expiry.pop(_ID_PAUSE, None)

    def clear_pause(self) -> None:
        self._player.clear_overlay(_ID_PAUSE)
        self._expiry.pop(_ID_PAUSE, None)

    def show_sound_sync(self, delay_ms: int) -> None:
        self._player.set_overlay(
            _ID_SOUND_SYNC, _sound_sync_ass(delay_ms, self._ui), CANVAS_W, CANVAS_H
        )
        self._expiry.pop(_ID_SOUND_SYNC, None)

    def clear_sound_sync(self) -> None:
        self._player.clear_overlay(_ID_SOUND_SYNC)
        self._expiry.pop(_ID_SOUND_SYNC, None)

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
            _ID_PAUSE, _ID_SOUND_SYNC,
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
    """Common ASS override tags for the TV readouts: chunky font, thick outline.

    The thick dark outline keeps the text readable over any picture. ``glow``
    swaps it for the old soft phosphor bloom.
    """
    color = _hex_to_ass(ui.color, alpha)
    outline = _hex_to_ass(ui.menu_background)
    tags = rf"\fn{ui.font}\b0\fs{size}\c{color}\1a&H{alpha:02X}&"
    if ui.glow:
        tags += rf"\bord2\blur4\3c{color}\4c{_BLACK}\shad0"
    else:
        tags += rf"\bord4\3c{outline}\4c{_BLACK}\4a&H80&\shad3"
    return tags


# --------------------------------------------------------------------------
# ASS builders (free functions so they are easy to unit test)
# --------------------------------------------------------------------------
def _channel_bug_ass(number: int, name: str, ui: UiConfig) -> str:
    """'CH 03' + show name, flashed in the top-right of the screen."""
    num = f"{number:02d}"
    number_line = (
        rf"{{\an9\pos({_IX1},{_IY0}){_style(ui, size=88)}}}CH {num}"
    )
    name_line = (
        rf"{{\an9\pos({_IX1},{_IY0 + 104}){_style(ui, size=40)}}}{_escape(name)}"
    )
    return "\n".join([number_line, name_line])


def _volume_ass(level: int, muted: bool, ui: UiConfig) -> str:
    """A 'Volume' label with solid bars (level) then dots (remainder)."""
    level = max(0, min(100, int(level)))
    segments = 20
    filled = 0 if muted else round(level / 100 * segments)

    bar_w = 22
    pitch = 40
    bar_h = 52
    total_w = (segments - 1) * pitch + bar_w
    x0 = _FRAME_CX - total_w // 2          # centre the bar on the screen
    row_top = _IY1 - bar_h                  # sit just above the bottom safe edge
    dot_r = 8
    fill = _hex_to_ass(ui.color)
    edge = _hex_to_ass(ui.menu_background)

    label = "Mute" if muted else "Volume"
    parts = [
        rf"{{\an7\pos({x0},{row_top - 62}){_style(ui, size=48)}}}{label}"
    ]

    for i in range(segments):
        cx = x0 + i * pitch + bar_w / 2
        if i < filled:
            parts.append(
                _filled_rect(x=x0 + i * pitch, y=row_top, w=bar_w, h=bar_h, fill=fill, edge=edge)
            )
        else:
            parts.append(_dot(cx=cx, cy=row_top + bar_h / 2, r=dot_r, fill=fill, edge=edge))
    return "\n".join(parts)


def _message_ass(text: str, ui: UiConfig) -> str:
    """A centred message (channel entry, 'NO SIGNAL', etc.)."""
    return rf"{{\an8\pos({_FRAME_CX},{_IY0}){_style(ui, size=60)}}}{_escape(text)}"


def _standby_ass(ui: UiConfig) -> str:
    return rf"{{\an5\pos({_FRAME_CX},{CANVAS_H // 2}){_style(ui, size=72)}}}STANDBY"


# Menu palette. Text colours are picked for strong contrast on the menu
# background (white on deep blue, deep blue on the yellow highlight), so
# young children can read them from the sofa.
_WHITE = "#FFFFFF"
_SOFT = "#C9D3FF"
# 90s kids' TV stripes along the top of the menu, softened.
_STRIPES = ("#E4572E", "#F3A712", "#FFD23F", "#4CB944", "#2E86DE")


def _text(ui: UiConfig, *, size: int, color: str) -> str:
    """Menu text: the menu font, flat colour, no outline (it sits on solid tiles)."""
    return (
        rf"\fn{ui.font}\b0\fs{size}\c{_hex_to_ass(color)}"
        rf"\1a&H00&\bord0\shad0"
    )


def _now_playing_ass(show: str, episode: str, ui: UiConfig) -> str:
    """'Now playing' card, bottom-left: show name over episode title."""
    show, episode = _clip(show, 34), _clip(episode, 48)
    # Rough text widths for the card: about half the point size per character.
    width = max(len(show) * 27, len(episode) * 19) + 64
    x, top, height = _IX0, _IY1 - 128, 128
    return "\n".join([
        _rounded_rect(x=x, y=top, w=width, h=height, r=24, fill=ui.menu_background),
        rf"{{\an7\pos({x + 32},{top + 18}){_text(ui, size=52, color=ui.menu_accent)}}}"
        rf"{_escape(show)}",
        rf"{{\an7\pos({x + 32},{top + 76}){_text(ui, size=36, color=_WHITE)}}}"
        rf"{_escape(episode)}",
    ])


def _pause_ass(show: str, code: str, episode: str, ui: UiConfig) -> str:
    """Paused: the picture dims and a card says what is on."""
    w, h = 900, 380
    x, y = (CANVAS_W - w) // 2, (CANVAS_H - h) // 2
    cx = CANVAS_W // 2
    parts = [
        # Dim the frozen picture so the card stands out.
        rf"{{\an7\pos(0,0)\p1\c{_BLACK}\1a&H60&\bord0\shad0}}"
        rf"m 0 0 l {CANVAS_W} 0 l {CANVAS_W} {CANVAS_H} l 0 {CANVAS_H}{{\p0}}",
        _rounded_rect(x=x, y=y, w=w, h=h, r=32, fill=ui.menu_background),
        # "PAUSED" on a yellow tab across the top edge of the card.
        _rounded_rect(x=cx - 120, y=y - 30, w=240, h=60, r=30, fill=ui.menu_accent),
        _pause_icon(cx - 70, y, ui.menu_background),
        rf"{{\an4\pos({cx - 38},{y}){_text(ui, size=38, color=ui.menu_background)}}}PAUSED",
        rf"{{\an8\pos({cx},{y + 56}){_text(ui, size=64, color=_WHITE)}}}{_escape(_clip(show, 26))}",
    ]
    line_y = y + 148
    if code:
        parts.append(
            rf"{{\an8\pos({cx},{line_y}){_text(ui, size=44, color=ui.menu_accent)}}}{_escape(code)}"
        )
        line_y += 64
    if episode:
        parts.append(
            rf"{{\an8\pos({cx},{line_y}){_text(ui, size=44, color=_WHITE)}}}"
            rf"{_escape(_clip(episode, 34))}"
        )
    parts.append(
        rf"{{\an2\pos({cx},{y + h - 24}){_text(ui, size=30, color=_SOFT)}}}"
        rf"Press PLAY or OK to carry on"
    )
    return "\n".join(parts)


def sound_sync_words(delay_ms: int) -> str:
    """``In sync``, ``Sound 150 ms later``, ``Sound 100 ms earlier``."""
    if delay_ms == 0:
        return "In sync"
    return f"Sound {abs(delay_ms)} ms {'later' if delay_ms > 0 else 'earlier'}"


def _sound_sync_ass(delay_ms: int, ui: UiConfig) -> str:
    """A bar along the bottom, so a face on screen stays in view."""
    x, w, h = _IX0, _IX1 - _IX0, 150
    top = _IY1 - h
    cx = x + w // 2
    return "\n".join([
        _rounded_rect(x=x, y=top, w=w, h=h, r=28, fill=ui.menu_background),
        rf"{{\an8\pos({cx},{top + 14}){_text(ui, size=34, color=_SOFT)}}}"
        r"Sound sync: watch someone talking",
        rf"{{\an8\pos({cx},{top + 52}){_text(ui, size=52, color=ui.menu_accent)}}}"
        rf"<   {_escape(sound_sync_words(delay_ms))}   >",
        rf"{{\an2\pos({cx},{top + h - 10}){_text(ui, size=30, color=_WHITE)}}}"
        r"LEFT = sound earlier      RIGHT = sound later      OK = done",
    ])


def _pause_icon(cx: int, cy: int, fill: str) -> str:
    """Two bars: the pause symbol, drawn so it does not depend on the font."""
    color = _hex_to_ass(fill)
    bars = []
    for dx in (-9, 5):
        bars.append(
            rf"{{\an7\pos({cx + dx},{cy - 13})\p1\c{color}\1a&H00&\bord0\shad0}}"
            rf"m 0 0 l 8 0 l 8 26 l 0 26{{\p0}}"
        )
    return "\n".join(bars)


def _menu_ass(menu: "Menu", ui: UiConfig) -> str:
    """The show menu: a solid screen of big rounded tiles, one highlighted."""
    screen = menu.screen
    x0, x1 = _IX0, _IX1
    tile_h, gap = 70, 14
    top = 184
    parts = [
        # Solid background: nothing busy shows through behind the words.
        _filled_rect(x=0, y=0, w=CANVAS_W, h=CANVAS_H, fill=_hex_to_ass(ui.menu_background)),
    ]
    stripe_w = CANVAS_W / len(_STRIPES)
    for i, color in enumerate(_STRIPES):
        parts.append(
            _filled_rect(x=i * stripe_w, y=0, w=stripe_w + 1, h=12, fill=_hex_to_ass(color))
        )
    if screen.breadcrumb:
        parts.append(
            rf"{{\an7\pos({x0},{_IY0 + 2}){_text(ui, size=32, color=_SOFT)}}}"
            rf"{_escape(_clip(screen.breadcrumb, 40))}  >"
        )
    parts.append(
        rf"{{\an7\pos({x0},{_IY0 + 34}){_text(ui, size=64, color=_WHITE)}}}"
        rf"{_escape(_clip(screen.title, 34))}"
    )
    if screen.note:
        parts.append(
            rf"{{\an7\pos({x0},{_IY0 + 108}){_text(ui, size=30, color=ui.menu_accent)}}}"
            rf"{_escape(_clip(screen.note, 70))}"
        )

    first, rows = menu.visible(MENU_ROWS)
    for offset, item in enumerate(rows):
        index = first + offset
        y = top + offset * (tile_h + gap)
        selected = index == screen.cursor
        if selected:
            # A white rim around a yellow tile: easy to spot at a glance.
            parts.append(_rounded_rect(x=x0 - 6, y=y - 6, w=x1 - x0 + 12, h=tile_h + 12,
                                       r=22, fill=_WHITE))
            parts.append(_rounded_rect(x=x0, y=y, w=x1 - x0, h=tile_h, r=18,
                                       fill=ui.menu_accent))
            color, detail_color = ui.menu_background, ui.menu_background
        else:
            parts.append(_rounded_rect(x=x0, y=y, w=x1 - x0, h=tile_h, r=18,
                                       fill=ui.menu_tile))
            color, detail_color = _WHITE, _SOFT
        arrow = "  >" if item.opens is not None else ""
        parts.append(
            rf"{{\an4\pos({x0 + 28},{y + tile_h // 2}){_text(ui, size=44, color=color)}}}"
            rf"{_escape(_clip(item.label, 40))}{arrow}"
        )
        if item.detail:
            parts.append(
                rf"{{\an6\pos({x1 - 28},{y + tile_h // 2}){_text(ui, size=32, color=detail_color)}}}"
                rf"{_escape(item.detail)}"
            )

    white = _hex_to_ass(_WHITE)
    if first > 0:
        parts.append(_triangle(cx=CANVAS_W // 2, cy=top - 20, up=True, fill=white))
    if first + len(rows) < len(screen.items):
        last_bottom = top + MENU_ROWS * (tile_h + gap) - gap
        parts.append(_triangle(cx=CANVAS_W // 2, cy=last_bottom + 22, up=False, fill=white))
    back = "go back" if menu.depth > 1 else "close"
    parts.append(
        rf"{{\an1\pos({x0},{_IY1 + 8}){_text(ui, size=32, color=_SOFT)}}}"
        rf"OK = choose      BACK = {back}"
    )
    return "\n".join(parts)


def _rounded_rect(*, x: float, y: float, w: float, h: float, r: float, fill: str) -> str:
    """A filled rectangle with rounded corners (``fill`` is ``#RRGGBB``)."""
    x, y, w, h = round(x), round(y), round(w), round(h)
    r = min(round(r), w // 2, h // 2)
    k = round(r * 0.4477)  # bezier handle offset for a quarter circle
    path = (
        f"m {r} 0 l {w - r} 0 b {w - k} 0 {w} {k} {w} {r} "
        f"l {w} {h - r} b {w} {h - k} {w - k} {h} {w - r} {h} "
        f"l {r} {h} b {k} {h} 0 {h - k} 0 {h - r} "
        f"l 0 {r} b 0 {k} {k} 0 {r} 0"
    )
    color = _hex_to_ass(fill)
    return rf"{{\an7\pos({x},{y})\p1\c{color}\1a&H00&\bord0\shad0}}{path}{{\p0}}"


def _triangle(*, cx: float, cy: float, up: bool, fill: str, size: int = 16) -> str:
    """A small arrow showing the list scrolls further that way."""
    x, y = round(cx), round(cy)
    if up:
        draw = f"m 0 {-size} l {size} {size // 2} l {-size} {size // 2}"
    else:
        draw = f"m 0 {size} l {size} {-size // 2} l {-size} {-size // 2}"
    return rf"{{\an7\pos({x},{y})\p1\c{fill}\1a&H00&\bord0\shad0}}{draw}{{\p0}}"


def _clip(text: str, limit: int) -> str:
    """Shorten long names so a row never runs off the screen."""
    return text if len(text) <= limit else text[: limit - 2].rstrip() + ".."


def _filled_rect(
    *, x: float, y: float, w: float, h: float, fill: str, edge: Optional[str] = None
) -> str:
    """An ASS drawing (\\p1) filled rectangle at absolute canvas coordinates.

    ``edge`` adds a thick outline in that colour, so the shape stands out on
    any picture.
    """
    x, y = round(x), round(y)
    w, h = round(w), round(h)
    draw = f"m 0 0 l {w} 0 l {w} {h} l 0 {h}"
    border = rf"\bord3\3c{edge}" if edge else r"\bord0"
    return rf"{{\an7\pos({x},{y})\p1\c{fill}\1a&H00&{border}\shad0}}{draw}{{\p0}}"


def _dot(*, cx: float, cy: float, r: float, fill: str, edge: Optional[str] = None) -> str:
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
    border = rf"\bord3\3c{edge}" if edge else r"\bord0"
    return rf"{{\an5\pos({x},{y})\p1\c{fill}\1a&H00&{border}\shad0}}{path}{{\p0}}"


def _escape(text: str) -> str:
    """Escape characters that are meaningful inside an ASS override block."""
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


__all__ = ["OverlayManager", "CANVAS_W", "CANVAS_H", "MENU_ROWS"]
