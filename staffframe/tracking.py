"""Trace the five real, possibly curved staff lines from the source ink.

The manual calibration (left/right range, first line Y, line gap) is only a
rough guide.  Each line is searched inside a corridor of one gap above and
below its rough position; neighbouring lines keep an order and stay between
two thirds and four thirds of the rough gap apart.  A beam search balances
sticking to dark ink against lateral continuity, so note beams and vertical
bar lines cannot drag a tracked line away.

Ink gaps shorter than two line gaps with ink evidence on both sides may be
bridged; columns where any line has a longer (or edge) interruption are
flagged for manual review and never receive a pitch name.
"""

import numpy as np

from image_lab.validation import Invalid, choice


LUMA = np.array([0.2126, 0.7152, 0.0722])

TRACE_VERSION = 1
CORRIDOR_GAPS = 1.0          # search one rough gap above and below each line
MIN_SPACING_RATIO = 2 / 3    # neighbouring lines: 2/3 ... 4/3 of the rough gap
MAX_SPACING_RATIO = 4 / 3
MAX_BRIDGE_GAPS = 2.0        # only gaps <= two line gaps may be bridged
REFINE_TOLERANCE = 2         # ink within +/-2 px of the path refines the line
BEAM_WIDTH = 32
SMOOTH_WEIGHT = 45.0
DRIFT_WEIGHT = 7.0           # pull toward the smooth local extrapolation
STATIC_WEIGHT = 1.4          # faint pull toward the manual rough line position
MAX_DELTA_RATIO = 0.35       # a staff line moves at most 0.35 gap per column
BLOCKED = 1.0e6


def luma_of(image):
    return image.astype(np.float64) @ LUMA


def _windowed_means(luma):
    """Mean luma over a three-row vertical window, indexed by the centre row."""
    padded = np.pad(luma, ((1, 1), (0, 0)), mode="edge")
    return (padded[:-2] + padded[1:-1] + padded[2:]) / 3.0


def _beam_track(unary, lower, upper, lo, hi, smooth, width, max_delta, seed):
    """Beam-search Viterbi over integer rows, state holds position and slope."""
    columns = unary.shape[1]
    rows = np.arange(lo, hi + 1)
    blocked = BLOCKED * ((rows[:, None] < lower[None, :]) | (rows[:, None] > upper[None, :]))
    static = STATIC_WEIGHT * (rows - seed) ** 2
    # Start on the manual rough line position; clefs and other symbols at the
    # left edge must not decide which ink stripe the line belongs to.
    first_cost = unary[:, 0] + blocked[:, 0] + static + DRIFT_WEIGHT * (rows - seed) ** 2
    keep = np.argsort(first_cost)[:width]
    beams = [(rows[keep], np.zeros(keep.size), first_cost[keep], np.full(keep.size, -1, np.int64))]
    for column in range(1, columns):
        prev_pos, prev_slope, prev_cost, _ = beams[-1]
        delta = rows[:, None] - prev_pos[None, :]
        slope_change = delta - prev_slope[None, :]
        predicted = prev_pos[None, :] + prev_slope[None, :]
        # Real staff lines move slowly column to column; this stops a long ink
        # gap from letting the path jump onto a neighbouring line or a beam.
        jump_block = BLOCKED * (np.abs(delta) > max_delta)
        # Extrapolation follows a genuine curved line; the faint static term
        # prevents clefs or beams from accumulating a drift onto another line.
        drift = DRIFT_WEIGHT * (rows[:, None] - predicted) ** 2
        scores = (prev_cost[None, :] + smooth * slope_change ** 2 + jump_block + drift
                  + static[:, None] + unary[:, column, None] + blocked[:, column, None])
        parent = np.argmin(scores, axis=1)
        cost = scores[np.arange(rows.size), parent]
        kept = np.argsort(cost)[:width]
        beams.append((rows[kept], rows[kept] - prev_pos[parent[kept]], cost[kept], parent[kept]))
    path = np.zeros(columns, dtype=np.int64)
    index = int(np.argmin(beams[-1][2]))
    for column in range(columns - 1, -1, -1):
        positions, _, _, parents = beams[column]
        path[column] = positions[index]
        index = parents[index] if column else index
    return path


def _runs(mask):
    """Yield (start, end_inclusive) runs of False entries in a boolean array."""
    index = 0
    size = mask.size
    while index < size:
        if mask[index]:
            index += 1
            continue
        start = index
        while index < size and not mask[index]:
            index += 1
        yield start, index - 1


def _refine_line(path, luma, ink_limit, left):
    """Centre each supported column on the local ink centroid.

    The correction stays within 1.5 px of the ordered Viterbi path, so the
    sub-pixel adjustment can never make two neighbouring lines cross.
    """
    columns = path.size
    offsets = np.arange(-REFINE_TOLERANCE, REFINE_TOLERANCE + 1)
    rows = np.clip(path[:, None] + offsets[None, :], 0, luma.shape[0] - 1)
    strip = luma[rows, (np.arange(columns) + left)[:, None]]
    weights = np.clip(ink_limit - strip, 0, None)
    supported = weights.sum(axis=1) > 0
    refined = path.astype(np.float64).copy()
    if supported.any():
        numerator = (weights * rows).sum(axis=1)
        denominator = weights.sum(axis=1)
        centroid = numerator[supported] / denominator[supported]
        refined[supported] = np.clip(centroid, path[supported] - 1.5, path[supported] + 1.5)
        known = np.flatnonzero(supported)
        # Interpolate only across interior runs; edge runs stay on the raw path.
        if known.size >= 2:
            filled = np.interp(np.arange(columns), known, refined[known])
            for start, end in _runs(supported):
                if start != 0 and end != columns - 1:
                    refined[start:end + 1] = filled[start:end + 1]
    return refined, supported


def trace_staff(image, config):
    """Return the five traced staff lines and the review flags for a card."""
    luma = luma_of(image)
    height = luma.shape[0]
    windowed = _windowed_means(luma)
    left, right = config["left"], config["right"]
    top, gap = config["top"], config["gap"]
    ink_limit = config["ink_limit"]
    columns = right - left + 1

    paths = []
    for line_index in range(5):
        rough = top + line_index * gap
        coarse_lo = max(0, int(round(rough - CORRIDOR_GAPS * gap)))
        coarse_hi = min(height - 1, int(round(rough + CORRIDOR_GAPS * gap)))
        if line_index == 0:
            lower = np.full(columns, coarse_lo, dtype=np.float64)
            upper = np.full(columns, coarse_hi, dtype=np.float64)
        else:
            previous = paths[-1]
            lower = np.maximum(coarse_lo, previous + MIN_SPACING_RATIO * gap)
            upper = np.minimum(coarse_hi, previous + MAX_SPACING_RATIO * gap)
        unary = windowed[coarse_lo:coarse_hi + 1, left:right + 1]
        paths.append(_beam_track(unary, lower, upper, coarse_lo, coarse_hi,
                                 SMOOTH_WEIGHT, BEAM_WIDTH, MAX_DELTA_RATIO * gap,
                                 int(round(rough))))

    refined_lines, pending_ranges, bridged_counts = [], [], []
    pending_columns = set()
    max_bridge = MAX_BRIDGE_GAPS * gap
    for line_index, path in enumerate(paths):
        refined, supported = _refine_line(path, luma, ink_limit, left)
        ranges = []
        bridged = 0
        for start, end in _runs(supported):
            length = end - start + 1
            touches_edge = start == 0 or end == columns - 1
            if touches_edge or length > max_bridge:
                ranges.append([left + start, left + end])
                pending_columns.update(range(left + start, left + end + 1))
            else:
                bridged += length
        refined_lines.append(refined)
        pending_ranges.append(ranges)
        bridged_counts.append(bridged)

    # Sub-pixel refinement stays close to the ordered integer paths; enforce the
    # required 2/3..4/3 gap spacing as a final geometric step. Each correction is
    # split evenly across the two neighbouring lines to stay near the ink.
    min_spacing = MIN_SPACING_RATIO * gap
    max_spacing = MAX_SPACING_RATIO * gap
    for index in range(4):
        upper, lower = refined_lines[index], refined_lines[index + 1]
        spacing = lower - upper
        too_close = spacing < min_spacing
        too_far = spacing > max_spacing
        shift_close = (min_spacing - spacing) / 2
        shift_far = (max_spacing - spacing) / 2
        upper[too_close] -= shift_close[too_close]
        lower[too_close] += shift_close[too_close]
        upper[too_far] -= shift_far[too_far]
        lower[too_far] += shift_far[too_far]
    # Re-assert strict ordering after the symmetric corrections.
    for upper, lower in zip(refined_lines, refined_lines[1:]):
        np.maximum(lower, upper + 0.6, out=lower)

    rounded_lines = [[round(float(value), 2) for value in line] for line in refined_lines]
    for column in range(columns):
        ys = [line[column] for line in rounded_lines]
        if any(later <= earlier for earlier, later in zip(ys, ys[1:])):
            raise Invalid("弯线追踪未能保持五线上下顺序，请调整手填标定后重试")

    return {
        "version": TRACE_VERSION,
        "left": left,
        "right": right,
        "top": top,
        "gap": gap,
        "lines": rounded_lines,
        "pending_ranges": pending_ranges,
        "pending_columns": sorted(pending_columns),
        "bridged_columns": bridged_counts,
    }


def valid_trace(trace, config):
    """Safety check for traces loaded from storage."""
    if not isinstance(trace, dict) or trace.get("version") != TRACE_VERSION:
        return False
    expected = config["right"] - config["left"] + 1
    if trace.get("left") != config["left"] or trace.get("right") != config["right"]:
        return False
    lines = trace.get("lines")
    if not isinstance(lines, list) or len(lines) != 5 or any(len(line) != expected for line in lines):
        return False
    if any(not np.isfinite(value) for line in lines for value in line):
        return False
    ranges = trace.get("pending_ranges")
    if not isinstance(ranges, list) or len(ranges) != 5:
        return False
    for line_ranges in ranges:
        for pair in line_ranges:
            if not (isinstance(pair, list) and len(pair) == 2 and config["left"] <= pair[0] <= pair[1] <= config["right"]):
                return False
    pending = trace.get("pending_columns")
    if not isinstance(pending, list) or any(not isinstance(value, int) for value in pending):
        return False
    return True


def staff_mode(value):
    return choice(value or "straight", "判音方式", ("straight", "traced"))


def line_y(trace, x, line_index):
    """Y of one traced line at image X, with linear interpolation and edge slope."""
    left = trace["left"]
    values = trace["lines"][line_index]
    index = x - left
    if index <= 0:
        return float(values[0]) if len(values) == 1 else float(values[0]) + (x - left) * (values[1] - values[0])
    if index >= len(values) - 1:
        last = len(values) - 1
        return float(values[last]) + (x - (left + last)) * (values[last] - values[last - 1])
    floor = int(index)
    fraction = index - floor
    return float(values[floor] * (1 - fraction) + values[floor + 1] * fraction)


def staff_position(x, y, config, mode, trace, pending=None):
    """Diastematic number: 0 on the bottom line, +2 per space upward."""
    if x < config["left"] or x > config["right"]:
        return None
    if mode == "traced" and trace is not None:
        if x in (pending if pending is not None else set(trace["pending_columns"])):
            return "pending"
        top_y = line_y(trace, x, 0)
        bottom_y = line_y(trace, x, 4)
    else:
        top_y = config["top"]
        bottom_y = config["top"] + 4 * config["gap"]
    span = bottom_y - top_y
    if span <= 0:
        return None
    # Diastematic number: top staff line = 8 (F5), bottom line = 0 (E4),
    # each line or space changes the number by 2.
    return 8 * (bottom_y - y) / span


def pitch_label(position):
    snapped = int(np.floor(position + 0.5))
    if not -6 <= snapped <= 14:
        return None
    index = 4 * 7 + 2 + snapped  # E4 is the bottom staff line.
    octave, letter = divmod(index, 7)
    return {"label": "CDEFGAB"[letter] + str(octave), "step": snapped}


def pitch_at(x, y, config, mode="straight", trace=None):
    position = staff_position(x, y, config, mode, trace)
    if position is None:
        return None
    if position == "pending":
        return {"pending": True}
    label = pitch_label(position)
    if label is None:
        return None
    snapped = label["step"]
    if mode == "traced" and trace is not None:
        half_gap = (line_y(trace, x, 4) - line_y(trace, x, 0)) / 8
    else:
        half_gap = config["gap"] / 2
    label["offset_px"] = float((position - snapped) * half_gap)
    return label


def trace_summary(trace):
    total = trace["right"] - trace["left"] + 1
    return {
        "columns": total,
        "pending_columns": len(trace["pending_columns"]),
        "pending_ranges": trace["pending_ranges"],
        "bridged_columns": trace["bridged_columns"],
    }
