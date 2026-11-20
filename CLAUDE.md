# haber-video çalışma kuralları

World Shift haber kanalları (TR / EN / DE) için Telegram → GitHub Actions → Make → YouTube / Instagram / TikTok video hattı. Ana dosyalar: `pipeline.py`, `render.py`, `config.json`, `.github/workflows/`.

1. Cevapları, durum güncellemelerini ve commit mesajlarını kısa ve açık Türkçe yaz.
2. Depo herkese açık. API anahtarı, token veya webhook adresini hiçbir dosyaya, commit'e, log satırına ya da cevaba yazma; yalnız `${{ secrets.AD }}` ve ortam değişkeni kullan. `state/tiktok.enc` dosyasını açmaya veya içeriğini yazdırmaya çalışma.
3. İş akışlarını (`auto`, `telegram`, `render`, `republish`, `tiktok-*`) elle çalıştırma, `repository_dispatch` gönderme. Bunlar gerçek video üretip yayınlayabilir; test gerekirse komutu ve beklenen sonucu yazıp onay iste.
4. `config.json` içinde `"approval": true` değerini kapatma; her video Telegram onayından geçmeye devam etmeli. Yayın saatleri, günlük limitler, diller ve TikTok gizlilik ayarı yalnız açıkça istendiğinde değişir.
5. YouTube yüklemelerinde YZ içerik etiketini (`containsSyntheticMedia` = evet) kaldırma veya kapatma.
6. Haber metninde kaynağa sadık kal: kaynakta olmayan bilgi, rakam, isim veya alıntı ekleme. Emin olunmayan her şeyi `[DOĞRULANACAK]` diye işaretle.
7. `assets/` (logolar, outro, b-roll) ve `state/state.json` dosyalarını elle düzenleme veya silme; durum dosyası yalnız kodun kendisi tarafından güncellenir.
8. Kod değiştirdikten sonra en az `python -m py_compile pipeline.py render.py` çalıştır. `main` dalına push etmeden önce neyin değiştiğini iki üç satırda özetle ve onay al.
