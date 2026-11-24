"""Read treble-staff positions and ink from calibrated source pixels."""

from io import BytesIO
import math

import numpy as np
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from image_lab.validation import Invalid, integer, number

from . import tracking


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


def pitch(x, y, config, mode="straight", trace=None):
    """Report the written natural pitch in treble clef.

    Straight mode keeps the manual horizontal lines; traced mode reads against
    the real curved lines (and gives no pitch name on review columns).
    """
    return tracking.pitch_at(x, y, config, mode, trace)


def sample(image, x, y, config, mode="straight", trace=None):
    return {**pixel(image, x, y, config), "pitch": pitch(x, y, config, mode, trace)}


def report(image, config, mode="straight", trace=None):
    x, y = config["note_x"], config["note_y"]
    if mode == "traced" and trace is not None:
        top = tracking.line_y(trace, x, 0)
        bottom = tracking.line_y(trace, x, 4)
    else:
        top = config["top"]
        bottom = lines(config)[-1]
    first = max(0, math.floor(top - 3 * config["gap"]))
    last = min(image.shape[0] - 1, math.ceil(bottom + 3 * config["gap"]))
    profile = [pixel(image, x, row, config) for row in range(first, last + 1)]
    return {
        "reading": sample(image, x, y, config, mode, trace),
        "profile": profile,
        "lines_y": lines(config),
        "staff_mode": mode,
        "trace": trace,
        "trace_summary": tracking.trace_summary(trace) if mode == "traced" and trace is not None else None,
    }


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

    def paper_at(px, py):
        value = (237 - 12 * math.exp(-((px - 709) / 108) ** 2) - 7 * math.sin(px / 240) ** 2
                 + 2 * math.sin(py * 0.55 + px * 0.19))
        noise = ((px * 17 + py * 31) % 5) - 2
        return (int(np.clip(value + 11 + noise, 0, 255)),
                int(np.clip(value + 5 + noise, 0, 255)),
                int(np.clip(value - 7 + noise, 0, 255)))

    def erase(x0, x1, line_index):
        rough_y = lines(cfg)[line_index]
        for px in range(x0, x1 + 1):
            bend = 3 * math.sin((px - 222) / 130) + 1.4 * math.sin((px + line_index * 4) / 53)
            cy = int(round(rough_y + bend))
            for py in range(cy - 2, cy + 3):
                photo.putpixel((px, py), paper_at(px, py))

    # A long interruption (> two line gaps) becomes a review column; a short one
    # (with ink at both ends) stays bridgeable. Both avoid notes, stems and bars.
    erase(500, 560, 0)
    erase(348, 370, 3)
    return photo.size, png(photo), cfg
