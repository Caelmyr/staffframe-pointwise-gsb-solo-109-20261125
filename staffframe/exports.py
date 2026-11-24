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
    bounds = result["measure"]
    panel = 19 + 20 * (3 if bounds["review"] else 2)
    output = Image.new("RGB", (max(390, width), height + panel), "#302b27")
    output.paste(Image.fromarray(image), (0, 0))
    draw = ImageDraw.Draw(output)
    config = card["settings"]
    for y in result["lines_y"]:
        draw.line((config["left"], y, config["right"], y), fill="#9b372a", width=2)
    if bounds["inside"]:
        stroke_width = max(2, round(config["gap"] / 9))
        for boundary in (bounds["left"], bounds["right"]):
            for stroke in boundary["strokes"]:
                draw.rectangle((stroke["start"], boundary["top"], stroke["end"], boundary["bottom"]),
                               outline="#1f5f8b", width=stroke_width)
        crop = bounds["crop"]
        top = min(bounds["left"]["top"], bounds["right"]["top"])
        bottom = max(bounds["left"]["bottom"], bounds["right"]["bottom"])
        draw.rectangle((crop["left"], top, crop["right"] - 1, bottom), outline="#c98a2e", width=1)
    point = result["reading"]
    x, y = point["x"], point["y"]
    draw.ellipse((x - 10, y - 10, x + 10, y + 10), outline="#356256", width=3)
    note = point["pitch"]
    if point["inside_measure"] and note:
        label = f'Treble position {note["label"]} · this measure'
    elif bounds["on_boundary"]:
        label = "Note sits on a barline; move it inside a measure"
    elif bounds["inside"]:
        label = "Outside this measure; no pitch given"
    else:
        label = "No complete measure boundary; review"
    draw.text((13, height + 13), label, fill="#f1e9dd")
    draw.text((13, height + 33), f'Source ({x}, {y})  Luma {point["level"]:.1f}', fill="#e6bc9b")
    if bounds["review"]:
        missing = " and ".join(name for name, key in (("Left", "left"), ("Right", "right"))
                               if bounds[key] is None)
        draw.text((13, height + 53), f"{missing} barline not found; review required", fill="#e6bc9b")
    return png(output)


def measure_image(image, result):
    """Crop the chosen measure from the source frame, keeping original pixel coordinates."""
    crop = result["measure"]["crop"]
    return png(Image.fromarray(image[:, crop["left"]:crop["right"]]))


def package(card, capture, image, result):
    buffer = BytesIO()
    bounds = result["measure"]
    packet = {"card": card, "reading": result["reading"], "lines_y": result["lines_y"],
              "measure": {"inside": bounds["inside"], "review": bounds["review"],
                          "left_x": bounds["left_x"], "right_x": bounds["right_x"],
                          "crop": bounds["crop"],
                          "left_strokes": [stroke["x"] for stroke in bounds["left"]["strokes"]] if bounds["left"] else [],
                          "right_strokes": [stroke["x"] for stroke in bounds["right"]["strokes"]] if bounds["right"] else [],
                          "double": bool(bounds["left"] and bounds["left"]["double"]) or
                                    bool(bounds["right"] and bounds["right"]["double"])}}
    with ZipFile(buffer, "w", ZIP_DEFLATED) as output:
        output.writestr("score-page.png", capture)
        output.writestr("reading.json", json.dumps(packet, ensure_ascii=False, indent=2))
        output.writestr("column.csv", trace_table(result))
        output.writestr("reading-proof.png", proof(image, card, result))
        if bounds["inside"]:
            output.writestr("measure.png", measure_image(image, result))
    return buffer.getvalue()
