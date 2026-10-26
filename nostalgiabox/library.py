"""The show library the menu browses: shows, their series, and episodes.

Channels already know their episode files. This module groups those files the
way a person thinks about them:

* a show with every episode in one folder (or with no season numbers) has a
  single series, and the menu lists its episodes straight away;
* a show with ``Season 1/``, ``Series 2/`` (any sub-folder) or ``S02E05`` style
  names has several series, and the menu lists the series first.

Shows are listed A to Z by name. Nothing here touches the player.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .channel import Channel, detect_season, episode_rel, natural_sort_key

# A leading "The " is ignored when sorting, so "The Raccoons" sits under R.
_ARTICLE = re.compile(r"^the\s+", re.IGNORECASE)


# Season and episode numbers inside a file name.
_SXE = re.compile(r"\bs(\d{1,2})[ ._-]?e(\d{1,3})\b", re.IGNORECASE)   # S01E02
_NXN = re.compile(r"\b(\d{1,2})x(\d{1,3})\b")                        # 1x02
_EP = re.compile(r"\b(?:episode|ep)[ ._-]*(\d{1,3})\b", re.IGNORECASE)  # Episode 2
_LEADING = re.compile(r"^(\d{1,3})\b")                                 # 02 - Title
_SEPARATORS = " -_.:"
_CODE_START = re.compile(r"(\d|s\d{1,2}[ ._-]?e\d|ep(isode)?\b)", re.IGNORECASE)


@dataclass(frozen=True)
class Episode:
    path: Path
    title: str
    # Parsed from the file (and folder) name when it can be: series number,
    # episode number, and the episode's own name.
    season: Optional[int] = None
    number: Optional[int] = None
    name: str = ""

    @property
    def code(self) -> str:
        """``Series 1  Episode 2``, ``Episode 2``, or empty."""
        parts = []
        if self.season is not None:
            parts.append(f"Series {self.season}")
        if self.number is not None:
            parts.append(f"Episode {self.number}")
        return "  ".join(parts)

    @property
    def label(self) -> str:
        """How the menu lists it: ``2. Arthur's Eyes``, ``Episode 2``, or the title."""
        if self.number is not None and self.name:
            return f"{self.number}. {self.name}"
        if self.number is not None:
            return f"Episode {self.number}"
        return self.title

    @property
    def caption(self) -> str:
        """Second line of the now-playing card: code and name, as available."""
        parts = [p for p in (self.code, self.name) if p]
        return "  -  ".join(parts) if parts else self.title


@dataclass(frozen=True)
class Series:
    name: str
    episodes: Tuple[Episode, ...]


@dataclass(frozen=True)
class Show:
    name: str
    channel_number: int
    series: Tuple[Series, ...]

    @property
    def episodes(self) -> Tuple[Episode, ...]:
        """Every episode, first series first: the "play from the start" order."""
        return tuple(ep for s in self.series for ep in s.episodes)

    @property
    def has_several_series(self) -> bool:
        return len(self.series) > 1


def episode_title(path: Path, show: str = "") -> str:
    """A readable title from a file name.

    ``Arthur - S01E02 - Arthur's Eyes.mp4`` in the show "Arthur" becomes
    ``S01E02 - Arthur's Eyes``: the show's own name is left off the front,
    since the menu already says which show it is.
    """
    stem = " ".join(path.stem.replace("_", " ").replace(".", " ").split())
    if show and stem.lower().startswith(show.lower()):
        tail = stem[len(show):]
        rest = tail.lstrip(" -")
        # Only drop the name when a separator or an episode code follows it:
        # "Noddy Goes to Toyland" keeps its "Noddy".
        if rest and (tail.lstrip(" ").startswith("-") or _CODE_START.match(rest)):
            stem = rest
    return stem or path.name


def parse_episode(path: Path, show: str = "", folder_season: Optional[int] = None) -> Episode:
    """Read series, episode number and name out of an episode's file name.

    ``Arthur - S01E02 - Arthur's Eyes.mp4`` gives series 1, episode 2,
    "Arthur's Eyes". ``Season 3/05 Lost.mp4`` gives series 3, episode 5,
    "Lost". Anything not found is left empty.
    """
    title = episode_title(path, show)
    text = title
    season = number = None
    for pattern in (_SXE, _NXN):
        match = pattern.search(text)
        if match:
            season, number = int(match.group(1)), int(match.group(2))
            text = text[:match.start()] + " " + text[match.end():]
            break
    else:
        match = _EP.search(text) or _LEADING.search(text)
        if match:
            value = match.group(1)
            if match.re is _LEADING and len(value) == 3 and folder_season is None:
                # 101, 102: series 1, episodes 1 and 2.
                season, number = int(value[0]), int(value[1:])
            else:
                number = int(value)
            text = text[:match.start()] + " " + text[match.end():]
    if season is None:
        season = folder_season
    name = " ".join(text.split()).strip(_SEPARATORS).strip()
    if show and name.lower() == show.lower():
        name = ""
    return Episode(path, title, season, number, name)


def _series_key(rel: str) -> Optional[str]:
    """The group an episode belongs to: its first sub-folder, else its season."""
    parts = rel.split("/")
    if len(parts) > 1:
        return parts[0]
    season = detect_season(parts[0])
    return f"Series {season}" if season is not None else None


def _series_label(key: str) -> str:
    season = detect_season(key) if not key.lower().startswith("series ") else None
    if season is not None:
        return f"Series {season}"
    return key


def _folder_season(key: Optional[str]) -> Optional[int]:
    """The series number a group's name gives (``Season 2``, ``Series 2``)."""
    if key is None:
        return None
    season = detect_season(key)
    if season is None:
        match = re.search(r"\bseries[ ._-]*(\d{1,2})\b", key, re.IGNORECASE)
        season = int(match.group(1)) if match else None
    return season


def _series_sort(key: Optional[str]) -> tuple:
    # Loose files next to series folders are usually extras, so they go last.
    if key is None:
        return (2, 0, ())
    season = detect_season(key)
    if season is None:
        match = re.search(r"(\d+)", key)
        season = int(match.group(1)) if match else None
    return (1, season if season is not None else 10_000, natural_sort_key(key))


def build_show(channel: Channel) -> Show:
    """Group one channel's episodes into series."""
    root = channel.config.path
    groups: Dict[Optional[str], List[Path]] = {}
    for path in channel.episodes:
        groups.setdefault(_series_key(episode_rel(root, path)), []).append(path)

    def episode(path: Path, key: Optional[str]) -> Episode:
        return parse_episode(path, channel.name, _folder_season(key))

    if len(groups) <= 1:
        (key,) = groups or (None,)
        episodes = tuple(episode(p, key) for p in channel.episodes)
        series: Tuple[Series, ...] = (Series("Episodes", episodes),) if episodes else ()
    else:
        ordered = sorted(groups.items(), key=lambda item: _series_sort(item[0]))
        series = tuple(
            Series(
                _series_label(key) if key is not None else "Other episodes",
                tuple(episode(p, key) for p in paths),
            )
            for key, paths in ordered
        )
    return Show(name=channel.name, channel_number=channel.number, series=series)


def show_sort_key(name: str) -> tuple:
    return natural_sort_key(_ARTICLE.sub("", name.strip()))


def build_library(channels: Sequence[Channel]) -> List[Show]:
    """Every show with at least one episode, A to Z."""
    shows = [build_show(ch) for ch in channels if ch.episodes]
    shows.sort(key=lambda s: show_sort_key(s.name))
    return shows


__all__ = [
    "Episode",
    "Series",
    "Show",
    "build_show",
    "build_library",
    "episode_title",
    "parse_episode",
    "show_sort_key",
]
