"""Generate Nexus desktop icon set (1024 PNG master + macOS iconset)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 1024
BACKGROUND = (16, 44, 35, 255)  # device green (#102C23)
PRIMARY = (29, 114, 84, 255)  # brand green, soft edge tone
INK = (246, 242, 233, 255)  # warm paper white
ACCENT = (178, 101, 39, 255)  # copper rule

ASSETS = Path(__file__).parent
MASTER = ASSETS / "nexus-1024.png"


def _font(size: int) -> ImageFont.FreeType:
    for candidate in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def render() -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Rounded-square tile with generous macOS safe-area margin.
    margin = 72
    box = [margin, margin, SIZE - margin, SIZE - margin]
    radius = 218
    draw.rounded_rectangle(box, radius=radius, fill=BACKGROUND)

    # Subtle inner top light.
    inner_margin = 96
    draw.rounded_rectangle(
        [inner_margin, inner_margin, SIZE - inner_margin, SIZE - inner_margin],
        radius=196,
        fill=(13, 74, 57, 255),
    )

    # Copper ledger rule below the N.
    rule_y = 720
    draw.rounded_rectangle([220, rule_y, 804, rule_y + 28], radius=14, fill=ACCENT)

    # Letter N.
    font = _font(520)
    bbox = draw.textbbox((0, 0), "N", font=font)
    w = bbox[2] - bbox[0]
    x = (SIZE - w) / 2 - bbox[0]
    y = 250 - bbox[1]
    draw.text((x, y), "N", font=font, fill=INK)

    return image


def main() -> None:
    image = render()
    image.save(MASTER, "PNG")
    iconset = ASSETS / "icon.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    # macOS required iconset filenames.
    mapping = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    for name, size in mapping.items():
        resized = image.resize((size, size), Image.LANCZOS)
        resized.save(iconset / name, "PNG")


if __name__ == "__main__":
    main()
