import os, json, render
q = render.elevenlabs_quota()
print("ELEVENLABS:", json.dumps(q) if q else "okunamadı")
