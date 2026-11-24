"""Produce the staff reading proof, column readings, and observation packet."""

from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile
import csv
import json

from PIL import Image, ImageDraw

from .staff import png


def trace_table(result):
    data = StringIO()
    writer = csv.writer(data)
    writer.writerow(("x_px", "y_px", "r", "g", "b", "luma", "ink"))
    for point in result["profile"]:
        writer.writerow((point["x"], point["y"], *point["rgb"], f'{point["level"]:.3f}', int(point["ink"])))
    return data.getvalue().encode()


def proof(image, card, result):
    height, width = image.shape[:2]
    output = Image.new("RGB", (max(390, width), height + 59), "#302b27")
    output.paste(Image.fromarray(image), (0, 0))
    draw = ImageDraw.Draw(output)
    config = card["settings"]
    for y in result["lines_y"]:
        draw.line((config["left"], y, config["right"], y), fill="#9b372a", width=2)
    point = result["reading"]
    x, y = point["x"], point["y"]
    draw.ellipse((x - 10, y - 10, x + 10, y + 10), outline="#356256", width=3)
    note = point["pitch"]
    label = f'Treble position {note["label"]}' if note else "Outside staff reading range"
    draw.text((13, height + 13), label, fill="#f1e9dd")
    draw.text((13, height + 33), f'Source ({x}, {y})  Luma {point["level"]:.1f}', fill="#e6bc9b")
    return png(output)


def package(card, capture, image, result):
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as output:
        output.writestr("score-page.png", capture)
        output.writestr("reading.json", json.dumps({"card": card, "reading": result["reading"],
                                                    "lines_y": result["lines_y"]}, ensure_ascii=False, indent=2))
        output.writestr("column.csv", trace_table(result))
        output.writestr("reading-proof.png", proof(image, card, result))
    return buffer.getvalue()
