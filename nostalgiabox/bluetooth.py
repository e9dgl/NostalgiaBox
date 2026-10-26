"""Pairing a Bluetooth remote from the TV, using ``bluetoothctl``.

The "Pair a remote" screen in the menu is driven by a USB keyboard or mouse
(the Bluetooth remote is the thing that is not working yet). It searches for
nearby devices, lists them, and pairs, trusts and connects the one chosen.
Trusting it is what makes it reconnect by itself after a reboot or a nap.

All ``bluetoothctl`` calls run on a background thread so the TV keeps playing
and the menu keeps responding. The screen reads :attr:`BluetoothPairer.status`
and :attr:`BluetoothPairer.devices`, and redraws when :attr:`version` changes.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

# runner(args, timeout) -> (exit code, output). Swapped out in tests.
Runner = Callable[[Sequence[str], float], Tuple[int, str]]

_DEVICE_LINE = re.compile(r"^Device\s+([0-9A-F]{2}(?::[0-9A-F]{2}){5})\s*(.*)$", re.IGNORECASE)
# bluetoothctl names an unnamed device after its address, with dashes.
_ADDRESS_NAME = re.compile(r"^([0-9A-F]{2}-){5}[0-9A-F]{2}$", re.IGNORECASE)


def run_bluetoothctl(args: Sequence[str], timeout: float) -> Tuple[int, str]:
    """Run one ``bluetoothctl`` command and return its exit code and output."""
    try:
        done = subprocess.run(
            ["bluetoothctl", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return 127, "bluetoothctl is not installed"
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        return 124, out if isinstance(out, str) else out.decode(errors="replace")
    return done.returncode, (done.stdout or "") + (done.stderr or "")


@dataclass(frozen=True)
class BtDevice:
    address: str
    name: str
    paired: bool = False
    connected: bool = False


def parse_devices(output: str) -> List[Tuple[str, str]]:
    """``Device AA:BB:.. Name`` lines from ``bluetoothctl devices``."""
    found = []
    for line in output.splitlines():
        match = _DEVICE_LINE.match(line.strip())
        if match:
            found.append((match.group(1).upper(), match.group(2).strip()))
    return found


class BluetoothPairer:
    """Searches for and pairs Bluetooth remotes in the background."""

    def __init__(
        self,
        runner: Runner = run_bluetoothctl,
        *,
        search_seconds: float = 20.0,
        scan_window: float = 4.0,
    ) -> None:
        self._run = runner
        self.search_seconds = search_seconds
        self.scan_window = scan_window
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.devices: List[BtDevice] = []
        self.status = "Press OK on Search to look for remotes."
        self.busy = False
        self._searching = False
        self._cancel = threading.Event()
        self._pending: Optional[str] = None
        # Bumped on every change so the screen knows to redraw.
        self.version = 0

    # -- public -------------------------------------------------------------
    def search(self) -> bool:
        """Look for nearby devices for a while. False if already busy."""
        return self._start(self._search)

    def pair(self, address: str) -> bool:
        """Pair, trust and connect one device. Stops a search that is running;
        False if a pairing is already under way."""
        if self.busy and self._searching:
            # Stop the search; the pairing starts as soon as it has wound down.
            self._pending = address
            self._cancel.set()
            self._set_status("Getting ready to pair...")
            return True
        return self._start(lambda: self._pair(address))

    def wait(self, timeout: Optional[float] = None) -> None:
        """Block until the background work ends, including a pairing that
        was queued behind a search (tests and shutdown)."""
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            thread = self._thread
            if thread is None:
                return
            thread.join(None if end is None else max(0.0, end - time.monotonic()))
            if thread is self._thread or (end is not None and time.monotonic() >= end):
                return

    # -- background jobs ----------------------------------------------------
    def _start(self, job: Callable[[], None]) -> bool:
        with self._lock:
            if self.busy:
                return False
            self.busy = True
        self._cancel.clear()
        self._thread = threading.Thread(target=self._guard, args=(job,), daemon=True)
        self._thread.start()
        return True

    def _guard(self, job: Callable[[], None]) -> None:
        try:
            job()
        except Exception:  # noqa: BLE001 - never let pairing take the TV down
            log.exception("bluetooth job failed")
            self._set_status("Something went wrong. Try again.")
        finally:
            pending, self._pending = self._pending, None
            self._searching = False
            self.busy = False
            self._changed()
        if pending is not None:
            self.pair(pending)

    def _search(self) -> None:
        self._searching = True
        code, out = self._run(["power", "on"], 10)
        if code == 127:
            self._set_status("Bluetooth tools are missing on this box.")
            return
        self._set_status("Searching... put the remote in pairing mode.")
        elapsed = 0.0
        while elapsed < self.search_seconds and not self._cancel.is_set():
            # Each short scan keeps discovery running; the list refreshes
            # between them so new remotes appear while the search goes on.
            self._run(["--timeout", f"{self.scan_window:g}", "scan", "on"], self.scan_window + 5)
            elapsed += self.scan_window
            self._refresh()
        if self._cancel.is_set():
            return
        if self.devices:
            self._set_status("Choose your remote and press OK.")
        else:
            self._set_status("No remotes found. Put it in pairing mode and search again.")

    def _pair(self, address: str) -> None:
        name = next((d.name for d in self.devices if d.address == address), address)
        self._set_status(f"Pairing with {name}...")
        code, out = self._run(
            ["--timeout", "20", "--agent", "NoInputNoOutput", "pair", address], 30
        )
        paired = code == 0 or "Pairing successful" in out or "AlreadyExists" in out
        if not paired:
            log.warning("pairing %s failed: %s", address, out.strip()[-200:])
            self._set_status("Could not pair. Put the remote in pairing mode and try again.")
            self._refresh()
            return
        self._run(["trust", address], 10)
        code, out = self._run(["--timeout", "10", "connect", address], 20)
        self._refresh()
        if code == 0 or "Connection successful" in out or self._is_connected(address):
            self._set_status(f"{name} is ready. Press Back to finish.")
        else:
            self._set_status(f"{name} is paired. Press a button on it to connect.")

    # -- helpers ------------------------------------------------------------
    def _refresh(self) -> None:
        _, all_out = self._run(["devices"], 10)
        _, paired_out = self._run(["devices", "Paired"], 10)
        _, connected_out = self._run(["devices", "Connected"], 10)
        paired = {a for a, _ in parse_devices(paired_out)}
        connected = {a for a, _ in parse_devices(connected_out)}
        devices = [
            BtDevice(addr, name, addr in paired, addr in connected)
            for addr, name in parse_devices(all_out)
            # Skip nameless gadgets: a remote always says what it is.
            if name and not _ADDRESS_NAME.match(name)
        ]
        devices.sort(key=lambda d: (not d.connected, not d.paired, d.name.lower()))
        self.devices = devices
        self._changed()

    def _is_connected(self, address: str) -> bool:
        return any(d.address == address and d.connected for d in self.devices)

    def _set_status(self, text: str) -> None:
        self.status = text
        self._changed()

    def _changed(self) -> None:
        self.version += 1


__all__ = ["BluetoothPairer", "BtDevice", "parse_devices", "run_bluetoothctl"]
