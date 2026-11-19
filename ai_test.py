"""Yapay zekâ anahtarlarını test eder (değerleri yazdırmaz)."""
import os, json, render
P = 'Sadece şu JSON\'u döndür: {"ok": true}'
for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
    print(f"{name}: {'VAR' if os.environ.get(name) else 'YOK'}")
for i, key in enumerate(render.gemini_keys()):
    os.environ["_K"] = key
    for model in ["gemini-3.5-flash-lite", "gemini-3.5-flash"]:
        try:
            r = render.http_json(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                 {"contents": [{"parts": [{"text": P}]}], "generationConfig": {"responseMimeType": "application/json"}},
                                 {"x-goog-api-key": key}, timeout=60)
            print(f"Gemini anahtar {i+1} / {model}: ÇALIŞIYOR")
            break
        except Exception as e:
            print(f"Gemini anahtar {i+1} / {model}: HATA {str(e)[:80]}")
for env, url, models in render.ALT_PROVIDERS:
    key = os.environ.get(env)
    if not key:
        continue
    for m in models:
        try:
            r = render.http_json(url, {"model": m, "messages": [{"role": "user", "content": P}]},
                                 {"Authorization": f"Bearer {key}"}, timeout=90)
            print(f"{env} / {m}: ÇALIŞIYOR -> {r['choices'][0]['message']['content'][:40]!r}")
        except Exception as e:
            body = getattr(e, "read", lambda: b"")().decode()[:200] if hasattr(e, "read") else ""
            print(f"{env} / {m}: HATA {str(e)[:80]} {body}")
if os.environ.get("OPENROUTER_API_KEY"):
    ms = render.http_json("https://openrouter.ai/api/v1/models", timeout=60)["data"]
    free = [m["id"] for m in ms if str(m.get("pricing", {}).get("prompt")) in ("0", "0.0") and str(m.get("pricing", {}).get("completion")) in ("0", "0.0")]
    print("ÜCRETSİZ MODELLER:", ", ".join(free))
