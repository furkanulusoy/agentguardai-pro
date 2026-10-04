# AgentGuard yerel ürün sürümü

Bu kopyanın güncel çalıştırma sözleşmesi. Eski roadmap/demo belgeleri tarihsel tasarım notlarıdır.

## Çalıştırma

Docker Desktop Linux containers açık olmalıdır. PowerShell 7 ile proje kökünde:

~~~powershell
pwsh -File scripts/start-local.ps1
~~~

**http://localhost:5000** adresini açın. **Kayıt olun** ile kendi workspace ve OWNER hesabınızı oluşturun. Hazır kullanıcı veya varsayılan parola yoktur.

Yalnız web portu 127.0.0.1:5000 üzerinde yayınlanır. API/PostgreSQL compose ağı içindedir. DB verileri bu kopyanın .local/pgdata dizininde kalır. Dotenv kullanılmaz. Yeni yerel altyapı anahtarları .local/platform-config.json ve .local/postgres-password içinde üretilir; kaynak denetimine/image'a girmez. Ayrı `worker` servisi, API kesintisinden önce kaydedilmiş ancak henüz claim edilmemiş yalnız `READY` operasyonları kurtarır. `EXECUTING` veya `UNKNOWN` operasyonları tekrar çalıştırmaz.

Linux/macOS:

~~~sh
python scripts/prepare-local.py
export AGENTGUARD_CONTAINER_USER="$(id -u):$(id -g)"
docker compose -f docker-compose.local.yml up -d --build
~~~

Linux'ta kullanıcı eşlemesi 0600 secret dosyalarını yalnız kendi UID'nizle okumak içindir; compose komutlarını aynı terminalde çalıştırın. Varsayılan Windows konteyner kullanıcısı root değildir.

Durdurma: docker compose -f docker-compose.local.yml stop.
Yeniden başlatma: docker compose -f docker-compose.local.yml up -d.
.local silinirse veriler/şifreleme anahtarları kaybolabilir. DB ve anahtarları birlikte, şifreli ve erişimi sınırlı yedekleyin.

## İlk gerçek agent

1. OAuth uygulamasını yapılandırın; Entegrasyonlar ekranından hesabı bağlayın.
2. Ajanlar sayfasında ayrı kimlik oluşturun. Tek gösterilen anahtarı kendi secret yöneticinize alın.
3. Connector grant açın. GitHub için tam owner/repo, Slack için kanal ID veya oluşturulacak kanal adını kaynak listesine ekleyin. Joker kabul edilmez. Gmail grant'i bağlı mailbox sınırıdır.
4. Minimal izinler agent.execute, connector.read, approval.read olarak ayrı kayıtlardır. Son izni kaldırmak erişimi kapatır.
5. Policy: ALLOW / REQUIRE_APPROVAL / DENY. Tenant DENY ajan override'ından üstündür.
6. SDK'yı http://localhost:5000/api adresine bağlayın.

~~~python
import os
import uuid
from agentguard_sdk import AgentGuardClient

async def run(connector_id):
    key = str(uuid.uuid4())  # İşinizin dayanıklı kaydında saklayın.
    async with AgentGuardClient(
        os.environ["AGENTGUARD_AGENT_KEY"],
        base_url="http://localhost:5000/api",
    ) as client:
        return await client.run(
            connector_id, "close_issue",
            {"repo": "acme/repository", "issue_number": 42},
            idempotency_key=key,
        )
~~~

SDK kaynakları: sdk/python ve sdk/typescript. MCP: apps/mcp_server/server.py; AGENTGUARD_API_BASE_URL=http://localhost:5000/api ve AGENTGUARD_AGENT_KEY kullanın.

## OAuth

Sağlayıcıda OAuth uygulaması oluşturup client ID / secret / callback değerlerini sunucu secret dosyasında yapılandırın. Gerçek değerleri loglamayın veya kaynak koduna koymayın. Mevcut DB/JWT/Fernet anahtarlarını değiştirmeyin.

| Servis | Ayar anahtarları | Yerel callback |
|---|---|---|
| Gmail | google_oauth_client_id, google_oauth_client_secret, google_oauth_redirect_uri | http://localhost:5000/api/connectors/gmail/callback |
| GitHub | github_oauth_client_id, github_oauth_client_secret, github_oauth_redirect_uri | http://localhost:5000/api/connectors/github/callback |
| Slack | slack_oauth_client_id, slack_oauth_client_secret, slack_oauth_redirect_uri | http://localhost:5000/api/connectors/slack/callback |

Yeni ayarları yüklemek için docker compose -f docker-compose.local.yml up -d --force-recreate api web çalıştırın. HTTPS callback isteyen sağlayıcı uygulamaları için kontrollü HTTPS dağıtım gerekir.

Gmail mesaj kimlikleri ve From/Subject/Date metadata'sı okunur; mail gönderme yoktur. GitHub OAuth repo yetkisi provider tarafında geniştir; AgentGuard grant'i uygulama sınırında daraltır. GitHub App kurulum yetkileri enterprise geçiş işidir. Slack mutasyonları varsayılan insan onayına tabidir. Dış GitHub notification issue akışı aktif ürün akışından çıkarılmıştır; onaylar dashboard'da takip edilir.

## Onay ve belirsiz sonuç

Gerçek parametreler yalnız approval.approve yetkili insanın review endpoint'inde çözülür; onay payload hash'ine bağlanır. Listeler/audit değerleri maskeler.

Yürütme sahipliği dış çağrıdan önce PostgreSQL'e yazılır. Aynı aktör ve idempotency anahtarı aynı operasyonu döndürür; farklı payload 409 alır. Değişen yetki/policy bekleyen eylemi durdurur. İptal ve claim workspace kilidinde sıralanır. Claim sonrası başlamış provider işlemi geri çağrılamaz.

UNKNOWN başarısızlık garantisi değildir; eylem gerçekleşmiş olabilir. Yeni anahtarla otomatik tekrar yapmayın. Sağlayıcının kayıtlarından sonucu doğrulayın; operation ID ve harici kanıtı olay kaydına ekleyin. Otomatik provider reconciliation veya exactly-once garantisi yoktur. Çöken EXECUTING kayıtlar worker veya okuma akışı tarafından 5 dakika sonra UNKNOWN yapılır. Claim edilmeyen READY kayıtları worker yeniden yetkilendirip devralır; worker uzun süre kapalı kalırsa eski READY kayıtları güvenli biçimde FAILED olur. Ayrı notification outbox henüz yoktur.

## Test ve operasyon

~~~powershell
pwsh -File scripts/test-local.ps1
npm --prefix apps/web run build
npm --prefix sdk/typescript run build
npm --prefix sdk/typescript test
~~~

Test runner ayrı agentguard_security_test PostgreSQL DB'sini kullanır. Harici çağrılar testlerde sınırda değiştirilir. Canlı OAuth/servis kabul testi gerçek sağlayıcı hesabı ve kullanıcı consent'i gerektirir.

Health: /api/health. OpenAPI: /api/openapi.json. HTTP logları route şablonu, request ID, durum ve süre içerir; query/body/token/email içermez. Yönetim audit'i aynı transaction'a katılır. DB yöneticisine karşı değiştirilemez WORM log değildir.

Yerel kurulum tek API worker kullanır. Internet production için TLS, DEPLOYMENT_MODE=production, COOKIE_SECURE=true, açık CORS origin, KMS/Vault, yedek geri yükleme testi ve ayrı migration/uygulama DB rolleri gerekir. Yerel compose internet yayını için hazır profil değildir.
