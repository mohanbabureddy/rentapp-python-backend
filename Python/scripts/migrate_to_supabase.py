"""One-off data migration from the Aiven MySQL database to a new Postgres
database (Supabase or Neon). Run manually, from the Python/ directory:

    .venv/Scripts/python.exe scripts/migrate_to_supabase.py --target "postgresql://..."

--source defaults to DB_URL in .env (the current Aiven MySQL URL) so it
doesn't need to be typed out. Safe to re-run: existing rows are merged by
primary key rather than duplicated.
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Complaint, Occupant, TenantBill, TransactionLog, User

MODELS_IN_ORDER = [User, TenantBill, Complaint, Occupant, TransactionLog]


def _normalize_postgres_url(url: str) -> str:
    # SQLAlchemy defaults plain postgres(ql):// URLs to psycopg2, which isn't
    # installed (no wheel for this Python version) -- route to psycopg (v3).
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def migrate(source_url: str, target_url: str, reset_target: bool = False) -> None:
    # Mirrors app/database.py's handling of Aiven's SSL requirement: PyMySQL
    # needs the CA cert content via connect_args, not a URL param.
    source_connect_args = {}
    ssl_ca_pem = os.getenv("SOURCE_DB_SSL_CA")
    if source_url.startswith("mysql") and ssl_ca_pem:
        ca_file = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False)
        ca_file.write(ssl_ca_pem)
        ca_file.close()
        source_connect_args = {"ssl": {"ca": ca_file.name}}

    source_engine = create_engine(source_url, connect_args=source_connect_args)
    target_engine = create_engine(_normalize_postgres_url(target_url))

    Base.metadata.create_all(bind=target_engine)

    if reset_target:
        with target_engine.connect() as conn:
            for model in reversed(MODELS_IN_ORDER):
                conn.exec_driver_sql(f"TRUNCATE TABLE {model.__tablename__} RESTART IDENTITY CASCADE")
            conn.commit()
        print("Target tables truncated.")

    SourceSession = sessionmaker(bind=source_engine)
    TargetSession = sessionmaker(bind=target_engine)
    src = SourceSession()
    dst = TargetSession()

    try:
        for model in MODELS_IN_ORDER:
            rows = src.query(model).all()
            print(f"{model.__tablename__}: migrating {len(rows)} rows")
            for row in rows:
                data = {c.name: getattr(row, c.name) for c in model.__table__.columns}
                dst.merge(model(**data))
            dst.commit()

        # Rows were inserted with explicit ids, so each table's auto-increment
        # sequence needs to be advanced past the highest id we just wrote --
        # otherwise the app's next INSERT (which omits id) collides with one
        # of these migrated rows.
        with target_engine.connect() as conn:
            for model in MODELS_IN_ORDER:
                table = model.__tablename__
                conn.exec_driver_sql(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table}), 1), "
                    f"(SELECT MAX(id) IS NOT NULL FROM {table}))"
                )
            conn.commit()
        print("Migration complete.")
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=os.getenv("DB_URL"), help="Source MySQL URL (defaults to DB_URL in .env)")
    parser.add_argument("--target", required=True, help="Target Postgres URL")
    parser.add_argument("--reset-target", action="store_true", help="Truncate target tables before migrating (use when re-running after a bad prior migration)")
    args = parser.parse_args()
    if not args.source:
        parser.error("--source is required (or set DB_URL in .env)")
    migrate(args.source, args.target, reset_target=args.reset_target)
