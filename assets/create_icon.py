#!/usr/bin/env python3
"""Erzeugt ein einfaches Platzhalter-Icon für ClassShare (PNG, 256×256)."""

from pathlib import Path


def create_icon(dest: Path) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("Pillow nicht gefunden – Icon wird nicht erstellt.")
        return

    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Blauer Kreis-Hintergrund
    margin = 8
    draw.ellipse([margin, margin, size - margin, size - margin], fill=(52, 101, 164, 255))

    # Weißes "CS" zentriert
    text = "CS"
    font_size = 96
    font = None
    for candidate in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ]:
        if Path(candidate).exists():
            try:
                font = ImageFont.truetype(candidate, font_size)
                break
            except Exception:
                pass

    if font is None:
        try:
            font = ImageFont.load_default(size=font_size)
        except TypeError:
            # Very old Pillow: default font is tiny; scale text area instead
            font = ImageFont.load_default()
            font_size = 11  # actual size of the default bitmap font
            # Re-draw on a smaller canvas and paste, to keep text visible
            small = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw_s = ImageDraw.Draw(small)
            draw_s.ellipse([margin, margin, size - margin, size - margin], fill=(52, 101, 164, 255))
            bbox = draw_s.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            x = (size - text_w) // 2 - bbox[0]
            y = (size - text_h) // 2 - bbox[1]
            draw_s.text((x, y), text, fill=(255, 255, 255, 255), font=font)
            img = small
            dest.parent.mkdir(parents=True, exist_ok=True)
            img.save(dest, "PNG")
            print(f"Icon erstellt (Pillow zu alt für Schriftgröße): {dest}")
            return

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (size - text_w) // 2 - bbox[0]
    y = (size - text_h) // 2 - bbox[1]
    draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "PNG")
    print(f"Icon erstellt: {dest}")


if __name__ == "__main__":
    import sys

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "icon.png"
    create_icon(out)
