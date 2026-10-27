from nostalgiabox.hdmi import (
    HdmiWatcher,
    detect_output,
    list_ports,
    resolve_audio_device,
)

PI4_CARDS = (
    " 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n"
    "                      bcm2835 Headphones\n"
    " 1 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0\n"
    "                      vc4-hdmi-0\n"
    " 2 [vc4hdmi1       ]: vc4-hdmi - vc4-hdmi-1\n"
    "                      vc4-hdmi-1\n"
)


def _pi(tmp_path, hdmi0, hdmi1=None, cards=PI4_CARDS):
    drm = tmp_path / "drm"
    for n, status in ((1, hdmi0), (2, hdmi1)):
        if status is not None:
            d = drm / f"card1-HDMI-A-{n}"
            d.mkdir(parents=True)
            (d / "status").write_text(status + "\n")
    (drm / "card1").mkdir(parents=True, exist_ok=True)
    (drm / "version").write_text("x")
    cards_file = tmp_path / "cards"
    cards_file.write_text(cards)
    dev = tmp_path / "dev"
    dev.mkdir()
    (dev / "cec0").write_text("")
    (dev / "cec1").write_text("")
    return dict(drm_root=drm, cards_file=cards_file, dev_root=dev)


def _set(tmp_path, n, status):
    (tmp_path / "drm" / f"card1-HDMI-A-{n}" / "status").write_text(status + "\n")


def test_tv_on_hdmi0(tmp_path):
    out = detect_output(**_pi(tmp_path, "connected", "disconnected"))
    assert (out.connector, out.label) == ("HDMI-A-1", "HDMI 0")
    assert out.audio_device == "alsa/hdmi:CARD=vc4hdmi0,DEV=0"
    assert out.cec_device.endswith("cec0")


def test_tv_on_hdmi1_moves_sound_and_remote_too(tmp_path):
    out = detect_output(**_pi(tmp_path, "disconnected", "connected"))
    assert (out.connector, out.label) == ("HDMI-A-2", "HDMI 1")
    assert out.audio_device == "alsa/hdmi:CARD=vc4hdmi1,DEV=0"
    assert out.cec_device.endswith("cec1")


def test_two_tvs_uses_hdmi0_only(tmp_path):
    out = detect_output(**_pi(tmp_path, "connected", "connected"))
    assert out.connector == "HDMI-A-1"


def test_no_tv(tmp_path):
    assert detect_output(**_pi(tmp_path, "disconnected", "disconnected")) is None


def test_pi3_single_port(tmp_path):
    cards = " 0 [vc4hdmi        ]: vc4-hdmi - vc4-hdmi\n"
    out = detect_output(**_pi(tmp_path, "connected", None, cards=cards))
    assert out.audio_device == "alsa/hdmi:CARD=vc4hdmi,DEV=0"


def test_not_a_pi(tmp_path):
    assert list_ports(tmp_path / "missing") == []


def test_audio_setting(tmp_path):
    out = detect_output(**_pi(tmp_path, "disconnected", "connected"))
    hdmi1 = "alsa/hdmi:CARD=vc4hdmi1,DEV=0"
    assert resolve_audio_device(None, out) == hdmi1
    assert resolve_audio_device("auto", out) == hdmi1
    # An old setting for the other port cannot send the sound nowhere.
    assert resolve_audio_device("alsa/hdmi:CARD=vc4hdmi0,DEV=0", out) == hdmi1
    # A USB sound card is left alone.
    assert resolve_audio_device("alsa/front:CARD=Device,DEV=0", out) == "alsa/front:CARD=Device,DEV=0"
    assert resolve_audio_device(None, None) is None
    assert resolve_audio_device("auto", None) is None


def test_watcher_switches_when_the_cable_moves(tmp_path):
    paths = _pi(tmp_path, "connected", "disconnected")
    watcher = HdmiWatcher("HDMI-A-1", drm_root=paths["drm_root"], interval=2)
    assert watcher.check(0) is None
    _set(tmp_path, 1, "disconnected")
    _set(tmp_path, 2, "connected")
    assert watcher.check(1) is None      # not time to look yet
    assert watcher.check(2) is None      # seen once: could be a blip
    assert watcher.check(4) == "HDMI-A-2"


def test_watcher_ignores_a_blip_and_a_sleeping_tv(tmp_path):
    paths = _pi(tmp_path, "connected", "disconnected")
    watcher = HdmiWatcher("HDMI-A-1", drm_root=paths["drm_root"], interval=2)
    _set(tmp_path, 1, "disconnected")    # TV asleep, nothing on the other port
    assert watcher.check(0) is None
    assert watcher.check(2) is None
    _set(tmp_path, 2, "connected")       # a blip on the other port
    assert watcher.check(4) is None
    _set(tmp_path, 1, "connected")
    assert watcher.check(6) is None
    assert watcher.check(8) is None


def test_watcher_never_steals_a_working_port(tmp_path):
    paths = _pi(tmp_path, "disconnected", "connected")
    watcher = HdmiWatcher("HDMI-A-2", drm_root=paths["drm_root"], interval=2)
    _set(tmp_path, 1, "connected")       # a second TV plugged into HDMI 0
    for t in (0, 2, 4, 6):
        assert watcher.check(t) is None


def test_watcher_picks_up_a_tv_that_was_off_at_power_on(tmp_path):
    paths = _pi(tmp_path, "disconnected", "disconnected")
    watcher = HdmiWatcher(None, drm_root=paths["drm_root"], interval=2)
    assert watcher.check(0) is None
    _set(tmp_path, 2, "connected")
    assert watcher.check(2) is None
    assert watcher.check(4) == "HDMI-A-2"


def test_app_stops_to_restart_on_the_new_port(tmp_path):
    from tests.test_menu import _app

    paths = _pi(tmp_path / "pi", "connected", "disconnected")
    app, player, _ = _app(tmp_path)
    app._hdmi_watcher = HdmiWatcher("HDMI-A-1", drm_root=paths["drm_root"], interval=0)
    app.start()
    app._running = True
    app.step()
    assert app._running and not app.restart_requested
    _set(tmp_path / "pi", 1, "disconnected")
    _set(tmp_path / "pi", 2, "connected")
    app.step()
    app.step()
    assert app.restart_requested and not app._running


def test_cec_follows_the_port():
    from nostalgiabox.input import manager

    class Fake:
        def __init__(self, **kw):
            self.kw = kw

        @staticmethod
        def is_available(binary):
            return True

    import nostalgiabox.input.cec as cec
    real = cec.CecBackend
    cec.CecBackend = Fake
    try:
        (backend,) = manager.create_backends({"keyboard": False, "cec_device": "/dev/cec1"})
    finally:
        cec.CecBackend = real
    assert backend.kw["extra_args"] == ["/dev/cec1"]
