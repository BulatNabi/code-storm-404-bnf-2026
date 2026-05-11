import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    projects: Mapped[list["Project"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    jira_connection: Mapped[list["JiraConnection"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    user: Mapped["User"] = relationship(back_populates="projects")
    analyses: Mapped[list["Analysis"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    project: Mapped["Project"] = relationship(back_populates="analyses")
    files: Mapped[list["AnalysisFile"]] = relationship(back_populates="analysis", cascade="all, delete-orphan")


class AnalysisFile(Base):
    __tablename__ = "analysis_files"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(String, ForeignKey("analyses.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    s3_key: Mapped[str] = mapped_column(String, nullable=False)  # fintech-radar/analyses/{analysis_id}/{filename}
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    analysis: Mapped["Analysis"] = relationship(back_populates="files")


class JiraConnection(Base):
    __tablename__ = "jira_connections"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), unique=True, nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    # токен хранится в открытом виде для MVP; в проде — шифровать через Fernet
    api_token: Mapped[str] = mapped_column(String, nullable=False)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped["User"] = relationship(back_populates="jira_connection")
