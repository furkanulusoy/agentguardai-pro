# Security Policy

AgentGuard AI Pro güvenlik sınırında çalışan bir üründür. Güvenlik açıklarını public issue, discussion, log çıktısı veya ekran görüntüsü içinde paylaşmayın.

## Desteklenen sürümler

| Sürüm | Güvenlik düzeltmeleri |
|---|---|
| `0.4.x` | Destekleniyor |
| `< 0.4` | Desteklenmiyor |

Proje `1.0` öncesi pilot olgunluğundadır. Bağımsız üçüncü taraf güvenlik denetimi ve penetrasyon testi henüz tamamlanmamıştır.

## Güvenlik açığı bildirme

Repository'nin **Security → Report a vulnerability** akışından private vulnerability report oluşturun. Bu özellik kullanılamıyorsa repository sahibine GitHub profili üzerinden özel olarak ulaşın. İlk iletişimde gerçek OAuth token, API key, cookie, kişisel veri veya müşteri verisi göndermeyin.

Rapor şunları içermelidir:

- Etkilenen commit veya sürüm
- Minimum ve güvenli yeniden üretim adımları
- Beklenen ve gerçekleşen davranış
- Tenant isolation, authorization, approval veya provider yan etkisi üzerindeki olası etki
- Varsa secretsiz test kodu ya da redacted kanıt

Rapor doğrulanana ve düzeltme yayımlanana kadar ayrıntıları gizli tutun. Projenin sözleşmesel bir yanıt süresi yoktur; durum değişiklikleri private advisory üzerinden paylaşılır.

## Kapsam

- `apps/api`, `apps/worker` ve `infrastructure`
- `connectors/gmail`, `connectors/github`, `connectors/slack`
- `apps/web`
- `sdk/python`, `sdk/typescript` ve `apps/mcp_server`
- `agentguard` core engine
- Docker ve self-host yapılandırması

`mock_services`, `demo` ve legacy `server` güvenlik eğitim/reference alanlarıdır. Bu alanlardaki kasıtlı demo davranışı aktif platform açığı değildir; ancak aktif platform sınırını aşan veya üretim yoluna taşınabilen bir davranış yine özel olarak bildirilmelidir.

## Ürün güvenlik sınırı

AgentGuard yalnız kendi enforcement API'sinden geçen çağrıları kontrol eder. Agent'a doğrudan provider credential verilmesi bu sınırı baypas eder. Provider desteği olmadan tüm dış servislerde evrensel exactly-once garantisi yoktur; `UNKNOWN` sonuçlar otomatik yeniden yürütülmez. Bilinen sınırlar ve kalan kontroller [güvenlik incelemesinde](docs/SECURITY_REVIEW.md) belgelenmiştir.
