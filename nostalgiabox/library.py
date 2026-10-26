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


@dataclass(frozen=True)
class Episode:
    path: Path
    title: str


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
        rest = stem[len(show):].lstrip(" -")
        if rest:
            stem = rest
    return stem or path.name


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

    if len(groups) <= 1:
        episodes = tuple(Episode(p, episode_title(p, channel.name)) for p in channel.episodes)
        series: Tuple[Series, ...] = (Series("Episodes", episodes),) if episodes else ()
    else:
        ordered = sorted(groups.items(), key=lambda item: _series_sort(item[0]))
        series = tuple(
            Series(
                _series_label(key) if key is not None else "Other episodes",
                tuple(Episode(p, episode_title(p, channel.name)) for p in paths),
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
    "show_sort_key",
]
