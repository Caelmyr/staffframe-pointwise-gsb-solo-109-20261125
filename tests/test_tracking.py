"""End-to-end checks for curved staff tracing. Run: python3 -I tests/test_tracking.py"""

import io
import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PIL import Image

from staffframe import staff, tracking
from staffframe.server import create_app
from staffframe.store import Cards


def close(values, expected, tolerance):
    return all(abs(a - b) <= tolerance for a, b in zip(values, expected))


def test_trace_follows_real_curves_within_corridor():
    size, data, config = staff.practice()
    image = staff.pixels_bytes(data)
    trace = tracking.trace_staff(image, config)
    width = config["right"] - config["left"] + 1
    assert len(trace["lines"]) == 5 and all(len(line) == width for line in trace["lines"])
    for line_index, line in enumerate(trace["lines"]):
        rough = config["top"] + line_index * config["gap"]
        for x, y in zip(range(config["left"], config["right"] + 1), line):
            assert rough - config["gap"] - 0.5 <= y <= rough + config["gap"] + 0.5, (line_index, x, y)
    # Ordering and 2/3..4/3 gap spacing for every column.
    for column in range(width):
        ys = [line[column] for line in trace["lines"]]
        assert all(ys[i] < ys[i + 1] for i in range(4))
        for a, b in zip(ys, ys[1:]):
            assert (2 / 3) * config["gap"] - 0.01 <= b - a <= (4 / 3) * config["gap"] + 0.01
    # Curvature is actually captured (the manual straight top line is not the real line).
    deviations = [y - config["top"] for y in trace["lines"][0]]
    assert max(deviations) - min(deviations) > 2.5


def test_tracking_accuracy_against_drawn_curves():
    size, data, config = staff.practice()
    image = staff.pixels_bytes(data)
    trace = tracking.trace_staff(image, config)
    # Rebuild the drawn curve (minus erased gaps) and require sub-2px accuracy.
    for line_index in range(5):
        rough = config["top"] + line_index * config["gap"]
        errors = []
        for offset, y in enumerate(trace["lines"][line_index]):
            x = config["left"] + offset
            true_y = rough + 3 * math.sin((x - 222) / 130) + 1.4 * math.sin((x + line_index * 4) / 53)
            in_gap = (line_index == 0 and 500 <= x <= 560) or (line_index == 3 and 348 <= x <= 370)
            if not in_gap:
                errors.append(abs(y - true_y))
        assert np.mean(errors) < 1.6, (line_index, np.mean(errors))
        assert np.percentile(errors, 95) < 3.0, (line_index, np.percentile(errors, 95))


def test_gap_classification():
    size, data, config = staff.practice()
    image = staff.pixels_bytes(data)
    trace = tracking.trace_staff(image, config)
    long_length = 560 - 500 + 1   # 61 px > two line gaps (44 px)
    short_length = 370 - 348 + 1  # 23 px <= two line gaps
    assert long_length > 2 * config["gap"] and short_length <= 2 * config["gap"]
    line0_ranges = trace["pending_ranges"][0]
    assert any(start <= 500 and end >= 560 for start, end in line0_ranges), line0_ranges
    # The short gap is bridged (no pending range covering it) and counted as bridged.
    line3_ranges = trace["pending_ranges"][3]
    assert not any(not (end < 348 or start > 370) for start, end in line3_ranges), line3_ranges
    assert trace["bridged_columns"][3] >= short_length
    # Pending columns never receive a pitch name.
    pending = set(trace["pending_columns"])
    assert 530 in pending and 360 not in pending
    assert staff.pitch(530, config["note_y"], config, "traced", trace) == {"pending": True}
    assert "label" in staff.pitch(360, config["note_y"], config, "traced", trace)


def test_beam_does_not_capture_note_beams():
    size, data, config = staff.practice()
    image = staff.pixels_bytes(data)
    trace = tracking.trace_staff(image, config)
    # The beam around x=386..430 sits above the staff; lines there must stay on staff ink.
    for column_x in range(386, 431, 4):
        offset = column_x - config["left"]
        ys = [line[offset] for line in trace["lines"]]
        for line_index, y in enumerate(ys):
            rough = config["top"] + line_index * config["gap"]
            assert abs(y - rough) < config["gap"] * 0.9


def test_straight_and_traced_pitch_differ_on_curved_photo():
    size, data, config = staff.practice()
    image = staff.pixels_bytes(data)
    trace = tracking.trace_staff(image, config)
    # Pick a column on the right where the paper warp shifts the real line.
    x = 600
    real_top = tracking.line_y(trace, x, 0)
    rough_top = config["top"]
    y = real_top  # sit exactly on the real (fractional) top line -> F5
    traced = staff.pitch(x, y, config, "traced", trace)
    straight = staff.pitch(x, y, config, "straight", None)
    assert traced and not traced.get("pending")
    assert traced["label"] == "F5", traced
    assert abs(traced["offset_px"]) < 0.6
    # The rough horizontal top line is offset from the real one, so straight
    # mode reads a different (less accurate) position at the same coordinates.
    assert abs(real_top - rough_top) > 0.4
    assert straight["label"] != traced["label"] or \
        abs(straight["offset_px"]) > abs(traced["offset_px"]) + 0.4
    # The bottom line still reads E4 and the top space E5, via both geometries.
    bottom_y = tracking.line_y(trace, x, 4)
    assert staff.pitch(x, bottom_y, config, "traced", trace)["label"] == "E4"
    space_y = (tracking.line_y(trace, x, 0) + tracking.line_y(trace, x, 1)) / 2
    assert staff.pitch(x, space_y, config, "traced", trace)["label"] == "E5"


def test_store_and_http_flow():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cards = Cards(root / "cards")
        card = cards.demo()
        # Adopt traced mode; trace is stored and survives reopening.
        saved = cards.update(card["id"], {"revision": card["revision"], "settings": {}, "staff_mode": "traced"})
        assert saved["staff_mode"] == "traced" and saved["trace"] is not None
        reopened = cards.read(saved["id"])
        assert reopened["staff_mode"] == "traced" and len(reopened["trace"]["lines"]) == 5
        # Switching back to straight drops the trace.
        straight = cards.update(saved["id"], {"revision": saved["revision"], "settings": {}, "staff_mode": "straight"})
        assert straight["staff_mode"] == "straight" and straight["trace"] is None
        # Re-tracing after changing the calibration keeps coordinates intact.
        again = cards.update(straight["id"], {"revision": straight["revision"],
                                              "settings": {"top": straight["settings"]["top"] + 1},
                                              "staff_mode": "traced"})
        assert again["settings"]["note_x"] == straight["settings"]["note_x"]
        assert again["trace"]["top"] == straight["settings"]["top"] + 1

        app = create_app(root)
        client = app.test_client()
        key = again["id"]
        preview = client.get(f"/staffframe/cards/{key}/trace-preview?revision={again['revision']}")
        assert preview.status_code == 200
        body = preview.get_json()
        assert body["staff_mode"] == "traced" and body["trace_summary"]["pending_columns"] >= 1
        proof = client.get(f"/staffframe/cards/{key}/export/proof.png?revision={again['revision']}")
        assert proof.status_code == 200 and proof.data[:8] == b"\x89PNG\r\n\x1a\n"
        probe = client.get(f"/staffframe/cards/{key}/probe?revision={again['revision']}&x=530&y=150")
        assert probe.get_json()["pitch"] == {"pending": True}
        bundle = client.get(f"/staffframe/cards/{key}/export/observation.zip?revision={again['revision']}")
        assert bundle.status_code == 200 and bundle.data[:2] == b"PK"


def test_invalid_stored_trace_falls_back():
    with tempfile.TemporaryDirectory() as tmp:
        cards = Cards(Path(tmp) / "cards")
        card = cards.demo()
        saved = cards.update(card["id"], {"revision": card["revision"], "settings": {}, "staff_mode": "traced"})
        folder = cards.folder(saved["id"])
        import json
        broken = json.loads((folder / "card.json").read_text())
        broken["trace"]["lines"] = broken["trace"]["lines"][:3]
        cards.commit(folder, broken)
        fallback = cards.read(saved["id"])
        assert fallback["staff_mode"] == "straight" and fallback["trace"] is None


if __name__ == "__main__":
    # Small helper kept local so the test never depends on package internals.
    def pixels_bytes(data):
        with Image.open(io.BytesIO(data)) as page:
            return np.asarray(page.convert("RGB"))
    staff.pixels_bytes = pixels_bytes
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("ALL PASSED" if failures == 0 else f"{failures} FAILURE(S)")
    sys.exit(1 if failures else 0)
