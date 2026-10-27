import threading

from nostalgiabox.actions import Action
from nostalgiabox.bluetooth import BluetoothPairer, parse_devices
from tests.test_menu import _app, send

REMOTE = "AA:BB:CC:11:22:33"


class FakeBluetoothctl:
    """Answers bluetoothctl commands like a Pi with one remote nearby."""

    def __init__(self, *, pair_ok=True):
        self.calls = []
        self.pair_ok = pair_ok
        self.paired = set()
        self.connected = set()
        self.scan_started = threading.Event()

    def __call__(self, args, timeout):
        args = list(args)
        self.calls.append(args)
        if args[-2:] == ["scan", "on"]:
            self.scan_started.set()
            return 0, ""
        if args == ["devices"]:
            return 0, (
                f"Device {REMOTE} HID Remote01\n"
                "Device 11:22:33:44:55:66 11-22-33-44-55-66\n"
                "Device 77:88:99:AA:BB:CC Kitchen Speaker\n"
            )
        if args == ["devices", "Paired"]:
            return 0, "".join(f"Device {a} x\n" for a in self.paired)
        if args == ["devices", "Connected"]:
            return 0, "".join(f"Device {a} x\n" for a in self.connected)
        if "pair" in args:
            if not self.pair_ok:
                return 1, "Failed to pair: org.bluez.Error.AuthenticationFailed"
            self.paired.add(args[-1])
            return 0, "Pairing successful"
        if "connect" in args:
            self.connected.add(args[-1])
            return 0, "Connection successful"
        return 0, ""


def test_parse_devices():
    assert parse_devices(f"Device {REMOTE} HID Remote01\nnoise\n") == [(REMOTE, "HID Remote01")]


def test_search_lists_named_devices():
    fake = FakeBluetoothctl()
    pairer = BluetoothPairer(fake, search_seconds=2, scan_window=1)
    assert pairer.search()
    pairer.wait(5)
    names = [d.name for d in pairer.devices]
    assert names == ["HID Remote01", "Kitchen Speaker"]  # unnamed device skipped
    assert ["power", "on"] in fake.calls
    assert "Choose your remote" in pairer.status


def test_pair_trusts_and_connects():
    fake = FakeBluetoothctl()
    pairer = BluetoothPairer(fake, search_seconds=1, scan_window=1)
    pairer.search()
    pairer.wait(5)
    pairer.pair(REMOTE)
    pairer.wait(5)
    assert ["trust", REMOTE] in fake.calls
    assert any("NoInputNoOutput" in c for c in fake.calls)
    remote = next(d for d in pairer.devices if d.address == REMOTE)
    assert remote.paired and remote.connected
    assert "is ready" in pairer.status


def test_pair_failure_says_so():
    pairer = BluetoothPairer(FakeBluetoothctl(pair_ok=False), search_seconds=1, scan_window=1)
    pairer.pair(REMOTE)
    pairer.wait(5)
    assert "Could not pair" in pairer.status


def test_pairing_during_a_search_waits_for_it():
    fake = FakeBluetoothctl()
    pairer = BluetoothPairer(fake, search_seconds=60, scan_window=1)
    pairer.search()
    assert fake.scan_started.wait(5)
    assert pairer.pair(REMOTE)  # does not block or get refused
    pairer.wait(10)
    assert REMOTE in fake.connected


def test_pair_a_remote_from_the_menu(tmp_path):
    fake = FakeBluetoothctl()
    app, player, _ = _app(tmp_path)
    app.pairer = BluetoothPairer(fake, search_seconds=1, scan_window=1)
    app.start()
    send(app, Action.BACK)
    app.menu.screen.cursor = len(app.menu.screen.items) - 1
    assert app.menu.screen.items[-1].label == "Pair a remote"
    send(app, Action.ENTER)
    assert app.menu.screen.title == "Pair a remote"
    app.pairer.wait(5)
    app.step()
    labels = [i.label for i in app.menu.screen.items]
    assert labels == ["Search for remotes", "HID Remote01", "Kitchen Speaker"]
    assert "Pair a remote" in player.overlays[5]

    send(app, Action.CHANNEL_DOWN)
    send(app, Action.ENTER)
    app.pairer.wait(5)
    app.step()
    assert app.menu.is_open  # pairing never closes the menu
    assert "is ready" in app.menu.screen.note
    assert app.menu.screen.items[1].detail == "connected"
