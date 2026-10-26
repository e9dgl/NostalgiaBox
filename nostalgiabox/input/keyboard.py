"""Keyboard / USB / IR remote input via Linux evdev.

Most cheap "media remotes" (and IR remotes bridged through a USB receiver or
LIRC's uinput) show up to Linux as ordinary keyboard-like input devices. This
backend reads their key-down events straight from ``/dev/input/event*`` using
the ``evdev`` package - no X server or desktop required, which is exactly what
we want on a headless Pi wired to a TV.

Bluetooth remotes come and go: they connect a while after boot, and they drop
off to save battery and reconnect on the next press, each time as a new
``/dev/input/event*``. So the backend looks for new devices every couple of
seconds rather than only once at start-up.
"""

from __future__ import annotations

import logging
import select
import time
from typing import Dict, List, Optional, Sequence

from ..actions import Action, InputEvent
from .base import InputBackend
from .keymap import evdev_key_to_event

log = logging.getLogger(__name__)

# Key-event values reported by evdev: 0=up, 1=down, 2=autorepeat.
_KEY_UP = 0
_KEY_DOWN = 1
_KEY_REPEAT = 2

# Channel surfing may repeat while the key is held. Volume and mute must not:
# Flirc often delays or drops the key-up, and the kernel then keeps sending
# autorepeat. Treating those as more presses is why one tap kept raising the
# sound after the on-screen bar had gone.
_REPEAT_ACTIONS = frozenset({Action.CHANNEL_UP, Action.CHANNEL_DOWN})

# How often to look for a remote that has just connected (Bluetooth).
_RESCAN_SECONDS = 2.0


def key_event_should_emit(value: int, action: Action, *, allow_repeat: bool) -> bool:
    """Return True when this evdev key state should become one app action.

    ``value`` is 0 (released), 1 (pressed), or 2 (autorepeat). A press emits
    once. A release never emits. Autorepeat emits only for channel keys, and
    only when the backend allows it. Volume, mute, digits, and power ignore
    repeat, so a stuck or missed key-up cannot keep stepping.
    """
    if value == _KEY_DOWN:
        return True
    if value == _KEY_REPEAT and allow_repeat and action in _REPEAT_ACTIONS:
        return True
    return False


class KeyboardBackend(InputBackend):
    """Reads remote/keyboard events from evdev input devices."""

    name = "keyboard"

    def __init__(
        self,
        *,
        device_paths: Optional[Sequence[str]] = None,
        name_filter: Optional[str] = None,
        grab: bool = False,
        allow_repeat: bool = True,
        overrides: Optional[Dict[str, Optional[InputEvent]]] = None,
    ) -> None:
        super().__init__()
        self._device_paths = list(device_paths) if device_paths else None
        self._name_filter = name_filter.lower() if name_filter else None
        self._grab = grab
        self._allow_repeat = allow_repeat
        # Per-key action overrides from config (key name -> InputEvent or None).
        self._overrides = dict(overrides or {})
        self._devices: List = []

    def _lookup(self, key_name: str) -> Optional[InputEvent]:
        """Config overrides win over the built-in defaults."""
        if key_name in self._overrides:
            return self._overrides[key_name]  # may be None (explicitly unbound)
        return evdev_key_to_event(key_name)

    @staticmethod
    def is_available() -> bool:
        try:
            import evdev  # noqa: F401
        except ImportError:
            return False
        return True

    def _open_devices(self, skip: Sequence[str] = ()):
        """Open every usable input device not already open (paths in ``skip``)."""
        import evdev
        from evdev import ecodes

        paths = self._device_paths or evdev.list_devices()
        devices = []
        for path in paths:
            if path in skip:
                continue
            try:
                dev = evdev.InputDevice(path)
            except (OSError, PermissionError) as exc:
                log.warning("cannot open input device %s: %s", path, exc)
                continue
            caps = dev.capabilities()
            if ecodes.EV_KEY not in caps:
                dev.close()
                continue
            if self._name_filter and self._name_filter not in (dev.name or "").lower():
                dev.close()
                continue
            if self._grab:
                try:
                    dev.grab()
                except OSError:
                    log.warning("could not grab %s (continuing ungrabbed)", dev.name)
            log.info("listening to input device: %s (%s)", dev.name, path)
            devices.append(dev)
        return devices

    def _run(self) -> None:
        if not self.is_available():
            log.error("evdev is not installed; keyboard/remote input disabled")
            return
        self._devices = self._open_devices()
        if not self._devices:
            log.warning("no input devices yet; waiting for a remote to connect")

        from evdev import ecodes

        next_scan = time.monotonic() + _RESCAN_SECONDS
        while not self.stopping:
            fd_to_device = {dev.fd: dev for dev in self._devices}
            try:
                r, _, _ = select.select(list(fd_to_device), [], [], 0.5)
            except (OSError, ValueError):
                # A device vanished between listing and select: drop dead ones.
                self._devices = [d for d in self._devices if _is_open(d)]
                continue
            for fd in r:
                dev = fd_to_device.get(fd)
                if dev is None:
                    continue
                try:
                    for event in dev.read():
                        if event.type == ecodes.EV_KEY:
                            self._handle_key_event(event)
                        elif event.type == ecodes.EV_REL and event.code == ecodes.REL_WHEEL:
                            self._handle_wheel(event.value)
                except OSError:
                    log.info("input device went away: %s", getattr(dev, "name", "?"))
                    self._drop(dev)
            if time.monotonic() >= next_scan:
                next_scan = time.monotonic() + _RESCAN_SECONDS
                self._devices.extend(
                    self._open_devices(skip=[d.path for d in self._devices])
                )

    def _handle_wheel(self, value: int) -> None:
        """A mouse wheel moves through the menu: up is up, down is down."""
        if value > 0:
            self.emit(InputEvent(Action.CHANNEL_UP))
        elif value < 0:
            self.emit(InputEvent(Action.CHANNEL_DOWN))

    def _drop(self, dev) -> None:
        """Forget a device that disconnected (a Bluetooth remote going to sleep)."""
        try:
            dev.close()
        except OSError:
            pass
        self._devices = [d for d in self._devices if d is not dev]

    def _handle_key_event(self, event) -> None:
        from evdev import ecodes

        # Key-up is ignored here. Volume stops because repeats are not turned
        # into more volume steps (see key_event_should_emit), so the stream
        # ending — or a missing key-up — cannot keep moving the level.
        if event.value == _KEY_UP:
            return

        input_event = None
        # One code can have several names (113 is both KEY_MIN_INTERESTING and
        # KEY_MUTE), so try each until one means something.
        # ``ecodes.keys`` covers KEY_* and BTN_* (mouse buttons) together.
        for key_name in _code_to_names(getattr(ecodes, "keys", ecodes.KEY), event.code):
            input_event = self._lookup(key_name)
            if input_event is not None:
                break
        if input_event is None:
            return
        if not key_event_should_emit(
            event.value, input_event.action, allow_repeat=self._allow_repeat
        ):
            return
        self.emit(input_event)

    def _close(self) -> None:
        for dev in self._devices:
            try:
                if self._grab:
                    dev.ungrab()
            except OSError:
                pass
            try:
                dev.close()
            except OSError:
                pass
        self._devices = []


def _is_open(dev) -> bool:
    try:
        return dev.fd >= 0
    except (OSError, ValueError):
        return False


def _code_to_names(key_table, code: int) -> List[str]:
    """evdev's KEY table maps a code to a name or a list of aliases."""
    name = key_table.get(code)
    if name is None:
        return []
    if isinstance(name, (list, tuple)):
        return list(name)
    return [name]


__all__ = ["KeyboardBackend", "key_event_should_emit"]
