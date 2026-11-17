"""Haberde geçen ülkeleri gösteren harita kartı (PNG, şeffaf köşeli)."""
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

GEO = Path(__file__).parent / "assets" / "geo" / "countries.json"
_FEATS = None


def feats():
    global _FEATS
    if _FEATS is None:
        _FEATS = json.loads(GEO.read_text())["features"]
    return _FEATS


def polys(f):
    g = f["geometry"]
    if g["type"] == "Polygon":
        return [g["coordinates"]]
    return g["coordinates"]


def bbox_of(isos):
    xs, ys = [], []
    for f in feats():
        if f["properties"]["iso"] in isos:
            for p in polys(f):
                for x, y in p[0]:
                    xs.append(x)
                    ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def to_iso3(code):
    code = (code or "").upper()
    if len(code) == 2:
        for f in feats():
            if f["properties"].get("iso2") == code:
                return f["properties"]["iso"]
    return code


def render_map(isos, labels, out_png, width=900, height=520, font=None):
    """isos: ISO-3 ya da ISO-2 kodları (ör. ["TUR","IR"]); labels: ekranda yazacak adlar."""
    isos = [to_iso3(i) for i in isos if i]
    bb = bbox_of(isos)
    if not bb:
        return None
    x0, y0, x1, y1 = bb
    # çok uzak ülkeler (ör. ABD + İran) için geniş, tek ülke için yakın çerçeve
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    span_x = max(x1 - x0, 25) * 1.6
    span_y = max(y1 - y0, 14) * 1.6
    ratio = width / height
    if span_x / span_y < ratio:
        span_x = span_y * ratio
    else:
        span_y = span_x / ratio
    span_x, span_y = min(span_x, 360), min(span_y, 180)
    lx0, ly1 = cx - span_x / 2, cy + span_y / 2
    S = 2  # yüksek çözünürlükte çiz, sonra küçült (kenar yumuşatma)
    W, H = width * S, height * S

    def P(x, y):
        return ((x - lx0) / span_x * W, (ly1 - y) / span_y * H)

    base = Image.new("RGBA", (W, H), (8, 12, 28, 235))
    d = ImageDraw.Draw(base)
    # ızgara
    for gx in range(-180, 181, 15):
        a, b = P(gx, -90), P(gx, 90)
        d.line([a, b], fill=(40, 70, 120, 70), width=S)
    for gy in range(-90, 91, 15):
        a, b = P(-180, gy), P(180, gy)
        d.line([a, b], fill=(40, 70, 120, 70), width=S)
    hl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dh = ImageDraw.Draw(hl)
    centers = {}
    for f in feats():
        iso = f["properties"]["iso"]
        on = iso in isos
        for p in polys(f):
            pts = [P(x, y) for x, y in p[0]]
            if len(pts) < 3:
                continue
            if on:
                dh.polygon(pts, fill=(227, 26, 45, 255))
            else:
                d.polygon(pts, fill=(28, 44, 78, 255), outline=(60, 95, 150, 255))
        if on:
            bx = bbox_of([iso])
            centers[iso] = P((bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2)
    glow = hl.filter(ImageFilter.GaussianBlur(10 * S))
    base = Image.alpha_composite(base, glow)
    base = Image.alpha_composite(base, hl)
    d = ImageDraw.Draw(base)
    try:
        fnt = ImageFont.truetype(str(font), 30 * S) if font else ImageFont.load_default()
    except Exception:  # noqa
        fnt = ImageFont.load_default()
    for iso, label in zip(isos, labels):
        if iso not in centers or not label:
            continue
        x, y = centers[iso]
        tw = d.textlength(label, font=fnt)
        x = min(max(x - tw / 2, 10 * S), W - tw - 10 * S)
        y = min(max(y - 18 * S, 10 * S), H - 50 * S)
        d.rounded_rectangle([x - 10 * S, y - 4 * S, x + tw + 10 * S, y + 40 * S], radius=8 * S,
                            fill=(0, 0, 0, 190))
        d.text((x, y), label, font=fnt, fill=(255, 255, 255, 255))
    # çerçeve + kırmızı üst şerit
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=24 * S, outline=(227, 26, 45, 255), width=4 * S)
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, H - 1], radius=24 * S, fill=255)
    base.putalpha(Image.composite(base.getchannel("A"), mask, mask))
    base = base.resize((width, height), Image.LANCZOS)
    base.save(out_png)
    return out_png
