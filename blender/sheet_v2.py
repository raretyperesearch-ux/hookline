"""Composite v2 renders into contact sheets and icons (system python3 + PIL).
Usage: python3 blender/sheet_v2.py <raw_dir> sheet|boats|icons [names...]"""
import os, sys
from PIL import Image, ImageDraw, ImageFont
RAW, MODE = sys.argv[1], sys.argv[2]
SP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FISH = ["tuna", "swordfish", "puffer", "catfish", "koi", "shark", "eel", "angler", "clown", "blobfish", "sunfish", "piranha", "jelly", "crab", "octopus"]
BOATS = ["boat_row", "boat_speed", "boat_trawler"]

def crop(im, pad=0.06):
    bb = im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    if not bb: return im
    im = im.crop(bb)
    w, h = im.size; s = max(w, h); p = int(s * pad)
    out = Image.new("RGBA", (s + 2 * p, s + 2 * p), (0, 0, 0, 0))
    out.alpha_composite(im, ((s - w) // 2 + p, (s - h) // 2 + p))
    return out

def font(sz):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.exists(f): return ImageFont.truetype(f, sz)
    return ImageFont.load_default()

def grid(names, cols, cell, out, bg=(214, 236, 246, 255)):
    rows = (len(names) + cols - 1) // cols
    W = Image.new("RGBA", (cols * cell, rows * cell + 0), bg)
    d = ImageDraw.Draw(W); f = font(max(14, cell // 18))
    for i, n in enumerate(names):
        p = os.path.join(RAW, n + ".png")
        if not os.path.exists(p): continue
        im = crop(Image.open(p).convert("RGBA")).resize((cell - 20, cell - 20), Image.LANCZOS)
        x, y = (i % cols) * cell, (i // cols) * cell
        W.alpha_composite(im, (x + 10, y + 4))
        d.text((x + 12, y + cell - f.size - 10), n.replace("fish_", "").replace("boat_", ""), fill=(20, 50, 80, 255), font=f)
    W.convert("RGB").save(out)
    print("wrote", out)

if MODE == "sheet":
    names = ["fish_" + f for f in FISH] + BOATS
    grid(names, 6, 300, os.path.join(SP, "renders", "models2_sheet.png"))
elif MODE == "boats":
    grid(BOATS, 3, 640, os.path.join(SP, "renders", "models2_boats.png"))
elif MODE == "fish":
    grid(["fish_" + f for f in FISH], 5, 300, os.path.join(SP, "renders", "v2raw_fish.png"))
elif MODE == "icons":
    art = os.path.join(SP, "site", "art"); os.makedirs(art, exist_ok=True)
    for f in FISH:
        p = os.path.join(RAW, "fish_" + f + ".png")
        if os.path.exists(p):
            crop(Image.open(p).convert("RGBA"), 0.05).resize((256, 256), Image.LANCZOS).save(os.path.join(art, f"f_{f}.webp"), "WEBP", quality=90, method=6)
    for b, short in zip(BOATS, ["row", "speed", "trawler"]):
        p = os.path.join(RAW, b + ".png")
        if os.path.exists(p):
            crop(Image.open(p).convert("RGBA"), 0.04).resize((256, 256), Image.LANCZOS).save(os.path.join(art, f"b2_{short}.webp"), "WEBP", quality=90, method=6)
    print("icons written")
