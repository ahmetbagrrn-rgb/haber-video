# haber-video çalışma kuralları

World Shift haber kanalları (TR / EN / DE) için Telegram → GitHub Actions → Make → YouTube / Instagram / TikTok video hattı. Ana dosyalar: `pipeline.py`, `render.py`, `config.json`, `.github/workflows/`.

1. Cevapları, durum güncellemelerini ve commit mesajlarını kısa ve açık Türkçe yaz.
2. Depo herkese açık. API anahtarı, token veya webhook adresini hiçbir dosyaya, commit'e, log satırına ya da cevaba yazma; yalnız `${{ secrets.AD }}` ve ortam değişkeni kullan. `state/tiktok.enc` dosyasını açmaya veya içeriğini yazdırmaya çalışma.
3. İş akışlarını (`auto`, `telegram`, `render`, `republish`, `tiktok-*`) elle çalıştırma, `repository_dispatch` gönderme. Bunlar gerçek video üretip yayınlayabilir; test gerekirse komutu ve beklenen sonucu yazıp onay iste.
4. `config.json` içinde `"approval": true` değerini kapatma; her video Telegram onayından geçmeye devam etmeli. Yayın saatleri, günlük limitler, diller ve TikTok gizlilik ayarı yalnız açıkça istendiğinde değişir.
5. YouTube yüklemelerinde YZ içerik etiketini (`containsSyntheticMedia` = evet) kaldırma veya kapatma.
6. Haber metninde kaynağa sadık kal: kaynakta olmayan bilgi, rakam, isim veya alıntı ekleme. Emin olunmayan her şeyi `[DOĞRULANACAK]` diye işaretle.
7. `assets/` (logolar, outro, b-roll) ve `state/state.json` dosyalarını elle düzenleme veya silme; durum dosyası yalnız kodun kendisi tarafından güncellenir.
8. İSTİSNA — yalnız "Haber otomasyonu sağlık kontrolü" zamanlanmış görevi ve kullanıcının Telegram'dan istediği `check` raporu için, onay sormadan şunlar yapılabilir:
   - Raporu Telegram'a göndermek için `tg_send` `repository_dispatch` göndermek (yalnız düz metin rapor).
   - Son 70 dakikada çalışma yoksa `telegram.yml` dinleyicisini `workflow_dispatch` ile başlatmak.
   - Kullanıcının Telegram'da ZATEN onayladığı, ama bazı dillerde Make'e ulaşmamış videolar için `republish` göndermek. Onay bekleyen videoyu asla yayınlama.
   - `state/state.json` içinde yalnız işlenen talepleri `check_requests` listesinden çıkarıp ts değerlerini `_check_done` listesine eklemek. Başka alana dokunma; `git pull --rebase` sonrası push et.
   Bunların dışında 3. ve 7. kurallar aynen geçerli: `auto`, `render`, `tiktok-*` elle çalıştırılmaz, başka `repository_dispatch` gönderilmez.
9. Kod değiştirdikten sonra en az `python -m py_compile pipeline.py render.py` çalıştır. `main` dalına push etmeden önce neyin değiştiğini iki üç satırda özetle ve onay al.
10. Çalışma düzeni:
   - Önce görevle ilgili dosyaları belirle ve yalnızca gerekli bölümleri oku.
   - Değişiklikten sonra ilgili testleri çalıştır. Hataları ve sonucu kısa özetle.
   - Uzun loglarda önce hata satırlarını ve çevresindeki gerekli satırları incele.
   - Son yanıtta yapılan değişikliği, doğrulamayı ve açık kalan sorunu yaz.
