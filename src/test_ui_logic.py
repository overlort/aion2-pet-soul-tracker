"""Логика оверлея без окна (tkinter не нужен): python test_ui_logic.py"""

import json
import os
import tempfile

import tracker_ui as ui


def test_opacity_clamped():
    assert ui.clamp_opacity(0.1) == ui.OPACITY_MIN
    assert ui.clamp_opacity(2) == ui.OPACITY_MAX
    assert ui.clamp_opacity("x") == ui.OPACITY_DEFAULT
    assert ui.clamp_opacity(0.6) == 0.6


def test_v1_alpha_migrates_to_opacity():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "ui_state.json")
    json.dump({"alpha": 0.7, "x": 5, "y": 6, "locked": True}, open(p, "w"))
    o = ui.TrackerOverlay.__new__(ui.TrackerOverlay)
    o.ui_state_path = p
    data = o._load_ui()
    assert data["opacity"] == 0.7 and data["locked"] is True


def test_defaults_show_everything():
    assert all(ui.DEFAULTS[k] for k in ("show_card", "show_recent", "show_closest"))
    assert ui.DEFAULTS["hidden"] is False and ui.TITLE == "Aion 2 Soul Tracker"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
