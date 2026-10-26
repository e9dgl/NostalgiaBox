"""The "what do you want to watch?" menu.

Three levels, all driven by Up / Down / OK / Back on the remote:

1. **Shows** - "Shuffle everything", then every show A to Z.
2. **One show** - "Play from the start", "Shuffle <show>", then either its
   series (when it has more than one) or its episodes.
3. **One series** - "Play <series> from the start", then its episodes.

Choosing an episode plays it and carries on through the rest of the show in
order. The menu itself is pure state: :meth:`Menu.select` returns a
:class:`Choice` and the app decides how to play it. Drawing lives in
``overlay.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .library import Series, Show

# Choice kinds.
SHUFFLE_ALL = "shuffle_all"    # random episodes from every show
SHUFFLE_SHOW = "shuffle_show"  # that show's channel, on shuffle
PLAY_QUEUE = "play_queue"      # paths[index], then onward in order


@dataclass(frozen=True)
class Choice:
    kind: str
    show: Optional[Show] = None
    paths: Tuple[Path, ...] = ()
    index: int = 0


@dataclass(frozen=True)
class MenuItem:
    label: str
    # What OK does: "open" pushes ``screen``; anything else returns ``choice``.
    choice: Optional[Choice] = None
    opens: Optional["Screen"] = None
    detail: str = ""


@dataclass
class Screen:
    title: str
    items: List[MenuItem]
    breadcrumb: str = ""
    cursor: int = 0


class Menu:
    """The menu's screens and highlight. Closed until :meth:`open` is called."""

    def __init__(self, shows: Sequence[Show]) -> None:
        self.shows = list(shows)
        self._stack: List[Screen] = []

    # -- state --------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return bool(self._stack)

    @property
    def screen(self) -> Screen:
        return self._stack[-1]

    @property
    def depth(self) -> int:
        return len(self._stack)

    def open(self, *, current_show: Optional[str] = None) -> None:
        """Show the A-Z list, highlighting the show that is on now."""
        top = self._shows_screen()
        if current_show is not None:
            for i, item in enumerate(top.items):
                if item.opens is not None and item.label == current_show:
                    top.cursor = i
                    break
        self._stack = [top]

    def close(self) -> None:
        self._stack = []

    # -- navigation ---------------------------------------------------------
    def move(self, step: int) -> None:
        if not self.is_open or not self.screen.items:
            return
        screen = self.screen
        screen.cursor = (screen.cursor + step) % len(screen.items)

    def back(self) -> None:
        """Go up one level. Backing out of the top level closes the menu."""
        if self._stack:
            self._stack.pop()

    def select(self) -> Optional[Choice]:
        """OK on the highlighted item: open a level, or return what to play."""
        if not self.is_open or not self.screen.items:
            return None
        item = self.screen.items[self.screen.cursor]
        if item.opens is not None:
            self._stack.append(item.opens)
            return None
        return item.choice

    def visible(self, rows: int) -> Tuple[int, List[MenuItem]]:
        """The slice of the list to draw, keeping the highlight on screen."""
        items = self.screen.items
        if len(items) <= rows:
            return 0, list(items)
        first = min(max(0, self.screen.cursor - rows // 2), len(items) - rows)
        return first, list(items[first:first + rows])

    # -- screens ------------------------------------------------------------
    def _shows_screen(self) -> Screen:
        items = [MenuItem("Shuffle everything", choice=Choice(SHUFFLE_ALL))]
        for show in self.shows:
            count = len(show.episodes)
            items.append(
                MenuItem(
                    show.name,
                    opens=self._show_screen(show),
                    detail=_count(count, "episode"),
                )
            )
        return Screen("What do you want to watch?", items)

    def _show_screen(self, show: Show) -> Screen:
        paths = tuple(ep.path for ep in show.episodes)
        items = [
            MenuItem("Play from the start", choice=Choice(PLAY_QUEUE, show, paths, 0)),
            MenuItem(f"Shuffle {show.name}", choice=Choice(SHUFFLE_SHOW, show)),
        ]
        if show.has_several_series:
            offset = 0
            for series in show.series:
                items.append(
                    MenuItem(
                        series.name,
                        opens=self._series_screen(show, series, paths, offset),
                        detail=_count(len(series.episodes), "episode"),
                    )
                )
                offset += len(series.episodes)
        else:
            for i, ep in enumerate(show.episodes):
                items.append(MenuItem(ep.title, choice=Choice(PLAY_QUEUE, show, paths, i)))
        return Screen(show.name, items, breadcrumb="All shows")

    def _series_screen(
        self, show: Show, series: Series, paths: Tuple[Path, ...], offset: int
    ) -> Screen:
        items = [
            MenuItem(
                f"Play {series.name} from the start",
                choice=Choice(PLAY_QUEUE, show, paths, offset),
            )
        ]
        for i, ep in enumerate(series.episodes):
            items.append(MenuItem(ep.title, choice=Choice(PLAY_QUEUE, show, paths, offset + i)))
        return Screen(series.name, items, breadcrumb=show.name)


def _count(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


__all__ = [
    "Menu",
    "MenuItem",
    "Screen",
    "Choice",
    "SHUFFLE_ALL",
    "SHUFFLE_SHOW",
    "PLAY_QUEUE",
]
