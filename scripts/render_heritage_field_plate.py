"""Render supplementary figure S8 from the eight indexed field photographs."""

import argparse
import csv
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

PAPER = Path(__file__).resolve().parents[1] / "paper"
SOURCE = PAPER / "materials/photos"
PANEL = (905, 505)
PAD = 18
GAP = 22


def draw_plate(font):
    canvas = Image.new(
        "RGB", (2 * PANEL[0] + 2 * PAD + GAP, 4 * PANEL[1] + 2 * PAD + 3 * GAP), "white"
    )
    drawing = ImageDraw.Draw(canvas)
    with (SOURCE / "files.csv").open(encoding="utf-8", newline="") as index:
        rows = list(csv.DictReader(index))
    if [row["panel"] for row in rows] != list("abcdefgh"):
        raise ValueError("Expected eight consecutive S8 panels")
    for i, row in enumerate(rows):
        label = row["panel"]
        with Image.open(SOURCE / row["file"]) as source:
            photo = ImageOps.exif_transpose(source).convert("RGB")
        if label == "a":
            photo.thumbnail(PANEL, Image.Resampling.LANCZOS)
        else:
            center_y = {"b": 0.48, "g": 0.0}.get(label, 0.5)
            photo = ImageOps.fit(
                photo, PANEL, method=Image.Resampling.LANCZOS, centering=(0.5, center_y)
            )
        x = PAD + (i % 2) * (PANEL[0] + GAP) + (PANEL[0] - photo.width) // 2
        y = PAD + (i // 2) * (PANEL[1] + GAP) + (PANEL[1] - photo.height) // 2
        canvas.paste(photo, (x, y))
        left, top = x + 9, y + 9
        drawing.rectangle((left, top, left + 43, top + 43), fill="white")
        drawing.text((left + 10, top + 1), label, font=font, fill="black")
    return canvas


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--font", default="Arial Bold.ttf", help="path to the bold label typeface")
    args = parser.parse_args()
    face = ImageFont.truetype(args.font, 33)
    draw_plate(face).save(args.output, format="JPEG", quality=94, subsampling=0, dpi=(300, 300))


if __name__ == "__main__":
    main()
