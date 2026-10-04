# Güvenlik ve ürün incelemesi — 2026-09-15

Kapsam: yalnız agentguard-withchatgpt çalışma kopyası. Bu belge kod incelemesi ve yerel test sonuçlarına dayanır; bağımsız pentest, canlı provider kabulü veya compliance sertifikası değildir. Doğrulanmış P0 bulunmadı; bu ifade sıfır açık garantisi değildir.

## Mimari

FastAPI + async SQLAlchemy + PostgreSQL + Alembic platform; React/Vite dashboard; senkron connector çağrıları threadpool'da; Python/TypeScript SDK ve MCP yalnız platform HTTP sözleşmesini kullanır. Python ana teknoloji olarak kaldı. Policy kararları services/governance.authorize içinde tekilleşti. Core guardrail connector action kapsamını kontrol eder; kurumsal yetkilendirmeyi iki dilde kopyalamaz.

## Bulgular ve uygulama durumu

### 1. P1 — Boş agent permission listesi erişimi genişletiyordu (düzeltildi)
- Kanıt: apps/api/dependencies.py — require_actor_permission; routers/agents.py — create_agent.
- Etki: son grant'i kaldırmak owner izinlerini geri getirebiliyordu.
- Minimum çözüm: her zaman owner izinleri ile açık grant kesişimi; minimal başlangıç grant kayıtları.
- Gerekli test: sıfır/tek/son grant, owner yetkisinin daralması. PostgreSQL regresyonları eklendi.

### 2. P1 — Agent veri izolasyonu (korundu ve genişletildi)
- Kanıt: routers/connectors/general.py — list_connectors/get_operation; routers/approvals.py — list_approvals/get_approval.
- Etki: aynı tenant içinde başka agent connector/approval/result keşfi.
- Minimum çözüm: connector grant, approval agent_id, operation actor_key filtresi; tenant şartı; revoke sonrası sonuç erişiminin yeniden doğrulanması.
- Gerekli test: aynı tenant iki agent, başka tenant, yetki iptali sonrası eski sonuç.

### 3. P1 — Approval TOCTOU ve iptal yarışı (düzeltildi)
- Kanıt: services/operations.py — run_operation; services/governance.py — authorize; services/governance_lock.py.
- Etki: beklerken kaldırılan yetki veya değişen policy ile eylem çalışması.
- Minimum çözüm: yürütmede güncel identity/grant/policy kontrolü, policy fingerprint, tenant DB kilidiyle iptal/claim sıralaması.
- Gerekli test: revoked agent, credential, grant; değişmiş policy; concurrent revoke/claim.
- Borç: aynı workspace içi claim/yönetim işlemleri kısa süre seri çalışır; yüksek hacimde ölçüm gerekir.

### 4. P1 — Dış eylemin çift çalışması (düzeltildi; exactly-once iddiası yok)
- Kanıt: models/operation.py, services/operations.py, routers/approvals.py — resolve_approval.
- Etki: aynı onayın iki çözülmesi veya provider sonrası DB çökmesi tekrar yan etki oluşturabilirdi.
- Minimum çözüm: aktör bazlı unique idempotency key, dış I/O öncesi committed EXECUTING, atomik onay claim, UNKNOWN.
- Gerekli test: paralel resolve, paralel execute, farklı payload/same key, provider sonrası commit çökmesi, tekrar denememe.

### 5. P1 — Provider SDK retry riski (düzeltildi)
- Kanıt: connectors/github/connector.py — connect; connectors/slack/connector.py — connect; connectors/gmail/connector.py — connect.
- Etki: platform tek kez çağırsa da SDK mutation'ı tekrar gönderebilir.
- Minimum çözüm: GitHub retry=0, Slack retry_handlers=[], 20 saniye istemci timeout; Gmail timeout.
- Gerekli test: client yapılandırması ve UNKNOWN sonucu.

### 6. P1 — Refresh token JSON ve rotation yarışı (düzeltildi)
- Kanıt: routers/auth.py — _issue_token_pair/refresh_tokens/logout/reset_password; web/src/api/client.ts.
- Etki: browser JavaScript token'ı okuyabilir, aynı refresh eşzamanlı tüketilebilir.
- Minimum çözüm: refresh yalnız HttpOnly cookie; row lock; access token bellekte; auth_version; frontend ortak refresh promise.
- Gerekli test: response schema, cookie flags, paralel rotation, eski refresh/JWT reset sonrası reddi, browser reload.

### 7. P1 — Audit payload/exception sızıntısı (yeni yazımlarda ve okuma yüzeyinde düzeltildi)
- Kanıt: services/privacy.py; routers/audit.py/approvals.py; services/operations.py.
- Etki: mesaj, e-posta içeriği, token veya provider exception'ı audit'e taşınabiliyordu.
- Minimum çözüm: sabit alan adları, maskeli değerler, hash/özet, sabit hata kodları; tam veri ayrı şifreli store.
- Gerekli test: içerik, key adı, result ve exception redaction; legacy read.
- Açık iş: önceden başka dağıtımlarda oluşmuş plaintext DB/log/yedekler otomatik silinmez; retention/migration planı gerekir. Aktör kimliği ilişkilendirmesi audit'in meşru metadata'sıdır.

### 8. P1 — Güçlü action şeması ve kaynak scope eksikliği (düzeltildi)
- Kanıt: connectors/schemas.py — validate_params; models/agent_credential_grant.py; governance.authorize.
- Etki: keyfi alanlar, yanlış tipler, beklenmeyen repo/kanal hedefi.
- Minimum çözüm: strict/extra-forbid Pydantic şemaları, uzunluk/ad/ID sınırları, açık resources listesi.
- Gerekli test: bool-as-int, negatif ID, traversal repo, unknown action/field, scope dışı hedef.

### 9. P1 — OAuth state ve connector.write sınırı (düzeltildi)
- Kanıt: services/oauth.py — begin_oauth/consume_oauth; connector OAuth router'ları.
- Etki: state replay, browser session binding eksikliği, düşük yetkili OAuth başlatma.
- Minimum çözüm: tek kullanımlık DB state, nonce cookie, süre, tenant/user/write kontrolü; callback'te tüketim.
- Gerekli test: nonce yok, yanlış browser, concurrent replay, yetki kaldırılması.
- Açık doğrulama: canlı Google/GitHub/Slack consent/code exchange yapılmadı.

### 10. P1 — Yönetim audit eksikliği (kısmen düzeltildi)
- Kanıt: services/security_events.py; routers/agents.py/policies.py/tenant.py/approvals.py; models/security_event.py.
- Etki: kimin grant/policy/approval değiştirdiği izlenemiyordu.
- Minimum çözüm: aynı transaction içinde sabit action/target/actor ID; human payload review erişimi kaydı.
- Gerekli test: grant/revoke/tenant isolation ve rollback.
- Açık iş: tüm auth/OAuth lifecycle olayları, WORM/harici export, DB-admin tamper resistance.

### 11. P1 — Browser/deployment sınırları (yerel profil düzeltildi)
- Kanıt: web/nginx.conf; api/main.py; config.py; docker-compose.local.yml.
- Etki: query token access log, zayıf cookie ayarı, açık DB portu, yanıltıcı health.
- Minimum çözüm: access log kapalı, route-template telemetry, CSP/referrer/frame/nosniff, no-store, private DB/API, nonroot API, gerçek 503 health.
- Gerekli test: HTTP headers, auth origin, bozuk JWT, 500 redaction, proxy cookie path.
- Açık iş: production TLS/secret manager/ayrı DB rolleri ve restore tatbikatı.

### 12. P2 — Queue/outbox ve reconciliation (kısmen düzeltildi)
- Kanıt: operations.py — expire_operations; request içinde threadpool provider çağrısı.
- Etki: sürekli arka plan kurtarma yok; UNKNOWN sağlayıcıdan manuel doğrulanır.
- Minimum çözüm: aynı operation claim'i kullanan worker, lease/heartbeat, provider destekli idempotency/reconciliation, explicit notification destination outbox.
- Gerekli test: worker kill/restart, lease expiry, stale writer, outbox duplicate delivery.
- Durum: aynı atomik claim yolunu kullanan PostgreSQL `READY` recovery worker eklendi. Ayrı broker, notification outbox ve provider reconciliation uygulanmadı; eski gizli GitHub notification side effect'i aktif akıştan çıkarıldı.

### 13. P2 — KMS/Vault, rotation, retention
- Kanıt: infrastructure/secrets/store.py — FernetSecretStore; Operation.encrypted_payload/encrypted_result.
- Etki: tek anahtar ve sınırsız saklama; DB/anahtar birlikte sızarsa plaintext erişimi.
- Minimum çözüm: KMS envelope encryption, key version, rewrap job; ayrı payload/metadata retention ve dry-run purge.
- Gerekli test: rotasyon, yanlış anahtar/tamper, key kaybından restore, tenant-scope retention.
- Durum: local Fernet mevcut; KMS/rotation/retention worker uygulanmadı.

### 14. P2 — Observability ve çoklu replica
- Kanıt: main.py — security_headers; rate_limit.py — limiter; docker-entrypoint.sh.
- Etki: local rate counter replica'lar arasında paylaşılmaz; temel log SLO/alert değildir.
- Minimum çözüm: shared rate store, metrik/tracing, UNKNOWN yaş alarmı, pool/latency SLO, kontrollü migration job.
- Gerekli test: multi-worker abuse, replica restart, log redaction, alarm eşiği.
- Durum: tek worker, güvenli request telemetry ve health eklendi; HA/dağıtık altyapı yok.

### 15. P2 — SSO/SCIM ve güçlü hesap yönetimi
- Kanıt: routers/auth.py/tenant.py — yerel password/JWT ve davet/reset.
- Etki: enterprise identity lifecycle ve merkezi offboarding eksik.
- Minimum çözüm: OIDC/SAML SSO, domain/tenant binding, SCIM, MFA/reauth ve session inventory.
- Gerekli test: issuer/audience/nonce, account linking, offboarding, privilege escalation.
- Durum: uygulanmadı; varmış gibi sunulmuyor.

### 16. P2 — Gerçek connector integrity telemetrisi yok
- Kanıt: services/execution.py — AgentGuard(monitored_resource verilmeden).
- Etki: gerçek provider'da beyan dışı her çağrıyı algılama/karantinaya alma garantisi yok.
- Minimum çözüm: güvenilir provider/egress telemetry, connector bazlı allowlist, platform dışı credential kullanımını kapatma.
- Gerekli test: yetkili eylemin tetiklediği ek outbound call, persisted quarantine, tenant isolation.
- Durum: core engine özelliği mevcut; gerçek SaaS entegrasyonu yok. Pazarlama iddiası kaldırıldı.

### 17. P2 — Test, CI ve supply-chain
- Kanıt: tests/, .github/workflows/tests.yml, API Dockerfile.
- Etki: eski sözleşmeleri koruyan testler, skip ile sahte yeşil, sürüm aralığıyla değişken build.
- Minimum çözüm: güncel sözleşmeli gerçek PG suite, atlamada fail, frontend+SDK build, image smoke, dependency taraması/lock ve image digest.
- Gerekli test: boş DB migration, restart, full suite, browser ve canlı provider kabulü.
- Durum: yerel test runner ve test image eklendi; son sonuçlar RELEASE_NOTES.md içinde raporlanır.

### 18. P3 — UI ve ürünleşme
- Kanıt: web/src/pages, components/Layout.tsx, index.css.
- Etki: demo dekorasyonu, yanlış başarı durumu ve yapılandırılmamış connector UX'i.
- Minimum çözüm: gerçek dashboard metrikleri, onboarding, responsive shell, operasyon durumu, güvenli payload review, dürüst OAuth readiness.
- Gerekli test: build, masaüstü/mobil navigasyon, reload session, boş/hata durumları, onay ve UNKNOWN görünümü.
- Durum: arayüz yenilendi; erişilebilirlik/UX dış kullanıcı kabul testi devam işidir.

## Ürün konumlandırması ve pilot sınırı

Farklılaşma önerisi: model niyetini tahmin etmek yerine, agent action'ının identity/scope/policy kararını ve insan onayını açıklanabilir şekilde yönetmek. Bu bir pazar üstünlüğü veya compliance sertifikası iddiası değildir.

Kontrollü self-host pilot için managed-path enforcement ve operasyon izlenebilirliği sağlanır. Internet enterprise üretim onayı; canlı connector testi, yük/fault testi, KMS/TLS/backup, SSO ve kurumun retention gereksinimleri doğrulanmadan verilmemelidir. Go ekleme kararı ancak ölçülmüş darboğaz veya ayrı collector/on-prem gereksinimi ile alınmalıdır.
