"""
Audit metadata never stores free-form content. Execution payloads have a separate encrypted
store.
"""

import hashlib
import json


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def audit_context(params: dict) -> str:
    return json.dumps(
        {
            "args": [],
            "kwargs": {
                key: "[REDACTED]"
                for key in params
                if key
                in {
                    "repo",
                    "issue_number",
                    "max_results",
                    "message_id",
                    "channel",
                    "text",
                    "ts",
                    "name",
                    "user_id",
                }
            },
            "payload_sha256": fingerprint(params),
        }
    )


def result_summary(value) -> str:
    return json.dumps(
        {
            "type": type(value).__name__,
            "count": len(value) if isinstance(value, (list, dict)) else None,
        }
    )


def safe_context(raw):
    """Redact historical rows on read too; arbitrary field names are not audit metadata."""
    try:
        value = json.loads(raw)
        return json.loads(audit_context(value.get("kwargs", {})))
    except (ValueError, TypeError, AttributeError):
        return {"args": [], "kwargs": {}}


def safe_error(raw):
    return (
        raw
        if raw
        in {
            "AUTHORIZATION_CHANGED",
            "HUMAN_DENIED",
            "PROVIDER_OUTCOME_UNKNOWN",
            "PROVIDER_AUTHENTICATION_FAILED",
            "EXECUTION_OUTCOME_UNKNOWN",
            "RESULT_LIMIT_EXCEEDED",
            "APPROVAL_EXPIRED",
            "EXECUTION_NOT_STARTED",
            "LEGACY_REQUEST_RESUBMIT_REQUIRED",
        }
        else ("EXECUTION_ERROR" if raw else None)
    )


def safe_result(raw):
    if not raw:
        return None
    try:
        value = json.loads(raw)
        return (
            {"type": value.get("type"), "count": value.get("count")}
            if isinstance(value, dict)
            and set(value) == {"type", "count"}
            and value.get("type") in {"list", "dict", "str", "int", "NoneType", "bool", "float"}
            and (value.get("count") is None or type(value.get("count")) is int)
            else {"type": "redacted", "count": None}
        )
    except (ValueError, TypeError):
        return {"type": "redacted", "count": None}
