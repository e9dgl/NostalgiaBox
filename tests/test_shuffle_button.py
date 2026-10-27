import random
from collections import Counter

from nostalgiabox.actions import Action
from nostalgiabox.input.keymap import evdev_key_to_event, parse_key_overrides
from nostalgiabox.playlist import GroupShuffle
from tests.test_menu import _app, send


def test_group_shuffle_gives_every_show_a_fair_turn():
    big = [f"big{i}" for i in range(200)]
    small = [f"small{i}" for i in range(4)]
    mix = GroupShuffle([big, small, []], random.Random(1))
    picks = [mix.next() for _ in range(100)]
    counts = Counter(p.startswith("big") for p in picks)
    assert counts[True] == counts[False] == 50
    # one show's episodes all play before any repeats
    smalls = [p for p in picks if p.startswith("small")][:4]
    assert sorted(smalls) == sorted(small)


def test_shuffle_keys():
    assert evdev_key_to_event("BTN_MIDDLE").action == Action.SHUFFLE
    assert evdev_key_to_event("KEY_SHUFFLE").action == Action.SHUFFLE
    overrides = parse_key_overrides({"KEY_F12": "shuffle"})
    assert overrides["KEY_F12"].action == Action.SHUFFLE


def test_shuffle_while_watching_pauses_and_asks(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    app.play_queue([tmp_path / "Pingu/Pingu 01.mp4", tmp_path / "Pingu/Pingu 02.mp4"])
    send(app, Action.SHUFFLE)
    assert player.paused
    assert app.menu.screen.title == "Shuffle something new?"
    assert [i.label for i in app.menu.screen.items] == ["Yes, shuffle", "No, keep watching"]
    send(app, Action.ENTER)  # Yes
    assert app.mode == "mix" and not app.menu.is_open
    assert not app.paused and not player.paused


def test_shuffle_no_carries_on(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    app.play_queue([tmp_path / "Pingu/Pingu 01.mp4"])
    send(app, Action.SHUFFLE)
    send(app, Action.CHANNEL_DOWN)
    send(app, Action.ENTER)  # No, keep watching
    assert app.mode == "queue" and player.current == tmp_path / "Pingu/Pingu 01.mp4"
    assert not player.paused


def test_shuffle_back_carries_on(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    app.play_queue([tmp_path / "Pingu/Pingu 01.mp4"])
    send(app, Action.SHUFFLE)
    send(app, Action.BACK)
    assert app.mode == "queue" and not player.paused and not app.menu.is_open


def test_shuffle_pressed_twice_shuffles(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    app.play_queue([tmp_path / "Pingu/Pingu 01.mp4"])
    send(app, Action.SHUFFLE)
    send(app, Action.SHUFFLE)
    assert app.mode == "mix" and not app.menu.is_open and not player.paused


def test_shuffle_with_nothing_on_starts_straight_away(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    app._playing_path = None
    send(app, Action.SHUFFLE)
    assert app.mode == "mix" and not app.menu.is_open
    assert player.current is not None


def test_shuffle_ignored_while_asleep(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    send(app, Action.POWER)
    send(app, Action.ENTER)  # Sleep
    send(app, Action.SHUFFLE)
    assert app.standby and player.current is None


def test_shuffle_from_the_power_screen_swaps_questions(tmp_path):
    app, player, _ = _app(tmp_path)
    app.start()
    send(app, Action.POWER)
    pauses = []
    real = player.set_pause
    player.set_pause = lambda p: (pauses.append(p), real(p))
    send(app, Action.SHUFFLE)
    assert app.menu.screen.kind == "shuffle"
    assert False not in pauses  # never un-paused in between
    send(app, Action.BACK)
    assert not player.paused


def test_shuffle_button_setting(tmp_path):
    from nostalgiabox.config import config_from_dict

    cfg = config_from_dict({"media_root": str(tmp_path), "shuffle_button": "KEY_F12",
                            "input": {"key_overrides": {"KEY_F1": "menu"}}})
    assert cfg.input_options["key_overrides"] == {"KEY_F1": "menu", "KEY_F12": "shuffle"}
