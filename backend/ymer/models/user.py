import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, LargeBinary, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ymer.db import Base
from ymer.models.base import TimestampMixin, uuid_pk

if TYPE_CHECKING:
    from ymer.models.workspace import WorkspaceMember


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        # Emails are stored normalised so the unique constraint is case-insensitive.
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # Bumped on logout or password change; tokens carrying an older value are rejected.
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    ai_provider: Mapped[str | None] = mapped_column(String(20))

    memberships: Mapped[list["WorkspaceMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<User {self.email}>"


class UserAiProfile(Base):
    __tablename__ = "user_ai_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(20), primary_key=True)
    model: Mapped[str] = mapped_column(String(200))
    encrypted_key: Mapped[bytes] = mapped_column(LargeBinary)
