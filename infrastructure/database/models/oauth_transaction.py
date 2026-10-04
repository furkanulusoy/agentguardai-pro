import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database.models.base import Base, TimestampMixin, UUIDPKMixin


class OAuthTransaction(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "oauth_transactions"
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    nonce_hash: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
