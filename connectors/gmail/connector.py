"""
Real Gmail connector -- google-api-python-client wraps the actual
Gmail REST API. Read-only scope on purpose for this first pass
(gmail.readonly): proves the OAuth + real-API integration works before
adding compose/send, which are a genuine, separate risk step up (see
docs/ROADMAP_TO_PRODUCTION.md).
"""

from __future__ import annotations

from typing import Any

from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from httplib2 import Http

from connectors.base import BaseConnector, ConnectorAuthenticationError, ConnectorError

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GmailConnector(BaseConnector):
    connector_type = "gmail"
    supported_actions = frozenset({"list_messages", "read_message"})

    def __init__(self) -> None:
        self._credentials: Credentials | None = None
        self._service: Resource | None = None

    def authenticate(self, credential: dict[str, Any]) -> None:
        """`credential` is whatever infrastructure/secrets decrypted --
        shape matches connectors/gmail/oauth.py's exchange_code_for_credential()."""
        self._credentials = Credentials(
            token=credential["access_token"],
            refresh_token=credential.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=credential["client_id"],
            client_secret=credential["client_secret"],
            scopes=credential.get("scopes", SCOPES),
        )

    def connect(self) -> None:
        if self._credentials is None:
            raise RuntimeError("authenticate() must be called before connect()")
        try:
            self._service = build(
                "gmail",
                "v1",
                http=AuthorizedHttp(self._credentials, http=Http(timeout=20)),
                cache_discovery=False,
            )
        except Exception as exc:
            raise ConnectorAuthenticationError(f"Could not build Gmail client: {exc}") from exc

    def disconnect(self) -> None:
        self._service = None

    def validate(self) -> bool:
        if self._service is None:
            return False
        try:
            self._service.users().getProfile(userId="me").execute()
            return True
        except HttpError:
            return False

    def list_messages(self, max_results: int = 10) -> list[dict[str, Any]]:
        if self._service is None:
            raise RuntimeError("not connected")
        try:
            result = (
                self._service.users().messages().list(userId="me", maxResults=max_results).execute()
            )
        except HttpError as exc:
            raise ConnectorError(f"Gmail list_messages failed: {exc}") from exc
        return result.get("messages", [])

    def read_message(self, message_id: str) -> dict[str, Any]:
        if self._service is None:
            raise RuntimeError("not connected")
        try:
            return (
                self._service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
        except HttpError as exc:
            raise ConnectorError(f"Gmail read_message failed: {exc}") from exc
