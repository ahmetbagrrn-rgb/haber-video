"""Eski ve yeni promptla aynı kaynaktan senaryo üretip Telegram'a karşılaştırma gönderir."""
import importlib.util, json, os, sys, xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline, render

def load_old(path):
    spec = importlib.util.spec_from_file_location("render_old", path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

old = load_old(sys.argv[1])
ch = pipeline.CONFIG["youtube_channels"][0]["channel_id"]
root = ET.fromstring(pipeline.fetch(f"https://www.youtube.com/feeds/videos.xml?channel_id={ch}"))
ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
e = root.find("a:entry", ns)
url = "https://www.youtube.com/watch?v=" + e.findtext("yt:videoId", namespaces=ns)
title = e.findtext("a:title", namespaces=ns)
print("Kaynak:", title, url)
out = {}
for name, mod in (("ESKİ", old), ("YENİ", render)):
    for lang in ("tr", "en"):
        pl = {"mode": "rewrite", "youtube_url": url, "target_lang": lang, "source": url}
        try:
            d = mod.make_script(pl)
        except SystemExit as ex:
            d = {"error": str(ex)}
        out[(name, lang)] = d
        print("=====", name, lang); print(json.dumps(d, ensure_ascii=False, indent=1))

def fmt(d):
    if d.get("skip"): return "(atlandı: niş dışı / haber değil)"
    if d.get("error"): return "(hata) " + d["error"][:200]
    sc = d.get("scenes", [])
    words = sum(len(s.get("text", "").split()) for s in sc)
    return (f"<b>{d.get('title','')}</b>\n" + "\n".join(f"{i+1}. {s.get('text','')}" for i, s in enumerate(sc))
            + f"\n<i>{len(sc)} sahne · {words} kelime · ~{round(words/2.6)} sn</i>\n{d.get('description','')}")

import html as H
for lang in ("tr", "en"):
    msg = f"🧪 Prompt karşılaştırma ({lang.upper()})\nKaynak: {H.escape(title)}\n\n🔸 ESKİ\n{fmt(out[('ESKİ',lang)])}\n\n🔹 YENİ\n{fmt(out[('YENİ',lang)])}"
    st = pipeline.load_state()
    if st.get("owner_chat") and os.environ.get("TELEGRAM_BOT_TOKEN"):
        pipeline.tg("sendMessage", {"chat_id": st["owner_chat"], "text": msg[:4000], "parse_mode": "HTML",
                                    "disable_web_page_preview": True})
