#!/usr/bin/env python3
"""Regenerate Bring PWA icons as pure #000000 / #FFFFFF wordmarks."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

PWA = Path(__file__).resolve().parents[1] / "bring_fast" / "static" / "pwa"
SIZES = (1024, 512, 192, 180, 32)
BLACK = (0, 0, 0, 255)
WHITE = (255, 255, 255, 255)


def extract_mark_mask(img: Image.Image) -> Image.Image:
    """Binary mask from a light-polarity source (white background, dark mark)."""
    rgba = img.convert("RGBA")
    mask = Image.new("L", rgba.size)
    src = rgba.load()
    out = mask.load()
    for y in range(rgba.size[1]):
        for x in range(rgba.size[0]):
            r, g, b, a = src[x, y]
            if a < 128:
                out[x, y] = 0
                continue
            lum = (r + g + b) / 3
            out[x, y] = 255 if lum < 128 else 0
    return mask


def resize_mask(mask: Image.Image, size: int) -> Image.Image:
    scaled = mask.resize((size, size), Image.Resampling.LANCZOS)
    return scaled.point(lambda p: 255 if p >= 128 else 0, mode="L")


def render_from_mask(mask: Image.Image, theme: str) -> Image.Image:
    bg = WHITE if theme == "light" else BLACK
    mark = BLACK if theme == "light" else WHITE
    out = Image.new("RGBA", mask.size)
    mpx = mask.load()
    opx = out.load()
    for y in range(mask.size[1]):
        for x in range(mask.size[0]):
            opx[x, y] = mark if mpx[x, y] >= 128 else bg
    return out


def write_png(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG", optimize=True)


def write_favicon(path: Path, img32: Image.Image) -> None:
    img32 = img32.convert("RGBA")
    sizes = [(32, 32), (16, 16)]
    images = [img32.resize((s, s), Image.Resampling.NEAREST) for s, _ in sizes]
    images[0].save(path, format="ICO", sizes=sizes)


def main() -> None:
    wordmark = extract_mark_mask(Image.open(PWA / "icon-light-1024.png"))
    maskable = extract_mark_mask(Image.open(PWA / "icon-light-512-maskable.png"))

    for theme in ("light", "dark"):
        base = render_from_mask(wordmark, theme)
        write_png(base, PWA / f"icon-{theme}-1024.png")
        for size in SIZES:
            if size == 1024:
                continue
            sized_mask = resize_mask(wordmark, size)
            write_png(render_from_mask(sized_mask, theme), PWA / f"icon-{theme}-{size}.png")

        maskable_base = render_from_mask(maskable, theme)
        write_png(maskable_base, PWA / f"icon-{theme}-512-maskable.png")

    light_fallback = {
        32: "icon-32.png",
        180: "icon-180.png",
        192: "icon-192.png",
        512: "icon-512.png",
    }
    for size, name in light_fallback.items():
        write_png(Image.open(PWA / f"icon-light-{size}.png"), PWA / name)

    write_png(Image.open(PWA / "icon-light-512-maskable.png"), PWA / "icon-512-maskable.png")
    write_favicon(PWA / "favicon.ico", Image.open(PWA / "icon-light-32.png"))


if __name__ == "__main__":
    main()
