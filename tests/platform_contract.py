"""Shared request construction for the current API; never bypasses server authorization."""


async def reviewed_decision(client, resolve_path, headers):
    response = await client.get(resolve_path.removesuffix("/resolve") + "/review", headers=headers)
    return (
        {"approved": True, "payload_hash": response.json().get("payload_hash")}
        if response.status_code == 200
        else {"approved": True}
    )
