"""White-background flat icon -> exact-color icon with recovered alpha, trimmed, padded, resized."""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

src, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
BRAND = np.array([0x6D, 0x4A, 0xFF], dtype=float)
rgb = np.asarray(Image.open(src).convert("RGB")).astype(float)
ink = (255 - rgb[..., 0]) + (255 - rgb[..., 1])  # red and green drop the most from white to violet
fill = np.median(rgb[ink > 0.9 * ink.max()], axis=0)
alpha = np.clip(ink / (0.95 * ((255 - fill[0]) + (255 - fill[1]))), 0, 1)  # the solid fill varies a few percent; make it fully opaque
alpha[alpha < 0.04] = 0
print(f"[icon] sampled fill #{''.join(f'{int(c):02x}' for c in fill)} -> brand #6d4aff; opaque px={int((alpha > 0.98).sum())}")

rgba = np.dstack([np.broadcast_to(BRAND, rgb.shape), alpha * 255]).astype(np.uint8)
icon = Image.fromarray(rgba, "RGBA")
icon = icon.crop(icon.getbbox())
side = round(max(icon.size) / 0.92)  # 4% padding on each side
square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
square.paste(icon, ((side - icon.width) // 2, (side - icon.height) // 2))
out_dir.mkdir(parents=True, exist_ok=True)
for size in (16, 32, 48, 96, 128, 512):
    square.resize((size, size), Image.LANCZOS).save(out_dir / f"icon-{size}.png", optimize=True)
print(f"[icon] trimmed {icon.size} -> square {side}px -> {out_dir}/icon-{{16,32,48,96,128,512}}.png")

sheet = Image.new("RGBA", (2 * (128 + 32 + 16 + 64) + 40, 168), (255, 255, 255, 255))
for column, background in enumerate([(255, 255, 255, 255), (21, 20, 26, 255)]):
    x0 = column * (128 + 32 + 16 + 64 + 40)
    sheet.paste(Image.new("RGBA", (128 + 32 + 16 + 64, 168), background), (x0, 0))
    x = x0 + 16
    for size in (128, 32, 16):
        small = Image.open(out_dir / f"icon-{size}.png")
        sheet.alpha_composite(small, (x, 20 + (128 - size) // 2))
        x += size + 16
sheet.convert("RGB").resize((sheet.width * 2, sheet.height * 2), Image.NEAREST).save(out_dir / "contact-sheet.png")
