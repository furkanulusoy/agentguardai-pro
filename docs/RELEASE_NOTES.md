# AgentGuard pilot sürümü — doğrulama notları

Tarih: 2026-10-05

Bu sürüm, AgentGuard'ın açıklanabilir policy kararı, insan onayı ve denetim kaydıyla
AI agent eylemlerini yöneten self-hosted pilot sürümüdür. Bu belge yalnız doğrulanan
davranışları listeler; compliance sertifikası veya internet-facing enterprise üretim
onayı değildir.

## Çalıştırma

Windows ve Docker Desktop üzerinde proje kökünde:

~~~powershell
pwsh -File scripts/start-local.ps1
~~~

Dashboard: http://localhost:5000

Hazır hesap veya varsayılan parola yoktur. İlk kullanıcı **Kayıt olun** ekranından kendi
workspace'ini oluşturur. API ve PostgreSQL yalnız compose ağı içindedir; dışarıya sadece
`127.0.0.1:5000` yayınlanır. `.env` okunmaz veya oluşturulmaz.

## Teslim edilen güvenlik davranışları

- Agent permission, connector grant, kaynak kapsamı ve policy kararı tek
  `services/governance.authorize` enforcement noktasında değerlendirilir.
- Agent connector, approval ve operasyon sonuçları tenant ve agent kimliğiyle izole edilir.
- Tenant `DENY` kararı agent override'ından üstündür; bilinmeyen eylem ve kaynak reddedilir.
- Gmail, GitHub ve Slack action parametreleri strict şemalarla provider çağrısından önce
  doğrulanır.
- Connector eylemleri aktör-scoped idempotency anahtarıyla kalıcı `Operation` kaydına alınır.
- PostgreSQL-backed worker, API kesintisinde claim edilmeden kalan yalnız `READY` işleri aynı
  authorization ve atomic claim yolu üzerinden kurtarır.
- Dış I/O öncesi `EXECUTING` claim commit edilir. Belirsiz provider sonucu `UNKNOWN` olur ve
  otomatik tekrar çalıştırılmaz.
- Onay sırasında güncel agent, credential, grant ve policy tekrar değerlendirilir. İnsan
  onayı, güvenli review endpoint'inden alınan payload hash'ine bağlanır.
- Refresh token yalnız `HttpOnly` cookie'dedir. Access token browser belleğinde tutulur;
  rotation ve eşzamanlı refresh yarışı korunur.
- Audit ve HTTP log yüzeyleri connector payload değerlerini, tokenları ve provider exception
  içeriklerini yazmaz. Yönetim değişiklikleri sabit actor/tenant/target metadata'sıyla kaydedilir.
- OAuth state süreli, browser nonce'una bağlı ve tek kullanımlı DB kaydıdır.
- Yerel profil non-root API, private PostgreSQL/API, gerçek DB health kontrolü, CSP,
  frame engelleme, `nosniff`, request body sınırı ve secret dosyaları kullanır.

## Son doğrulama sonuçları

| Kontrol | Sonuç |
|---|---:|
| Gerçek PostgreSQL tam Python suite | 322 geçti; 0 failure; 0 error; 0 skip |
| `READY` recovery worker concurrency/reauthorization | 2 geçti |
| Son auth/governance odaklı PostgreSQL suite | 62 geçti |
| Frontend Vitest | 11 geçti |
| Frontend TypeScript + production build | Geçti |
| TypeScript SDK Vitest | 10 geçti |
| TypeScript SDK build + örnek tip kontrolü | Geçti |
| Ruff: core, platform, connector, SDK ve testler | Geçti |
| Mypy: platform/core | 66 dosya, sorun yok |
| Mypy: Python SDK | 5 dosya, sorun yok |
| Frontend ve TypeScript SDK `npm audit` | Bilinen açık yok |
| Kaynak kod secret taraması | Geçti; CI kapısı eklendi |
| API production image `pip-audit` | Bilinen bağımlılık açığı yok |
| MCP server import/startup sözleşmesi | Geçti |
| CI YAML yapı kontrolü | 6 job geçerli |
| `localhost:5000/api/health` | HTTP 200, PostgreSQL connected |
| Masaüstü ve mobil browser QA | Geçti; mobil yatay taşma giderildi |
| API ve worker container image kimliği | Aynı image; eski worker riski giderildi |

Python dependency taraması, PyPI'da yayınlanmamış yerel `agentguard` paketinin kendi kaynak
kodunu taramaz; kaynak kodu lint, tip kontrolü ve testlerle doğrulanmıştır. GitHub Actions
iş akışı güncellenmiştir fakat bu çalışma kopyasından uzak GitHub runner'ında henüz
çalıştırılmamıştır.

## Bilinen pilot sınırları

- Google, GitHub ve Slack gerçek OAuth credential'larıyla canlı kabul testi yapılmadı.
- PostgreSQL-backed `READY` recovery worker vardır; ayrı broker, notification outbox ve provider
  reconciliation yoktur. `UNKNOWN` sonucu operasyon ekibi provider'dan doğrulamalıdır.
- Local Fernet anahtarı vardır; KMS/Vault envelope encryption, rotation ve rewrap yoktur.
- Tenant retention/purge worker, eski plaintext kayıt temizliği ve WORM audit export yoktur.
- OIDC/SAML SSO, SCIM, MFA ve session inventory yoktur.
- Merkezi metrics/tracing/alerting, dağıtık rate limit, çoklu replica, backup/restore tatbikatı
  ve disaster recovery kanıtı yoktur.
- AgentGuard yalnız kendi üzerinden geçirilen çağrıları enforce eder. Credential'ın platform
  dışında kullanılmasını mevcut sürüm algılayamaz.

Bu sınırlar giderilmeden ürün internet-facing çok tenantlı enterprise SaaS veya compliance
hazır ürün olarak sunulmamalıdır. Mevcut sürüm kontrollü, az tenantlı self-hosted pilot ve
ürün kabul çalışmaları için uygundur.
