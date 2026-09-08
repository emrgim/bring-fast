#!/usr/bin/env python3
"""Regenerate Bring PWA icons as pure black/white wordmarks."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image

PWA = Path(__file__).resolve().parents[1] / "bring_fast" / "static" / "pwa"
LIGHT_BG = (245, 240, 238)
DARK_BG = (26, 10, 12)
MARK = (232, 74, 64)
SIZES = (1024, 512, 192, 180, 32)


def recolor_pixel(r: int, g: int, b: int, a: int, theme: str) -> tuple[int, int, int, int]:
    bg_val = 255 if theme == "light" else 0
    mark_val = 0 if theme == "light" else 255
    if a < 8:
        return (bg_val, bg_val, bg_val, 0)
    bg = LIGHT_BG if theme == "light" else DARK_BG
    bg_dist = math.hypot(r - bg[0], g - bg[1], b - bg[2])
    red = (r - max(g, b)) / 255.0
    if red > 0.18:
        return (mark_val, mark_val, mark_val, a)
    if bg_dist < 18:
        return (bg_val, bg_val, bg_val, a)
    mark_dist = math.hypot(r - MARK[0], g - MARK[1], b - MARK[2])
    if mark_dist < bg_dist:
        strength = max(0.0, min(1.0, 1.0 - mark_dist / 55.0))
        value = round(bg_val + (mark_val - bg_val) * strength)
        return (value, value, value, a)
    return (bg_val, bg_val, bg_val, a)


def recolor(img: Image.Image, theme: str) -> Image.Image:
    src = img.convert("RGBA")
    out = Image.new("RGBA", src.size)
    src_px = src.load()
    out_px = out.load()
    for y in range(src.size[1]):
        for x in range(src.size[0]):
            out_px[x, y] = recolor_pixel(*src_px[x, y], theme)
    return out


def resize(img: Image.Image, size: int) -> Image.Image:
    return img.resize((size, size), Image.Resampling.LANCZOS)


def write_png(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG", optimize=True)


def write_favicon(path: Path, img32: Image.Image) -> None:
    img32 = img32.convert("RGBA")
    sizes = [(32, 32), (16, 16)]
    images = [resize(img32, size) for size, _ in sizes]
    images[0].save(path, format="ICO", sizes=sizes)


def main() -> None:
    for theme in ("light", "dark"):
        base = recolor(Image.open(PWA / f"icon-{theme}-1024.png"), theme)
        write_png(base, PWA / f"icon-{theme}-1024.png")
        for size in SIZES:
            if size == 1024:
                continue
            write_png(resize(base, size), PWA / f"icon-{theme}-{size}.png")

        maskable = recolor(Image.open(PWA / f"icon-{theme}-512-maskable.png"), theme)
        write_png(maskable, PWA / f"icon-{theme}-512-maskable.png")

    dark_names = {
        32: "icon-32.png",
        180: "icon-180.png",
        192: "icon-192.png",
        512: "icon-512.png",
    }
    for size, name in dark_names.items():
        src = PWA / f"icon-dark-{size}.png"
        write_png(Image.open(src), PWA / name)

    maskable = Image.open(PWA / "icon-dark-512-maskable.png")
    write_png(maskable, PWA / "icon-512-maskable.png")
    write_favicon(PWA / "favicon.ico", Image.open(PWA / "icon-dark-32.png"))


if __name__ == "__main__":
    main()
