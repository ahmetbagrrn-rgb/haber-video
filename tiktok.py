"""
TikTok Content Posting API (Direct Post) entegrasyonu.

Gerekli GitHub secret'ları: TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET
Token'lar state/tiktok.enc içinde, TIKTOK_CLIENT_SECRET ile AES-256 şifreli saklanır.

  python tiktok.py exchange <code>   -> giriş kodunu token'a çevirir ve kaydeder
  python tiktok.py test <video.mp4>  -> deneme paylaşımı (gizli)
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
TOKEN_FILE = ROOT / "state" / "tiktok.enc"
REDIRECT = "https://ahmetbagrrn-rgb.github.io/haber-video/tiktok-callback.html"
API = "https://open.tiktokapis.com/v2"


def log(*a):
    print("[tiktok]", *a, flush=True)


def _openssl(args, data):
    pw = os.environ["TIKTOK_CLIENT_SECRET"]
    r = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-a", "-A",
                        *args, "-pass", "env:TT_PW"], input=data, capture_output=True,
                       env={**os.environ, "TT_PW": pw}, check=True)
    return r.stdout


def save_tokens(tok):
    TOKEN_FILE.parent.mkdir(exist_ok=True)
    tok["saved_at"] = int(time.time())
    TOKEN_FILE.write_bytes(_openssl([], json.dumps(tok).encode()))


def load_tokens():
    if not TOKEN_FILE.exists() or not os.environ.get("TIKTOK_CLIENT_SECRET"):
        return None
    return json.loads(_openssl(["-d"], TOKEN_FILE.read_bytes()))


def _post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def _api(path, token, body):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json; charset=UTF-8"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"TikTok {path} {e.code}: {e.read().decode(errors='ignore')[:400]}")


def exchange(code):
    r = _post_form(API + "/oauth/token/", {
        "client_key": os.environ["TIKTOK_CLIENT_KEY"], "client_secret": os.environ["TIKTOK_CLIENT_SECRET"],
        "code": code, "grant_type": "authorization_code", "redirect_uri": REDIRECT})
    if "access_token" not in r:
        raise RuntimeError(f"Token alınamadı: {r}")
    r["obtained_at"] = int(time.time())
    save_tokens(r)
    log("TikTok hesabı bağlandı, open_id:", r.get("open_id", "")[:6] + "…")
    return r


def access_token():
    tok = load_tokens()
    if not tok:
        raise RuntimeError("TikTok hesabı bağlı değil")
    if time.time() < tok["obtained_at"] + tok.get("expires_in", 86400) - 600:
        return tok["access_token"]
    r = _post_form(API + "/oauth/token/", {
        "client_key": os.environ["TIKTOK_CLIENT_KEY"], "client_secret": os.environ["TIKTOK_CLIENT_SECRET"],
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
    if "access_token" not in r:
        raise RuntimeError(f"Token yenilenemedi: {r}")
    r["obtained_at"] = int(time.time())
    save_tokens({**tok, **r})
    return r["access_token"]


def post_video(path, title, privacy=None):
    """Videoyu doğrudan TikTok hesabına yükler. Denetimden (audit) geçmemiş uygulamada sadece SELF_ONLY olur."""
    token = access_token()
    info = _api("/post/publish/creator_info/query/", token, {}).get("data", {})
    options = info.get("privacy_level_options") or ["SELF_ONLY"]
    privacy = privacy or os.environ.get("TIKTOK_PRIVACY") or "SELF_ONLY"
    if privacy not in options:
        privacy = "SELF_ONLY" if "SELF_ONLY" in options else options[0]
    size = Path(path).stat().st_size
    init = _api("/post/publish/video/init/", token, {
        "post_info": {"title": title[:2000], "privacy_level": privacy,
                      "disable_duet": False, "disable_comment": False, "disable_stitch": False},
        "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1}})
    data = init.get("data") or {}
    if not data.get("upload_url"):
        raise RuntimeError(f"TikTok yükleme başlatılamadı: {init}")
    req = urllib.request.Request(data["upload_url"], data=Path(path).read_bytes(), method="PUT",
                                 headers={"Content-Type": "video/mp4",
                                          "Content-Range": f"bytes 0-{size - 1}/{size}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        log("Yükleme:", r.status)
    pid = data.get("publish_id")
    for _ in range(20):
        time.sleep(6)
        st = _api("/post/publish/status/fetch/", token, {"publish_id": pid}).get("data", {})
        log("Durum:", st.get("status"))
        if st.get("status") in ("PUBLISH_COMPLETE", "FAILED"):
            if st.get("status") == "FAILED":
                raise RuntimeError(f"TikTok paylaşımı başarısız: {st.get('fail_reason')}")
            break
    return {"publish_id": pid, "privacy": privacy}


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "exchange":
        exchange(sys.argv[2])
    elif len(sys.argv) >= 3 and sys.argv[1] == "test":
        print(post_video(sys.argv[2], "World Shift test"))
    else:
        sys.exit(__doc__)
