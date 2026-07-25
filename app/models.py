"""
Data models for the Telegram Information Extraction Agent.

This module defines two kinds of models:

1. SQLAlchemy ORM models (`TelegramMessage`, `ExtractedInfo`) that describe
   the persistent database schema.
2. Pydantic models (`ExtractionResult`, `Category`, plus API response
   schemas) that describe the shape of data moving through the pipeline and
   the JSON returned by the Gemini API / FastAPI endpoints.
"""

from __future__ import annotations

import datetime as dt
import enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------
# SQLAlchemy ORM models
# --------------------------------------------------------------------------


class TelegramMessage(Base):
    """A raw message received from a monitored Telegram group."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("group_id", "message_id", name="uq_group_message"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    group_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sender_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message_date: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow, nullable=False
    )

    extraction: Mapped[Optional["ExtractedInfo"]] = relationship(
        back_populates="message", uselist=False, cascade="all, delete-orphan"
    )


class ExtractedInfo(Base):
    """Structured information Gemini extracted from a TelegramMessage."""

    __tablename__ = "extracted_info"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    company: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    deadline: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    apply_link: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    contact: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    important_links: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON-encoded list
    skills: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON-encoded list
    tags: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON-encoded list
    is_useful: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    extraction_failed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow, nullable=False
    )

    message: Mapped["TelegramMessage"] = relationship(back_populates="extraction")


# --------------------------------------------------------------------------
# Pydantic models (Gemini structured output + API schemas)
# --------------------------------------------------------------------------


class Category(str, enum.Enum):
    JOB = "Job"
    INTERNSHIP = "Internship"
    HACKATHON = "Hackathon"
    AI_NEWS = "AI News"
    EVENT = "Event"
    FUNDING = "Funding"
    OPEN_SOURCE = "Open Source"
    RESEARCH_PAPER = "Research Paper"
    DEADLINE = "Deadline"
    IMPORTANT_LINK = "Important Link"
    CONTACT = "Contact"
    OTHER = "Other"


class ExtractionResult(BaseModel):
    """The structured JSON Gemini must return for every message.

    Mirrors the schema requested by the product spec exactly. All fields are
    optional except `is_useful`, since irrelevant messages only need to
    return `{"is_useful": false}`.
    """

    model_config = ConfigDict(use_enum_values=True)

    category: Optional[Category] = None
    title: Optional[str] = None
    summary: Optional[str] = None
    company: Optional[str] = None
    role: Optional[str] = None
    location: Optional[str] = None
    deadline: Optional[str] = None
    apply_link: Optional[str] = None
    contact: Optional[str] = None
    important_links: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    is_useful: bool = False


class ExtractionResponse(BaseModel):
    """API representation of a stored extraction, joined with its message."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    message_id: int
    telegram_message_id: int
    group_name: str
    sender_name: Optional[str]
    raw_text: str
    message_date: dt.datetime
    category: Optional[str]
    title: Optional[str]
    summary: Optional[str]
    company: Optional[str]
    role: Optional[str]
    location: Optional[str]
    deadline: Optional[str]
    apply_link: Optional[str]
    contact: Optional[str]
    important_links: list[str]
    skills: list[str]
    tags: list[str]
    is_useful: bool
    created_at: dt.datetime
