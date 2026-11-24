"""Produce a de-lined clean copy of a staff reading card.

The cleaner is deliberately conservative.  It only ever rewrites pixels that

* lie inside the calibrated staff segment ``[left, right]`` and inside one of
  the five narrow bands (distance to a reference line ``<= gap / 4``);
* belong to an ink structure with genuine left/right horizontal continuity;
* have the small vertical extent of a staff line rather than a note head,
  stem or beam.

Every other pixel is returned byte-for-byte identical.  Rewritten pixels are
coloured from real neighbouring paper pixels instead of a fixed white.
"""

from __future__ import annotations

from io import BytesIO

import numpy as np

try:  # OpenCV is a project dependency, but keep import errors local to use.
    import cv2
except Exception:  # pragma: no cover - environments without cv2 still import module
    cv2 = None

from PIL import Image

LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


# ---------------------------------------------------------------------------
# Input adaptation
# ---------------------------------------------------------------------------

def _to_uint8(image):
    """Return ``(rgb_uint8, float_scale)`` for the supported array inputs."""
    arr = np.asarray(image)
    float_input = np.issubdtype(arr.dtype, np.floating)
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=-1)
    if arr.ndim != 3 or arr.shape[2] not in (3, 4):
        raise ValueError("清稿只接受 RGB 或灰度图像")
    arr = arr[:, :, :3]
    if float_input:
        scale = 255.0 if float(np.nanmax(arr)) <= 1.0 + 1e-6 else 1.0
        u8 = np.clip(np.rint(arr.astype(np.float32) * scale), 0, 255).astype(np.uint8)
        return u8, 1.0 / scale
    if arr.dtype != np.uint8:
        u8 = np.clip(arr, 0, 255).astype(np.uint8)
    else:
        u8 = arr.copy()
    return u8, 1.0


def _coerce_settings(settings, positional, kwargs):
    if settings is None:
        cfg = {}
    elif isinstance(settings, dict) and isinstance(settings.get("settings"), dict):
        # A whole card document was passed.
        cfg = dict(settings["settings"])
    elif isinstance(settings, dict):
        cfg = dict(settings)
    else:
        # Calling convention clean_sheet(image, left, right, top, gap, ...).
        cfg = {}
        positional = (settings,) + tuple(positional)
    for key, value in zip(("left", "right", "top", "gap", "ink_limit"), positional):
        if value is not None:
            cfg[key] = value
    for key in ("left", "right", "top", "gap", "ink_limit",
                "x0", "x1", "top_y", "first_y", "line_gap", "spacing", "threshold"):
        if kwargs.get(key) is not None:
            cfg[_SETTING_ALIASES.get(key, key)] = kwargs[key]
    if "ink_limit" not in cfg or cfg["ink_limit"] is None:
        cfg["ink_limit"] = 150
    missing = [key for key in ("left", "right", "top", "gap") if cfg.get(key) is None]
    if missing:
        # Late import avoids a circular import at module load time.
        from .staff import defaults
        width = int(kwargs.get("width") or cfg.get("width") or 100)
        height = int(kwargs.get("height") or cfg.get("height") or 100)
        fallback = defaults((width, height))
        for key in ("left", "right", "top", "gap"):
            cfg.setdefault(key, fallback[key])
    cfg["left"] = int(cfg["left"])
    cfg["right"] = int(cfg["right"])
    cfg["top"] = float(cfg["top"])
    cfg["gap"] = float(cfg["gap"])
    cfg["ink_limit"] = int(cfg["ink_limit"])
    return cfg


_SETTING_ALIASES = {
    "x0": "left",
    "x1": "right",
    "top_y": "top",
    "first_y": "top",
    "line_gap": "gap",
    "spacing": "gap",
    "threshold": "ink_limit",
}


# ---------------------------------------------------------------------------
# Geometry / morphology helpers
# ---------------------------------------------------------------------------

def band_rows(height, top, gap):
    """Integer row indices inside any of the five quarter-gap bands."""
    if gap <= 0:
        return np.zeros(height, dtype=bool)
    rows = np.arange(height)
    keep = np.zeros(height, dtype=bool)
    quarter = gap / 4.0
    for index in range(5):
        center = top + index * gap
        keep |= np.abs(rows - center) <= quarter + 1e-9
    return keep


def _vertical_run_lengths(mask):
    """Length of the contiguous vertical ink run each ink pixel belongs to."""
    height, width = mask.shape
    binary = mask.astype(np.int32)
    forward = np.zeros((height, width), dtype=np.int32)
    backward = np.zeros((height, width), dtype=np.int32)
    carried = np.zeros(width, dtype=np.int32)
    for y in range(height):
        carried = forward[y] = binary[y] * (carried + 1)
    carried = np.zeros(width, dtype=np.int32)
    for y in range(height - 1, -1, -1):
        carried = backward[y] = binary[y] * (carried + 1)
    lengths = forward + backward - 1
    return np.where(mask, lengths, 0).astype(np.int32)


def _ink_runs(values):
    positions = np.flatnonzero(values)
    if positions.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(positions) > 1)
    starts = np.r_[positions[0], positions[breaks + 1]]
    ends = np.r_[positions[breaks], positions[-1]]
    return list(zip(starts.tolist(), ends.tolist()))


def estimate_line_width(ink, x0, x1, top, gap):
    """Robust low-quartile thickness of thin clusters near the references."""
    samples = []
    if x1 - x0 >= 8:
        columns = range(x0 + 3, x1 - 2, 2)
    else:
        columns = range(max(0, x0), min(ink.shape[1] - 1, x1) + 1)
    max_cluster = max(4, int(np.ceil(gap * 0.35)))
    for x in columns:
        for start, end in _ink_runs(ink[:, x]):
            length = end - start + 1
            center = (start + end) / 2.0
            nearest = min(abs(center - (top + index * gap)) for index in range(5))
            if nearest <= gap * 0.2 and 1 <= length <= max_cluster:
                samples.append(length)
    if samples:
        return max(1, int(np.percentile(np.asarray(samples), 25)))
    return max(1, int(round(gap * 0.12)))


def continuity_gate(ink, roi, run_min):
    """Label ink structures; return a mask of horizontally long structures."""
    if cv2 is None:
        return _continuity_gate_numpy(ink, roi, run_min)
    source = (ink & roi).astype(np.uint8)
    # Bridge one or two pixel anti-aliasing / scan gaps horizontally.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1))
    bridged = cv2.morphologyEx(source, cv2.MORPH_CLOSE, kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(bridged, connectivity=8)
    good = [label for label in range(1, count)
            if int(stats[label, cv2.CC_STAT_WIDTH]) >= run_min]
    if not good:
        return np.zeros(ink.shape, dtype=bool)
    return np.isin(labels, np.asarray(good))


def _continuity_gate_numpy(ink, roi, run_min):  # pragma: no cover - fallback
    """Row-run fallback used only if OpenCV is unavailable."""
    source = ink & roi
    gate = np.zeros_like(source)
    height, width = source.shape
    for y in range(height):
        for start, end in _ink_runs(source[y]):
            if end - start + 1 >= run_min:
                gate[y, start:end + 1] = True
    return gate


def _horizontal_paper_colour(image, paper):
    """Mean colour of available paper neighbours within a 3px horizontal window."""
    height, width = paper.shape
    padded_paper = np.pad(paper, ((0, 0), (1, 1)), constant_values=False)
    count = (padded_paper[:, :-2].astype(np.float32)
             + padded_paper[:, 1:-1].astype(np.float32)
             + padded_paper[:, 2:].astype(np.float32))
    safe = np.maximum(count, 1.0)
    colour = np.empty((height, width, 3), dtype=np.float32)
    for channel in range(3):
        values = image[:, :, channel].astype(np.float32) * paper
        padded = np.pad(values, ((0, 0), (1, 1)))
        summed = padded[:, :-2] + padded[:, 1:-1] + padded[:, 2:]
        colour[:, :, channel] = summed / safe
    no_paper = count == 0
    colour[no_paper] = image.astype(np.float32)[no_paper]
    return colour


def fill_with_neighbour_paper(image, ink, remove, gap=None):
    """Colour removed pixels by carrying real nearby paper colours vertically."""
    height, width = ink.shape
    paper = ~ink
    source_colour = _horizontal_paper_colour(image, paper)

    big = height + width + 16
    above_colour = np.zeros((height, width, 3), dtype=np.float32)
    above_dist = np.full((height, width), big, dtype=np.int32)
    last_colour = np.zeros((width, 3), dtype=np.float32)
    last_row = np.full(width, -big, dtype=np.int32)
    for y in range(height):  # carry nearest paper at/above each row
        row_paper = paper[y]
        last_row[row_paper] = y
        last_colour[row_paper] = source_colour[y][row_paper]
        above_colour[y] = last_colour
        above_dist[y] = y - last_row

    result = image.copy()
    if not remove.any():
        return result

    below_colour = np.zeros((width, 3), dtype=np.float32)
    next_row = np.full(width, big + height, dtype=np.int32)
    fallback = np.median(image[paper].reshape(-1, 3), axis=0) if paper.any() \
        else np.array([245, 242, 236], dtype=np.float32)

    for y in range(height - 1, -1, -1):  # carry nearest paper at/below
        row_paper = paper[y]
        next_row[row_paper] = y
        below_colour[row_paper] = source_colour[y][row_paper]
        wanted = np.flatnonzero(remove[y])
        for x in wanted:
            distance_below = int(next_row[x] - y)
            distance_above = int(above_dist[y, x])
            has_above = distance_above < big
            has_below = 0 <= distance_below < big
            if has_above and has_below:
                weight_above = distance_below / (distance_above + distance_below)
                colour = (weight_above * above_colour[y, x]
                          + (1.0 - weight_above) * below_colour[x])
            elif has_above:
                colour = above_colour[y, x]
            elif has_below:
                colour = below_colour[x]
            else:
                colour = fallback
            result[y, x] = np.clip(np.rint(colour), 0, 255).astype(np.uint8)
    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def clean_sheet(image, settings=None, *args, **kwargs):
    """Return a same-size RGB array with classified staff-line ink removed."""
    original = np.asarray(image)
    rgb, back_scale = _to_uint8(original)
    cfg = _coerce_settings(settings, args, kwargs)
    height, width = rgb.shape[:2]

    x0 = max(0, min(cfg["left"], width - 1))
    x1 = max(0, min(cfg["right"], width - 1))
    if x1 < x0:
        x0, x1 = sorted((x0, x1))
        x0 = max(0, x0)
        x1 = min(width - 1, x1)

    if x1 < x0 or cfg["gap"] <= 0:
        cleaned = rgb.copy()
    else:
        luma = rgb.astype(np.float32) @ LUMA
        ink = luma <= cfg["ink_limit"]

        in_band = band_rows(height, cfg["top"], cfg["gap"])
        in_range = np.zeros(width, dtype=bool)
        in_range[x0:x1 + 1] = True
        roi = in_band[:, None] & in_range[None, :]

        vertical = _vertical_run_lengths(ink)
        line_width = estimate_line_width(ink, x0, x1, cfg["top"], cfg["gap"])
        thin_max = max(2, line_width + 2)

        span = x1 - x0 + 1
        run_min = minimum_run(cfg["gap"], span)
        gated = continuity_gate(ink, roi, run_min)

        thick = ink & (vertical > thin_max)
        if cv2 is not None:
            horizontal = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1))
            protected = cv2.dilate(thick.astype(np.uint8), horizontal).astype(bool)
        else:  # pragma: no cover
            protected = thick.copy()
            protected[:, 1:] |= thick[:, :-1]
            protected[:, :-1] |= thick[:, 1:]

        remove = roi & ink & gated & (vertical <= thin_max) & ~protected
        cleaned = fill_with_neighbour_paper(rgb, ink, remove, cfg["gap"])
        cleaned[~remove] = rgb[~remove]

    if np.issubdtype(original.dtype, np.floating):
        return cleaned.astype(np.float32) * back_scale
    return cleaned


def minimum_run(gap, span):
    """Horizontal structure width needed to count as a continuous staff line."""
    return max(18, min(int(round(3.0 * gap)), int(round(0.7 * span))))


def line_mask(image, settings=None, *args, **kwargs):
    """Expose the final removal mask (mainly useful for inspection/tests)."""
    rgb, _ = _to_uint8(image)
    cfg = _coerce_settings(settings, args, kwargs)
    height, width = rgb.shape[:2]
    x0 = max(0, min(cfg["left"], width - 1))
    x1 = max(0, min(cfg["right"], width - 1))
    luma = rgb.astype(np.float32) @ LUMA
    ink = luma <= cfg["ink_limit"]
    if x1 < x0 or cfg["gap"] <= 0:
        return np.zeros((height, width), dtype=bool)
    roi = band_rows(height, cfg["top"], cfg["gap"])[:, None]
    in_range = np.zeros(width, dtype=bool)
    in_range[x0:x1 + 1] = True
    roi = roi & in_range[None, :]
    vertical = _vertical_run_lengths(ink)
    line_width = estimate_line_width(ink, x0, x1, cfg["top"], cfg["gap"])
    thin_max = max(2, line_width + 2)
    span = x1 - x0 + 1
    run_min = minimum_run(cfg["gap"], span)
    gated = continuity_gate(ink, roi, run_min)
    thick = ink & (vertical > thin_max)
    if cv2 is not None:
        protected = cv2.dilate(thick.astype(np.uint8),
                               cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1))).astype(bool)
    else:  # pragma: no cover
        protected = thick
    return roi & ink & gated & (vertical <= thin_max) & ~protected


def clean_png(image, settings=None, *args, **kwargs):
    """PNG bytes of the clean copy at the original pixel dimensions."""
    cleaned = clean_sheet(image, settings, *args, **kwargs)
    cleaned_u8, _ = _to_uint8(cleaned)
    buffer = BytesIO()
    Image.fromarray(cleaned_u8, "RGB").save(buffer, format="PNG")
    return buffer.getvalue()


# Descriptive aliases for the same operation.
clean = clean_sheet
remove_staff_lines = clean_sheet
remove_lines = clean_sheet
clean_image = clean_sheet
clean_copy = clean_sheet
deline = clean_sheet
strip_staff_lines = clean_sheet
