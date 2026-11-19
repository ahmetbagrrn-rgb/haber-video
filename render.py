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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

W, H, FPS = 1080, 1920, 30
LANDSCAPE = False
try:
    CONFIG = json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))
except Exception:  # noqa
    CONFIG = {}
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
    import time
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "haber-video/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
                shutil.copyfileobj(r, f)
            return
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))


LANG = "tr"


def tr_upper(s):
    if LANG in ("tr", "az"):
        return s.replace("i", "İ").replace("ı", "I").upper()
    return s.upper()


# ---------------------------------------------------------------- 1) Senaryo

PROMPT = """Sen dünyanın en iyi kısa dikey haber videosu (YouTube Shorts / Reels / TikTok) editörüsün. Hedef: 30-40 saniyelik, izleyicinin sonuna kadar izleyip tekrar izlediği bir video.
Aşağıdaki haberi 5-6 sahneye böl.
Kurallar:
- DİL: Haber hangi dilde yazıldıysa "text", "headline", "accent", "title", "description" ve "hashtags" alanlarının TAMAMINI o dilde yaz (İngilizce haber -> her şey İngilizce). "lang" alanına dilin 2 harfli kodunu yaz (tr, en, de...).
- Metni yeniden yazma; kullanıcının cümlelerine sadık kal, sadece hafif düzelt ve sadeleştir.
- TEK OLAY: Videoda yalnızca bir olay anlat. Başka gelişmelerden bahsetme.
- 1. sahne (ilk 2 saniye) KANCA: Soru DEĞİL; haberin en çarpıcı somut olgusu (kim + ne + rakam/yer). Örnek: "74 ülkede bankalar aynı anda durdu." Selamlama, giriş, "bugün size..." YOK.
- 2-3. sahne: ne oldu, kim yaptı, ne zaman, nerede; somut ayrıntı ve rakamlarla.
- 4. sahne: neden önemli; arkasındaki neden ya da büyük resim (analiz). Her videoda farklı bir geçiş cümlesi kullan; kalıp ifade TEKRARLAMA.
- 5. sahne: izleyiciye etkisi: "{AUDIENCE}" izleyicisini nasıl etkiler (fiyatlar, enerji, güvenlik, seyahat, işler vb.). Kaynakta dayanak yoksa genel ama doğru bir etki yaz, sayı uydurma.
- Son sahne: kısa, merak bırakan YENİ bir cümle (ilk sahneyi tekrar ETME); anlamca ilk sahneye bağlansın ki video döngüye girince akış kopmasın.
- Her sahnenin "text" alanı tek cümle, 8-16 kelime. Kısa, aktif, günlük dil; uzun yan cümle yok.
- "headline": sahnenin özü, 1-3 kısa kelime (rakam varsa rakamı kullan).
- "accent": başlığa eşlik eden tek kelime (italik gösterilecek), headline'daki kelimeleri tekrar etme.
- "search": Pixabay'de stok VİDEO aramak için 1-3 kelimelik İNGİLİZCE terim. Somut, görsel, aranabilir olsun (ör. "city night", "police car", "stock market screen"). Kişi adı veya marka yazma.
- "style": tek sıradaki sahneler (1,3,5) "headline", çift sıradakiler "subtitle".
- "countries": sahnede adı geçen ülkelerin ISO-3166 alpha-3 kodları (ör. ["TUR","IRN"]); yoksa [].
- "country_names": aynı ülkelerin çıktı dilindeki kısa adları (ör. ["Türkiye","İran"]).
- "title": YouTube başlığı, en fazla 65 karakter. İlk 40 karakterde aranan anahtar kelime (ülke/kişi/kurum adı) olsun. Kalıp: İsim + olgu/rakam + sonuç ya da gerçek bir soru. "Küresel kriz", "dünya değişiyor" gibi genel başlık YASAK. Yanıltıcı olmasın.
- "description": 2 satır: 1. satır başlıktaki anahtar kelimeyle başlayan tek cümlelik özet; 2. satır izleyiciye yorum yaptıracak tek bir soru.
- "tags": 8-12 SEO anahtar kelimesi (çıktı dilinde, insanların gerçekten arayacağı ifadeler; özel isimleri içersin).
- "hashtags": 3-5 adet, # ile; biri konuya özel olsun.
Sadece JSON döndür:
{"lang":"tr","title":"...","description":"...","hashtags":["#..."],"scenes":[{"text":"...","headline":"...","accent":"...","search":"...","style":"headline","countries":[],"country_names":[]}],"tags":["..."]}

Haber:
"""


PROMPT_LONG = """Sen YouTube için uzun (yatay, 5-8 dakikalık) haber-analiz videosu editörüsün.
Kaynaklardan bu haftanın en önemli gelişmelerini derleyip TEK bir akıcı analiz videosu senaryosu yaz.
Kurallar:
- 16-24 sahne. Her sahnenin "text" alanı 2-3 cümle, en fazla 45 kelime.
- Video 4-5 bölümden oluşsun. Her bölümün İLK sahnesine "chapter" alanında kısa bölüm başlığı yaz (diğer sahnelerde "chapter" boş).
- 1. sahne güçlü bir kanca: izleyiciye bu videoda neyi öğreneceğini merak ettirecek şekilde söyle.
- Son bölüm: "Bundan sonra ne olabilir?" — olası senaryolar ve kendi analizin. Son cümle izleyiciye yorumlarda fikrini soran bir soru olsun.
- "headline", "accent", "search", "style", "countries", "country_names" alanları kısa videodaki gibi.
- "search": yatay stok video için 1-3 kelimelik İNGİLİZCE terim.
- "title": YouTube başlığı (en fazla 70 karakter), merak uyandıran ama yanıltıcı olmayan.
- "thumb_texts": küçük resim için 3 FARKLI kısa yazı seçeneği (her biri en fazla 4 kelime, BÜYÜK etki).
- "description": 2-3 cümlelik açıklama. "tags": 10-15 SEO anahtar kelimesi. "hashtags": 3-5 adet.
Sadece JSON döndür:
{"lang":"tr","topic":"...","title":"...","thumb_texts":["...","...","..."],"description":"...","hashtags":["#..."],"tags":["..."],"scenes":[{"chapter":"...","text":"...","headline":"...","accent":"...","search":"...","style":"headline","countries":[],"country_names":[]}]}

Kaynaklar:
"""

AUDIENCE = {"tr": "Türkiye'deki", "en": "ABD'deki", "de": "Almanya, Avusturya ve İsviçre'deki", "fr": "Fransa'daki", "es": "İspanyolca konuşan"}

LANG_NAMES = {"tr": "Türkçe", "en": "İngilizce (ABD)", "de": "Almanca", "fr": "Fransızca", "es": "İspanyolca"}

EDITOR_RULES = """
EDİTÖR KURALLARI (kaynak bir YouTube videosu ya da başka bir haber):
- Profesyonel bir haber editörüsün. Konuşma dilini, selamlaşmaları, "abone olun" gibi çağrıları, reklam/sponsor kısımlarını, kanal tanıtımlarını ve kişisel anekdotları tamamen at.
- Kaynaktaki cümleleri KOPYALAMA, alıntı yapma. Sadece olguları ve konuyu al; tamamen kendi cümlelerinle, kendi yapınla yeniden yaz.
- Görüş ve iddiaları sahibine atfet ("... göre", "... iddia ediyor"). Siyasi taraf tutma, tarafsız ve olgusal kal.
- Kaynakta olmayan bilgi, sayı ya da isim uydurma. Yer adlarının türünü (şehir, boğaz, nehir, ülke) doğru yaz; emin değilsen türünü belirtme.
- Kişilerin unvanlarını kaynakta geçtiği gibi kullan; kendi bilgine dayanarak "eski", "mevcut" gibi unvan ekleme (bilgin güncel olmayabilir).
- TEK OLAY ZORUNLU (en önemli kural): Kaynak birden çok konu içeriyorsa, ÖNCE tek bir olay seç, sonra yalnızca onu yaz; en somut ayrıntısı (isim, rakam, yer) ve en geniş etkisi olan TEK olayı seç; diğerlerini tamamen at. "topic" alanı bu tek olayı anlatsın.
- KANAL NİŞİ: jeopolitik, savaş ve güvenlik, uluslararası ilişkiler, küresel ekonomi, enerji, teknoloji ve siber güvenlik. Kaynaktaki tüm konular bunların DIŞINDAYSA (magazin, kraliyet ailesi, ünlüler, spor, moda, yaşam tarzı) sadece {"skip": true} döndür.
- Kaynak haber niteliği taşımıyorsa (sadece soru-cevap, reklam, kişisel vlog vb.) sadece {"skip": true} döndür.
"""


def build_prompt(payload):
    target = (payload.get("target_lang") or "").lower()[:2]
    p = PROMPT_LONG if payload.get("long") else PROMPT
    if payload.get("mode") == "rewrite" or payload.get("youtube_url"):
        p = p.replace("- Metni yeniden yazma; kullanıcının cümlelerine sadık kal, sadece hafif düzelt ve sadeleştir.\n", "")
        p = EDITOR_RULES + "\n" + p
    if target:
        name = LANG_NAMES.get(target, target)
        p = (f"ÇIKTI DİLİ: Kaynak hangi dilde olursa olsun TÜM alanları {name} yaz ve \"lang\" alanına \"{target}\" koy. "
             f"Bu dili konuşan izleyici için doğal, yerel bir anlatım kullan.\n") + p
    aud = AUDIENCE.get(target or "tr", "hedef")
    p = p.replace("{AUDIENCE}", aud)
    if target and target != "tr":
        p += ("\nNOT: Bu bir çeviri değil; aynı olayı " + aud + " izleyici için baştan yaz. Onların bildiği örnekleri, "
              "para birimini ve bakış açısını kullan.\n")
    p = p.replace('{"lang":"tr",', '{"lang":"tr","topic":"(konunun 5-8 kelimelik İngilizce özeti)",')
    if payload.get("youtube_url"):
        p = p.replace("\nHaber:\n", "\nKaynak: ekteki YouTube videosu (ilk 15 dakikası).\n")
        p = p.replace("\nKaynaklar:\n", "\nKaynaklar: ekteki YouTube videosu (ilk 15 dakikası) ve aşağıdaki notlar.\n")
    return p


def strip_fences(s):
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s


VERIFY_PROMPT = """Sen bir haber doğrulama editörüsün. Aşağıda bir kısa video senaryosu (JSON) ve varsa kaynak metin var.
Görevin SADECE hataları düzeltmek:
- Yer adlarının türü yanlışsa düzelt (ör. nehri boğaz diye yazmak), ülke/şehir eşleşmesi yanlışsa düzelt.
- Kaynakta dayanağı olmayan kesin rakam, tarih veya alıntı varsa kaldır ya da genel ifadeye çevir.
- Bir kişiye kaynakta olmayan unvan eklenmişse kaldır.
- Bir iddia kesin olgu gibi yazılmışsa "... göre" ile sahibine atfet.
Üslubu, sahne sayısını, alanları ve dili DEĞİŞTİRME. Hata yoksa senaryoyu aynen döndür.
Sadece düzeltilmiş JSON'u döndür, ayrıca "_fixes" alanına yaptığın düzeltmeleri kısa liste olarak yaz (yoksa []).
"""


def verify_script(data, payload, key):
    if os.environ.get("NO_VERIFY") or not data.get("scenes"):
        return data
    src = (payload.get("text") or "")[:6000]
    body = {"contents": [{"parts": [{"text": VERIFY_PROMPT + "\nSENARYO:\n" + json.dumps(data, ensure_ascii=False)
                                     + ("\n\nKAYNAK METİN:\n" + src if src else "\n\n(Kaynak bir video; metni sende yok. Rakam ve ayrıntıları SİLME, genelleştirme; yalnızca coğrafya, unvan ve atıf gibi bariz hataları düzelt.)")}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}}
    for model in ["gemini-3.5-flash", "gemini-flash-latest", "gemini-3.5-flash-lite"]:
        try:
            r = http_json(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                          body, {"x-goog-api-key": key}, timeout=60)
            raw = "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"] if not p.get("thought"))
            fixed = json.loads(strip_fences(raw))
            if (isinstance(fixed.get("scenes"), list) and len(fixed["scenes"]) == len(data["scenes"])):
                fx = fixed.pop("_fixes", []) or []
                log("Doğrulama:", "; ".join(map(str, fx)) if fx else "düzeltme yok")
                for k in ("topic", "lang"):
                    if data.get(k) and not fixed.get(k):
                        fixed[k] = data[k]
                return fixed
        except Exception as e:  # noqa
            log(f"Doğrulama ({model}) hatası:", e)
            if "404" not in str(e):
                continue
    return data


def make_script(payload):
    if isinstance(payload.get("scenes"), list) and payload["scenes"]:
        log("Sahneler payload'dan geldi")
        return payload
    text = (payload.get("text") or "").strip()
    yt = payload.get("youtube_url")
    if not text and not yt:
        sys.exit("Haber metni boş: PAYLOAD_JSON içinde 'text' ya da 'scenes' yok.")
    key = os.environ.get("GEMINI_API_KEY")
    if not key and yt:
        sys.exit("YouTube kaynağı için GEMINI_API_KEY gerekli")
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
    order = (["gemini-3.5-flash", "gemini-flash-latest", "gemini-3.5-flash-lite"] if (yt or payload.get("mode") == "rewrite")
             else ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-flash-latest"])
    models = [m for m in [os.environ.get("GEMINI_MODEL")] + order + ["gemini-flash-lite-latest"] if m]
    prompt = build_prompt(payload)
    if yt:
        parts = [{"fileData": {"fileUri": yt}, "videoMetadata": {"startOffset": "0s", "endOffset": "900s"}},
                 {"text": prompt + (text or "")}]
        gen = {"responseMimeType": "application/json", "temperature": 0.7, "mediaResolution": "MEDIA_RESOLUTION_LOW"}
    else:
        parts = [{"text": prompt + text}]
        gen = {"responseMimeType": "application/json", "temperature": 0.7}
    body = {"contents": [{"parts": parts}], "generationConfig": gen}
    last = None
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(2 if ("lite" in model or yt) else 1):
            try:
                r = http_json(url, body, {"x-goog-api-key": key}, timeout=240 if yt else 45)
                parts = r["candidates"][0]["content"]["parts"]
                raw = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                data = json.loads(strip_fences(raw))
                if data.get("skip"):
                    log("Gemini: kaynak haber niteliği taşımıyor, atlandı")
                    return {"skip": True}
                if data.get("scenes"):
                    log("Gemini modeli:", model)
                    return verify_script(data, payload, key)
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


EDGE_VOICES = {"tr": ["tr-TR-AhmetNeural", "tr-TR-EmelNeural"],
               "en": ["en-US-GuyNeural", "en-US-ChristopherNeural", "en-US-EricNeural"],
               "de": ["de-DE-ConradNeural", "de-DE-KillianNeural", "de-DE-KatjaNeural"],
               "fr": ["fr-FR-HenriNeural"], "es": ["es-ES-AlvaroNeural"], "ar": ["ar-SA-HamedNeural"]}


def tts_edge(text, mp3):
    rate = os.environ.get("EDGE_RATE") or "+8%"
    voices = EDGE_VOICES.get(LANG, EDGE_VOICES["tr"])
    if LANG == "tr" and os.environ.get("EDGE_VOICE"):
        voices = [os.environ["EDGE_VOICE"]] + voices
    last = None
    for v in voices:
        for attempt in range(2):
            try:
                words = asyncio.run(_edge(text, mp3, v, rate))
                if Path(mp3).exists() and Path(mp3).stat().st_size > 1000:
                    return words
            except Exception as e:  # noqa
                last = e
                log(f"Edge ses ({v}) hatası: {e}")
    raise RuntimeError(f"Ücretsiz ses üretilemedi: {last}")


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


TTS_USED = ""


def choose_provider(scenes):
    forced = os.environ.get("TTS_PROVIDER")
    if forced:
        return forced
    edge_langs = [x.strip() for x in (os.environ.get("EDGE_LANGS") or "tr").split(",") if x.strip()]
    if not CONFIG.get("elevenlabs_enabled", True):
        return "edge"
    if LANG in edge_langs or not os.environ.get("ELEVENLABS_API_KEY"):
        return "edge"
    q = elevenlabs_quota()
    need = sum(len(sc["text"]) for sc in scenes) + 200
    if q and q["left"] < need:
        log(f"ElevenLabs kotası yetersiz ({q['left']} karakter): ücretsiz sese geçiliyor")
        return "edge"
    return "elevenlabs"


def make_audio(scenes):
    provider = choose_provider(scenes)
    try:
        return _make_audio(scenes, provider)
    except Exception as e:  # noqa
        if provider == "edge":
            raise
        log(f"{provider} hata verdi ({e}); ücretsiz Microsoft sesine geçiliyor")
        return _make_audio(scenes, "edge")


def _make_audio(scenes, provider):
    global TTS_USED
    TTS_USED = provider
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
                    portrait = (v["height"] > v["width"]) != LANDSCAPE
                    tags = set(re.findall(r"[a-z]+", (h.get("tags") or "").lower()))
                    ai = bool(tags & {"ai", "generated", "artificial", "fantasy", "lion", "animal", "anime", "cartoon", "animation", "3d", "render", "illustration", "game", "cyberpunk",
                                     "green", "screen", "chroma", "greenscreen", "character", "avatar"} - qwords)
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


USED_LOCAL = set()
MAPS_USED = {"n": 0, "last": -9, "keys": set()}


def local_pick(query, any_ok=False):
    """assets/broll içindeki hazır tema klipleri (dosya adındaki kelimelerle eşleşir)."""
    d = ASSETS / "broll"
    if not d.is_dir() or (LANDSCAPE and not any_ok):
        return None
    q = set(re.findall(r"[a-z]+", (query or "").lower()))
    best = []
    for f in sorted(d.glob("*.mp4")):
        if f in USED_LOCAL:
            continue
        score = len(q & set(f.stem.lower().split("-")))
        best.append((score, random.random(), f))
    if not best:
        return None
    best.sort(reverse=True)
    score, _, f = best[0]
    if score >= 1 or any_ok:
        USED_LOCAL.add(f)
        return f
    return None


PALETTES = [("0x07122b", "0x1b4fd1"), ("0x12051f", "0x7b2ff7"), ("0x02161a", "0x0aa5a8"),
            ("0x1a0606", "0xd13b1b"), ("0x050505", "0x2a2a2a")]


def scene_clip(i, sc):
    dur = sc["dur"]
    out = WORK / f"scene_{i}.mp4"
    src = local_pick(sc.get("search"))
    url = None if src else pixabay_pick(sc.get("search") or "abstract", dur)
    if url:
        src = WORK / f"src_{i}.mp4"
        try:
            download(url, src)
        except Exception as e:  # noqa
            log("İndirme hatası:", e)
            src = None
    if not src:
        src = local_pick(sc.get("search"), any_ok=True)
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
    mp = map_card(i, sc)
    if mp:
        # Harita kartı: sahnenin 0.3. saniyesinde yumuşakça belirir
        my = int(H * (0.10 if LANDSCAPE else 0.16))
        fc = (f"[0:v]{vf}[b];[1:v]format=rgba,fade=in:st=0.3:d=0.4:alpha=1[m];"
              f"[b][m]overlay=x=(W-w)/2:y={my}:shortest=1,format=yuv420p[v]")
        run(["ffmpeg", "-y", "-v", "error", *inp, "-loop", "1", "-i", mp, "-t", f"{dur:.3f}", "-an",
             "-filter_complex", fc, "-map", "[v]",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-r", str(FPS), out])
        return out
    run(["ffmpeg", "-y", "-v", "error", *inp, "-t", f"{dur:.3f}", "-an", "-vf", vf,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-r", str(FPS), out])
    return out


def map_card(i, sc):
    """Altyazı sahnelerinde, ülke adı geçiyorsa harita kartı üret."""
    global MAPS_USED
    isos = [c for c in (sc.get("countries") or []) if isinstance(c, str) and len(c) in (2, 3)][:4]
    style = sc.get("style") or ("headline" if i % 2 == 0 else "subtitle")
    if not isos or i == 0 or (style != "subtitle" and not LANDSCAPE):
        return None
    # Az ama etkili: en fazla 2 (kısa) / 5 (uzun) harita, art arda değil, aynı ülkeler tekrar değil
    key = ",".join(sorted(isos))
    if (MAPS_USED["n"] >= (5 if LANDSCAPE else 2) or MAPS_USED["last"] == i - 1 or key in MAPS_USED["keys"]):
        return None
    MAPS_USED["n"] += 1
    MAPS_USED["last"] = i
    MAPS_USED["keys"].add(key)
    sc["_map"] = True
    try:
        import mapcard
        labels = [tr_upper(x) for x in (sc.get("country_names") or isos)][:len(isos)]
        w = int(W * (0.42 if LANDSCAPE else 0.86))
        h = int(w * 0.58)
        return mapcard.render_map(isos, labels, WORK / f"map_{i}.png", width=w, height=h,
                                  font=FONTS / "Montserrat-Bold.ttf")
    except Exception as e:  # noqa
        log("Harita üretilemedi:", e)
        return None


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


def ass_head():
    k = 0.72 if LANDSCAPE else 1.0
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Head,Anton,{int(150*k)},{CYAN},{CYAN},&H00000000,&H96000000,0,0,0,0,100,100,1,0,1,0,6,5,40,40,0,1
Style: Accent,Playfair Display,{int(140*k)},{CYAN_SOFT},{CYAN_SOFT},&H00000000,&H96000000,0,1,0,0,100,100,0,0,1,0,5,5,40,40,0,1
Style: Word,Anton,{int(96*k)},{YELLOW},{YELLOW},&H00000000,&HA0000000,0,1,0,0,100,100,2,0,1,3,4,5,40,40,0,1
Style: Box,Montserrat,{int(58*k)},{WHITE},{WHITE},&H40000000,&H40000000,1,0,0,0,100,100,0,0,3,18,0,5,40,40,0,1
Style: Hook,Anton,{int(140*k)},{WHITE},{WHITE},&H00000000,&H96000000,0,0,0,0,100,100,1,0,1,4,7,5,40,40,0,1
Style: Strip,Anton,{int(74*k)},{WHITE},{WHITE},&H002D1AE3,&H002D1AE3,0,0,0,0,100,100,4,0,3,16,0,5,40,40,0,1
Style: Follow,Anton,{int(92*k)},{WHITE},{WHITE},&H002D1AE3,&H002D1AE3,0,0,0,0,100,100,3,0,3,22,0,5,40,40,0,1
Style: Brand,Montserrat,{int(38*k)},{WHITE},{WHITE},&H00000000,&H80000000,1,0,0,0,100,100,1,0,1,0,3,9,48,48,60,1
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
    lines = wrap_lines(words, 20 if LANDSCAPE else 10)[:3]
    longest = max(len(l) for l in lines)
    size = int(min(210 * (0.75 if LANDSCAPE else 1), W * 0.88 / max(1, 0.44 * longest)))
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


DEFAULT_CHANNELS = {
    "en": {"name": "WORLD SHIFT", "slug": "world-shift", "follow": "FOLLOW FOR MORE"},
    "de": {"name": "WORLD SHIFT DEUTSCH", "slug": "world-shift-de", "follow": "JETZT FOLGEN"},
    "tr": {"name": "WORLD SHIFT", "slug": "global-news", "follow": "TAKİP ET"},
}


def channel():
    """Haberin diline göre kanal (config.json -> channels)."""
    chs = {**DEFAULT_CHANNELS, **(CONFIG.get("channels") or {})}
    c = dict(chs.get(LANG) or chs["tr"])
    slug = c.get("slug", "global-news")
    c["logo"] = ASSETS / "logos" / f"{slug}.png"
    c["outro"] = ASSETS / "outro" / f"{slug}.mp4"
    return c


def clean_title(t):
    t = re.sub(r"[^\w\s'’?!.,:%&-]", "", t or "")
    return re.sub(r"\s+", " ", t).strip()


def hook_events(title, sc, ev):
    """1. sahne: çarpıcı başlık kartı + kırmızı kanal şeridi."""
    s0, s1 = sc["start"], sc["start"] + sc["dur"]
    words = tr_upper(clean_title(title)).split()
    if not words:
        return headline_events(sc, ev)
    lines = wrap_lines(words, 26 if LANDSCAPE else 13)[:5]
    longest = max(len(l) for l in lines)
    size = int(min(150 * (0.8 if LANDSCAPE else 1), W * 0.9 / max(1, 0.47 * longest)))
    line_h = size * 1.0
    y = H * 0.36 - (line_h * len(lines)) / 2 + line_h / 2
    t = s0 + 0.05
    for i, ln in enumerate(lines):
        color = "" if i < len(lines) - 1 or len(lines) == 1 else f"\\c{YELLOW}&"
        tag = (f"{{\\pos({W // 2},{int(y)})\\fs{size}{color}\\fad(80,150)"
               f"\\fscx125\\fscy125\\t(0,180,\\fscx100\\fscy100)}}")
        ev.append(("Hook", t, s1, tag + esc(ln)))
        y += line_h
        t += 0.12
    ch = channel()
    tag = f"{{\\pos({W // 2},{int(y + 40)})\\fad(150,150)\\fscx60\\t(0,220,\\fscx100)}}"
    ev.append(("Strip", t + 0.1, s1, tag + esc(ch["name"])))


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


def make_ass(scenes, total, title="", outro=0.0):
    ev = []
    for i, sc in enumerate(scenes):
        if i == 0 and title:
            hook_events(title, sc, ev)
            word_events(sc, ev)
        elif sc.get("_map"):
            word_events(sc, ev) if LANDSCAPE else box_events(sc, ev)
        elif (sc.get("style") or ("headline" if i % 2 == 0 else "subtitle")) == "headline":
            headline_events(sc, ev)
            word_events(sc, ev)
        else:
            box_events(sc, ev)
    if outro:
        tag = f"{{\\pos({W // 2},{int(H * 0.78)})\\fad(120,0)\\fscx70\\fscy70\\t(0,220,\\fscx100\\fscy100)}}"
        ev.append(("Follow", total + 0.25, total + outro, tag + esc(channel()["follow"])))
    brand = os.environ.get("CHANNEL_NAME", "").strip()
    if brand:
        ev.append(("Brand", 0, total, esc(brand)))
    lines = [ass_head(), "[Events]",
             "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for style, a, b, txt in ev:
        lines.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},{style},,0,0,0,,{txt}")
    p = WORK / "subs.ass"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------- 5) Birleştir

OUTRO = 2.0


def outro_clip():
    """Son 2 sn: kanal logosu + takip çağrısı (yazı ASS ile eklenir)."""
    out = WORK / "outro.mp4"
    anim = channel()["outro"]
    if anim.exists() and not LANDSCAPE:
        run(["ffmpeg", "-y", "-v", "error", "-i", anim, "-t", f"{OUTRO}", "-an",
             "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1,format=yuv420p",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-r", str(FPS), out])
        return out
    logo = channel()["logo"]
    bg = ["-f", "lavfi", "-i", f"gradients=s={W}x{H}:c0=0x05050c:c1=0x1a0a2a:x0=0:y0=0:x1={W}:y1={H}:speed=0.02:r={FPS}"]
    if logo.exists():
        fc = (f"[1:v]scale={int(min(W, H) * 0.8)}:-1,format=rgba,fade=in:st=0:d=0.35:alpha=1[l];"
              f"[0:v][l]overlay=x=(W-w)/2:y=H*0.40-h/2,setsar=1,format=yuv420p[v]")
        run(["ffmpeg", "-y", "-v", "error", *bg, "-loop", "1", "-i", logo, "-t", f"{OUTRO}",
             "-filter_complex", fc, "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
             "-r", str(FPS), out])
    else:
        run(["ffmpeg", "-y", "-v", "error", *bg, "-t", f"{OUTRO}", "-vf", "format=yuv420p",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-r", str(FPS), out])
    return out


def find_music():
    if ASSETS.is_dir():
        ms = sorted(ASSETS.glob("*.mp3"))
        if ms:
            return random.choice(ms)
    return None


def compose(clips, voice, ass, total, tv=None):
    tv = tv or total
    lst = WORK / "clips.txt"
    lst.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    joined = WORK / "joined.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", joined])
    music = find_music()
    out = OUT / "video.mp4"
    logo = channel()["logo"]
    inputs = ["-i", joined, "-i", voice,
              "-f", "lavfi", "-i", f"color=c=0xE31A2D:s={W}x12:r={FPS}:d={tv:.3f}"]
    fc = (f"[0:v]subtitles=filename='{ass}':fontsdir='{FONTS}'[v1];"
          f"[v1][2:v]overlay=x='-w+w*t/{tv:.3f}':y=0:shortest=1[v2]")
    nxt = 3
    if logo.exists():
        inputs += ["-loop", "1", "-i", logo]
        fc += (f";[{nxt}:v]scale=190:-1,format=rgba,colorchannelmixer=aa=0.92[lg];"
               f"[v2][lg]overlay=x=W-w-28:y=40:enable='lt(t,{total:.3f})':shortest=1[v]")
        nxt += 1
    else:
        fc += ";[v2]null[v]"
    fc += f";[1:a]apad=whole_dur={tv:.3f}[va]"
    if music:
        log("Müzik:", music.name)
        inputs += ["-stream_loop", "-1", "-i", music]
        fc += (f";[{nxt}:a]volume=0.10,afade=t=out:st={max(0, tv - 1.5):.3f}:d=1.5[m];"
               f"[va][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]")
    else:
        fc += ";[va]anull[a]"
    amap = "[a]"
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", fc, "-map", "[v]", "-map", amap,
         "-t", f"{tv:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", out])
    thumb = OUT / "thumb.jpg"
    run(["ffmpeg", "-y", "-v", "error", "-ss", "1.2", "-i", out, "-frames:v", "1", "-q:v", "3", thumb])
    return out


# ---------------------------------------------------------------- Küçük resimler (A/B)

def make_thumbs(video, texts, out_dir):
    """Uzun video için 3 farklı küçük resim (1280x720): YouTube 'Test & Compare' için."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    texts = (list(texts) + list(texts) * 3)[:3]
    dur = probe_duration(video)
    styles = [((255, 212, 0), (0, 0, 0)), ((255, 255, 255), (227, 26, 45)), ((92, 225, 255), (0, 0, 0))]
    for k, (txt, (fg, box)) in zip("abc", zip(texts, styles)):
        frame = WORK / f"thumb_src_{k}.jpg"
        clips = sorted(WORK.glob("src_*.mp4")) or sorted(WORK.glob("scene_*.mp4"))  # yazısız ham görüntüler
        src = clips[{"a": 1, "b": len(clips) // 2, "c": -2}[k] % len(clips)] if clips else video
        run(["ffmpeg", "-y", "-v", "error", "-ss", "1.0", "-i", src, "-frames:v", "1",
             "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720", frame])
        im = Image.open(frame).convert("RGB").filter(ImageFilter.GaussianBlur(1))
        im = Image.blend(im, Image.new("RGB", im.size, (0, 0, 0)), 0.35)
        d = ImageDraw.Draw(im)
        words = tr_upper(clean_title(txt)).split()
        lines = wrap_lines(words, 12)[:3]
        size = 150 if len(lines) <= 2 else 120
        fnt = ImageFont.truetype(str(FONTS / "Anton-Regular.ttf"), size)
        y = 360 - len(lines) * size * 0.55
        for i, ln in enumerate(lines):
            tw = d.textlength(ln, font=fnt)
            x = 60
            if i == len(lines) - 1:
                d.rectangle([x - 20, y + 8, x + tw + 20, y + size + 18], fill=box)
            d.text((x, y), ln, font=fnt, fill=fg, stroke_width=6, stroke_fill=(0, 0, 0))
            y += size * 1.08
        logo = channel()["logo"]
        if logo.exists():
            lg = Image.open(logo).convert("RGBA")
            lg.thumbnail((230, 230))
            im.paste(lg, (1280 - lg.width - 20, 20), lg)
        im.save(out_dir / f"thumb_{k}.jpg", quality=90)


# ---------------------------------------------------------------- Kota

def elevenlabs_quota():
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        return None
    try:
        d = http_json("https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": key}, timeout=30)
        used, limit = int(d.get("character_count", 0)), int(d.get("character_limit", 0))
        return {"used": used, "limit": limit, "left": max(0, limit - used),
                "reset_unix": d.get("next_character_count_reset_unix")}
    except Exception as e:  # noqa
        log("ElevenLabs kota okunamadı:", e)
        return None


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


def source_line(payload, lang):
    src = (payload.get("source") or payload.get("youtube_url") or "").strip()
    if not src.startswith("http"):
        return ""
    host = re.sub(r"^www\.", "", urllib.parse.urlparse(src).netloc)
    if "youtu" in host:
        return ""  # YouTuber kaynağını linkleyerek trafiği dışarı vermiyoruz
    word = {"tr": "Kaynak", "de": "Quelle"}.get(lang, "Source")
    return f"\n\n{word}: {host}"


def produce(payload, out_dir=None, tag=""):
    """Tek bir video üretir; sonuç sözlüğünü döndürür (skip ise {"skip": True})."""
    global OUT, WORK, LANG, USED_IDS, USED_LOCAL, LANDSCAPE, W, H
    long = bool(payload.get("long"))
    LANDSCAPE = long
    W, H = (1920, 1080) if long else (1080, 1920)
    OUT = Path(out_dir or os.environ.get("OUT_DIR", "out"))
    WORK = OUT / "work"
    USED_IDS, USED_LOCAL = set(), set()
    MAPS_USED.update({"n": 0, "last": -9, "keys": set()})
    WORK.mkdir(parents=True, exist_ok=True)
    script = make_script(payload)
    if script.get("skip"):
        return {"skip": True}
    LANG = (script.get("lang") or payload.get("target_lang") or payload.get("lang") or "tr").lower()[:2]
    log("Dil:", LANG)
    scenes = [s for s in script["scenes"] if (s.get("text") or "").strip()][:26 if long else 9]
    log(f"{len(scenes)} sahne")
    voice, total = make_audio(scenes)
    clips = [scene_clip(i, sc) for i, sc in enumerate(scenes)]
    clips.append(outro_clip())
    ass = make_ass(scenes, total, title=script.get("title") or "", outro=OUTRO)
    video = compose(clips, voice, ass, total, total + OUTRO)
    total += OUTRO

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    tag = tag or os.environ.get("RELEASE_TAG", "")
    tags = " ".join(script.get("hashtags") or [])
    chapters = ""
    if long:
        rows = [(sc["start"], sc.get("chapter")) for sc in scenes if sc.get("chapter")]
        if rows and rows[0][0] > 0.5:
            rows.insert(0, (0, script.get("title", "")[:40]))
        if rows:
            rows[0] = (0, rows[0][1])
            chapters = "\n".join(f"{int(t // 60)}:{int(t % 60):02d} {c}" for t, c in rows)
        make_thumbs(video, script.get("thumb_texts") or [script.get("title", "")], OUT)
    q = elevenlabs_quota()
    chars = sum(len(sc["text"]) for sc in scenes)
    if q:
        quota_text = (f"ElevenLabs: {q['left']:,} / {q['limit']:,} karakter kaldı "
                      f"(bu video {'ücretsiz ses' if TTS_USED != 'elevenlabs' else f'{chars:,} karakter'})").replace(",", ".")
    else:
        quota_text = "ElevenLabs: kota okunamadı"
    result = {
        "title": (script.get("title") or scenes[0]["text"])[:95],
        "description": ((script.get("description") or "") + ("\n\n" + chapters if chapters else "")
                        + source_line(payload, LANG) + "\n\n" + tags).strip(),
        "tags": script.get("tags") or [],
        "long": long,
        "thumbs": [f"https://github.com/{repo}/releases/download/{tag}/thumb_{k}.jpg" for k in "abc"] if long else [],
        "hashtags": script.get("hashtags") or [],
        "lang": LANG,
        "topic": script.get("topic") or script.get("title") or "",
        "tts": TTS_USED,
        "duration": round(total, 2),
        "video_url": f"https://github.com/{repo}/releases/download/{tag}/video.mp4" if repo and tag else "",
        "thumb_url": f"https://github.com/{repo}/releases/download/{tag}/thumb.jpg" if repo and tag else "",
        "scenes": [{k: s.get(k) for k in ("text", "headline", "accent", "search", "style", "countries", "country_names", "chapter")} for s in scenes],
        "chat_id": payload.get("chat_id"),
        "source": payload.get("youtube_url") or payload.get("source") or "",
        "publish_at": (__import__("datetime").datetime.utcnow() + __import__("datetime").timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "elevenlabs": q,
        "quota_text": quota_text,
        "test": bool(payload.get("test")) or os.environ.get("PUBLISH", "true").lower() == "false",
    }
    (OUT / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "notes.md").write_text(result["description"] or result["title"], encoding="utf-8")
    log("Bitti:", video, f"{total:.1f} sn")
    return result


def main():
    res = produce(load_payload())
    if res.get("skip"):
        sys.exit("Kaynak atlandı (haber niteliği taşımıyor)")


if __name__ == "__main__":
    main()
