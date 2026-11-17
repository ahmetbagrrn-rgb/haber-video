#!/usr/bin/env python3
"""
Tam otomatik akış (GitHub Actions'ta çalışır).

  python pipeline.py auto        -> Takip edilen YouTube kanallarından (yoksa haber RSS'inden)
                                    bir konu seçer, Türkçe + İngilizce video üretir, Make'e gönderir.
  python pipeline.py tg-check    -> Telegram'da yeni mesaj var mı? (GITHUB_OUTPUT'a has=1/0 yazar)
  python pipeline.py telegram    -> Telegram mesajlarını işler (video üretir, cevap verir).

Durum (işlenen videolar, konular, Telegram offset) state/state.json içinde tutulur.
"""
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import render

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "state" / "state.json"
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
REPO = os.environ.get("GITHUB_REPOSITORY", "")
RUN_ID = os.environ.get("GITHUB_RUN_ID", "local")
CHANNEL_NAMES = {"en": "World Shift", "tr": "Global News"}


def log(*a):
    print("[pipeline]", *a, flush=True)


# ---------------------------------------------------------------- durum

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"tg_offset": 0, "owner_chat": None, "processed": [], "topics": [], "daily": {}}


DRY = os.environ.get("PUBLISH", "true").lower() == "false"


def save_state(st):
    if DRY:
        log("Test modu: durum kaydedilmedi")
        return
    STATE_FILE.parent.mkdir(exist_ok=True)
    st["processed"] = st["processed"][-500:]
    st["topics"] = st["topics"][-200:]
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def today():
    return dt.datetime.utcnow().strftime("%Y-%m-%d")


def words(s):
    return {w for w in re.findall(r"[a-zçğıöşü0-9]+", (s or "").lower()) if len(w) > 2}


def similar_topic(topic, st, days=7):
    """Son günlerde çok benzer bir konu işlendi mi?"""
    w = words(topic)
    if not w:
        return False
    limit = dt.datetime.utcnow() - dt.timedelta(days=days)
    for t in st["topics"]:
        try:
            if dt.datetime.fromisoformat(t["ts"]) < limit:
                continue
        except Exception:  # noqa
            pass
        o = words(t["topic"])
        if o and len(w & o) / len(w | o) >= 0.45:
            log("Benzer konu zaten işlenmiş:", t["topic"])
            return True
    return False


def remember_topic(st, topic):
    st["topics"].append({"topic": topic, "ts": dt.datetime.utcnow().isoformat(timespec="seconds")})


# ---------------------------------------------------------------- Telegram

def tg(method, **params):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(params).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        log(f"Telegram {method} hatası {e.code}: {body[:200]}")
        return {"ok": False, "error_code": e.code, "description": body}


def tg_send(chat_id, text):
    if chat_id:
        tg("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview=True)


def tg_updates(offset):
    info = tg("getWebhookInfo") or {}
    if (info.get("result") or {}).get("url"):
        log("Telegram webhook'u (Make) kaldırılıyor; artık GitHub dinliyor")
        tg("deleteWebhook", drop_pending_updates=False)
    r = tg("getUpdates", offset=offset, timeout=0, allowed_updates=["message"]) or {}
    return r.get("result") or []


# ---------------------------------------------------------------- yayın

def release(tag, out_dir, title):
    files = [str(Path(out_dir) / f) for f in ("video.mp4", "thumb.jpg", "result.json")]
    subprocess.run(["gh", "release", "create", tag, *files, "--title", title[:120],
                    "--notes-file", str(Path(out_dir) / "notes.md")], check=True)


def publish(result):
    url = os.environ.get("MAKE_WEBHOOK_URL")
    if result.get("test") or not url:
        log("Yayın atlandı (test ya da webhook yok)")
        return False
    req = urllib.request.Request(url, data=json.dumps(result).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        log("Make cevabı:", r.status)
    return True


def make_video(payload, tag, instagram=True):
    out_dir = ROOT / "out" / tag
    res = render.produce(payload, out_dir=out_dir, tag=tag)
    if res.get("skip"):
        return res
    res["instagram"] = bool(instagram)
    (out_dir / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    release(tag, out_dir, res["title"])
    res["published"] = publish(res)
    return res


def summary_line(res):
    ch = CHANNEL_NAMES.get(res.get("lang"), "Global News")
    ig = " + Instagram" if res.get("instagram") else ""
    state = "yayına gönderildi" if res.get("published") else "üretildi (yayınlanmadı)"
    return f"• {ch}{ig}: {res['title']} — {state}"


def quota_block(res):
    return f"📊 Kalan krediler:\n• {res.get('quota_text', 'ElevenLabs: bilgi yok')}\n• Make: panelden kontrol et"


# ---------------------------------------------------------------- kaynaklar

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 haber-video"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def youtube_candidates(st):
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
          "media": "http://search.yahoo.com/mrss/"}
    out = []
    max_age = dt.datetime.utcnow() - dt.timedelta(days=CONFIG.get("max_video_age_days", 4))
    for ch in CONFIG.get("youtube_channels", []):
        try:
            root = ET.fromstring(fetch(f"https://www.youtube.com/feeds/videos.xml?channel_id={ch['channel_id']}"))
        except Exception as e:  # noqa
            log("YouTube RSS okunamadı:", ch.get("name"), e)
            continue
        for e in root.findall("a:entry", ns):
            vid = e.findtext("yt:videoId", namespaces=ns)
            title = e.findtext("a:title", namespaces=ns) or ""
            pub = e.findtext("a:published", namespaces=ns) or ""
            try:
                pdt = dt.datetime.fromisoformat(pub.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:  # noqa
                pdt = dt.datetime.utcnow()
            if not vid or f"yt:{vid}" in st["processed"] or pdt < max_age:
                continue
            out.append({"id": f"yt:{vid}", "url": f"https://www.youtube.com/watch?v={vid}",
                        "title": title, "published": pdt, "channel": ch.get("name")})
    out.sort(key=lambda x: x["published"])  # en eskiden yeniye: sırayla işler
    return out


def news_candidates(st):
    out = []
    for feed in CONFIG.get("news_feeds", []):
        try:
            root = ET.fromstring(fetch(feed))
        except Exception as e:  # noqa
            log("Haber RSS okunamadı:", feed, e)
            continue
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            desc = re.sub(r"<[^>]+>", " ", html.unescape(it.findtext("description") or "")).strip()
            key = "news:" + (link or title)
            if not title or key in st["processed"]:
                continue
            out.append({"id": key, "title": title, "text": f"{title}.\n{desc}", "link": link})
            if len(out) >= 40:
                break
    return out


# ---------------------------------------------------------------- komutlar

def cmd_auto():
    st = load_state()
    day = today()
    done = st["daily"].get(day, 0)
    if done >= CONFIG.get("max_auto_per_day", 3) and os.environ.get("FORCE") != "1":
        log("Günlük otomatik video sınırına ulaşıldı:", done)
        return
    owner = st.get("owner_chat")
    langs = CONFIG.get("auto_langs", ["tr", "en"])
    ig_lang = CONFIG.get("instagram_lang", "tr")

    for cand in youtube_candidates(st) + news_candidates(st):
        if similar_topic(cand["title"], st):
            st["processed"].append(cand["id"])
            continue
        log("Kaynak:", cand["id"], cand["title"])
        base = {"mode": "rewrite", "source": cand.get("url") or cand.get("link") or ""}
        if cand["id"].startswith("yt:"):
            base["youtube_url"] = cand["url"]
        else:
            base["text"] = cand["text"]
        results = []
        try:
            for i, lang in enumerate(langs):
                tag = f"auto-{RUN_ID}-{lang}"
                pl = {**base, "target_lang": lang}
                if i > 0 and results:
                    # 2. dil: aynı senaryodan uyarla (aynı konu, videoyu tekrar izletmeye gerek yok)
                    first = results[0]
                    pl = {"mode": "rewrite", "source": base["source"], "target_lang": lang,
                          "text": first["title"] + ".\n" + "\n".join(sc["text"] for sc in first["scenes"])}
                res = make_video(pl, tag, instagram=(lang == ig_lang))
                if res.get("skip"):
                    break
                if i == 0 and similar_topic(res.get("topic", ""), st):
                    break
                results.append(res)
        except Exception as e:  # noqa
            log("Üretim hatası:", e)
            tg_send(owner, f"⚠️ Otomatik video üretilemedi: {cand['title']}\nHata: {str(e)[:300]}")
            st["processed"].append(cand["id"])
            save_state(st)
            raise
        st["processed"].append(cand["id"])
        if not results:
            continue
        remember_topic(st, results[0].get("topic") or cand["title"])
        st["daily"] = {day: done + 1}
        save_state(st)
        src = "YouTube: " + (cand.get("channel") or "") if cand["id"].startswith("yt:") else "Haber akışı"
        tg_send(owner, "🤖 Otomatik video (" + src + ")\n" + "\n".join(summary_line(r) for r in results)
                + "\n\n" + quota_block(results[-1]))
        return
    save_state(st)
    log("İşlenecek yeni kaynak bulunamadı")


def cmd_tg_check():
    st = load_state()
    ups = tg_updates(st.get("tg_offset", 0))
    has = any((u.get("message") or {}).get("text") for u in ups)
    if not has and ups:  # metin olmayan güncellemeleri atla
        st["tg_offset"] = ups[-1]["update_id"] + 1
        save_state(st)
    log("Yeni Telegram mesajı:", has)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"has={'1' if has else '0'}\n")


def cmd_telegram():
    st = load_state()
    ups = tg_updates(st.get("tg_offset", 0))
    for n, u in enumerate(ups):
        st["tg_offset"] = u["update_id"] + 1
        save_state(st)  # önce offset: hata olursa aynı mesaj tekrar işlenmesin
        msg = u.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat = (msg.get("chat") or {}).get("id")
        if not text or not chat:
            continue
        if st.get("owner_chat") is None:
            st["owner_chat"] = chat
            save_state(st)
        if chat != st["owner_chat"]:
            log("Yabancı sohbetten mesaj yok sayıldı")
            continue
        if text.startswith("/start") or text.startswith("/durum"):
            q = render.elevenlabs_quota()
            left = f"{q['left']:,} / {q['limit']:,}".replace(",", ".") if q else "bilinmiyor"
            tg_send(chat, f"✅ Sistem çalışıyor.\nBugün otomatik: {st['daily'].get(today(), 0)}/"
                          f"{CONFIG.get('max_auto_per_day', 3)}\nElevenLabs: {left} karakter\n"
                          "Haber metni gönder → video. Başına /ikidil yazarsan Türkçe + İngilizce üretilir.")
            continue
        both = text.startswith("/ikidil")
        text = re.sub(r"^/ikidil\s*", "", text)
        tg_send(chat, "🎬 Video hazırlanıyor, 3-5 dk sürer.")
        try:
            results = []
            langs = CONFIG.get("auto_langs", ["tr", "en"]) if both else [None]
            for lang in langs:
                payload = {"text": text, "chat_id": chat}
                if lang:
                    payload.update({"target_lang": lang})
                tag = f"tg-{RUN_ID}-{n}-{lang or 'x'}"
                res = make_video(payload, tag, instagram=(not both or lang == CONFIG.get("instagram_lang", "tr")))
                results.append(res)
            remember_topic(st, results[0].get("topic") or results[0]["title"])
            save_state(st)
            tg_send(chat, "✅ Video hazır\n" + "\n".join(summary_line(r) for r in results)
                    + "\n" + results[0]["video_url"] + "\n\n" + quota_block(results[-1]))
        except Exception as e:  # noqa
            log("Telegram videosu üretilemedi:", e)
            tg_send(chat, f"⚠️ Video üretilemedi: {str(e)[:300]}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"auto": cmd_auto, "tg-check": cmd_tg_check, "telegram": cmd_telegram}.get(cmd, lambda: sys.exit(
        "Kullanım: pipeline.py auto | tg-check | telegram"))()
