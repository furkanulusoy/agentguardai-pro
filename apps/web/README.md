# AgentGuard web dashboard

React ve TypeScript tabanlı yönetim arayüzü. Tenant yöneticileri bu arayüzden ajan
kimliklerini, connector yetkilerini, deterministik politikaları, insan onaylarını,
yürütme kayıtlarını ve denetim olaylarını yönetir.

## Yerel çalıştırma

Proje kökünde kanonik self-hosted ortamı başlatın:

```powershell
.\scripts\start-local.ps1
```

Ardından `http://localhost:5000` adresini açın. Tarayıcı trafiği aynı origin altındaki
`/api` yolundan FastAPI servisine yönlendirilir. Refresh token JavaScript'e açılmaz;
HttpOnly cookie içinde tutulur. Access token yalnız çalışan sekmenin belleğinde saklanır.

## Ayrı frontend geliştirme sunucusu

```powershell
npm ci
npm run dev
```

Gerekirse `VITE_API_BASE_URL` ile API adresini süreç ortamında verin. Anahtarları kaynak
koda veya Git deposuna yazmayın. Örnek AgentGuard anahtarı açıklaması:

```text
BURAYA AGENTGUARD AGENT ANAHTARINI KOPYALAYIN
```

## Doğrulama

```powershell
npm test
npm run lint
npm run build
npm audit --omit=dev
```
