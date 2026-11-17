#!/usr/bin/env python3
"""
Ücretsiz kısa video üretici (GitHub Actions için).

Girdi (ortam değişkenleri):
  PAYLOAD_JSON   : Make'ten gelen JSON. {"text": "..."}  veya  {"scenes": [...], "title": "..."}
  MANUAL_TEXT    : Elle çalıştırmada haber metni (PAYLOAD_JSON yoksa)
  GEMINI_API_KEY, GEMINI_MODEL (boşsa sırayla birkaç flash modeli denenir)
  ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, ELEVENLABS_MODEL (vars. eleven_multilingual_v2)
  EDGE_VOICE     : ElevenLabs yoksa ücretsiz Microsoft sesi (vars. tr-TR-AhmetNeural)
  TTS_PROVIDER   : elevenlabs | edge | dummy   (boşsa: anahtar varsa elevenlabs, yoksa edge)
  PIXABAY_API_KEY
  CHANNEL_NAME   : Sağ üstte görünecek kanal adı (opsiyonel)
  FONTS_DIR      : Yazı tipleri klasörü (vars. fonts)
  OUT_DIR        : Çıktı klasörü (vars. out)

Çıktı: out/video.mp4, out/thumb.jpg, out/result.json
"""
import asyncio
import base64
import json
import os
import random
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

W, H, FPS = 1080, 1920, 30
OUT = Path(os.environ.get("OUT_DIR", "out"))
WORK = OUT / "work"
FONTS = Path(os.environ.get("FONTS_DIR", "fonts"))
ASSETS = Path(os.environ.get("ASSETS_DIR", "assets"))
SCENE_PAD = 0.25  # her sahnenin sonunda kısa nefes payı (saniye)

# Renkler (ASS formatı: &HAABBGGRR)
CYAN = "&H00FFE15C"      # #5CE1FF
CYAN_SOFT = "&H00FFEFA8"  # #A8EFFF
YELLOW = "&H0000D4FF"    # #FFD400
NAVY = "&H00301808"      # #081830
WHITE = "&H00FFFFFF"


def log(*a):
    print("[render]", *a, flush=True)


def run(cmd):
    log(" ".join(str(c) for c in cmd)[:300])
    subprocess.run([str(c) for c in cmd], check=True)


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def http_json(url, data=None, headers=None, timeout=120):
    headers = dict(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers.setdefault("Content-Type", "application/json")
    headers.setdefault("User-Agent", "haber-video/1.0")
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def download(url, dest, timeout=180):
    req = urllib.request.Request(url, headers={"User-Agent": "haber-video/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


LANG = "tr"


def tr_upper(s):
    if LANG in ("tr", "az"):
        return s.replace("i", "İ").replace("ı", "I").upper()
    return s.upper()


# ---------------------------------------------------------------- 1) Senaryo

PROMPT = """Sen kısa dikey video (YouTube Shorts / Reels) editörüsün.
Aşağıdaki haberi 5-7 sahneye böl.
Kurallar:
- DİL: Haber hangi dilde yazıldıysa "text", "headline", "accent", "title", "description" ve "hashtags" alanlarının TAMAMINI o dilde yaz (İngilizce haber -> her şey İngilizce). "lang" alanına dilin 2 harfli kodunu yaz (tr, en, de...).
- Metni yeniden yazma; kullanıcının cümlelerine sadık kal, sadece hafif düzelt ve sadeleştir.
- 1. sahne güçlü bir kanca olsun: merak boşluğu açsın, cevabı sona sakla.
- Sondan bir önceki sahne "Ama asıl mesele şu..." tarzı bir dönüş cümlesi olsun.
- Son sahne kısa bir sonuç ya da düşündüren bir cümle olsun.
- Her sahnenin "text" alanı 1-2 cümle, en fazla 22 kelime.
- "headline": sahnenin özü, 1-3 kısa kelime.
- "accent": başlığa eşlik eden tek kelime (italik gösterilecek), headline'daki kelimeleri tekrar etme.
- "search": Pixabay'de stok VİDEO aramak için 1-3 kelimelik İNGİLİZCE terim. Somut, görsel, aranabilir olsun (ör. "city night", "police car", "stock market screen"). Kişi adı veya marka yazma.
- "style": tek sıradaki sahneler (1,3,5,7) "headline", çift sıradakiler "subtitle".
- "Ama asıl mesele şu..." kalıbını haberin dilinde kullan (ör. İngilizce: "But here's the real issue...").
- "title": YouTube başlığı, en fazla 70 karakter, merak uyandıran.
- "description": 1-2 cümlelik açıklama.
- "hashtags": 3-5 adet, # ile.
Sadece JSON döndür:
{"lang":"tr","title":"...","description":"...","hashtags":["#..."],"scenes":[{"text":"...","headline":"...","accent":"...","search":"...","style":"headline"}]}

Haber:
"""


def strip_fences(s):
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s


def make_script(payload):
    if isinstance(payload.get("scenes"), list) and payload["scenes"]:
        log("Sahneler payload'dan geldi")
        return payload
    text = (payload.get("text") or "").strip()
    if not text:
        sys.exit("Haber metni boş: PAYLOAD_JSON içinde 'text' ya da 'scenes' yok.")
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        log("GEMINI_API_KEY yok: metin cümlelere bölünüyor")
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()][:8]
        scenes = []
        for i, s in enumerate(sents):
            words = re.findall(r"\w+", s)
            longest = sorted(words, key=len, reverse=True)
            scenes.append({"text": s, "headline": " ".join(longest[:2]), "accent": "",
                           "search": "news", "style": "headline" if i % 2 == 0 else "subtitle"})
        return {"title": sents[0][:70], "description": text[:200], "hashtags": [], "scenes": scenes}
    models = [m for m in [os.environ.get("GEMINI_MODEL"), "gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-flash-latest",
                          "gemini-flash-lite-latest"] if m]
    body = {"contents": [{"parts": [{"text": PROMPT + text}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.7}}
    last = None
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(1 if "lite" not in model else 2):
            try:
                r = http_json(url, body, {"x-goog-api-key": key}, timeout=45)
                parts = r["candidates"][0]["content"]["parts"]
                raw = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                data = json.loads(strip_fences(raw))
                if data.get("scenes"):
                    log("Gemini modeli:", model)
                    return data
            except Exception as e:  # noqa
                last = e
                log(f"Gemini ({model}) hatası:", e)
                if "404" in str(e):
                    break
    sys.exit(f"Gemini senaryo üretemedi: {last}")


# ---------------------------------------------------------------- 2) Ses

def words_from_alignment(al):
    chars = al["characters"]
    st = al["character_start_times_seconds"]
    en = al["character_end_times_seconds"]
    words, cur, s0, e0 = [], "", None, None
    for c, a, b in zip(chars, st, en):
        if c.isspace():
            if cur:
                words.append((cur, s0, e0))
            cur, s0 = "", None
            continue
        if not cur:
            s0 = a
        cur += c
        e0 = b
    if cur:
        words.append((cur, s0, e0))
    return words


def tts_elevenlabs(text, mp3, prev_text, next_text):
    key = os.environ["ELEVENLABS_API_KEY"]
    voice = os.environ.get("ELEVENLABS_VOICE_ID") or "21m00Tcm4TlvDq8ikWAM"
    model = os.environ.get("ELEVENLABS_MODEL") or "eleven_multilingual_v2"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps?output_format=mp3_44100_128"
    body = {"text": text, "model_id": model}
    if prev_text:
        body["previous_text"] = prev_text
    if next_text:
        body["next_text"] = next_text
    r = http_json(url, body, {"xi-api-key": key}, timeout=180)
    Path(mp3).write_bytes(base64.b64decode(r["audio_base64"]))
    return words_from_alignment(r.get("alignment") or r["normalized_alignment"])


async def _edge(text, mp3, voice, rate):
    import edge_tts
    try:
        comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    except TypeError:
        comm = edge_tts.Communicate(text, voice, rate=rate)
    words = []
    with open(mp3, "wb") as f:
        async for ch in comm.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                s = ch["offset"] / 1e7
                words.append((ch["text"], s, s + ch["duration"] / 1e7))
    return words


def tts_edge(text, mp3):
    default = {"tr": "tr-TR-AhmetNeural", "en": "en-US-GuyNeural", "de": "de-DE-ConradNeural",
               "fr": "fr-FR-HenriNeural", "es": "es-ES-AlvaroNeural", "ar": "ar-SA-HamedNeural"}
    voice = (os.environ.get("EDGE_VOICE") if LANG == "tr" else None) or default.get(LANG, "tr-TR-AhmetNeural")
    rate = os.environ.get("EDGE_RATE") or "+8%"
    return asyncio.run(_edge(text, mp3, voice, rate))


def tts_dummy(text, mp3):
    words, t = [], 0.1
    for w in text.split():
        d = 0.18 + 0.045 * len(w)
        words.append((w, t, t + d))
        t += d + 0.06
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=180:duration={t + 0.1:.2f}",
         "-af", "volume=0.05", str(mp3)])
    return words


def align_to_text(spoken, text):
    """Zamanlamadaki kelimeleri, metindeki noktalı kelimelerle eşle (altyazıda noktalama kalsın)."""
    toks = text.split()
    if len(toks) == len(spoken):
        return [(tok, s, e) for tok, (_, s, e) in zip(toks, spoken)]
    return spoken


def make_audio(scenes):
    provider = os.environ.get("TTS_PROVIDER") or ("elevenlabs" if os.environ.get("ELEVENLABS_API_KEY") else "edge")
    log("Ses sağlayıcı:", provider)
    t = 0.0
    wavs = []
    for i, sc in enumerate(scenes):
        mp3 = WORK / f"voice_{i}.mp3"
        text = sc["text"].strip()
        if provider == "elevenlabs":
            prev_t = scenes[i - 1]["text"] if i else None
            next_t = scenes[i + 1]["text"] if i + 1 < len(scenes) else None
            words = tts_elevenlabs(text, mp3, prev_t, next_t)
        elif provider == "edge":
            words = tts_edge(text, mp3)
        else:
            words = tts_dummy(text, mp3)
        words = align_to_text(words, text)
        dur = probe_duration(mp3) + SCENE_PAD
        wav = WORK / f"voice_{i}.wav"
        run(["ffmpeg", "-y", "-v", "error", "-i", mp3, "-af", f"apad=whole_dur={dur:.3f}",
             "-t", f"{dur:.3f}", "-ar", "44100", "-ac", "2", wav])
        sc["start"], sc["dur"] = t, dur
        sc["words"] = [(w, t + s, t + e) for w, s, e in words]
        wavs.append(wav)
        t += dur
    lst = WORK / "audio.txt"
    lst.write_text("".join(f"file '{w.resolve()}'\n" for w in wavs))
    voice = WORK / "voice.wav"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", voice])
    return voice, t


# ---------------------------------------------------------------- 3) Görüntü

USED_IDS = set()


def pixabay_pick(query, need):
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return None
    for q in [query, " ".join(query.split()[:1])]:
        if not q:
            continue
        url = ("https://pixabay.com/api/videos/?" +
               urllib.parse.urlencode({"key": key, "q": q, "per_page": 30, "safesearch": "true"}))
        try:
            hits = http_json(url).get("hits", [])
        except Exception as e:  # noqa
            log("Pixabay hatası:", e)
            return None
        cands = []
        qwords = set(re.findall(r"[a-z]+", q.lower()))
        for rank, h in enumerate(hits):
            if h["id"] in USED_IDS:
                continue
            for size in ("large", "medium", "small"):
                v = h.get("videos", {}).get(size) or {}
                if v.get("url") and v.get("width"):
                    portrait = v["height"] > v["width"]
                    tags = set(re.findall(r"[a-z]+", (h.get("tags") or "").lower()))
                    ai = bool(tags & {"ai", "generated", "artificial", "fantasy", "lion", "animal", "anime", "cartoon", "animation", "3d", "render", "illustration", "game", "cyberpunk"} - qwords)
                    score = (3 * len(qwords & tags) / max(1, len(qwords)) + (1.5 if portrait else 0)
                             + (0.5 if h.get("duration", 0) >= need else 0) - (3 if ai else 0)
                             + min(v["height"], 1920) / 1920 - rank * 0.05)
                    cands.append((score, random.random(), h["id"], v["url"]))
                    break
        if cands:
            cands.sort(reverse=True)
            top = cands[:3]
            _, _, vid, url = random.choice(top)
            USED_IDS.add(vid)
            return url
    return None


PALETTES = [("0x07122b", "0x1b4fd1"), ("0x12051f", "0x7b2ff7"), ("0x02161a", "0x0aa5a8"),
            ("0x1a0606", "0xd13b1b"), ("0x050505", "0x2a2a2a")]


def scene_clip(i, sc):
    dur = sc["dur"]
    out = WORK / f"scene_{i}.mp4"
    src = None
    url = pixabay_pick(sc.get("search") or "abstract", dur)
    if url:
        src = WORK / f"src_{i}.mp4"
        try:
            download(url, src)
        except Exception as e:  # noqa
            log("İndirme hatası:", e)
            src = None
    # Hafif kaydırma (Ken Burns): görüntüyü %10 büyüt, sahne boyunca yavaşça kaydır
    sw, sh = int(W * 1.1) // 2 * 2, int(H * 1.1) // 2 * 2
    dx, dy = sw - W, sh - H
    direction = i % 4
    xs = {0: f"{dx}*t/{dur:.3f}", 1: f"{dx}-{dx}*t/{dur:.3f}", 2: f"{dx}/2", 3: f"{dx}/2"}[direction]
    ys = {0: f"{dy}/2", 1: f"{dy}/2", 2: f"{dy}*t/{dur:.3f}", 3: f"{dy}-{dy}*t/{dur:.3f}"}[direction]
    vf = (f"scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
          f"crop={W}:{H}:x='{xs}':y='{ys}',setsar=1,fps={FPS},"
          f"eq=brightness=-0.07:saturation=1.12,vignette=PI/5,format=yuv420p")
    if src:
        inp = ["-stream_loop", "-1", "-i", src]
    else:
        c0, c1 = PALETTES[i % len(PALETTES)]
        inp = ["-f", "lavfi", "-i", f"gradients=s={W}x{H}:c0={c0}:c1={c1}:x0=0:y0=0:x1={W}:y1={H}:speed=0.015:r={FPS}"]
    run(["ffmpeg", "-y", "-v", "error", *inp, "-t", f"{dur:.3f}", "-an", "-vf", vf,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-r", str(FPS), out])
    return out


# ---------------------------------------------------------------- 4) Yazılar (ASS)

def ass_time(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    cs = int(round((s - int(s)) * 100))
    if cs == 100:
        s, cs = int(s) + 1, 0
    return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"


def esc(s):
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


ASS_HEAD = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Head,Anton,150,{CYAN},{CYAN},&H00000000,&H96000000,0,0,0,0,100,100,1,0,1,0,6,5,40,40,0,1
Style: Accent,Playfair Display,140,{CYAN_SOFT},{CYAN_SOFT},&H00000000,&H96000000,0,1,0,0,100,100,0,0,1,0,5,5,40,40,0,1
Style: Word,Anton,96,{YELLOW},{YELLOW},&H00000000,&HA0000000,0,1,0,0,100,100,2,0,1,3,4,5,40,40,0,1
Style: Box,Montserrat,60,{NAVY},{NAVY},{CYAN},{CYAN},1,0,0,0,100,100,0,0,3,16,0,5,40,40,0,1
Style: Brand,Montserrat,38,{WHITE},{WHITE},&H00000000,&H80000000,1,0,0,0,100,100,1,0,1,0,3,9,48,48,60,1
"""


def wrap_lines(words, max_chars):
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


def headline_events(sc, ev):
    s0, s1 = sc["start"], sc["start"] + sc["dur"]
    words = tr_upper(sc.get("headline") or "").split()
    if not words:
        return
    lines = wrap_lines(words, 10)[:3]
    longest = max(len(l) for l in lines)
    size = int(min(210, 960 / max(1, 0.44 * longest)))
    line_h = size * 0.98
    accent = (sc.get("accent") or "").strip().split(" ")[0]
    block_h = line_h * len(lines) + (size * 0.9 if accent else 0)
    y = H * 0.40 - block_h / 2 + line_h / 2
    t = s0 + 0.08
    for ln in lines:
        tag = (f"{{\\pos({W // 2},{int(y)})\\fs{size}\\fad(100,120)"
               f"\\fscx118\\fscy118\\t(0,200,\\fscx100\\fscy100)}}")
        ev.append(("Head", t, s1, tag + esc(ln)))
        y += line_h
        t += 0.28
    if accent:
        a = accent[:1].upper() + accent[1:]
        asize = int(size * 0.85)
        tag = (f"{{\\pos({W // 2},{int(y + asize * 0.05)})\\fs{asize}\\fad(150,120)"
               f"\\frz-4\\t(0,250,\\frz0)}}")
        ev.append(("Accent", t + 0.1, s1, tag + esc(a)))


def word_events(sc, ev):
    """Headline sahnelerinde: konuşulan kelime, sarı italik, tek tek."""
    ws = sc["words"]
    end_scene = sc["start"] + sc["dur"]
    for k, (w, s, e) in enumerate(ws):
        nxt = ws[k + 1][1] if k + 1 < len(ws) else min(e + 0.3, end_scene)
        w = re.sub(r"[^\w'’%-]", "", tr_upper(w))
        if not w:
            continue
        tag = f"{{\\pos({W // 2},{int(H * 0.70)})\\fscx112\\fscy112\\t(0,120,\\fscx100\\fscy100)}}"
        ev.append(("Word", s, max(nxt, s + 0.12), tag + esc(w)))


def box_events(sc, ev):
    """Subtitle sahnelerinde: kutulu altyazı, kelimeler konuştukça beliriyor."""
    ws = sc["words"]
    end_scene = sc["start"] + sc["dur"]
    chunks, cur = [], []
    for item in ws:
        cur.append(item)
        if len(" ".join(x[0] for x in cur)) > 44 or len(cur) >= 8:
            chunks.append(cur)
            cur = []
    if cur:
        if chunks and len(cur) <= 2:
            chunks[-1].extend(cur)
        else:
            chunks.append(cur)
    for ci, ch in enumerate(chunks):
        chunk_end = chunks[ci + 1][0][1] if ci + 1 < len(chunks) else end_scene
        texts = [x[0] for x in ch]
        half = (len(texts) + 1) // 2
        for k, (w, s, e) in enumerate(ch):
            nxt = ch[k + 1][1] if k + 1 < len(ch) else chunk_end
            shown = texts[:k + 1]
            l1, l2 = shown[:half], shown[half:]
            body = esc(" ".join(l1)) + ("\\N" + esc(" ".join(l2)) if l2 else "")
            tag = f"{{\\pos({W // 2},{int(H * 0.66)})}}"
            ev.append(("Box", s, max(nxt, s + 0.05), tag + body))


def make_ass(scenes, total):
    ev = []
    for i, sc in enumerate(scenes):
        if (sc.get("style") or ("headline" if i % 2 == 0 else "subtitle")) == "headline":
            headline_events(sc, ev)
            word_events(sc, ev)
        else:
            box_events(sc, ev)
    brand = os.environ.get("CHANNEL_NAME", "").strip()
    if brand:
        ev.append(("Brand", 0, total, esc(brand)))
    lines = [ASS_HEAD, "[Events]",
             "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for style, a, b, txt in ev:
        lines.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},{style},,0,0,0,,{txt}")
    p = WORK / "subs.ass"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------- 5) Birleştir

def find_music():
    if ASSETS.is_dir():
        ms = sorted(ASSETS.glob("*.mp3"))
        if ms:
            return random.choice(ms)
    return None


def compose(clips, voice, ass, total):
    lst = WORK / "clips.txt"
    lst.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    joined = WORK / "joined.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", joined])
    music = find_music()
    out = OUT / "video.mp4"
    inputs = ["-i", joined, "-i", voice,
              "-f", "lavfi", "-i", f"color=c=0x5CE1FF:s={W}x12:r={FPS}:d={total:.3f}"]
    fc = (f"[0:v]subtitles=filename='{ass}':fontsdir='{FONTS}'[v1];"
          f"[v1][2:v]overlay=x='-w+w*t/{total:.3f}':y=0:shortest=1[v]")
    if music:
        log("Müzik:", music.name)
        inputs += ["-stream_loop", "-1", "-i", music]
        fc += (f";[3:a]volume=0.10,afade=t=out:st={max(0, total - 1.5):.3f}:d=1.5[m];"
               f"[1:a][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]")
        amap = "[a]"
    else:
        amap = "1:a"
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", fc, "-map", "[v]", "-map", amap,
         "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", out])
    thumb = OUT / "thumb.jpg"
    run(["ffmpeg", "-y", "-v", "error", "-ss", "1.2", "-i", out, "-frames:v", "1", "-q:v", "3", thumb])
    return out


# ---------------------------------------------------------------- main

def load_payload():
    raw = (os.environ.get("PAYLOAD_JSON") or "").strip()
    if raw and raw not in ("null", "{}"):
        p = json.loads(raw)
        # Make bazen tüm JSON'u tek bir alan içinde metin olarak gönderebilir
        for k in ("data", "payload", "json"):
            if isinstance(p.get(k), str) and p[k].strip().startswith("{"):
                p = json.loads(strip_fences(p[k]))
        return p
    manual = (os.environ.get("MANUAL_TEXT") or "").strip()
    if manual:
        return {"text": manual}
    sys.exit("Girdi yok: PAYLOAD_JSON veya MANUAL_TEXT gerekli.")


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    payload = load_payload()
    script = make_script(payload)
    global LANG
    LANG = (script.get("lang") or payload.get("lang") or "tr").lower()[:2]
    log("Dil:", LANG)
    scenes = [s for s in script["scenes"] if (s.get("text") or "").strip()][:9]
    log(f"{len(scenes)} sahne")
    voice, total = make_audio(scenes)
    clips = [scene_clip(i, sc) for i, sc in enumerate(scenes)]
    ass = make_ass(scenes, total)
    video = compose(clips, voice, ass, total)

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    tag = os.environ.get("RELEASE_TAG", "")
    tags = " ".join(script.get("hashtags") or [])
    result = {
        "title": (script.get("title") or scenes[0]["text"])[:95],
        "description": ((script.get("description") or "") + "\n\n" + tags).strip(),
        "hashtags": script.get("hashtags") or [],
        "duration": round(total, 2),
        "video_url": f"https://github.com/{repo}/releases/download/{tag}/video.mp4" if repo and tag else "",
        "thumb_url": f"https://github.com/{repo}/releases/download/{tag}/thumb.jpg" if repo and tag else "",
        "scenes": [{k: s.get(k) for k in ("text", "headline", "accent", "search", "style")} for s in scenes],
        "chat_id": payload.get("chat_id"),
    }
    (OUT / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "notes.md").write_text(result["description"] or result["title"], encoding="utf-8")
    log("Bitti:", video, f"{total:.1f} sn")


if __name__ == "__main__":
    main()
