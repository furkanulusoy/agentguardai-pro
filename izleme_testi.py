"""
Bu dosya, kullanicinin kendi PowerShell'inden calistirdigi, gercek bir
"AI agent kodu" ornegi -- AgentGuard'in kendi Python SDK'sini kullanarak,
gercek bir HTTP istegiyle platforma baglanir. Ogretici amacli, elle
calistirmak icin -- CI/test suite'inin bir parcasi degil.
"""
import asyncio
import os

from agentguard_sdk import AgentGuardClient, AgentGuardDenied, AgentGuardTimeout, AgentGuardError

AGENT_KEY_PLACEHOLDER = "BURAYA AGENTGUARD AGENT ANAHTARINI KOPYALAYIN"
CONNECTOR_ID_PLACEHOLDER = "BURAYA AGENTGUARD CONNECTOR KIMLIGINI KOPYALAYIN"


async def main() -> None:
    agent_key = os.environ.get("AGENTGUARD_AGENT_KEY", "").strip()
    connector_id = os.environ.get("AGENTGUARD_CONNECTOR_ID", "").strip()
    if not agent_key or agent_key == AGENT_KEY_PLACEHOLDER:
        print(
            "AGENTGUARD_AGENT_KEY ortam degiskeni eksik. Degeri kaynak koda "
            "yazmayin; PowerShell oturumunda guvenli sekilde ayarlayin."
        )
        return
    if not connector_id or connector_id == CONNECTOR_ID_PLACEHOLDER:
        print(
            "AGENTGUARD_CONNECTOR_ID ortam degiskeni eksik. Dashboard'daki "
            "connector kimligini PowerShell oturumunda ayarlayin."
        )
        return

    action = os.environ.get("AGENTGUARD_ACTION", "list_repos")
    if action == "list_repos":
        params = {"max_results": 10}
    elif action == "close_issue":
        repo = os.environ.get("AGENTGUARD_GITHUB_REPO", "").strip()
        issue_number = os.environ.get("AGENTGUARD_GITHUB_ISSUE_NUMBER", "").strip()
        if not repo or not issue_number.isdigit():
            print(
                "close_issue icin AGENTGUARD_GITHUB_REPO ve sayisal "
                "AGENTGUARD_GITHUB_ISSUE_NUMBER zorunludur."
            )
            return
        params = {"repo": repo, "issue_number": int(issue_number)}
    else:
        print(f"Bu guvenli ornek tarafindan desteklenmeyen aksiyon: {action}")
        return

    base_url = os.environ.get("AGENTGUARD_BASE_URL", "http://localhost:5000/api")
    async with AgentGuardClient(agent_key, base_url=base_url) as client:
        print(f"--> AgentGuard'a baglaniliyor, aksiyon: github.{action}")
        if action == "close_issue":
            print("--> Bu HIGH risk bir aksiyon; insan onayi bekleniyor.")
            print("--> Dashboard'daki Onaylar sayfasindan istegi inceleyin.")
        try:
            outcome = await client.run(connector_id, action, params)
        except AgentGuardDenied:
            print("--> SONUC: Bir insan bu aksiyonu REDDETTI (DENY).")
            return
        except AgentGuardTimeout:
            print("--> SONUC: Kimse zamaninda onaylamadi/reddetmedi (suresi doldu).")
            return
        except AgentGuardError as exc:
            print(f"--> SONUC: AgentGuard bu istegi reddetti veya gercek servis hata verdi:\n    {exc}")
            return

        print(f"--> SONUC: Basarili. Donen veri:\n{outcome['result']}")


if __name__ == "__main__":
    asyncio.run(main())
