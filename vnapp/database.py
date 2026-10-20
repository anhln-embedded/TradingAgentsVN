"""Durable accounts, subscriptions, job queue and event log (schema version 1)."""
import os
from contextlib import contextmanager

from sqlalchemy import (Boolean, CheckConstraint, Column, Float, ForeignKey, Index, Integer, MetaData,
                        String, Table, Text, UniqueConstraint, create_engine, event)

from vnapp.settings import data_dir

metadata = MetaData()
users = Table("saas_users", metadata,
    Column("id", String(36), primary_key=True),
    Column("email", String(160), nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("role", String(20), nullable=False),
    Column("active", Boolean, nullable=False, default=True),
    Column("created_at", Float, nullable=False),
    CheckConstraint("role IN ('user', 'superadmin')", name="saas_user_role"))
sessions = Table("saas_sessions", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", ForeignKey(users.c.id), nullable=False, index=True),
    Column("csrf", String(64), nullable=False),
    Column("expires_at", Float, nullable=False))
periods = Table("saas_periods", metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", ForeignKey(users.c.id), nullable=False, index=True),
    Column("plan", String(80), nullable=False),
    Column("starts_at", Float, nullable=False),
    Column("ends_at", Float, nullable=False),
    Column("allowance", Integer, nullable=False),
    Column("reserved", Integer, nullable=False, default=0),
    Column("used", Integer, nullable=False, default=0),
    CheckConstraint("allowance >= 0 AND used >= 0 AND reserved >= 0 AND allowance >= used + reserved", name="saas_quota_nonnegative"))
jobs = Table("saas_jobs", metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", ForeignKey(users.c.id), nullable=False, index=True),
    Column("period_id", ForeignKey(periods.c.id), nullable=False),
    Column("request_key", String(100), nullable=False),
    Column("ticker", String(20), nullable=False),
    Column("trade_date", String(10), nullable=False),
    Column("analysts", Text, nullable=False),
    Column("status", String(20), nullable=False, index=True),
    Column("created_at", Float, nullable=False),
    Column("started_at", Float), Column("finished_at", Float),
    Column("heartbeat_at", Float), Column("worker_token", String(36)),
    Column("event_seq", Integer, nullable=False, default=0),
    Column("snapshot", Text, nullable=False, default="{}"),
    UniqueConstraint("user_id", "request_key"))
events = Table("saas_events", metadata,
    Column("job_id", ForeignKey(jobs.c.id), primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("kind", String(40), nullable=False),
    Column("data", Text, nullable=False),
    Column("created_at", Float, nullable=False))
audit = Table("saas_audit", metadata,
    Column("id", String(36), primary_key=True),
    Column("actor_id", String(36), nullable=False),
    Column("action", String(60), nullable=False),
    Column("target_id", String(100), nullable=False),
    Column("details", Text, nullable=False),
    Column("created_at", Float, nullable=False))
ledger = Table("saas_usage", metadata,
    Column("id", String(36), primary_key=True),
    Column("period_id", ForeignKey(periods.c.id), nullable=False),
    Column("job_id", ForeignKey(jobs.c.id)),
    Column("kind", String(30), nullable=False),
    Column("amount", Integer, nullable=False),
    Column("request_key", String(160), nullable=False, unique=True),
    Column("created_at", Float, nullable=False))
throttles = Table("saas_throttles", metadata,
    Column("key", String(64), primary_key=True),
    Column("window_start", Float, nullable=False),
    Column("count", Integer, nullable=False))
system_lock = Table("saas_system_lock", metadata,
    Column("id", String(20), primary_key=True))
Index("saas_jobs_queue", jobs.c.status, jobs.c.created_at)


def make_engine(url=None):
    url = url or os.environ.get("TA_DATABASE_URL")
    if not url:
        if os.environ.get("TA_ENV") == "production":
            raise RuntimeError("TA_DATABASE_URL (PostgreSQL) is required in production")
        folder = data_dir() / "saas"
        folder.mkdir(parents=True, exist_ok=True)
        url = "sqlite:///" + (folder / "app.sqlite3").as_posix()
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    if os.environ.get("TA_ENV") == "production" and not url.startswith("postgresql+"):
        raise RuntimeError("Production requires PostgreSQL")
    engine = create_engine(url, pool_pre_ping=True,
        **({"connect_args": {"check_same_thread": False, "timeout": 30}} if url.startswith("sqlite") else {}))
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
    return engine


@contextmanager
def transaction(engine):
    with engine.connect() as connection:
        # SQLite is only for local development/tests; serialize write transactions.
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            connection.begin()
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
