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
import time
import uuid
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
CHANNEL_NAMES = {"en": "World Shift (EN)", "tr": "World Shift Dünya Haberleri (TR)", "de": "World Shift Deutsch"}
APPROVAL = CONFIG.get("approval", True)


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
        log("TELEGRAM_BOT_TOKEN tanımlı değil (GitHub secret adı doğru mu?)")
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
    except (urllib.error.URLError, OSError) as e:
        log(f"Telegram {method} ağ hatası: {e}")
        return None


def tg_send(chat_id, text):
    if chat_id:
        tg("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview=True)


def tg_updates(offset):
    info = tg("getWebhookInfo") or {}
    if (info.get("result") or {}).get("url"):
        log("Telegram webhook'u (Make) kaldırılıyor; artık GitHub dinliyor")
        tg("deleteWebhook", drop_pending_updates=False)
    r = tg("getUpdates", offset=offset, timeout=0, allowed_updates=["message", "callback_query"]) or {}
    return r.get("result") or []


# ---------------------------------------------------------------- yayın

def release(tag, out_dir, title):
    names = ["video.mp4", "thumb.jpg", "result.json"] + [f"thumb_{k}.jpg" for k in "abc"
                                                         if (Path(out_dir) / f"thumb_{k}.jpg").exists()]
    files = [str(Path(out_dir) / f) for f in names]
    subprocess.run(["gh", "release", "create", tag, *files, "--title", title[:120],
                    "--notes-file", str(Path(out_dir) / "notes.md")], check=True)


def tiktok_publish(result):
    conf = CONFIG.get("tiktok") or {}
    if not conf.get("enabled") or result.get("long") or result.get("lang") not in conf.get("langs", ["tr"]):
        return None
    try:
        import tiktok
        d = ROOT / "out" / "tt"
        d.mkdir(parents=True, exist_ok=True)
        subprocess.run(["gh", "release", "download", result["tag"], "-p", "video.mp4", "-D", str(d), "--clobber"],
                       check=True)
        caption = (result["title"] + " " + " ".join(result.get("hashtags") or []))[:2000]
        r = tiktok.post_video(d / "video.mp4", caption, conf.get("privacy"))
        log("TikTok'a gönderildi:", r)
        return r
    except Exception as e:  # noqa
        log("TikTok hatası:", e)
        return {"error": str(e)[:200]}


def publish(result):
    url = os.environ.get("MAKE_WEBHOOK_URL")
    if result.get("test") or not url:
        log("Yayın atlandı (test ya da webhook yok)")
        return False
    req = urllib.request.Request(url, data=json.dumps(result).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            log("Make cevabı:", r.status)
    except urllib.error.HTTPError:
        raise
    except Exception as e:  # noqa  zaman aşımı: Make isteği aldı, yüklemeyi kendi sürdürüyor
        if "timed out" not in str(e).lower():
            raise
        log("Make isteği aldı, yükleme arka planda sürüyor")
    try:
        result["tiktok"] = tiktok_publish(result)
    except Exception as e:  # noqa
        log("TikTok hatası:", e)
    return True


def next_slot(lang, st):
    """Yayın saati: config.publish_slots[lang] (yerel saat) içinden, henüz kullanılmamış ilk boş slot."""
    from zoneinfo import ZoneInfo
    conf = (CONFIG.get("publish_slots") or {}).get(lang)
    if not conf:
        return ""
    tz = ZoneInfo(conf["tz"])
    now = dt.datetime.now(tz)
    used = set(st.setdefault("scheduled", {}).get(lang, []))
    for day in range(0, 7):
        d = (now + dt.timedelta(days=day)).date()
        for hm in conf["times"]:
            h, m = map(int, hm.split(":"))
            t = dt.datetime(d.year, d.month, d.day, h, m, tzinfo=tz)
            iso = t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if t > now + dt.timedelta(minutes=20) and iso not in used:
                st["scheduled"].setdefault(lang, []).append(iso)
                st["scheduled"][lang] = st["scheduled"][lang][-60:]
                return iso
    return ""


def soon(minutes=5):
    return (dt.datetime.utcnow() + dt.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def queue_or_publish(results, st, header):
    """Onay açıksa Telegram'a onay isteği gönderir; kapalıysa hemen yayınlar."""
    owner = st.get("owner_chat")
    if not APPROVAL or not owner or DRY:
        for r in results:
            slot = next_slot(r["lang"], st)
            r["scheduled"] = bool(slot)
            r["publish_at"] = slot or soon()
            r["published"] = publish(r)
        return False
    st.setdefault("pending", {})
    gid = uuid.uuid4().hex[:12]  # benzersiz: onay/iptal kimlikleri asla çakışmasın
    st["pending"][gid] = {"results": results, "ts": dt.datetime.utcnow().isoformat(timespec="seconds")}
    lines = [header, ""]
    for r in results:
        ch = CHANNEL_NAMES.get(r["lang"], r["lang"])
        lines.append(f"• {ch}{' + Instagram' if r.get('instagram') else ''}: {r['title']}\n  {r['video_url']}")
    if results and results[0].get("thumbs"):
        lines.append("\n🖼 A/B küçük resimler:\n" + "\n".join(results[0]["thumbs"]))
    lines.append("\nYayınlansın mı?")
    tg("sendMessage", chat_id=owner, text="\n".join(lines)[:4000], disable_web_page_preview=True,
       reply_markup={"inline_keyboard": [[{"text": "✅ Yayınla", "callback_data": f"ok:{gid}"},
                                           {"text": "❌ İptal", "callback_data": f"no:{gid}"}]]})
    return True


def handle_callback(cb, st):
    data = cb.get("data") or ""
    chat = ((cb.get("message") or {}).get("chat") or {}).get("id")
    msg_id = (cb.get("message") or {}).get("message_id")
    if chat != st.get("owner_chat") or ":" not in data:
        return
    action, gid = data.split(":", 1)
    item = st.get("pending", {}).pop(gid, None)
    st.setdefault("_removed", []).append(gid)
    if not item:
        tg_send(chat, "Bu video zaten işlenmiş ya da süresi dolmuş.")
        return
    if action == "no":
        tg("editMessageReplyMarkup", chat_id=chat, message_id=msg_id, reply_markup={"inline_keyboard": []})
        tg_send(chat, "❌ İptal edildi, yayınlanmadı.")
        return
    out, failed = [], []
    for r in item["results"]:
        ch = CHANNEL_NAMES.get(r["lang"], r["lang"])
        try:
            slot = next_slot(r["lang"], st)
            r["publish_at"] = slot or soon()
            r["published"] = publish(r)
            out.append(f"• {ch}: {to_tr_time(slot) if slot else 'hemen'}")
        except Exception as e:  # noqa  bir dil hata verse de diğerleri yayınlansın
            log("Yayın hatası:", r["lang"], e)
            failed.append(r)
            out.append(f"• {ch}: ⚠️ hata ({str(e)[:80]})")
    tg("editMessageReplyMarkup", chat_id=chat, message_id=msg_id, reply_markup={"inline_keyboard": []})
    tg_send(chat, "✅ Yayına gönderildi:\n" + "\n".join(out) + "\n\n" + quota_block(item["results"][-1]))
    if failed:  # hatalı olanları yeniden onaya koy
        queue_or_publish(failed, st, "🔁 Yayınlanamayanlar (tekrar dene)")


def to_tr_time(iso):
    from zoneinfo import ZoneInfo
    t = dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    return t.astimezone(ZoneInfo("Europe/Istanbul")).strftime("%d.%m %H:%M (TR saati)")


def expire_pending(st, hours=48):
    lim = dt.datetime.utcnow() - dt.timedelta(hours=hours)
    for gid in list(st.get("pending", {})):
        try:
            if dt.datetime.fromisoformat(st["pending"][gid]["ts"]) < lim:
                st["pending"].pop(gid)
                st.setdefault("_removed", []).append(gid)
        except Exception:  # noqa
            st["pending"].pop(gid, None)


def make_video(payload, tag, instagram=True):
    out_dir = ROOT / "out" / tag
    res = render.produce(payload, out_dir=out_dir, tag=tag)
    if res.get("skip"):
        return res
    res["instagram"] = bool(instagram)
    res["tag"] = tag
    (out_dir / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    release(tag, out_dir, res["title"])
    return res


def summary_line(res):
    ch = CHANNEL_NAMES.get(res.get("lang"), "World Shift Dünya Haberleri (TR)")
    ig = " + Instagram" if res.get("instagram") else ""
    state = "yayına gönderildi" if res.get("published") else "üretildi (yayınlanmadı)"
    return f"• {ch}{ig}: {res['title']} — {state}"


def quota_block(res):
    ev = "kapalı (ücretsiz Microsoft sesi kullanılıyor)" if not CONFIG.get("elevenlabs_enabled", True) \
        else res.get("quota_text", "bilgi yok")
    return f"📊 Krediler:\n• ElevenLabs: {ev}\n• Make: panelden kontrol et (eu1.make.com)"


# ---------------------------------------------------------------- kaynaklar

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 haber-video"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def youtube_candidates(st, ignore_processed=False):
    """Takip edilen kanalların işlenmemiş videoları + kanalın son yükleme zamanı."""
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    out, latest = [], None
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
            latest = max(latest or pdt, pdt)
            if not vid or (f"yt:{vid}" in st["processed"] and not ignore_processed) or pdt < max_age:
                continue
            out.append({"id": f"yt:{vid}", "url": f"https://www.youtube.com/watch?v={vid}",
                        "title": title, "published": pdt, "channel": ch.get("name")})
    out.sort(key=lambda x: x["published"], reverse=True)  # önce en yeni
    return out, latest


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


def trend_candidates(st):
    """Google Trends günlük trendleri (ABD/Almanya/Türkiye) içinden nişe uygun olanı Gemini seçer."""
    ns = {"ht": "https://trends.google.com/trending/rss"}
    items = []
    for geo in CONFIG.get("trend_geos", ["US", "DE", "TR"]):
        try:
            root = ET.fromstring(fetch(f"https://trends.google.com/trending/rss?geo={geo}"))
        except Exception as e:  # noqa
            log("Trend RSS okunamadı:", geo, e)
            continue
        for it in root.iter("item"):
            q = (it.findtext("title") or "").strip()
            news = []
            for n in it.findall("ht:news_item", ns):
                t = n.findtext("ht:news_item_title", namespaces=ns) or ""
                sn = n.findtext("ht:news_item_snippet", namespaces=ns) or ""
                src = n.findtext("ht:news_item_source", namespaces=ns) or ""
                news.append(f"- {html.unescape(t)} ({src}): {html.unescape(sn)}")
            key = f"trend:{geo}:{q.lower()}"
            if q and news and key not in st["processed"]:
                items.append({"id": key, "title": q, "geo": geo, "news": news[:4]})
    if not items:
        return []
    listing = "\n".join(f"{i}. [{x['geo']}] {x['title']} :: {x['news'][0][:160]}" for i, x in enumerate(items[:60]))
    niche = CONFIG.get("niche", "jeopolitik, uluslararası ilişkiler, savunma, siber güvenlik, yapay zekâ, teknoloji, enerji, küresel ekonomi")
    prompt = (f"Aşağıdaki Google Trends konularından şu nişe EN UYGUN ve en çok ilgi çekecek 3 tanesini seç: {niche}. "
              "Spor, magazin, ünlü hayatı, suç/kaza haberleri SEÇME. Sadece JSON döndür: {\"picks\":[numara,...]}\n\n" + listing)
    try:
        picks = render.llm_json(prompt, 0.3, models=["gemini-3.5-flash-lite", "gemini-flash-lite-latest"],
                                ok=lambda d: isinstance(d.get("picks"), list))["picks"]
    except Exception as e:  # noqa
        log("Trend seçimi yapılamadı:", e)
        return []
    out = []
    for i in picks:
        if isinstance(i, int) and 0 <= i < len(items):
            x = items[i]
            out.append({"id": x["id"], "title": x["title"],
                        "text": f"Trend arama: {x['title']} ({x['geo']})\nHaberler:\n" + "\n".join(x["news"])})
    return out


STORY_PROMPT = """Sen bir haber masası şefisin. Ekteki YouTube videosunu (ilk 15 dakika) izle.
Videoda anlatılan, birbirinden BAĞIMSIZ ve haber değeri taşıyan olayları çıkar (en fazla {n} tane, en önemliden başlayarak).
Sadece şu nişe uyanları al: jeopolitik, savaş ve güvenlik, uluslararası ilişkiler, küresel ekonomi, enerji, teknoloji, siber güvenlik.
Magazin, kişisel yorum, reklam, abone çağrısı ALMA.
Her olay için:
- "title": olayın kısa, somut Türkçe adı (kim + ne)
- "facts": videoda söylenen olgular, Türkçe, 4-7 cümle. İsimler, yerler, rakamlar ve tarihler videoda geçtiği gibi; iddiaları kime ait olduğuyla yaz. Videoda olmayan bilgi EKLEME. Konuşmacının cümlelerini kopyalama, olguları aktar.
- "score": 1-10 arası haber değeri (somutluk + geniş etki + izleyici ilgisi)
Sadece JSON döndür: {"stories":[{"title":"...","facts":"...","score":8}]}
Haber niteliğinde olay yoksa {"stories":[]} döndür."""


def extract_stories(url, n):
    if not render.gemini_keys():
        return None
    parts = [{"fileData": {"fileUri": url}, "videoMetadata": {"startOffset": "0s", "endOffset": "900s"}},
             {"text": STORY_PROMPT.replace("{n}", str(n))}]
    gen = {"responseMimeType": "application/json", "temperature": 0.3, "mediaResolution": "MEDIA_RESOLUTION_LOW"}
    try:
        data = render.gemini_json(parts, gen, ["gemini-3.5-flash", "gemini-flash-latest", "gemini-3.5-flash-lite"],
                                  timeout=240, ok=lambda d: isinstance(d.get("stories"), list))
    except Exception as e:  # noqa
        log("Haber çıkarma başarısız:", str(e)[:150])
        return None
    st = [x for x in data["stories"] if x.get("title") and x.get("facts")]
    st.sort(key=lambda x: -(x.get("score") or 0))
    log(f"Videodan {len(st)} haber çıkarıldı:", " | ".join(x["title"] for x in st))
    return st[:n]


def youtube_story_candidates(st, ignore_processed=False):
    """YouTuber videolarını konu konu böler: her video birden çok kısa video kaynağı olur."""
    vids, latest = youtube_candidates(st, ignore_processed)
    store = st.setdefault("yt_stories", {})
    n = CONFIG.get("stories_per_video", 3)
    min_score = CONFIG.get("min_story_score", 5)
    out = []
    for v in vids:
        if v["id"] not in store:
            stories = extract_stories(v["url"], n)
            if stories is None:  # çıkarma başarısız: eski yöntem (videonun tamamı)
                out.append(v)
                continue
            store[v["id"]] = {"stories": stories, "used": []}
            save_state(st)
        rec = store[v["id"]]
        left = [k for k, x in enumerate(rec["stories"])
                if k not in rec["used"] and (x.get("score") or 10) >= min_score]
        if not left:
            st["processed"].append(v["id"])
            continue
        for k in left:
            x = rec["stories"][k]
            out.append({"id": f"{v['id']}#{k}", "video": v["id"], "story": k, "url": v["url"],
                        "published": v["published"], "score": x.get("score") or 5,
                        "title": x["title"], "text": f"{x['title']}.\n{x['facts']}"})
    # eski kayıtları temizle
    for vid in list(store)[:-20]:
        store.pop(vid, None)
    return out, latest


def mark_story(st, cand):
    if cand.get("video"):
        rec = st.get("yt_stories", {}).get(cand["video"])
        if rec is not None and cand["story"] not in rec["used"]:
            rec["used"].append(cand["story"])
            if len(rec["used"]) >= len(rec["stories"]):
                st["processed"].append(cand["video"])
    else:
        st["processed"].append(cand["id"])


# ---------------------------------------------------------------- komutlar

SEQ = {"n": 0}  # aynı çalışmada birden çok konu üretilirse release etiketleri çakışmasın


def is_transient(e):
    m = str(e)
    return any(k in m for k in ("503", "429", "timed out", "Service Unavailable", "Too Many Requests"))


def produce_topic(cand, st, header):
    """Bir konudan tüm dillerde video üretir, onaya/yayına gönderir."""
    langs = CONFIG.get("auto_langs", ["tr", "en", "de"])
    ig_lang = CONFIG.get("instagram_lang", "tr")
    base = {"mode": "rewrite", "source": cand.get("url") or cand.get("link") or ""}
    if cand["id"].startswith("yt:") and not cand.get("text"):
        base["youtube_url"] = cand["url"]
    else:
        base["text"] = cand["text"]
    results = []
    limits = CONFIG.get("weekly_limit", {})
    now = time.time()
    lw = st.setdefault("lang_week", {})
    def week_ok(lg):
        lw[lg] = [t for t in lw.get(lg, []) if now - t < 7 * 86400]
        return lg not in limits or len(lw[lg]) < limits[lg]
    langs = [lg for j, lg in enumerate(langs) if j == 0 or week_ok(lg)]
    for i, lang in enumerate(langs):
        tag = f"auto-{RUN_ID}-{lang}" if not SEQ["n"] else f"auto-{RUN_ID}-{SEQ['n']}-{lang}"
        pl = {**base, "target_lang": lang}
        if i > 0 and results:
            first = results[0]
            pl = {"mode": "rewrite", "source": base["source"], "target_lang": lang,
                  "text": first["title"] + ".\n" + "\n".join(sc["text"] for sc in first["scenes"])}
        res = make_video(pl, tag, instagram=(lang == ig_lang))
        if res.get("skip"):
            break
        if i == 0 and similar_topic(res.get("topic", ""), st):
            break
        results.append(res)
        if lang in limits:
            lw.setdefault(lang, []).append(now)
    if results:
        r0 = results[0]
        remember_topic(st, r0.get("topic") or cand["title"])
        st["topics"][-1]["summary"] = r0["title"] + ". " + " ".join(sc["text"] for sc in r0["scenes"])[:900]
        queue_or_publish(results, st, header)
    return results


def cmd_auto():
    st = load_state()
    expire_pending(st)
    slot = os.environ.get("SLOT") or "source"
    day = today()
    done = st["daily"].get(day, 0)
    owner = st.get("owner_chat")
    if slot != "long" and done >= CONFIG.get("max_auto_per_day", 3) and os.environ.get("FORCE") != "1":
        log("Günlük otomatik video sınırına ulaşıldı:", done)
        return
    weekly_reminder(st)

    if slot == "long":
        return cmd_long(st)
    if slot == "backfill":
        return cmd_backfill(st)

    if slot == "trend":
        cands, label = trend_candidates(st), "📈 Trend konu videosu"
        if not cands:
            cands, label = news_candidates(st), "📰 Ajans haberi (trend bulunamadı)"
    else:
        yts, latest = youtube_story_candidates(st)
        cands, label = yts, "🎥 YouTuber videosundan"
        if not yts and slot == "yt":
            log("YouTuber'da işlenecek konu yok; ek YouTuber saati atlandı")
            save_state(st)
            return
        if not yts:
            # YouTuber'da işlenmemiş yeni video yoksa (son 24 saatte paylaşım yok ya da zaten işlendi) ajanslara geç
            log("YouTuber son yükleme:", latest)
            cands, label = news_candidates(st), "📰 Ajans haberinden (YouTuber'da yeni video yok)"

    for cand in cands:
        if similar_topic(cand["title"], st):
            mark_story(st, cand)
            continue
        log("Kaynak:", cand["id"], cand["title"])
        try:
            results = produce_topic(cand, st, f"{label}\nKaynak: {cand['title']}")
        except (Exception, SystemExit) as e:  # noqa
            log("Üretim hatası:", e)
            if is_transient(e):  # Gemini meşgul: konu kaybolmasın, sonraki saatte tekrar denensin
                save_state(st)
                return
            tg_send(owner, f"⚠️ Otomatik video üretilemedi: {cand['title']}\nHata: {str(e)[:300]}")
            mark_story(st, cand)
            save_state(st)
            return
        mark_story(st, cand)
        if not results:
            continue
        st["daily"] = {day: done + 1}
        save_state(st)
        return
    save_state(st)
    log("İşlenecek yeni kaynak bulunamadı")


def cmd_backfill(st):
    """Tek seferlik: YouTuber'ın son N gününü konu konu işler, sonra normal rutine dönülür."""
    days = int(os.environ.get("BACKFILL_DAYS", "7"))
    limit = int(os.environ.get("BACKFILL_MAX", "10"))
    CONFIG["max_video_age_days"] = days
    subprocess.run(["git", "config", "user.name", "haber-bot"])
    subprocess.run(["git", "config", "user.email", "haber-bot@users.noreply.github.com"])
    cands, _ = youtube_story_candidates(st, ignore_processed=True)
    now = dt.datetime.utcnow()
    def ok(c):
        age = (now - c["published"]).days if c.get("published") else 0
        return c.get("score", 5) >= (7 if age >= 3 else 5)  # eski videolardan sadece güçlü konular
    cands = [c for c in cands if c.get("text") and ok(c)]
    cands.sort(key=lambda c: (-(c.get("score") or 0), -c["published"].timestamp()))
    log(f"Arşiv: {len(cands)} aday konu, en fazla {limit} işlenecek")
    tg_send(st.get("owner_chat"), f"🗂 YouTuber'ın son {days} günü işleniyor: {len(cands)} uygun konu bulundu, "
                                  f"en güçlü {min(limit, len(cands))} tanesi sırayla onayına gelecek.")
    done = 0
    for cand in cands:
        if done >= limit:
            break
        if similar_topic(cand["title"], st):
            mark_story(st, cand)
            continue
        log("Arşiv kaynağı:", cand["id"], cand["title"])
        SEQ["n"] += 1
        try:
            results = produce_topic(cand, st, f"🗂 YouTuber arşivinden ({cand['published']:%d.%m})\nKonu: {cand['title']}")
        except (Exception, SystemExit) as e:  # noqa
            log("Üretim hatası:", e)
            results = None
            if is_transient(e):
                time.sleep(90)  # Gemini kotası: bekle, konuyu işaretleme (sonra tekrar denenir)
                continue
        mark_story(st, cand)
        save_state(st)
        try:
            cmd_commit_state()
        except SystemExit as e:
            log("Durum kaydı ertelendi:", e)
        st = load_state()
        if results:
            done += 1
    tg_send(st.get("owner_chat"), f"🗂 Arşiv tamamlandı: {done} konu onaya gönderildi. Normal rutine dönüldü.")


def cmd_long(st):
    """Haftalık uzun (yatay) analiz videosu: YouTuber'ın son videosu + haftanın konuları."""
    week = dt.datetime.utcnow() - dt.timedelta(days=7)
    notes = []
    for t in st["topics"]:
        try:
            if dt.datetime.fromisoformat(t["ts"]) >= week and t.get("summary"):
                notes.append("- " + t["summary"])
        except Exception:  # noqa
            pass
    yts, _ = youtube_candidates({"processed": []})
    base = {"long": True, "mode": "rewrite", "text": "Haftanın notları:\n" + "\n".join(notes[-12:])}
    if yts:
        base["youtube_url"] = yts[0]["url"]
        base["source"] = yts[0]["url"]
    if not notes and not yts:
        log("Uzun video için malzeme yok")
        return
    results = []
    for lang in CONFIG.get("long_langs", CONFIG.get("auto_langs", ["tr", "en", "de"])):
        tag = f"long-{RUN_ID}-{lang}"
        pl = {**base, "target_lang": lang}
        if results:  # 2. dil: ilk senaryodan uyarla (video tekrar izletilmez)
            first = results[0]
            pl = {"long": True, "mode": "rewrite", "target_lang": lang, "source": base.get("source", ""),
                  "text": first["title"] + "\n" + "\n".join(
                      (f"[{sc.get('chapter')}] " if sc.get("chapter") else "") + sc["text"] for sc in first["scenes"])}
        res = make_video(pl, tag, instagram=False)
        if res.get("skip"):
            break
        results.append(res)
    if results:
        queue_or_publish(results, st, "🎬 Haftalık UZUN analiz videosu\n"
                         "A/B testi: YouTube Studio → video → 'Test ve karşılaştır' → aşağıdaki 3 küçük resmi yükle.")
    save_state(st)


def weekly_reminder(st):
    """Pazartesi günleri: ElevenLabs ve ses rötuşu hatırlatması."""
    if dt.datetime.utcnow().weekday() != 0 or st.get("reminded") == today():
        return
    st["reminded"] = today()
    if not CONFIG.get("elevenlabs_enabled", True):
        tg_send(st.get("owner_chat"), "🔔 Hatırlatma: ElevenLabs şu an kapalı (videolar ücretsiz Microsoft sesiyle).\n"
                "Kendi sesini klonlamak için sesine rötuş/kayıt hazırlığı yapmayı düşünebilirsin. "
                "Hazır olunca Claude'a 'ElevenLabs'i aç' demen yeterli.")


def cmd_tg_check():
    st = load_state()
    me = (tg("getMe") or {}).get("result") or {}
    info = (tg("getWebhookInfo") or {}).get("result") or {}
    log("Bot:", me.get("username"), "| webhook:", (info.get("url") or "yok")[:40], "| bekleyen:", info.get("pending_update_count"))
    ups = tg_updates(st.get("tg_offset", 0))
    has = any((u.get("message") or {}).get("text") or u.get("callback_query") for u in ups)
    if not has and ups:
        st["tg_offset"] = ups[-1]["update_id"] + 1
        save_state(st)
    log("Yeni Telegram mesajı/onayı:", has)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"has={'1' if has else '0'}\n")


def cmd_telegram():
    st = load_state()
    expire_pending(st)
    process_updates(st, tg_updates(st.get("tg_offset", 0)))


def cmd_listen():
    """Telegram'ı uzun süreli dinler: onay düğmesine basınca saniyeler içinde işlenir."""
    minutes = float(os.environ.get("LISTEN_MINUTES", "55"))
    end = time.time() + minutes * 60
    tg_updates(0)  # webhook varsa kaldır
    while time.time() < end - 60:
        st = load_state()
        r = tg("getUpdates", offset=st.get("tg_offset", 0), timeout=45,
               allowed_updates=["message", "callback_query"])
        if r is None:  # geçici ağ hatası: bekle ve yeniden dene
            time.sleep(10)
            continue
        ups = r.get("result") or []
        if not ups:
            continue
        log(f"{len(ups)} Telegram güncellemesi")
        expire_pending(st)
        process_updates(st, ups)
        save_state(st)
        try:
            cmd_commit_state()
        except SystemExit as e:
            log("Durum kaydı ertelendi:", e)


def process_updates(st, ups):
    # Önce tüm düğme basışlarına anında cevap ver (Telegram 15 sn içinde yanıt bekler)
    for u in ups:
        cb = u.get("callback_query")
        if cb:
            tg("answerCallbackQuery", callback_query_id=cb["id"], text="Alındı ⏳")
            m = cb.get("message") or {}
            if (m.get("chat") or {}).get("id") == st.get("owner_chat"):
                tg("editMessageReplyMarkup", chat_id=m["chat"]["id"], message_id=m.get("message_id"),
                   reply_markup={"inline_keyboard": [[{"text": "⏳ İşleniyor…", "callback_data": "noop"}]]})
    for n, u in enumerate(ups):
        st["tg_offset"] = u["update_id"] + 1
        save_state(st)  # önce offset: hata olursa aynı mesaj tekrar işlenmesin
        if u.get("callback_query"):
            try:
                handle_callback(u["callback_query"], st)
            except Exception as e:  # noqa
                log("Onay işlenemedi:", e)
                tg_send(st.get("owner_chat"), f"⚠️ Yayın hatası: {str(e)[:300]}")
            save_state(st)
            continue
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
            tg_send(chat, f"✅ Sistem çalışıyor.\nBugün otomatik: {st['daily'].get(today(), 0)}/"
                          f"{CONFIG.get('max_auto_per_day', 3)}\nOnay bekleyen: {len(st.get('pending', {}))}\n"
                          "Haber metni gönder → video. Başına /ucdil yazarsan TR + EN + DE üretilir.")
            continue
        if text.startswith("/tiktok"):
            code = text.split(maxsplit=1)[1].strip() if len(text.split()) > 1 else ""
            if not code:
                key = os.environ.get("TIKTOK_CLIENT_KEY", "")
                tg_send(chat, "TikTok hesabını bağlamak için:\nhttps://ahmetbagrrn-rgb.github.io/haber-video/tiktok-login.html"
                              + (f"?key={key}" if key else ""))
                continue
            try:
                import tiktok
                tiktok.exchange(code)
                tg_send(chat, "✅ TikTok hesabı bağlandı.")
            except Exception as e:  # noqa
                tg_send(chat, f"⚠️ TikTok bağlanamadı: {str(e)[:300]}\nKodun süresi dolmuş olabilir; /tiktok yazıp tekrar dene.")
            continue
        multi = text.startswith("/ucdil") or text.startswith("/ikidil")
        text = re.sub(r"^/(ucdil|ikidil)\s*", "", text)
        tg_send(chat, "🎬 Video hazırlanıyor, 3-5 dk sürer.")
        for _ in range(60):  # dinleyici araçları arka planda kuruyorsa bekle
            if not os.environ.get("LISTEN_MINUTES") or Path("/tmp/tools_ready").exists():
                break
            time.sleep(5)
        try:
            results = []
            langs = CONFIG.get("auto_langs", ["tr", "en", "de"]) if multi else [None]
            for lang in langs:
                payload = {"text": text, "chat_id": chat}
                if lang:
                    payload["target_lang"] = lang
                    if results:
                        payload = {"mode": "rewrite", "target_lang": lang, "chat_id": chat,
                                   "text": results[0]["title"] + ".\n" + "\n".join(sc["text"] for sc in results[0]["scenes"])}
                tag = f"tg-{RUN_ID}-{n}-{lang or 'x'}"
                res = make_video(payload, tag, instagram=(not multi or lang == CONFIG.get("instagram_lang", "tr")))
                results.append(res)
            remember_topic(st, results[0].get("topic") or results[0]["title"])
            queue_or_publish(results, st, "📝 Telegram'dan gönderdiğin haber")
            save_state(st)
        except Exception as e:  # noqa
            log("Telegram videosu üretilemedi:", e)
            tg_send(chat, f"⚠️ Video üretilemedi: {str(e)[:300]}")


def cmd_republish():
    """Hazır videoları (release etiketleriyle) yeniden yayına gönderir; yayın saati yeni slottan."""
    st = load_state()
    out = []
    for tag in sys.argv[2:]:
        d = Path("/tmp") / tag
        d.mkdir(exist_ok=True)
        subprocess.run(["gh", "release", "download", tag, "-p", "result.json", "-D", str(d), "--clobber"], check=True)
        r = json.loads((d / "result.json").read_text(encoding="utf-8"))
        slot = next_slot(r["lang"], st)
        r["publish_at"] = slot or soon()
        publish(r)
        out.append(f"• {CHANNEL_NAMES.get(r['lang'], r['lang'])}: {r['title']} → {to_tr_time(slot) if slot else 'hemen'}")
        log("Yeniden gönderildi:", tag, r["publish_at"])
    save_state(st)
    tg_send(st.get("owner_chat"), "🔁 Yeniden yayına gönderildi:\n" + "\n".join(out))


def cmd_requeue():
    """Hazır video gruplarını (etiket öneki) yeniden Telegram onayına koyar."""
    st = load_state()
    rel = json.loads(subprocess.run(["gh", "api", f"repos/{os.environ['GITHUB_REPOSITORY']}/releases?per_page=100"],
                                    capture_output=True, text=True, check=True).stdout)
    tags = [r["tag_name"] for r in rel]
    for prefix in sys.argv[2:]:
        results = []
        for lang in ("tr", "en", "de"):
            tag = f"{prefix}-{lang}"
            if tag not in tags:
                continue
            d = Path("/tmp") / tag
            d.mkdir(exist_ok=True)
            subprocess.run(["gh", "release", "download", tag, "-p", "result.json", "-D", str(d), "--clobber"], check=True)
            r = json.loads((d / "result.json").read_text(encoding="utf-8"))
            r["instagram"] = lang == CONFIG.get("instagram_lang", "tr")
            r["tag"] = tag
            results.append(r)
        if results:
            queue_or_publish(results, st, "🔁 Onayı kaybolan arşiv videosu (tekrar)")
            save_state(st)
            log("Yeniden onaya kondu:", prefix)


def merge_states(local, remote):
    """İki iş akışı aynı anda çalışırsa durum dosyalarını kayıpsız birleştirir."""
    m = dict(remote)
    m["tg_offset"] = max(local.get("tg_offset", 0), remote.get("tg_offset", 0))
    m["owner_chat"] = local.get("owner_chat") or remote.get("owner_chat")
    seen = set()
    m["processed"] = [x for x in remote.get("processed", []) + local.get("processed", [])
                      if not (x in seen or seen.add(x))][-500:]
    tk = set()
    m["topics"] = sorted([t for t in remote.get("topics", []) + local.get("topics", [])
                          if not ((t["topic"], t["ts"]) in tk or tk.add((t["topic"], t["ts"])))],
                         key=lambda t: t["ts"])[-200:]
    m["daily"] = {d: max(local.get("daily", {}).get(d, 0), remote.get("daily", {}).get(d, 0))
                  for d in set(local.get("daily", {})) | set(remote.get("daily", {}))}
    m["daily"] = {d: m["daily"][d] for d in sorted(m["daily"])[-3:]}
    sch = {}
    for src in (remote.get("scheduled", {}), local.get("scheduled", {})):
        for lang, xs in src.items():
            sch[lang] = sorted(set(sch.get(lang, [])) | set(xs))[-60:]
    m["scheduled"] = sch
    removed = set(local.get("_removed", [])) | set(remote.get("_removed", []))
    pend = {**remote.get("pending", {}), **local.get("pending", {})}
    m["pending"] = {k: v for k, v in pend.items() if k not in removed}
    m["_removed"] = list(removed)[-100:]
    m["reminded"] = max(local.get("reminded") or "", remote.get("reminded") or "")
    ys = {}
    for src in (remote.get("yt_stories", {}), local.get("yt_stories", {})):
        for vid, rec in src.items():
            cur = ys.setdefault(vid, {"stories": rec.get("stories", []), "used": []})
            cur["used"] = sorted(set(cur["used"]) | set(rec.get("used", [])))
    m["yt_stories"] = dict(list(ys.items())[-20:])
    lw = {}
    for src in (remote.get("lang_week", {}), local.get("lang_week", {})):
        for lg, xs in src.items():
            lw[lg] = sorted(set(lw.get(lg, [])) | set(xs))[-20:]
    m["lang_week"] = lw
    return m


def cmd_commit_state():
    """Durumu git'e yazar; uzak sürümle birleştirip çakışmasız gönderir."""
    if not STATE_FILE.exists():
        return
    local = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    tt = ROOT / "state" / "tiktok.enc"
    tt_bytes = tt.read_bytes() if tt.exists() else None
    for attempt in range(5):
        subprocess.run(["git", "fetch", "-q", "origin", "main"], check=True)
        try:
            remote = json.loads(subprocess.run(["git", "show", "origin/main:state/state.json"],
                                               capture_output=True, text=True, check=True).stdout)
        except Exception:  # noqa
            remote = {}
        merged = merge_states(local, remote)
        subprocess.run(["git", "checkout", "-q", "-f", "-B", "main", "origin/main"], check=True)
        STATE_FILE.parent.mkdir(exist_ok=True)
        STATE_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
        subprocess.run(["git", "add", str(STATE_FILE)], check=True)
        if tt_bytes is not None:
            tt.write_bytes(tt_bytes)
            subprocess.run(["git", "add", str(tt)], check=True)
        if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
            log("Durum değişmedi")
            return
        subprocess.run(["git", "commit", "-qm", "Durum güncellendi [skip ci]"], check=True)
        if subprocess.run(["git", "push", "-q", "origin", "main"]).returncode == 0:
            log("Durum kaydedildi")
            return
        import time
        time.sleep(3 + attempt * 3)
    raise SystemExit("Durum kaydedilemedi")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"auto": cmd_auto, "tg-check": cmd_tg_check, "telegram": cmd_telegram, "listen": cmd_listen, "republish": cmd_republish, "requeue": cmd_requeue,
     "commit-state": cmd_commit_state}.get(cmd, lambda: sys.exit(
        "Kullanım: pipeline.py auto | tg-check | telegram"))()
