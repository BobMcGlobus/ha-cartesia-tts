"""Generate the brand icon for the Cartesia Sonic TTS integration.

Original artwork, deliberately not an imitation of Cartesia's own mark, so the
repository ships nothing it has no licence for: a rounded badge with a five-bar
voice waveform. Drawn at 8x and downsampled so the curves stay clean at 256 px.

Run with ``pip install Pillow && python scripts/make_icon.py``. The generated
PNGs are committed, so this only needs running when the artwork changes.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = (
    Path(__file__).resolve().parents[1] / "custom_components" / "cartesia_tts" / "brand"
)
SS = 8  # supersampling factor
BASE = 512  # largest real output; 256 is derived from it

# Violet -> cyan, distinct from Cartesia's own palette and legible on both a
# light and a dark Home Assistant theme.
TOP_LEFT = (124, 92, 255)
BOTTOM_RIGHT = (34, 211, 238)

# Bar heights as a fraction of the badge, symmetric around the middle.
BARS = [0.34, 0.60, 0.86, 0.60, 0.34]


def gradient(size: int) -> Image.Image:
    """Diagonal two-stop gradient."""
    image = Image.new("RGB", (size, size))
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            pixels[x, y] = tuple(
                round(start + (end - start) * t)
                for start, end in zip(TOP_LEFT, BOTTOM_RIGHT, strict=True)
            )
    return image


def build(size: int) -> Image.Image:
    """Render the icon at `size` pixels."""
    big = size * SS

    # Rounded-square badge as an alpha mask. No padding: the brands guidelines
    # ask for the artwork to be trimmed to the content.
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, big - 1, big - 1), radius=round(big * 0.235), fill=255
    )

    icon = gradient(big).convert("RGBA")
    icon.putalpha(mask)

    # Five rounded bars, centred, in the white of the badge.
    draw = ImageDraw.Draw(icon)
    bar_width = big * 0.100
    gap = big * 0.056
    total = len(BARS) * bar_width + (len(BARS) - 1) * gap
    x = (big - total) / 2
    middle = big / 2
    for fraction in BARS:
        half = big * fraction / 2
        draw.rounded_rectangle(
            (x, middle - half, x + bar_width, middle + half),
            radius=bar_width / 2,
            fill=(255, 255, 255, 255),
        )
        x += bar_width + gap

    return icon.resize((size, size), Image.LANCZOS)


def main() -> None:
    """Write both icon sizes and report what was produced."""
    OUT.mkdir(parents=True, exist_ok=True)
    icon = build(BASE)
    icon.save(OUT / "icon@2x.png", optimize=True)
    icon.resize((256, 256), Image.LANCZOS).save(OUT / "icon.png", optimize=True)
    for name in ("icon.png", "icon@2x.png"):
        path = OUT / name
        with Image.open(path) as saved:
            print(f"{name}: {saved.size} {saved.mode} {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
