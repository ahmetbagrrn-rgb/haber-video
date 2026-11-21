# Yedekler — hangi adım çökerse ne devreye girer

| Adım | Ana yol | Çökerse sırayla devreye giren yedek(ler) | Hiçbiri olmazsa |
|---|---|---|---|
| Haber metni / senaryo (metin kaynağı) | Gemini (anahtar 1, 3 model) | Gemini anahtar 2 (ve 3) → OpenRouter `nvidia/nemotron-3-ultra-550b-a55b:free` → Groq (anahtar varsa) → Make webhook `task=metin` (Nemotron → Make içinde Gemini 2.5 Flash) | Video üretilmez, Telegram'a hata yazılır |
| YouTube videosundan konu/senaryo | Gemini video analizi (tüm anahtarlar) | Yok — video yalnız Gemini ile izlenebiliyor; 10 dk Gemini'yi atlayıp RSS/haber kaynaklarına geçilir | O konu atlanır |
| Doğrulama ve konu seçimi (`llm_json`) | Gemini | Aynı zincir: Gemini 2 → OpenRouter Nemotron → Groq → Make `task=metin` | Doğrulama atlanır, senaryo olduğu gibi kalır |
| TR → EN / DE çeviri | Ücretsiz Nemotron: OpenRouter (anahtar varsa) → Make webhook `task=metin` | Çıktı boş / bozuk / yanlış dil / Almancada "du" / kelime sınırı (EN 90, DE 75) aşılırsa → eski yol: hedef dile yeniden yazım (yukarıdaki metin zinciri) | O dil atlanır, diğer diller devam eder |
| Seslendirme | ElevenLabs (config'teki dillerde) | Kota yetersiz ya da hata → ücretsiz Microsoft edge-tts (dil başına 2-3 ses, 2'şer deneme) | Video üretilmez |
| Make'e yayın gönderimi (onaydan SONRA) | Make webhook | 2 kez yeniden dene (15 sn, 45 sn) → `state.json` → `yayin_bekliyor` kuyruğu; `republish` (etiketsiz) kuyruğu tekrar gönderir, 5 denemeden sonra çıkarır | Telegram'a bildirilir |
| Onay | Telegram ✅/❌ | **Yedek yok, kural değişmez:** onaysız hiçbir video gönderilmez; 48 saatte onaylanmayan düşer (`sure_dolan`) | — |
| Durum kaydı | `commit-state` (birleştirerek push) | 5 deneme, her seferinde uzak sürümle birleştirme (metrics ve `yayin_bekliyor` dahil) | İş akışı hata verir, bir sonraki çalışma yeniden dener |

Her yapay zekâ denemesinin zaman aşımı en fazla 90 sn'dir; 429/5xx/zaman aşımı kalıcı hata sayılmaz, sıradaki sağlayıcıya geçilir.
Ölçüm: `state.json` → `metrics.gunluk` (son 7 gün). Telegram'da `check` yazınca onay oranı, yedeğe düşme oranı, günlük ortalama video ve en çok hata veren sağlayıcı görünür.
