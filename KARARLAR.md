# Kararlar

tarih | sorun | ölçüm | yapılan değişiklik | geri alma yolu
---|---|---|---|---
2026-11-21 | Kullanıcı talebi: haber ağı durduruldu, öncelik Küçük Mucitler | — | auto.yml devre dışı, sağlık kontrolü kapalı; Telegram dinleyici açık, planlı YouTube yayınları sürüyor | auto.yml/enable + sağlık kontrolünü aç
2026-11-21 | Kullanıcı: haberlere devam | Üretim Claude kredisi harcamıyor | auto.yml yeniden açık; sağlık kontrolü (Claude kredisi harcar) kapalı kalıyor | auto.yml/disable
2026-11-27 | Türkçe seslendirme kalitesi düşük (edge Ahmet) | 7 ses örneği aynı metinle karşılaştırıldı, kullanıcı 5'i seçti | TR sesi ElevenLabs "Murat" (dgeCtiGkvIwzoR09qzjl, eleven_multilingual_v2); elevenlabs_langs=["tr","en"]; EN için 8000 karakter rezerv (kredi azalınca EN önce ücretsiz sese döner, TR korunur); kota bitince otomatik edge yedeği sürüyor | config.json: elevenlabs_langs=["en"] yap, elevenlabs_voices/elevenlabs_reserve sil
2026-11-27 | Facebook Reel yayını sessizce başarısızdı (Ignore handler gizliyordu) | Test: "(#200) pages_manage_posts izni yok"; Instagram testi başarılı | Kullanıcı Facebook'u yeniden yetkilendirdi (yeni bağlantı 13479903, sayfa "dünyadan haberler"); test Reel başarılı; ana senaryoda FB modülü (52) yeni bağlantıya alındı | Modül 52 bağlantısını 13069469'a geri al
