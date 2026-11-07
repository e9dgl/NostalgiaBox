"""Fans multiple input backends into one queue of actions.

The application creates an :class:`InputManager`, starts it, and then simply
calls :meth:`get` in its main loop. Which backends are active is decided by
:func:`create_backends` based on the ``input:`` section of the config and on
what is actually available on the machine.
"""

from __future__ import annotations

import logging
from queue import Empty, Queue
from typing import Dict, List, Optional

from ..actions import InputEvent
from .base import InputBackend

log = logging.getLogger(__name__)


class InputManager:
    """Owns the shared event queue and the lifecycle of all input backends."""

    def __init__(self, backends: List[InputBackend]) -> None:
        self._backends = backends
        self._queue: "Queue[InputEvent]" = Queue()
        self._started = False

    @property
    def backends(self) -> List[InputBackend]:
        return list(self._backends)

    def start(self) -> None:
        if self._started:
            return
        for backend in self._backends:
            backend.start(self._queue)
        self._started = True

    def get(self, timeout: Optional[float] = None) -> Optional[InputEvent]:
        """Return the next input event, or None if none arrives within timeout."""
        try:
            return self._queue.get(timeout=timeout)
        except Empty:
            return None

    def put(self, event: InputEvent) -> None:
        """Inject an event directly (used by dev mode / scripted tests)."""
        self._queue.put(event)

    def stop(self) -> None:
        for backend in self._backends:
            try:
                backend.stop()
            except Exception:  # noqa: BLE001
                log.debug("error stopping backend %s", backend.name, exc_info=True)
        self._started = False


def create_backends(options: Optional[Dict] = None) -> List[InputBackend]:
    """Build the list of input backends from the config ``input:`` options.

    Recognised options (all optional)::

        keyboard: true            # evdev USB/IR remote & keyboard input
        cec: true                 # HDMI-CEC (TV remote)
        stdin: false              # developer terminal input
        keyboard_devices: [/dev/input/event0, ...]
        keyboard_name_filter: "remote"
        keyboard_grab: false
        cec_binary: cec-client
        cec_osd_name: NostalgiaBox

    Backends that are requested but unavailable on this machine are quietly
    skipped, so the same config works on the Pi and on a dev laptop.
    """
    options = dict(options or {})
    backends: List[InputBackend] = []

    if options.get("keyboard", True):
        from .keyboard import DEFAULT_POINTER_HINT_NAMES, KeyboardBackend
        from .keymap import DEFAULT_HOLD_SECONDS, parse_hold_actions, parse_key_overrides

        try:
            overrides = parse_key_overrides(options.get("key_overrides"))
        except ValueError as exc:
            log.error("ignoring invalid key_overrides: %s", exc)
            overrides = {}
        try:
            hold_actions = parse_hold_actions(options.get("hold_actions"))
        except ValueError as exc:
            log.error("ignoring invalid hold_actions: %s", exc)
            hold_actions = parse_hold_actions(None)
        try:
            hold_seconds = float(options.get("hold_seconds", DEFAULT_HOLD_SECONDS))
        except (TypeError, ValueError):
            hold_seconds = DEFAULT_HOLD_SECONDS
        if KeyboardBackend.is_available():
            backends.append(
                KeyboardBackend(
                    device_paths=options.get("keyboard_devices"),
                    name_filter=options.get("keyboard_name_filter"),
                    grab=bool(options.get("keyboard_grab", False)),
                    overrides=overrides,
                    hold_actions=hold_actions,
                    hold_seconds=max(0.3, hold_seconds),
                    pointer_hint_names=_name_list(
                        options.get("pointer_hint", DEFAULT_POINTER_HINT_NAMES)
                    ),
                )
            )
        else:
            log.info("evdev not available; skipping keyboard backend")

    if options.get("cec", True):
        from .cec import CecBackend

        binary = options.get("cec_binary", "cec-client")
        if CecBackend.is_available(binary):
            backends.append(
                CecBackend(
                    binary=binary,
                    osd_name=options.get("cec_osd_name", "NostalgiaBox"),
                    # The CEC adapter of the HDMI port the TV is on (a Pi 4
                    # has one per port); none means cec-client's first.
                    extra_args=[str(options["cec_device"])] if options.get("cec_device") else None,
                )
            )
        else:
            log.info("cec-client not available; skipping HDMI-CEC backend")

    if options.get("stdin", False):
        from .stdin_backend import StdinBackend

        if StdinBackend.is_available():
            backends.append(StdinBackend())

    return backends


__all__ = ["InputManager", "create_backends"]


def _name_list(raw) -> List[str]:
    """``input.pointer_hint``: device-name parts (a string, a list, or
    false/none to turn it off)."""
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    return [str(item) for item in raw]
