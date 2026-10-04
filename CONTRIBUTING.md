# Contributing to AgentGuard AI Pro

AgentGuard bir güvenlik enforcement katmanıdır. Değişiklikleri küçük tutun; deny-by-default, least privilege, explicit scope ve auditability ilkelerini koruyun.

## Geliştirme akışı

1. Değişikliğin tehdit modelini ve kullanıcıya etkisini açıklayan bir issue açın.
2. Küçük bir branch oluşturun ve yalnız ilgili katmanı değiştirin.
3. Policy, authorization veya approval kararını SDK, MCP ya da connector içinde kopyalamayın. Platform kararlarının tek kaynağı `apps/api/services/governance.py` olmalıdır.
4. Güvenlik davranışını değiştiren her düzeltmeye fail-before/pass-after testi ekleyin.
5. Commit veya issue içine OAuth token, API key, cookie, kişisel veri, provider payload'ı ya da `.local/` içeriği koymayın.

## Yerel doğrulama

```powershell
pwsh -File scripts/start-local.ps1
pwsh -File scripts/test-local.ps1
npm --prefix apps/web ci
npm --prefix apps/web run lint
npm --prefix apps/web test
npm --prefix apps/web run build
npm --prefix sdk/typescript ci
npm --prefix sdk/typescript run build
npm --prefix sdk/typescript run typecheck:examples
npm --prefix sdk/typescript test
```

Python suite gerçek ve izole bir PostgreSQL test veritabanı kullanır. Test çalışmadıysa sonucu başarılı olarak işaretlemeyin; gerekli ortam koşulunu PR açıklamasına yazın.

## Pull request içeriği

- Problem ve güvenlik/ürün etkisi
- Minimum çözüm ve değişen güven sınırı
- Çalıştırılan testler ile gerçek sonuçları
- Migration, deployment veya geriye uyumluluk etkisi
- Yeni mimari/güvenlik borcu varsa açık kaydı

Güvenlik açıkları için public issue açmayın; [SECURITY.md](SECURITY.md) sürecini kullanın.
