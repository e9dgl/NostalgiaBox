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
