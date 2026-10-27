"""Which HDMI port the TV is plugged into.

A Raspberry Pi 4 has two micro-HDMI ports. Picture, sound and the TV remote
(HDMI-CEC) each belong to one port, so a box set up for HDMI 0 would play
silently, and ignore the TV remote, when the cable is in HDMI 1.

NostalgiaBox therefore looks for the port with a TV on it when it starts and
uses that one for everything: picture, sound and CEC. Only one port is ever
used. If both have a TV, HDMI 0 (next to the power socket) wins. If the cable
is moved while the box is on, :class:`HdmiWatcher` notices and the app restarts
itself on the new port.

On a Pi 3 there is one port, so this always picks it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

DRM_ROOT = Path("/sys/class/drm")
ASOUND_CARDS = Path("/proc/asound/cards")
DEV_ROOT = Path("/dev")

_CONNECTOR = re.compile(r"^card\d+-(HDMI-A-(\d+))$")
_CARD_ID = re.compile(r"^\s*\d+\s+\[([^\]]+?)\s*\]")


@dataclass(frozen=True)
class HdmiPort:
    connector: str      # the DRM name, e.g. "HDMI-A-2"
    index: int          # 0 for the port printed HDMI0, 1 for HDMI1
    connected: bool

    @property
    def label(self) -> str:
        return f"HDMI {self.index}"


@dataclass(frozen=True)
class HdmiOutput:
    """Everything that has to point at the same port."""

    connector: str
    index: int
    audio_device: Optional[str]   # mpv's name for that port's sound
    cec_device: Optional[str]     # /dev/cecN for that port's TV remote

    @property
    def label(self) -> str:
        return f"HDMI {self.index}"


def list_ports(drm_root: Path = DRM_ROOT) -> List[HdmiPort]:
    """The HDMI ports the graphics driver knows about, HDMI 0 first."""
    ports = []
    try:
        entries = list(drm_root.iterdir())
    except OSError:
        return []
    for entry in entries:
        match = _CONNECTOR.match(entry.name)
        if not match:
            continue
        try:
            status = (entry / "status").read_text().strip()
        except OSError:
            status = ""
        ports.append(HdmiPort(match.group(1), int(match.group(2)) - 1, status == "connected"))
    ports.sort(key=lambda p: p.index)
    return ports


def connected_port(ports: List[HdmiPort]) -> Optional[HdmiPort]:
    """The port to use: the first one with a TV on it, if any."""
    return next((p for p in ports if p.connected), None)


def hdmi_sound_cards(cards_file: Path = ASOUND_CARDS) -> List[str]:
    """ALSA card ids of the Pi's HDMI sound (``vc4hdmi``, ``vc4hdmi0``, ...)."""
    try:
        text = cards_file.read_text()
    except OSError:
        return []
    ids = []
    for line in text.splitlines():
        match = _CARD_ID.match(line)
        if match and match.group(1).startswith("vc4hdmi"):
            ids.append(match.group(1))
    return ids


def audio_device_for(index: int, cards: List[str]) -> Optional[str]:
    """mpv's audio device for one port's HDMI sound."""
    if not cards:
        return None
    if len(cards) == 1:
        # A Pi 3 (or a Pi 4 set to one port) has a single HDMI sound card.
        card = cards[0]
    else:
        card = f"vc4hdmi{index}"
        if card not in cards:
            return None
    return f"alsa/hdmi:CARD={card},DEV=0"


def detect_output(
    drm_root: Path = DRM_ROOT,
    cards_file: Path = ASOUND_CARDS,
    dev_root: Path = DEV_ROOT,
) -> Optional[HdmiOutput]:
    """The port with a TV on it and its sound and CEC devices, or None when no
    TV is found (the TV is unplugged, or this is not a Pi)."""
    port = connected_port(list_ports(drm_root))
    if port is None:
        return None
    cec = dev_root / f"cec{port.index}"
    return HdmiOutput(
        connector=port.connector,
        index=port.index,
        audio_device=audio_device_for(port.index, hdmi_sound_cards(cards_file)),
        cec_device=str(cec) if cec.exists() else None,
    )


def resolve_audio_device(configured: Optional[str], output: Optional[HdmiOutput]) -> Optional[str]:
    """The sound device to use.

    Left out, ``auto``, or any Pi HDMI device: the port the TV is on, so an old
    ``vc4hdmi0`` setting cannot send the sound to the empty port. Anything else
    (a USB sound card, say) is used as written.
    """
    value = (configured or "").strip()
    if value.lower() in ("", "auto") or "vc4hdmi" in value:
        if output is not None and output.audio_device:
            if value and value.lower() != "auto" and value != output.audio_device:
                log.info(
                    "audio_device %s is the other HDMI port; using %s, where the TV is",
                    value, output.audio_device,
                )
            return output.audio_device
        return None if value.lower() in ("", "auto") else value
    return value


class HdmiWatcher:
    """Notices when the TV moves to the other port while the box is on.

    It only asks for a switch when the port in use has lost its TV and another
    port has one, seen on two checks in a row, so a TV that briefly drops the
    signal while changing input does not cause a restart.
    """

    def __init__(
        self,
        current: Optional[str],
        *,
        drm_root: Path = DRM_ROOT,
        interval: float = 2.0,
    ) -> None:
        self.current = current
        self._drm_root = drm_root
        self._interval = interval
        self._next_check = 0.0
        self._seen: Optional[str] = None

    def check(self, now: float) -> Optional[str]:
        """The connector to switch to, once it is certain; otherwise None."""
        if now < self._next_check:
            return None
        self._next_check = now + self._interval
        ports = list_ports(self._drm_root)
        target = None
        current = next((p for p in ports if p.connector == self.current), None)
        if current is None or not current.connected:
            port = connected_port(ports)
            if port is not None and port.connector != self.current:
                target = port.connector
        if target is None or target != self._seen:
            self._seen = target
            return None
        return target


__all__ = [
    "HdmiOutput",
    "HdmiPort",
    "HdmiWatcher",
    "audio_device_for",
    "connected_port",
    "detect_output",
    "hdmi_sound_cards",
    "list_ports",
    "resolve_audio_device",
]
