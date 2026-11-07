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
from .keymap import DEFAULT_HOLD_SECONDS, evdev_key_to_event

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

# Air-mouse remotes: the pointer button switches the arrows into moving an
# invisible mouse pointer, with no light to say so, so the arrows seem broken.
# The pointer button itself sends nothing, so the first sign is the pointer
# moving or clicking. From then on the app blocks the buttons and says how to
# switch back, until the remote sends a normal arrow or OK again. Movement
# this far (mouse units, in one burst) counts; a gap this long ends a burst;
# and the reminder is not repeated more often than this.
_MOTION_GAP = 0.3
_MOTION_THRESHOLD = 2
_POINTER_HINT_EVERY = 3.0
# Keys an air mouse only sends with normal arrows: seeing one means pointer
# mode is off again.
_NORMAL_MODE_KEYS = frozenset({
    "KEY_UP", "KEY_DOWN", "KEY_LEFT", "KEY_RIGHT", "KEY_SELECT", "KEY_OK", "KEY_ENTER",
})
DEFAULT_POINTER_HINT_NAMES = ("hid remote",)


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
        hold_actions: Optional[Dict[str, Optional[InputEvent]]] = None,
        hold_seconds: float = DEFAULT_HOLD_SECONDS,
        pointer_hint_names: Sequence[str] = (),
    ) -> None:
        super().__init__()
        self._device_paths = list(device_paths) if device_paths else None
        self._name_filter = name_filter.lower() if name_filter else None
        self._grab = grab
        self._allow_repeat = allow_repeat
        # Per-key action overrides from config (key name -> InputEvent or None).
        self._overrides = dict(overrides or {})
        # Key name -> what holding it does (see keymap.DEFAULT_HOLD_ACTIONS).
        self._hold_actions = {k: v for k, v in (hold_actions or {}).items() if v is not None}
        self._hold_seconds = hold_seconds
        # Keys with a hold job that are down right now:
        # code -> [pressed at, tap event, hold event, hold already fired?]
        self._held: Dict[int, list] = {}
        # Air-mouse remotes whose pointer movement brings up the hint.
        self._pointer_hint_names = [n.lower() for n in pointer_hint_names if n]
        # Pointer movement: [last move, |dx| so far, |dy| so far, last hint, hinted?]
        self._motion = [0.0, 0, 0, 0.0, False]
        # True once an air mouse has moved its pointer or clicked, until it
        # sends a normal arrow or OK again.
        self._pointer_on = False
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
            # While a hold-able key is down, wake often to notice the hold.
            timeout = 0.05 if self._held else 0.5
            try:
                r, _, _ = select.select(list(fd_to_device), [], [], timeout)
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
                            self._handle_key_event(event, dev)
                        elif event.type == ecodes.EV_REL and event.code == ecodes.REL_WHEEL:
                            self._handle_wheel(event.value)
                        elif event.type == ecodes.EV_REL and self._is_air_mouse(dev):
                            self._handle_motion(
                                event.code, event.value, time.monotonic(),
                                rel_x=getattr(ecodes, "REL_X", 0),
                                rel_y=getattr(ecodes, "REL_Y", 1),
                            )
                except OSError:
                    log.info("input device went away: %s", getattr(dev, "name", "?"))
                    self._drop(dev)
            self._check_holds(time.monotonic())
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

    def _is_air_mouse(self, dev) -> bool:
        name = (getattr(dev, "name", "") or "").lower()
        return any(n in name for n in self._pointer_hint_names)

    def _handle_motion(self, code: int, value: int, now: float, *, rel_x: int, rel_y: int) -> None:
        """The remote's pointer moved: once it has moved a little, ask the
        app to explain pointer mode (not more than every few seconds)."""
        if code not in (rel_x, rel_y):
            return
        state = self._motion
        if now - state[0] > _MOTION_GAP:
            state[1] = state[2] = 0  # a new burst
        state[0] = now
        if code == rel_x:
            state[1] += abs(value)
        else:
            state[2] += abs(value)
        if state[1] + state[2] < _MOTION_THRESHOLD:
            return
        self._pointer_mode_seen(now)

    def _pointer_mode_seen(self, now: float, *, always: bool = False) -> None:
        """The air mouse is in pointer mode: tell the app straight away the
        first time, then remind it now and then while it carries on."""
        state = self._motion
        if self._pointer_on and not always and now - state[3] < _POINTER_HINT_EVERY:
            return
        self._pointer_on = True
        state[3], state[4] = now, True
        self.emit(InputEvent(Action.POINTER_MODE))

    def _drop(self, dev) -> None:
        """Forget a device that disconnected (a Bluetooth remote going to sleep)."""
        try:
            dev.close()
        except OSError:
            pass
        self._devices = [d for d in self._devices if d is not dev]
        # A remote that drops off mid-press never sends the key-up: forget
        # the press rather than firing its hold job.
        self._held.clear()

    def _check_holds(self, now: float) -> None:
        """Fire the hold job of any key held long enough (once per press)."""
        for state in self._held.values():
            pressed_at, _tap, hold, fired = state
            if not fired and now - pressed_at >= self._hold_seconds:
                state[3] = True
                log.info("button held: %s", hold.action.name)
                self.emit(hold)

    def _handle_key_event(self, event, dev=None) -> None:
        from evdev import ecodes

        names = _code_to_names(getattr(ecodes, "keys", ecodes.KEY), event.code)
        if dev is not None and self._is_air_mouse(dev):
            if any(n.startswith("BTN_") for n in names):
                # A click from the air mouse only happens in pointer mode.
                if event.value == _KEY_DOWN:
                    self._pointer_mode_seen(time.monotonic(), always=True)
                return
            if self._pointer_on and event.value == _KEY_DOWN and _NORMAL_MODE_KEYS & set(names):
                self._pointer_on = False
                log.info("air mouse back to normal arrows")
                self.emit(InputEvent(Action.POINTER_OFF))
        hold = next((self._hold_actions[n] for n in names if n in self._hold_actions), None)
        if hold is not None or event.code in self._held:
            self._handle_holdable(event, hold, names)
            return

        # Key-up is ignored here. Volume stops because repeats are not turned
        # into more volume steps (see key_event_should_emit), so the stream
        # ending — or a missing key-up — cannot keep moving the level.
        if event.value == _KEY_UP:
            return

        # One code can have several names (113 is both KEY_MIN_INTERESTING and
        # KEY_MUTE), so try each until one means something.
        # ``ecodes.keys`` covers KEY_* and BTN_* (mouse buttons) together.
        input_event = self._tap_event(names)
        if input_event is None:
            if event.value == 1:
                # Says which button was pressed, so it can be given a job with
                # key_overrides (journalctl -u nostalgiabox | grep "does nothing").
                log.info("button %s does nothing (code %d)", "/".join(names) or "?", event.code)
            return
        if not key_event_should_emit(
            event.value, input_event.action, allow_repeat=self._allow_repeat
        ):
            return
        self.emit(input_event)

    def _tap_event(self, names: Sequence[str]) -> Optional[InputEvent]:
        for key_name in names:
            input_event = self._lookup(key_name)
            if input_event is not None:
                return input_event
        return None

    def _handle_holdable(self, event, hold: Optional[InputEvent], names) -> None:
        """A key with a hold job acts when it is let go (a quick press does
        its usual job) or once it has been held long enough (its hold job)."""
        if event.value == _KEY_DOWN:
            if hold is None:
                return
            self._held[event.code] = [time.monotonic(), self._tap_event(names), hold, False]
        elif event.value == _KEY_UP:
            state = self._held.pop(event.code, None)
            if state is None:
                return
            pressed_at, tap, _hold, fired = state
            if fired:
                return
            if time.monotonic() - pressed_at >= self._hold_seconds:
                # Held long enough but the loop had not noticed yet.
                self.emit(_hold)
            elif tap is not None:
                self.emit(tap)
        # Autorepeat (value 2) is ignored: the timer decides when it is a hold.

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
