"""The only action input contract. Unknown fields and invalid types fail before persistence."""

from typing import Annotated

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ListParams(Params):
    max_results: int = Field(default=10, ge=1, le=100)


class GmailRead(Params):
    message_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class CloseIssue(Params):
    repo: str = Field(
        min_length=3,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    issue_number: int = Field(ge=1, le=2147483647)


Channel = Annotated[str, Field(pattern=r"^[CG][A-Z0-9]{2,30}$")]


class SendMessage(Params):
    channel: Channel
    text: str = Field(min_length=1, max_length=4000)


class CreateChannel(Params):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_-]+$")


class DeleteMessage(Params):
    channel: Channel
    ts: str = Field(pattern=r"^[0-9]{10,16}\.[0-9]{1,6}$")


class InviteUser(Params):
    channel: Channel
    user_id: str = Field(pattern=r"^[UW][A-Z0-9]{2,30}$")


ACTION_SCHEMAS: dict[tuple[str, str], type[Params]] = {
    ("gmail", "list_messages"): ListParams,
    ("gmail", "read_message"): GmailRead,
    ("github", "list_repos"): ListParams,
    ("github", "close_issue"): CloseIssue,
    ("slack", "list_channels"): ListParams,
    ("slack", "send_message"): SendMessage,
    ("slack", "create_channel"): CreateChannel,
    ("slack", "delete_message"): DeleteMessage,
    ("slack", "invite_user"): InviteUser,
}


def validate_params(connector_type: str, action: str, params: dict) -> dict:
    schema = ACTION_SCHEMAS.get((connector_type, action))
    if schema is None:
        raise HTTPException(403, "Action outside connector scope")
    try:
        return schema.model_validate(params).model_dump()
    except ValidationError as exc:
        # Pydantic error input values can contain secrets. Never echo them.
        issues = [
            {"field": ".".join(map(str, e["loc"])), "type": e["type"]}
            for e in exc.errors(include_input=False, include_url=False)
        ]
        raise HTTPException(422, issues) from None
