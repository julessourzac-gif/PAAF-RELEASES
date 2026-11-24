#!/usr/bin/env python3
"""
Génère le logo de PAAF (outil de développement, nécessite Pillow) :
  macos/AppIcon.icns        icône de l'application (via iconutil, macOS)
  src/assets/logo_*.png     logo affiché dans l'interface

    python3 macos/make_icon.py        (Pillow requis : pip install pillow)
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
S = 2048                      # dessin suréchantillonné, réduit ensuite
# Couleurs du logo PAAF (maquettes 1a)
BG_TOP = (30, 44, 74)
BG_BOTTOM = (20, 30, 54)
LANE = (46, 61, 88)
VIDEO = (90, 174, 230)
AAF = (76, 201, 176)
VAUD = (242, 206, 60)
MARK = (22, 33, 58)
PLAYHEAD = (232, 71, 58)


def lerp(a, b, t):
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


def vertical_gradient(size, top, bottom):
    w, h = size
    g = Image.new("RGB", (1, h))
    for y in range(h):
        g.putpixel((0, y), lerp(top, bottom, y / max(1, h - 1)))
    return g.resize((w, h))


def draw_logo(detail=True):
    """Icône : pistes empilées (vidéo, deux AAF, audio des vidéos) et tête de lecture rouge.
    detail=False : sans perforations ni formes d'onde (petites tailles)."""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    margin = int(S * 0.098)              # grille d'icône macOS (corps ~80 %)
    bx0, by0, bx1, by1 = margin, margin, S - margin, S - margin
    bw, bh = bx1 - bx0, by1 - by0
    radius = int(bw * 0.225)
    X = lambda fx: bx0 + fx * bw  # noqa: E731
    Y = lambda fy: by0 + fy * bh  # noqa: E731

    # ombre portée
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (bx0, by0 + S * 0.012, bx1, by1 + S * 0.012), radius, fill=(0, 0, 0, 150))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(S * 0.018)))

    # fond dégradé
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((bx0, by0, bx1, by1), radius, fill=255)
    img.paste(vertical_gradient((S, S), BG_TOP, BG_BOTTOM).convert("RGBA"), (0, 0), mask)
    rim = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(rim).rounded_rectangle((bx0, by0, bx1, by1), radius, outline=(255, 255, 255, 30),
                                          width=int(S * 0.005))
    img.alpha_composite(rim)

    d = ImageDraw.Draw(img)
    lx0, lx1, cx0 = X(0.085), X(0.915), X(0.405)
    rows = [(0.145, 0.285), (0.335, 0.455), (0.500, 0.620), (0.665, 0.785), (0.830, 0.885)]
    r_lane = bw * 0.025
    for t, b in rows:
        d.rounded_rectangle((lx0, Y(t), lx1, Y(b)), r_lane, fill=LANE)

    def clip(row, x_end, color):
        t, b = rows[row]
        pad = bh * 0.008
        box = (cx0, Y(t) + pad, x_end - pad, Y(b) - pad)
        d.rounded_rectangle(box, bw * 0.02, fill=color)
        return box

    v = clip(0, lx1, VIDEO)
    a1 = clip(1, lx1, AAF)
    a2 = clip(2, lx1, AAF)
    va = clip(3, X(0.735), VAUD)
    if detail:
        # perforations de la piste vidéo
        hw, hh = bw * 0.03, bh * 0.016
        n = 9
        for row_y in (v[1] + (v[3] - v[1]) * 0.22, v[1] + (v[3] - v[1]) * 0.78):
            for k in range(n):
                cx = v[0] + (v[2] - v[0]) * (k + 0.75) / (n + 0.5)
                d.rounded_rectangle((cx - hw / 2, row_y - hh / 2, cx + hw / 2, row_y + hh / 2), hh * 0.35,
                                    fill=MARK)

        # formes d'onde
        def wave(box, n, x_from, x_to):
            cy, half = (box[1] + box[3]) / 2, (box[3] - box[1]) * 0.36
            amps = [0.55, 0.8, 0.45, 1.0, 0.6, 0.85, 0.4, 0.95, 0.7, 0.5, 1.0, 0.65, 0.85, 0.45, 0.75, 0.55]
            lw = bw * 0.0085
            for k in range(n):
                x = box[0] + (box[2] - box[0]) * (x_from + (x_to - x_from) * k / (n - 1))
                h = half * amps[k % len(amps)]
                d.rounded_rectangle((x - lw / 2, cy - h, x + lw / 2, cy + h), lw / 2, fill=MARK)

        wave(a1, 16, 0.14, 0.86)
        wave(a2, 16, 0.14, 0.86)
        wave(va, 10, 0.16, 0.84)

    # tête de lecture
    px, lw = X(0.392), bw * 0.016
    top, bottom = Y(0.085), Y(0.885)
    d.rectangle((px - lw / 2, top, px + lw / 2, bottom), fill=PLAYHEAD)
    tri = bw * 0.042
    d.polygon([(px - tri, top - tri * 0.25), (px + tri, top - tri * 0.25), (px, top + tri * 0.95)],
              fill=PLAYHEAD)
    return img


def main():
    big = draw_logo(detail=True)
    small = draw_logo(detail=False)     # sans perforations ni ondes aux petites tailles (lisibilité)
    assets = ROOT / "src" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    big.resize((1024, 1024), Image.LANCZOS).save(assets / "logo_1024.png")
    for px in (64, 128, 256):
        src = small if px <= 64 else big
        src.resize((px, px), Image.LANCZOS).save(assets / f"logo_{px}.png")

    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "AppIcon.iconset"
            iconset.mkdir()
            for base in (16, 32, 128, 256, 512):
                for scale in (1, 2):
                    px = base * scale
                    src = small if px <= 64 else big
                    name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
                    src.resize((px, px), Image.LANCZOS).save(iconset / name)
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o",
                            str(ROOT / "macos" / "AppIcon.icns")], check=True)
        print("✓ macos/AppIcon.icns")
    print("✓ src/assets/logo_*.png")


if __name__ == "__main__":
    main()
