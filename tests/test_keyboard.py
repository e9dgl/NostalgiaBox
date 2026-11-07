"""The evdev backend, against a fake evdev module (no real devices needed)."""

import os
import sys
import time
import types
from queue import Queue

import pytest

from nostalgiabox.actions import Action
from nostalgiabox.input import keyboard as kb

EV_KEY = 1
EV_REL = 2
REL_WHEEL = 8
KEY_NAMES = {
    103: "KEY_UP",
    172: "KEY_HOMEPAGE",
    353: "KEY_SELECT",
    113: ["KEY_MIN_INTERESTING", "KEY_MUTE"],
    158: "KEY_BACK",
    164: "KEY_PLAYPAUSE",
    272: ["BTN_LEFT", "BTN_MOUSE"],
}


class FakeDevice:
    """Looks like evdev.InputDevice; reads come from a real pipe so select works."""

    registry = {}

    def __init__(self, path):
        spec = self.registry[path]
        self.path = path
        self.name = spec["name"]
        self._read_fd, self._write_fd = os.pipe()
        self.fd = self._read_fd
        self._events = []
        spec["device"] = self

    def capabilities(self):
        return {EV_KEY: [103]}

    def press(self, code, *, type=EV_KEY, value=1):
        self._events.append(types.SimpleNamespace(type=type, code=code, value=value))
        os.write(self._write_fd, b"x")

    def read(self):
        os.read(self._read_fd, 64)
        events, self._events = self._events, []
        return events

    def close(self):
        if self.fd >= 0:
            os.close(self._read_fd)
            os.close(self._write_fd)
            self.fd = -1


@pytest.fixture
def fake_evdev(monkeypatch):
    FakeDevice.registry = {}
    ecodes = types.SimpleNamespace(
        EV_KEY=EV_KEY, EV_REL=EV_REL, REL_WHEEL=REL_WHEEL, KEY=KEY_NAMES, keys=KEY_NAMES
    )
    module = types.SimpleNamespace(
        InputDevice=FakeDevice,
        list_devices=lambda: list(FakeDevice.registry),
        ecodes=ecodes,
    )
    monkeypatch.setitem(sys.modules, "evdev", module)
    monkeypatch.setitem(sys.modules, "evdev.ecodes", ecodes)
    monkeypatch.setattr(kb, "_RESCAN_SECONDS", 0.05)
    yield FakeDevice.registry
    for spec in FakeDevice.registry.values():
        if "device" in spec:
            spec["device"].close()


def _wait_for(predicate, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_remote_that_connects_later_is_picked_up(fake_evdev):
    backend = kb.KeyboardBackend()
    events = []
    backend.emit = events.append
    backend.start(Queue())
    try:
        # Nothing connected at start-up: the backend keeps waiting.
        time.sleep(0.1)
        fake_evdev["/dev/input/event5"] = {"name": "HID Remote01"}
        assert _wait_for(lambda: "device" in fake_evdev["/dev/input/event5"])
        fake_evdev["/dev/input/event5"]["device"].press(158)
        assert _wait_for(lambda: events)
        assert events[0].action == Action.BACK
    finally:
        backend.stop()


def test_key_with_several_names_still_maps(fake_evdev):
    fake_evdev["/dev/input/event1"] = {"name": "HID Remote01"}
    backend = kb.KeyboardBackend()
    events = []
    backend.emit = events.append
    backend.start(Queue())
    try:
        assert _wait_for(lambda: "device" in fake_evdev["/dev/input/event1"])
        fake_evdev["/dev/input/event1"]["device"].press(113)  # mute
        assert _wait_for(lambda: events)
        assert events[0].action == Action.MUTE
    finally:
        backend.stop()


def test_media_keys_and_a_usb_mouse(fake_evdev):
    fake_evdev["/dev/input/event2"] = {"name": "USB Mouse"}
    backend = kb.KeyboardBackend()
    events = []
    backend.emit = events.append
    backend.start(Queue())
    try:
        assert _wait_for(lambda: "device" in fake_evdev["/dev/input/event2"])
        dev = fake_evdev["/dev/input/event2"]["device"]
        dev.press(164)                                   # play/pause
        dev.press(272)                                   # left click
        dev.press(REL_WHEEL, type=EV_REL, value=-1)      # wheel down
        assert _wait_for(lambda: len(events) == 3)
        assert [e.action for e in events] == [
            Action.PLAY_PAUSE, Action.ENTER, Action.CHANNEL_DOWN,
        ]
    finally:
        backend.stop()


def _hold_backend(fake_evdev, path):
    from nostalgiabox.input.keymap import parse_hold_actions

    fake_evdev[path] = {"name": "HID Remote01 Consumer Control"}
    backend = kb.KeyboardBackend(hold_actions=parse_hold_actions(None), hold_seconds=0.3)
    events = []
    backend.emit = events.append
    backend.start(Queue())
    assert _wait_for(lambda: "device" in fake_evdev[path])
    return backend, events, fake_evdev[path]["device"]


def test_quick_press_of_ok_and_home_does_their_usual_job(fake_evdev):
    backend, events, dev = _hold_backend(fake_evdev, "/dev/input/event3")
    try:
        dev.press(353)            # OK down
        dev.press(353, value=0)   # ...and straight up
        dev.press(172)            # Home down
        dev.press(172, value=0)
        assert _wait_for(lambda: len(events) == 2)
        assert [e.action for e in events] == [Action.ENTER, Action.MENU]
    finally:
        backend.stop()


def test_holding_home_turns_off_and_holding_ok_shuffles(fake_evdev):
    backend, events, dev = _hold_backend(fake_evdev, "/dev/input/event4")
    try:
        dev.press(172)                         # hold Home...
        assert _wait_for(lambda: len(events) == 1)
        assert events[0].action == Action.POWER
        dev.press(172, value=2)                # autorepeat while held: ignored
        dev.press(172, value=0)                # letting go does nothing more
        dev.press(353)                         # hold OK
        assert _wait_for(lambda: len(events) == 2)
        dev.press(353, value=0)
        time.sleep(0.4)
        assert [e.action for e in events] == [Action.POWER, Action.SHUFFLE]
    finally:
        backend.stop()


def test_mouse_click_can_be_held_too(fake_evdev):
    backend, events, dev = _hold_backend(fake_evdev, "/dev/input/event6")
    try:
        dev.press(272)                         # left click held (pointer mode OK)
        assert _wait_for(lambda: len(events) == 1)
        assert events[0].action == Action.SHUFFLE
    finally:
        backend.stop()
