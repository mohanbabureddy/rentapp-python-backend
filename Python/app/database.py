import os
from pathlib import Path

from dotenv import load_dotenv
from flask import g
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DB_URL = os.getenv("DB_URL") or os.getenv("DATABASE_URL") or "sqlite:///tenant_billing.db"

connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    """Returns one SQLAlchemy session per Flask request, reused across every
    get_db() call within that request. close_db (registered as a
    teardown_appcontext handler) closes it when the request ends -- without that,
    every route leaked its session's pooled connection and the pool would
    eventually be exhausted, timing out every subsequent request."""
    if "db_session" not in g:
        g.db_session = SessionLocal()
    return g.db_session


def close_db(exception=None) -> None:
    db = g.pop("db_session", None)
    if db is not None:
        db.close()
