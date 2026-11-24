"""Tables for the AI layer: embeddings, the indexing queue and Q&A conversations.

- ``ai_chunks``: pieces of page text (documents, mind maps, drawings) and database rows
  with their embedding, for semantic search and workspace Q&A. Every chunk belongs to a
  page, and reads filter by the pages the user can see, so the page permission rules
  apply to everything the AI reads. A chunk of a database row belongs to the database.
- ``ai_index_queue``: pages whose chunks are out of date. Saving a page marks it; the
  worker (``flask --app wsgi ai-worker``) re-chunks and embeds it.
- ``ai_conversations`` / ``ai_messages``: a user's Q&A threads in one workspace. Private
  to that user.
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ymer.db import Base
from ymer.models.base import TimestampMixin, uuid_pk


class AiChunk(Base):
    __tablename__ = "ai_chunks"
    __table_args__ = (
        Index("ix_ai_chunks_page_id", "page_id"),
        Index("ix_ai_chunks_workspace_id_model", "workspace_id", "model"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pages.id", ondelete="CASCADE"), nullable=False
    )
    # Set for chunks of database rows (page_id is then the database).
    row_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("database_rows.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # sha256 of model + content, so unchanged chunks are not embedded again.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Embedding model name; vectors of different models are never compared.
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    # No fixed dimension, so the embedding model can be swapped without a migration.
    embedding = mapped_column(Vector(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AiIndexJob(Base):
    __tablename__ = "ai_index_queue"

    page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pages.id", ondelete="CASCADE"), primary_key=True
    )
    enqueued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_error: Mapped[str | None] = mapped_column(Text)


class AiConversation(TimestampMixin, Base):
    __tablename__ = "ai_conversations"
    __table_args__ = (Index("ix_ai_conversations_user_workspace", "user_id", "workspace_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")


class AiMessage(Base):
    __tablename__ = "ai_messages"
    __table_args__ = (Index("ix_ai_messages_conversation_id", "conversation_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # For answers: [{n, page_id, row_id, title, snippet}] in citation order.
    sources: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
