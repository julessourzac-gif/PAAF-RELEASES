#!/usr/bin/env python3
"""
Génère le logo de PAAF (outil de développement, nécessite Pillow) :
  macos/AppIcon.icns        icône de l'application (via iconutil, macOS)
  src/assets/logo_*.png     logo affiché dans l'interface

    .venv/bin/pip install pillow && .venv/bin/python macos/make_icon.py
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
S = 2048                      # dessin suréchantillonné, réduit ensuite
BG_TOP = (30, 34, 58)
BG_BOTTOM = (11, 13, 24)
TEAL = (45, 212, 191)
VIOLET = (139, 92, 246)
ORANGE = (255, 138, 61)


def lerp(a, b, t):
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


def vertical_gradient(size, top, bottom):
    w, h = size
    g = Image.new("RGB", (1, h))
    for y in range(h):
        g.putpixel((0, y), lerp(top, bottom, y / max(1, h - 1)))
    return g.resize((w, h))


def font(size):
    for path, index in [("/System/Library/Fonts/SFNS.ttf", 0),
                        ("/System/Library/Fonts/HelveticaNeue.ttc", 1),
                        ("/System/Library/Fonts/Helvetica.ttc", 1)]:
        try:
            f = ImageFont.truetype(path, size, index=index)
            try:
                f.set_variation_by_name("Bold")
            except Exception:
                pass
            return f
        except Exception:
            continue
    return ImageFont.load_default()


def draw_logo(wordmark=True):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    margin = int(S * 0.098)              # grille d'icône macOS (corps ~80 %)
    body = (margin, margin, S - margin, S - margin)
    radius = int((S - 2 * margin) * 0.225)

    # ombre portée
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (body[0], body[1] + S * 0.012, body[2], body[3] + S * 0.012), radius, fill=(0, 0, 0, 150))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(S * 0.018)))

    # fond dégradé
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(body, radius, fill=255)
    bg = vertical_gradient((S, S), BG_TOP, BG_BOTTOM).convert("RGBA")
    img.paste(bg, (0, 0), mask)

    # liseré clair en haut
    rim = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(rim).rounded_rectangle(body, radius, outline=(255, 255, 255, 38), width=int(S * 0.006))
    img.alpha_composite(rim)

    # forme d'onde : barres arrondies, dégradé turquoise -> violet
    wave = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    wd = ImageDraw.Draw(wave)
    n = 15
    x0, x1 = S * 0.235, S * 0.765
    cy = S * (0.44 if wordmark else 0.5)
    bar_w = (x1 - x0) / n * 0.56
    amp = [0.18, 0.32, 0.5, 0.36, 0.62, 0.86, 0.55, 1.0, 0.7, 0.9, 0.48, 0.66, 0.4, 0.26, 0.16]
    max_h = S * 0.36
    for i, a in enumerate(amp):
        cx = x0 + (i + 0.5) * (x1 - x0) / n
        h = max(bar_w, max_h * a)
        col = lerp(TEAL, VIOLET, i / (n - 1))
        wd.rounded_rectangle((cx - bar_w / 2, cy - h / 2, cx + bar_w / 2, cy + h / 2),
                             bar_w / 2, fill=col + (255,))
    glow = wave.filter(ImageFilter.GaussianBlur(S * 0.02))
    img.alpha_composite(Image.blend(Image.new("RGBA", (S, S), (0, 0, 0, 0)), glow, 0.55))
    img.alpha_composite(wave)

    # tête de lecture orange (le point d'entrée timecode)
    ph = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    pd = ImageDraw.Draw(ph)
    px = x0 + 10 * (x1 - x0) / n          # entre deux barres
    top = cy - max_h * 0.66
    bottom = cy + max_h * 0.62
    lw = S * 0.014
    pd.rounded_rectangle((px - lw / 2, top, px + lw / 2, bottom), lw / 2, fill=ORANGE + (255,))
    tri = S * 0.05
    pd.polygon([(px - tri, top - tri * 1.1), (px + tri, top - tri * 1.1), (px, top + tri * 0.2)],
               fill=ORANGE + (255,))
    img.alpha_composite(ph.filter(ImageFilter.GaussianBlur(S * 0.008)))
    img.alpha_composite(ph)

    if wordmark:
        d = ImageDraw.Draw(img)
        f = font(int(S * 0.135))
        text = "PAAF"
        tw = d.textlength(text, font=f)
        spacing = S * 0.012
        total = tw + spacing * (len(text) - 1)
        x = (S - total) / 2
        y = S * 0.70
        for ch in text:
            d.text((x, y), ch, font=f, fill=(238, 241, 250, 255))
            x += d.textlength(ch, font=f) + spacing
    return img


def main():
    big = draw_logo(wordmark=True)
    small = draw_logo(wordmark=False)   # sans texte aux petites tailles (lisibilité)
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
