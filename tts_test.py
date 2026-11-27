"""Türkçe ses karşılaştırma örnekleri üretir (değerlendirme içindir)."""
import os, json, base64, subprocess, asyncio, render
from pathlib import Path
T = ("Kerç Köprüsü dün gece yeniden vuruldu. Rusya ile Kırım arasındaki ana ikmal hattı saatlerce kapalı kaldı. "
     "Peki bu saldırı, Karadeniz'deki dengeleri nasıl değiştirecek?")
out = Path("tts_out"); out.mkdir(exist_ok=True)
for v in ("tr-TR-AhmetNeural", "tr-TR-EmelNeural"):
    for rate in ("+0%", "+8%"):
        f = out / f"edge_{v.split('-')[2]}_{rate.strip('+%')}.mp3"
        try:
            asyncio.run(render._edge(T, str(f), v, rate)); print("OK", f)
        except Exception as e:
            print("HATA", f, e)
key = os.environ.get("ELEVENLABS_API_KEY")
for name, vid in (("cavit_haber", "Q2IX97JeHBY3vNGzgM5s"), ("emin_haber", "UeybxBRjI6mrBwoIZOKU"), ("murat_genc", "dgeCtiGkvIwzoR09qzjl")):
    for model in ("eleven_multilingual_v2",):
        try:
            r = render.http_json(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}/with-timestamps?output_format=mp3_44100_128",
                                 {"text": T, "model_id": model}, {"xi-api-key": key}, timeout=120)
            f = out / f"eleven_{name}.mp3"; f.write_bytes(base64.b64decode(r["audio_base64"])); print("OK", f)
        except Exception as e:
            print("HATA eleven", name, str(e)[:120], getattr(e, "read", lambda: b"")()[:200] if hasattr(e, "read") else "")
# Gemini TTS
gk = os.environ.get("GEMINI_API_KEY_2") or os.environ.get("GEMINI_API_KEY")
ms = render.http_json("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200", headers={"x-goog-api-key": gk})
tts = [m["name"].split("/")[1] for m in ms.get("models", []) if "tts" in m["name"]]
print("GEMINI TTS MODELLER:", tts)
for model in tts[:3]:
    for voice in ("Charon", "Orus"):
        try:
            r = render.http_json(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                {"contents": [{"parts": [{"text": "Bir haber spikeri gibi, net ve akıcı Türkçeyle oku: " + T}]}],
                 "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}},
                {"x-goog-api-key": gk}, timeout=120)
            pcm = base64.b64decode(r["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
            raw = out / "g.pcm"; raw.write_bytes(pcm)
            f = out / f"gemini_{model}_{voice}.mp3"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", str(raw), str(f)], check=True)
            print("OK", f)
        except Exception as e:
            print("HATA gemini", model, voice, str(e)[:120])
    break
