# Ücretsiz Haber Videosu Otomasyonu

Telegram → Make → **GitHub Actions** (Gemini + ses + Pixabay + FFmpeg) → Make → YouTube / Instagram

Her video: 5-7 sahne, her sahnede ayrı stok video, büyük animasyonlu başlık, kelime kelime altyazı, üstte ilerleme çubuğu, isteğe bağlı müzik. 1080×1920, 30 fps.

| Parça | Ücret |
|---|---|
| GitHub Actions (açık / public repo) | Ücretsiz |
| Gemini API (Google AI Studio) | Ücretsiz katman |
| Pixabay video API | Ücretsiz |
| Ses | ElevenLabs (üyeliğin) veya ücretsiz Microsoft sesi (`edge`) |
| Make | Ücretsiz plan (video başına ~7 işlem) |

---

## 1. GitHub deposu

1. github.com → **New repository** → ad: `haber-video` → **Public** → *Create*.
2. **Add file → Upload files** → `render.py` ve `README.md` dosyalarını yükle → *Commit*.
3. **Add file → Create new file** → dosya adı kutusuna tam olarak şunu yaz:
   `.github/workflows/render.yml`
   İçine `render.yml` dosyasının içeriğini yapıştır → *Commit*.
4. (İsteğe bağlı) Arka plan müziği: `assets` klasörüne Pixabay Music'ten indirdiğin 1-3 adet `.mp3` yükle. Her videoda rastgele biri, düşük seste çalar.

## 2. Anahtarlar

Repo → **Settings → Secrets and variables → Actions**

**Secrets** sekmesi → *New repository secret*:

| Ad | Nereden |
|---|---|
| `GEMINI_API_KEY` | aistudio.google.com → Get API key |
| `PIXABAY_API_KEY` | pixabay.com/api/docs (giriş yapınca sayfada yazar) |
| `ELEVENLABS_API_KEY` | ElevenLabs → Profile → API Keys |
| `ELEVENLABS_VOICE_ID` | ElevenLabs → Voices → sesin yanındaki ID |
| `MAKE_WEBHOOK_URL` | 4. adımda oluşturacağız |

**Variables** sekmesi (isteğe bağlı):

| Ad | Örnek | Ne işe yarar |
|---|---|---|
| `CHANNEL_NAME` | `@kanaladi` | Sağ üstte kanal adı |
| `TTS_PROVIDER` | `edge` | ElevenLabs kredisi harcamadan ücretsiz Microsoft sesi |
| `EDGE_VOICE` | `tr-TR-EmelNeural` | Kadın ses (varsayılan `tr-TR-AhmetNeural`) |
| `GEMINI_MODEL` | `gemini-3.5-flash` | Boş bırak: sistem çalışan bir flash modelini kendisi seçer |

ElevenLabs anahtarı yoksa sistem otomatik olarak ücretsiz Microsoft sesini kullanır.

## 3. İlk test (Make olmadan)

Repo → **Actions** → *Haber videosu üret* → **Run workflow** → haber metnini yapıştır → *Run*.
3-5 dakika sonra repo ana sayfasında sağdaki **Releases** bölümünde `video.mp4` çıkar.
Hata olursa Actions'taki kırmızı adıma dokun, son satırları bana gönder.

## 4. Make

### Senaryo A: Telegram → GitHub

1. **Telegram Bot → Watch Updates** (mevcut modülün).
2. **JSON → Create JSON** → *Data structure: Add* → *Generator* → şunu yapıştır:
   `{"event_type":"render","client_payload":{"text":"örnek","chat_id":"1"}}`
   Alanlar: `event_type` = `render`, `text` = Telegram mesaj metni, `chat_id` = Telegram chat ID.
3. **HTTP → Make a request**
   - URL: `https://api.github.com/repos/KULLANICI_ADIN/haber-video/dispatches`
   - Method: `POST`
   - Headers:
     - `Authorization` = `Bearer GITHUB_TOKEN`
     - `Accept` = `application/vnd.github+json`
   - Body type: *Raw*, Content type: *JSON*, Request content: 2. modülün JSON çıktısı.

**GITHUB_TOKEN:** GitHub → Settings → Developer settings → *Fine-grained tokens* → *Generate new token* → Repository access: sadece `haber-video` → Permissions: **Contents: Read and write** → oluştur, kopyala.

Başarılı istek **204** döner (boş cevap normaldir).

### Senaryo B: Video hazır → Yayın

1. **Webhooks → Custom webhook** → *Add* → çıkan URL'yi kopyala → GitHub'da `MAKE_WEBHOOK_URL` secret'ı olarak kaydet.
   Bir kere elle test videosu üret ki Make gelen veri yapısını öğrensin.
2. **HTTP → Get a file** → URL: `video_url`
3. **YouTube → Upload a video** → dosya: 2. modül, başlık: `title`, açıklama: `description`, gizlilik: Public.
4. **Instagram for Business → Create a Reel post** → Video URL: `video_url`, açıklama: `description`.
5. (İsteğe bağlı) **Telegram → Send a message** → `chat_id`'ye "Yayınlandı ✅".

Webhook'a gelen alanlar: `title`, `description`, `hashtags`, `duration`, `video_url`, `thumb_url`, `scenes`, `chat_id`.

---

## Notlar

- Repo açık olduğu için kod ve videolar herkese görünür; **anahtarlar Secrets'ta gizli kalır**.
- Sahne sayısı, renkler ve yazı boyutları `render.py` içinde (en üstteki renk satırları ve `PROMPT`).
- Gemini'nin seçtiği arama terimine uygun video bulunamazsa sahne, renkli hareketli bir arka planla devam eder.
- Microsoft sesi (`edge`) resmi olmayan bir yol; bir gün çalışmazsa `TTS_PROVIDER` değişkenini silerek ElevenLabs'e dönebilirsin.
