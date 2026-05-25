from __future__ import annotations

from pathlib import Path

GREEN = "#00693E"
POLYGONS = (
    ((24, 4), (12, 20), (36, 20)),
    ((24, 14), (8, 32), (40, 32)),
    ((24, 25), (6, 42), (42, 42)),
)
TRUNK = (21, 39, 27, 46)


def main() -> None:
    Path("frontend/assets").mkdir(parents=True, exist_ok=True)
    for size in (512, 1024):
        output = f"frontend/assets/pinegraf-logo-{size}.png"
        try:
            import cairosvg

            cairosvg.svg2png(
                url="frontend/favicon.svg",
                write_to=output,
                output_width=size,
                output_height=size,
            )
        except (ImportError, OSError):
            _draw_with_pillow(size, output)


def _draw_with_pillow(size: int, output: str) -> None:
    from PIL import Image, ImageDraw

    scale = size / 48
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for polygon in POLYGONS:
        draw.polygon([(x * scale, y * scale) for x, y in polygon], fill=GREEN)
    left, top, right, bottom = TRUNK
    draw.rounded_rectangle(
        (left * scale, top * scale, right * scale, bottom * scale),
        radius=scale,
        fill=GREEN,
    )
    image.save(output)


if __name__ == "__main__":
    main()
