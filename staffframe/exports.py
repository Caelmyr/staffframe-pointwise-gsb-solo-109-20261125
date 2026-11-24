"""Produce the staff reading proof, column readings, and observation packet."""

from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile
import csv
import json

from PIL import Image, ImageDraw

from . import tracking
from .staff import png


def trace_table(result):
    data = StringIO()
    writer = csv.writer(data)
    writer.writerow(("x_px", "y_px", "r", "g", "b", "luma", "ink"))
    for point in result["profile"]:
        writer.writerow((point["x"], point["y"], *point["rgb"], f'{point["level"]:.3f}', int(point["ink"])))
    return data.getvalue().encode()


def _dashed(draw, points, fill, width, dash=8, gap=6):
    """Dashed polyline helper (ImageDraw.line dash support varies by Pillow)."""
    for index in range(0, len(points) - 1, dash + gap):
        draw.line(points[index:min(index + dash, len(points))], fill=fill, width=width)


def _draw_staff(draw, config, result):
    mode, trace = result["staff_mode"], result.get("trace")
    if mode == "traced" and trace is not None:
        left, right = trace["left"], trace["right"]
        for line_index, values in enumerate(trace["lines"]):
            points = [(left + offset, y) for offset, y in enumerate(values)]
            draw.line(points, fill="#2e7d6b", width=2)
            for start, end in trace["pending_ranges"][line_index]:
                segment = points[start - left:end - left + 1]
                if len(segment) >= 2:
                    _dashed(draw, segment, fill="#d8942f", width=3)
    else:
        for y in result["lines_y"]:
            draw.line((config["left"], y, config["right"], y), fill="#9b372a", width=2)


def proof(image, card, result):
    height, width = image.shape[:2]
    output = Image.new("RGB", (max(390, width), height + 59), "#302b27")
    output.paste(Image.fromarray(image), (0, 0))
    draw = ImageDraw.Draw(output)
    config = card["settings"]
    _draw_staff(draw, config, result)
    point = result["reading"]
    x, y = point["x"], point["y"]
    draw.ellipse((x - 10, y - 10, x + 10, y + 10), outline="#356256", width=3)
    pitch = point["pitch"]
    if pitch is None:
        label = "Outside staff reading range"
    elif pitch.get("pending"):
        label = "Review column (staff line gap) - no pitch assigned"
    else:
        label = f'Treble position {pitch["label"]}'
    draw.text((13, height + 13), label, fill="#f1e9dd")
    draw.text((13, height + 33), f'Source ({x}, {y})  Luma {point["level"]:.1f}', fill="#e6bc9b")
    return png(output)


def package(card, capture, image, result):
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as output:
        output.writestr("score-page.png", capture)
        output.writestr("reading.json", json.dumps({
            "card": card,
            "staff_mode": result["staff_mode"],
            "trace_summary": result.get("trace_summary"),
            "reading": result["reading"],
            "lines_y": result["lines_y"],
        }, ensure_ascii=False, indent=2))
        output.writestr("column.csv", trace_table(result))
        output.writestr("reading-proof.png", proof(image, card, result))
    return buffer.getvalue()
