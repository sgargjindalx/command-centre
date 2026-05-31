"""Thin PostgreSQL wrapper using psycopg2."""
import logging
from contextlib import contextmanager
from pathlib import Path

import psycopg2
import psycopg2.extras

import config

logger = logging.getLogger(__name__)
_pool = None


def _connect():
    return psycopg2.connect(config.DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


@contextmanager
def get_cursor(commit: bool = False):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            yield cur
            if commit:
                conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def apply_schema():
    path = Path(__file__).parent / "schema.sql"
    if not path.exists():
        return
    with get_cursor(commit=True) as cur:
        cur.execute(path.read_text(encoding="utf-8"))
    logger.info("schema.sql applied")


def healthcheck() -> bool:
    try:
        with get_cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False
