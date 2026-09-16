import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from flask import g
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DB_URL = os.getenv("DB_URL") or os.getenv("DATABASE_URL") or "sqlite:///tenant_billing.db"

# Providers (Supabase, Neon, Heroku-style) hand out plain "postgresql://" or
# "postgres://" URLs, which SQLAlchemy defaults to the old psycopg2 driver --
# not installed here since it has no prebuilt wheel for this Python version.
# Route explicitly to psycopg (v3) instead, which does.
if DB_URL.startswith("postgres://"):
    DB_URL = "postgresql+psycopg://" + DB_URL[len("postgres://"):]
elif DB_URL.startswith("postgresql://"):
    DB_URL = "postgresql+psycopg://" + DB_URL[len("postgresql://"):]

# PyMySQL has no "ssl_mode" URL param (that's a MySQL Connector/Python-ism) --
# TLS has to be requested via connect_args instead. DB_SSL_CA holds the PEM
# certificate content (e.g. Aiven's CA cert) directly, since Render env vars
# can't reference a file path on disk. Postgres (e.g. Supabase) needs no
# equivalent -- its connection string/driver negotiate TLS on their own.
_ssl_ca_pem = os.getenv("DB_SSL_CA")
if DB_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}
elif DB_URL.startswith("mysql") and _ssl_ca_pem:
    ca_file = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False)
    ca_file.write(_ssl_ca_pem)
    ca_file.close()
    connect_args = {"ssl": {"ca": ca_file.name}}
else:
    connect_args = {}

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
