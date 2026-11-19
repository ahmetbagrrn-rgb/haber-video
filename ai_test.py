"""Gemini 2. anahtarın Pro model erişimini test eder (anahtar yazdırılmaz)."""
import os, render
P = 'Sadece şu JSON\'u döndür: {"ok": true}'
for i, key in enumerate(render.gemini_keys()):
    ms = render.http_json("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200", headers={"x-goog-api-key": key})
    names = [m["name"].split("/")[1] for m in ms.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
    pros = [n for n in names if "pro" in n]
    print(f"ANAHTAR {i+1} PRO MODELLER:", ", ".join(pros))
    for model in pros[:4]:
        try:
            render.http_json(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                             {"contents": [{"parts": [{"text": P}]}], "generationConfig": {"responseMimeType": "application/json"}},
                             {"x-goog-api-key": key}, timeout=90)
            print(f"ANAHTAR {i+1} / {model}: ÇALIŞIYOR")
        except Exception as e:
            body = e.read().decode()[:160] if hasattr(e, "read") else ""
            print(f"ANAHTAR {i+1} / {model}: HATA {str(e)[:60]} {body}")
