import json
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.settings import settings

engine = None
Session = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote: Mapped[str] = mapped_column(Text)
    normalized: Mapped[str] = mapped_column(Text)
    theme: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(40), default="pendente")
    needs_response: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    summary: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    missing: Mapped[str] = mapped_column(Text, default="")
    sources_json: Mapped[str] = mapped_column(Text, default="[]")
    pipeline_json: Mapped[str] = mapped_column(Text, default="[]")
    notified: Mapped[int] = mapped_column(Integer, default=0)
    origin: Mapped[str] = mapped_column(String(24), default="demo")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Segment(Base):
    __tablename__ = "segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    speaker: Mapped[str] = mapped_column(String(80), default="")
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Notice(Base):
    __tablename__ = "notices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(Integer)
    channel: Mapped[str] = mapped_column(String(40), default="sala_imprensa")
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RunState(Base):
    __tablename__ = "run_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    running: Mapped[int] = mapped_column(Integer, default=0)
    phase: Mapped[str] = mapped_column(String(40), default="idle")
    last_error: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def init_db() -> None:
    global engine, Session
    url = settings.database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with Session() as db:
        if db.get(RunState, 1) is None:
            db.add(RunState(id=1, running=0, phase="idle"))
            db.commit()


def session():
    if Session is None:
        raise RuntimeError("banco ainda não iniciado")
    return Session()


def case_dict(row: Case) -> dict:
    try:
        sources = json.loads(row.sources_json or "[]")
    except json.JSONDecodeError:
        sources = []
    try:
        pipeline = json.loads(row.pipeline_json or "[]")
    except json.JSONDecodeError:
        pipeline = []
    return {
        "id": row.id,
        "quote": row.quote,
        "normalized": row.normalized,
        "theme": row.theme,
        "status": row.status,
        "needs_response": bool(row.needs_response),
        "confidence": row.confidence,
        "summary": row.summary,
        "note": row.note,
        "missing": row.missing,
        "sources": sources,
        "pipeline": pipeline,
        "notified": bool(row.notified),
        "origin": row.origin,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


def notice_dict(row: Notice) -> dict:
    return {
        "id": row.id,
        "case_id": row.case_id,
        "channel": row.channel,
        "title": row.title,
        "body": row.body,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }
