"""The "what do you want to watch?" menu.

Three levels, all driven by Up / Down / OK / Back on the remote:

1. **Shows** - "Shuffle everything", then every show A to Z.
2. **One show** - "Play from the start", "Shuffle <show>", then either its
   series (when it has more than one) or its episodes.
3. **One series** - "Play <series> from the start", then its episodes.

Choosing an episode plays it and carries on through the rest of the show in
order. The last line of the top level, **Settings**, holds **How to use the
remote** (a drawing of the remote with each button explained), **Sound sync**
(lines the sound up with the picture) and **Pair a remote** (a screen the app
fills in as Bluetooth devices are found). The power button opens a separate "Turn off" screen
(:meth:`Menu.open_power`). The menu itself is pure state: :meth:`Menu.select` returns a
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
PAIR_REMOTE = "pair_remote"    # open the Bluetooth pairing screen
PAIR_SEARCH = "pair_search"    # search for remotes again
PAIR_DEVICE = "pair_device"    # pair the device whose address is ``value``
SOUND_SYNC = "sound_sync"      # line the voices up with the mouths
POWER_SLEEP = "power_sleep"    # blank the screen; Power wakes it
POWER_SHUTDOWN = "power_shutdown"  # shut the Pi down (after "are you sure?")
POWER_CANCEL = "power_cancel"  # close the power screen and keep watching
POWER_MENU = "power_menu"      # open the power screen (same as holding Home)


@dataclass(frozen=True)
class Choice:
    kind: str
    show: Optional[Show] = None
    paths: Tuple[Path, ...] = ()
    index: int = 0
    value: str = ""


@dataclass(frozen=True)
class HelpPage:
    """One page of "How to use the remote": which buttons to light up on the
    drawing, and what they do while watching and in the menu."""

    title: str
    keys: Tuple[str, ...] = ()
    watching: str = ""
    in_menu: str = ""
    note: str = ""


# Button names the remote drawing knows (see ``overlay._remote_ass``).
REMOTE_KEYS = (
    "power", "mute", "rewind", "play", "forward",
    "up", "down", "left", "right", "ok",
    "back", "pointer", "home", "minus", "plus",
)


@dataclass(frozen=True)
class MenuItem:
    label: str
    # What OK does: "open" pushes ``screen``; anything else returns ``choice``.
    choice: Optional[Choice] = None
    opens: Optional["Screen"] = None
    detail: str = ""
    # Set on the pages of the "How to use the remote" screen.
    help: Optional[HelpPage] = None


@dataclass
class Screen:
    title: str
    items: List[MenuItem]
    breadcrumb: str = ""
    cursor: int = 0
    # A line of status text under the title (the pairing screen uses it).
    note: str = ""
    # Lets the app recognise screens it fills in itself, like "pairing".
    kind: str = ""


class Menu:
    """The menu's screens and highlight. Closed until :meth:`open` is called."""

    def __init__(
        self,
        shows: Sequence[Show],
        *,
        seek_back_seconds: float = 10.0,
        seek_forward_seconds: float = 30.0,
    ) -> None:
        self.shows = list(shows)
        self._stack: List[Screen] = []
        self._seek_back = seek_back_seconds
        self._seek_forward = seek_forward_seconds

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

    def open_shuffle(self) -> None:
        """The shuffle button's "are you sure?", shown while something plays."""
        self._stack = [
            Screen(
                "Shuffle something new?",
                [
                    MenuItem("Yes, shuffle", choice=Choice(SHUFFLE_ALL)),
                    MenuItem("No, keep watching", choice=Choice(POWER_CANCEL)),
                ],
                note="A random episode of a random show.",
                kind="shuffle",
            )
        ]

    def open_power(self) -> None:
        """The power button's screen: sleep, or shut down (with a second
        "are you sure?" screen), or carry on watching."""
        confirm = Screen(
            "Shut down the box?",
            [
                MenuItem("No, keep watching", choice=Choice(POWER_CANCEL)),
                MenuItem("Yes, shut down", choice=Choice(POWER_SHUTDOWN)),
            ],
            breadcrumb="Turn off",
            note="Unplug it once the green light stops flashing.",
            kind="power",
        )
        self._stack = [
            Screen(
                "Turn off",
                [
                    MenuItem("Sleep", choice=Choice(POWER_SLEEP), detail="screen off"),
                    MenuItem("Shut down", opens=confirm, detail="safe to unplug"),
                    MenuItem("Keep watching", choice=Choice(POWER_CANCEL)),
                ],
                note="Sleep turns the picture off. Press Power to wake it up.",
                kind="power",
            )
        ]

    # -- navigation ---------------------------------------------------------
    def move(self, step: int) -> None:
        if not self.is_open or not self.screen.items:
            return
        screen = self.screen
        screen.cursor = (screen.cursor + step) % len(screen.items)

    def push(self, screen: Screen) -> None:
        """Open a screen the app built (it stays until Back)."""
        self._stack.append(screen)

    def set_items(self, items: List[MenuItem]) -> None:
        """Replace the current screen's list, keeping the highlight on the
        same entry when it is still there."""
        screen = self.screen
        old_index = screen.cursor
        old = screen.items[old_index] if screen.items else None
        screen.items = items
        screen.cursor = min(old_index, max(0, len(items) - 1))
        if old is not None:
            for i, item in enumerate(items):
                if item.choice == old.choice and item.opens is None:
                    screen.cursor = i
                    break

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
        items.append(MenuItem("Settings", opens=self._settings_screen()))
        items.append(MenuItem("Turn off", choice=Choice(POWER_MENU), detail="sleep or shut down"))
        return Screen("What do you want to watch?", items)

    def _settings_screen(self) -> Screen:
        return Screen(
            "Settings",
            [
                MenuItem("How to use the remote", opens=self._help_screen()),
                MenuItem("Sound sync", choice=Choice(SOUND_SYNC)),
                MenuItem("Pair a remote", choice=Choice(PAIR_REMOTE)),
            ],
            breadcrumb="All shows",
        )

    def _help_screen(self) -> Screen:
        pages = help_pages(self._seek_back, self._seek_forward)
        return Screen(
            "How to use the remote",
            [MenuItem(page.title, help=page) for page in pages],
            breadcrumb="Settings",
            kind="help",
        )

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
                items.append(MenuItem(ep.label, choice=Choice(PLAY_QUEUE, show, paths, i)))
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
            items.append(MenuItem(ep.label, choice=Choice(PLAY_QUEUE, show, paths, offset + i)))
        return Screen(series.name, items, breadcrumb=show.name)


def help_pages(seek_back: float = 10.0, seek_forward: float = 30.0) -> List[HelpPage]:
    """The pages of "How to use the remote", one per button (or pair)."""
    return [
        HelpPage(
            "Your remote",
            note="Press DOWN to see what each button does. Press BACK when you're done.",
        ),
        HelpPage(
            "OK",
            ("ok",),
            watching="Opens the menu",
            in_menu="Chooses the yellow one",
            note="Hold OK for a moment to shuffle.",
        ),
        HelpPage(
            "Hold OK: Shuffle",
            ("ok",),
            watching="Plays a random episode of a random show",
            note="If something is on, it asks first.",
        ),
        HelpPage(
            "Back",
            ("back",),
            watching="Opens the menu",
            in_menu="Goes back a step",
        ),
        HelpPage(
            "Home",
            ("home",),
            watching="Opens the menu",
            in_menu="Closes the menu",
            note="Hold Home for a moment to turn the box off.",
        ),
        HelpPage(
            "Hold Home: Turn off",
            ("home",),
            watching="Asks: sleep, or shut down?",
            note="Asleep? Hold Home again to wake it up.",
        ),
        HelpPage(
            "Up and down",
            ("up", "down"),
            watching="Changes the show",
            in_menu="Moves up and down the list",
        ),
        HelpPage(
            "Volume",
            ("left", "right", "minus", "plus"),
            watching="Left or minus is quieter. Right or plus is louder.",
            in_menu="Quieter and louder, too",
        ),
        HelpPage(
            "Mute",
            ("mute",),
            watching="Turns the sound off",
            note="Press it again to bring the sound back.",
        ),
        HelpPage(
            "Play and pause",
            ("play",),
            watching="Pauses, and says what's on",
            note="Press it again, or OK, to carry on.",
        ),
        HelpPage(
            "Rewind and fast forward",
            ("rewind", "forward"),
            watching=f"Back {seek_back:g} seconds, or on {seek_forward:g} seconds",
        ),
        HelpPage(
            "Pointer",
            ("pointer",),
            watching="Makes the arrows move a pointer instead. Press it again to switch back.",
            note="The box tells you if the arrows are in pointer mode.",
        ),
        HelpPage(
            "Power",
            ("power",),
            note="This one is for your TV, not the box. To turn the box off, hold Home.",
        ),
        HelpPage(
            "Using the menu",
            ("up", "down", "ok", "back"),
            in_menu="Pick a show, then Play from the start, Shuffle, or an episode",
            note="Shuffle everything is at the top. Turn off is at the bottom.",
        ),
    ]


def _count(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


__all__ = [
    "Menu",
    "MenuItem",
    "HelpPage",
    "REMOTE_KEYS",
    "help_pages",
    "Screen",
    "Choice",
    "SHUFFLE_ALL",
    "SHUFFLE_SHOW",
    "PLAY_QUEUE",
    "PAIR_REMOTE",
    "PAIR_SEARCH",
    "PAIR_DEVICE",
    "SOUND_SYNC",
    "POWER_SLEEP",
    "POWER_SHUTDOWN",
    "POWER_CANCEL",
    "POWER_MENU",
]
