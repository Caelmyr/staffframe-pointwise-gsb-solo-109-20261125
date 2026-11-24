"""Read treble-staff positions, barlines, and ink from calibrated source pixels."""

from io import BytesIO
import math

import numpy as np
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from image_lab.validation import Invalid, integer, number


LUMA = np.array([0.2126, 0.7152, 0.0722])
MAX_IMAGE_BYTES = 18 * 1024 * 1024


def png(image):
    result = BytesIO()
    image.save(result, format="PNG")
    return result.getvalue()


def decode(data):
    if len(data) > MAX_IMAGE_BYTES:
        raise Invalid("单张原片不得超过18 MiB")
    try:
        with Image.open(BytesIO(data)) as page:
            if page.format not in {"PNG", "JPEG", "TIFF", "BMP", "WEBP"} or getattr(page, "n_frames", 1) != 1:
                raise Invalid("请选择单帧 PNG、JPEG、TIFF、BMP 或 WebP")
            if min(page.size) < 32 or max(page.size) > 2600 or math.prod(page.size) > 4_000_000:
                raise Invalid("原片边长须在32–2600 px内，总像素不超过400万")
            normalized = ImageOps.exif_transpose(page).convert("RGB")
            return normalized.size, png(normalized)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise Invalid("原片无法解码") from error


def pixels(path):
    with Image.open(path) as page:
        return np.asarray(page.convert("RGB"))


def settings(value, size):
    width, height = size
    left = integer(value.get("left"), "起点 X", 0, width - 9)
    top = number(value.get("top"), "第一线 Y", 0, height - 13)
    return {
        "left": left,
        "right": integer(value.get("right"), "终点 X", left + 8, width - 1),
        "top": top,
        "gap": number(value.get("gap"), "线距", 3, (height - 1 - top) / 4),
        "note_x": integer(value.get("note_x"), "音符 X", 0, width - 1),
        "note_y": integer(value.get("note_y"), "音符 Y", 0, height - 1),
        "ink_limit": integer(value.get("ink_limit"), "墨迹亮度上限", 1, 254),
    }


def defaults(size):
    w, h = size
    return settings({
        "left": w // 10, "right": w - 1 - w // 10, "top": h // 4, "gap": max(3, h * 0.09),
        "note_x": w // 2, "note_y": h // 2, "ink_limit": 150,
    }, size)


def lines(config):
    return [config["top"] + index * config["gap"] for index in range(5)]


def pixel(image, x, y, config):
    rgb = image[y, x]
    level = float(rgb @ LUMA)
    return {"x": x, "y": y, "level": level, "rgb": [int(value) for value in rgb],
            "ink": bool(level <= config["ink_limit"])}


def pitch(x, y, config):
    """Name the written natural pitch from the staff geometry, without a barline test."""
    bottom = lines(config)[-1]
    step = (bottom - y) / (config["gap"] / 2)
    snapped = math.floor(step + 0.5)
    if x < config["left"] or x > config["right"] or not -6 <= snapped <= 14:
        return None
    index = 4 * 7 + 2 + snapped  # E4 is the bottom staff line.
    octave, letter = divmod(index, 7)
    return {"label": "CDEFGAB"[letter] + str(octave), "step": snapped, "offset_px": (step - snapped) * config["gap"] / 2}


def _bands(config):
    gap = float(config["gap"])
    top = float(config["top"])
    bottom = top + 4 * gap
    return top, bottom, max(0.0, top - 2.5 * gap), bottom + 2.5 * gap


def _line_rows(mask, left, right):
    """Rows mostly covered by horizontal staff ink, as opposed to a single glyph."""
    return {int(row) for row in range(mask.shape[0])
            if mask[row, left:right + 1].mean() >= 0.12}


def _notehead(mask, x, gap, top, bottom, line_rows):
    """Detect a notehead attached to a stroke near either outer end of the staff.

    Rows just inside the outer lines hold no staff line ink. A glyph-sized dark run
    there that touches this column belongs to a notehead on the stroke, not to a
    barline, so a stem flush with an outer line is rejected. Separately drawn ink a
    little to the side is not counted against the stroke.
    """
    depth = max(1, int(round(0.55 * gap)))
    width = max(4, int(round(0.55 * gap)))
    column = int(round(x))
    for first, last in ((int(top) + 2, int(top) + depth), (int(bottom) - depth, int(bottom) - 2)):
        for row in range(max(0, first), min(mask.shape[0] - 1, last) + 1):
            if row in line_rows:
                continue
            start = column
            while start - 1 >= 0 and mask[row, start - 1]:
                start -= 1
            end = column
            while end + 1 < mask.shape[1] and mask[row, end + 1]:
                end += 1
            if mask[row, column] and end - start + 1 >= width:
                return True
    return False


def barlines(image, config):
    """Find ink strokes that run from close to the first line through to close to the fifth.

    Each column is measured over a band wide enough to see a stem overrun. A stroke
    qualifies only when its ink run starts and ends close to the two outer staff lines,
    spans nearly the full four staff spaces, and is nearly unbroken between them. A
    note stem is about three and a half spaces long and fails the span or endpoint
    test; one flush with the staff is rejected by its notehead. The search stays inside
    the card's left/right calibration range.
    """
    height, width = image.shape[:2]
    gap = float(config["gap"])
    top, bottom, band_top, band_bottom = _bands(config)
    first = max(0, math.floor(band_top))
    last = min(height - 1, math.ceil(band_bottom))
    left = max(0, int(config["left"]))
    right = min(width - 1, int(config["right"]))
    if last <= first or right < left:
        return []
    tolerance = max(2.0, 0.35 * gap)
    minimum_span = 3.8 * gap
    mask = (image @ LUMA) <= config["ink_limit"]
    allowed_gap = max(1, int(round(0.12 * gap)))
    minimum_coverage = 0.88
    line_rows = _line_rows(mask, left, right)
    columns = []
    for x in range(left, right + 1):
        rows = np.flatnonzero(mask[first:last + 1, x]) + first
        if not rows.size:
            continue
        if abs(int(rows[0]) - top) > tolerance or abs(int(rows[-1]) - bottom) > tolerance:
            continue
        span = int(rows[-1] - rows[0]) + 1
        if span < minimum_span:
            continue
        dropped = int(np.sum(np.maximum(0, np.diff(rows) - 1 - allowed_gap)))
        coverage = 1 - dropped / span
        if coverage < minimum_coverage:
            continue
        if _notehead(mask, x, gap, top, bottom, line_rows):
            continue
        columns.append((x, int(rows[0]), int(rows[-1]), float(coverage)))
    groups = []
    for column in columns:
        if groups and column[0] - groups[-1][-1][0] <= 1:
            groups[-1].append(column)
        else:
            groups.append([column])
    maximum_width = max(3, int(round(0.6 * gap)))
    strokes = []
    for group in groups:
        start, end = group[0][0], group[-1][0]
        if end - start + 1 > maximum_width:
            continue
        tops = [column[1] for column in group]
        bottoms = [column[2] for column in group]
        if max(tops) - min(tops) > max(3.0, 0.3 * gap) or max(bottoms) - min(bottoms) > max(3.0, 0.3 * gap):
            continue
        strokes.append({
            "x": (start + end) / 2, "start": start, "end": end, "width": end - start + 1,
            "top": min(tops), "bottom": max(bottoms), "coverage": max(column[3] for column in group),
        })
    return strokes


def _boundaries(strokes, gap):
    """Treat two full-span strokes at most half a staff space apart as one boundary."""
    boundaries = []
    for stroke in sorted(strokes, key=lambda stroke: stroke["x"]):
        if boundaries and stroke["x"] - boundaries[-1][-1]["x"] <= gap / 2:
            boundaries[-1].append(stroke)
        else:
            boundaries.append([stroke])
    return boundaries


def _covering(groups, note_x):
    """A boundary whose drawn span contains the note, so the note is not inside a measure."""
    for group in groups:
        if group[0]["start"] <= note_x <= group[-1]["end"]:
            return group
    return None


def _boundary(group, side):
    """Describe a boundary; the stroke nearer this measure judges membership."""
    nearest = group[-1] if side == "left" else group[0]
    return {
        "x": nearest["x"],
        "start": nearest["start"], "end": nearest["end"],
        "top": min(stroke["top"] for stroke in group), "bottom": max(stroke["bottom"] for stroke in group),
        "double": len(group) > 1,
        "strokes": [{"x": stroke["x"], "start": stroke["start"], "end": stroke["end"],
                     "top": stroke["top"], "bottom": stroke["bottom"]} for stroke in group],
    }


def crop_bounds(config, left, right, note_x, width):
    """Trim the measure no more than a quarter staff space before or after either barline.

    The allowance is measured from the two judging strokes. Inside it the edges pull
    back far enough to keep those strokes whole; a parallel partner lying farther out
    than the allowance is left out rather than reaching deeper into the neighbouring
    measure. The chosen note is always kept, and this padding does not change which
    points judge as inside the measure.
    """
    limit = float(config["gap"]) / 4
    low = math.ceil(left["x"] - limit)
    high = min(width - 1, math.floor(right["x"] + limit))
    left_edge = min(max(low, left["start"]), note_x)
    right_edge = max(min(high, right["end"]), note_x)
    return {"left": int(left_edge), "right": int(right_edge) + 1,
            "width": int(right_edge) + 1 - int(left_edge),
            "limit": limit, "margin_left": float(left["x"] - left_edge),
            "margin_right": float(right_edge - right["x"])}


def measure(image, config):
    """Locate the two nearest real boundaries around the chosen note.

    A boundary is one barline or a close parallel pair. A missing side is reported for
    review rather than invented. Only points strictly between the two judging strokes
    belong to this measure; crop padding never widens that range.
    """
    note_x = config["note_x"]
    gap = float(config["gap"])
    groups = _boundaries(barlines(image, config), gap)
    left = right = None
    on_boundary = bool(_covering(groups, note_x))
    if not on_boundary:
        left_group = next((group for group in reversed(groups) if group[-1]["x"] < note_x), None)
        right_group = next((group for group in groups if group[0]["x"] > note_x), None)
        left = _boundary(left_group, "left") if left_group else None
        right = _boundary(right_group, "right") if right_group else None
    reviews = []
    if left is None and not on_boundary:
        reviews.append("音符左侧未见贯通第一线至第五线的小节线，请复核")
    if right is None and not on_boundary:
        reviews.append("音符右侧未见贯通第一线至第五线的小节线，请复核")
    inside = left is not None and right is not None
    return {
        "left": left, "right": right,
        "left_x": left["x"] if left else None, "right_x": right["x"] if right else None,
        "inside": inside, "on_boundary": on_boundary,
        "review": "；".join(reviews) if reviews else None,
        "crop": crop_bounds(config, left, right, note_x, image.shape[1]) if inside else None,
        "search": {"left": int(config["left"]), "right": int(config["right"])},
    }


def in_measure(x, config, bounds):
    """Whether a point lies strictly between the two judging strokes of adjacent boundaries."""
    return bool(bounds and bounds["inside"] and bounds["left"]["x"] < x < bounds["right"]["x"]
                and config["left"] <= x <= config["right"])


def sample(image, x, y, config, bounds=None):
    if bounds is None:
        bounds = measure(image, config)
    inside = in_measure(x, config, bounds)
    return {**pixel(image, x, y, config), "pitch": pitch(x, y, config) if inside else None,
            "inside_measure": inside}


def report(image, config):
    x, y = config["note_x"], config["note_y"]
    bounds = measure(image, config)
    bottom = lines(config)[-1]
    first = max(0, math.floor(config["top"] - 3 * config["gap"]))
    last = min(image.shape[0] - 1, math.ceil(bottom + 3 * config["gap"]))
    profile = [pixel(image, x, row, config) for row in range(first, last + 1)]
    return {"reading": sample(image, x, y, config, bounds), "profile": profile,
            "lines_y": lines(config), "measure": bounds}


def practice():
    w, h = 780, 430
    cfg = settings({"left": 88, "right": 698, "top": 138, "gap": 22,
                    "note_x": 332, "note_y": 193, "ink_limit": 156}, (w, h))
    yy, xx = np.indices((h, w))
    shade = 237 - 12 * np.exp(-((xx - 709) / 108) ** 2) - 7 * np.sin(xx / 240) ** 2
    shade += 2 * np.sin(yy * 0.55 + xx * 0.19)
    photo = Image.fromarray(np.stack([shade + 11, shade + 5, shade - 7], axis=-1).clip(0, 255).astype("uint8"), "RGB")
    draw = ImageDraw.Draw(photo)
    ink = (35, 35, 34)
    draw.text((88, 70), "REHEARSAL NOTES", fill=ink)
    draw.text((88, 98), "Treble staff  /  Practice image", fill=(104, 95, 86))
    for index, y in enumerate(lines(cfg)):
        points = [(x, y + round(3 * math.sin((x - 222) / 130) + 1.4 * math.sin((x + index * 4) / 53)))
                  for x in range(88, 699)]
        draw.line(points, fill=ink, width=2)
    draw.arc((112, 135, 153, 210), 10, 327, fill=ink, width=5)
    draw.line([(134, 216), (146, 112), (139, 107), (127, 134), (157, 169)], fill=ink, width=4)
    draw.arc((116, 205, 145, 247), 17, 321, fill=ink, width=5)
    for x in (270, 469, 611):
        draw.line((x, 139, x, 228), fill=ink, width=3)
    draw.line((694, 136, 694, 225), fill=ink, width=2)
    draw.line((702, 136, 702, 225), fill=ink, width=5)
    for x, y in [(190, 204), (223, 215), (291, 182), (332, 193), (377, 171), (420, 182),
                 (498, 204), (530, 215), (566, 226), (637, 215), (667, 204)]:
        draw.ellipse((x - 10, y - 6, x + 10, y + 6), fill=ink)
        draw.line((x + 9, y, x + 9, y - 44), fill=ink, width=3)
    draw.polygon([(386, 124), (430, 136), (430, 141), (386, 129)], fill=ink)
    draw.text((88, 312), "Count in slowly  /  Check the marked note", fill=(104, 95, 86))
    return photo.size, png(photo), cfg
