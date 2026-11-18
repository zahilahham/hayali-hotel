import atexit
import os
import threading
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

SCHEMA = """
CREATE SCHEMA IF NOT EXISTS screens;
CREATE SCHEMA IF NOT EXISTS laundry;
CREATE SCHEMA IF NOT EXISTS makeup;
CREATE SCHEMA IF NOT EXISTS reception;

CREATE TABLE IF NOT EXISTS screens.room_state (
    room_id text PRIMARY KEY,
    dnd boolean NOT NULL DEFAULT false,
    makeup boolean NOT NULL DEFAULT false,
    laundry boolean NOT NULL DEFAULT false,
    screen_rev integer NOT NULL DEFAULT 0,
    seen_at timestamptz,
    updated_at timestamptz
);

CREATE TABLE IF NOT EXISTS screens.bell_link (
    id text PRIMARY KEY,
    room text NOT NULL DEFAULT '',
    url text NOT NULL,
    last_result text NOT NULL DEFAULT '',
    last_room text NOT NULL DEFAULT '',
    last_time text NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS laundry.request (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    room_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('requested', 'accepted', 'acknowledged', 'canceled')),
    start_time timestamptz NOT NULL DEFAULT now(),
    end_time timestamptz,
    source text NOT NULL DEFAULT 'room' CHECK (source IN ('room', 'reception')),
    requested_by text NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS laundry_one_open_request
    ON laundry.request (room_id)
    WHERE status IN ('requested', 'accepted');

CREATE TABLE IF NOT EXISTS makeup.request (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    room_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('requested', 'acknowledged', 'canceled')),
    start_time timestamptz NOT NULL DEFAULT now(),
    end_time timestamptz,
    source text NOT NULL DEFAULT 'room' CHECK (source IN ('room', 'reception')),
    requested_by text NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS makeup_one_open_request
    ON makeup.request (room_id)
    WHERE status = 'requested';

CREATE TABLE IF NOT EXISTS reception.account (
    username text PRIMARY KEY,
    password_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reception.session (
    token text PRIMARY KEY,
    username text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    ended_at timestamptz
);

CREATE TABLE IF NOT EXISTS reception.reservation (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    guest_name text NOT NULL CHECK (char_length(btrim(guest_name)) > 0),
    start_date date NOT NULL,
    end_date date NOT NULL CHECK (end_date >= start_date),
    message text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by text NOT NULL
);

CREATE TABLE IF NOT EXISTS screens.device (
    room_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('shelly', 'small_screen', 'big_screen')),
    address text NOT NULL DEFAULT '',
    last_result text NOT NULL DEFAULT '',
    last_at timestamptz,
    reachable boolean,
    checked_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text NOT NULL DEFAULT '',
    PRIMARY KEY (room_id, kind)
);

CREATE TABLE IF NOT EXISTS screens.device_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    room_id text NOT NULL,
    kind text NOT NULL,
    previous_address text NOT NULL DEFAULT '',
    address text NOT NULL DEFAULT '',
    changed_at timestamptz NOT NULL DEFAULT now(),
    changed_by text NOT NULL
);

CREATE INDEX IF NOT EXISTS screens_device_log_changed
    ON screens.device_log (changed_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS screens.simulator_key (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    key bytea NOT NULL
);

CREATE TABLE IF NOT EXISTS screens.presence (
    room_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('small_screen', 'big_screen')),
    address text NOT NULL DEFAULT '',
    seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (room_id, kind)
);

CREATE TABLE IF NOT EXISTS screens.activity (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    room_id text NOT NULL,
    at timestamptz NOT NULL DEFAULT now(),
    text text NOT NULL
);

CREATE INDEX IF NOT EXISTS screens_activity_room
    ON screens.activity (room_id, id DESC);
"""

MIGRATE = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'laundry' AND table_name = 'request' AND column_name = 'requested_at'
    ) THEN
        ALTER TABLE laundry.request RENAME COLUMN requested_at TO start_time;
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'laundry' AND table_name = 'request' AND column_name = 'closed_at'
    ) THEN
        ALTER TABLE laundry.request RENAME COLUMN closed_at TO end_time;
    END IF;
END $$;
ALTER TABLE laundry.request DROP CONSTRAINT IF EXISTS request_status_check;
UPDATE laundry.request SET status = 'canceled' WHERE status = 'closed';
ALTER TABLE laundry.request
    ADD CONSTRAINT request_status_check
    CHECK (status IN ('requested', 'accepted', 'acknowledged', 'canceled'));
DROP INDEX IF EXISTS laundry.laundry_one_open_request;
CREATE UNIQUE INDEX laundry_one_open_request
    ON laundry.request (room_id)
    WHERE status IN ('requested', 'accepted');
ALTER TABLE screens.device ADD COLUMN IF NOT EXISTS reachable boolean;
ALTER TABLE screens.device ADD COLUMN IF NOT EXISTS checked_at timestamptz;

ALTER TABLE laundry.request ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'room';
ALTER TABLE laundry.request ADD COLUMN IF NOT EXISTS requested_by text NOT NULL DEFAULT '';
ALTER TABLE makeup.request ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'room';
ALTER TABLE makeup.request ADD COLUMN IF NOT EXISTS requested_by text NOT NULL DEFAULT '';
ALTER TABLE laundry.request DROP CONSTRAINT IF EXISTS laundry_request_source_check;
ALTER TABLE laundry.request
    ADD CONSTRAINT laundry_request_source_check CHECK (source IN ('room', 'reception'));
ALTER TABLE makeup.request DROP CONSTRAINT IF EXISTS makeup_request_source_check;
ALTER TABLE makeup.request
    ADD CONSTRAINT makeup_request_source_check CHECK (source IN ('room', 'reception'));
ALTER TABLE reception.reservation ADD COLUMN IF NOT EXISTS room_id text;
CREATE INDEX IF NOT EXISTS reception_reservation_room
    ON reception.reservation (room_id, end_date);
"""


def load_env() -> None:
    """Read backend/.env. A variable already set for the process wins."""
    path = Path(__file__).resolve().parent / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


# The file is read once, when the server starts. It is not read on every query.
load_env()


def setting(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def database_url() -> str:
    url = setting("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is missing from backend/.env")
    return url


def _database_name(url: str) -> str:
    name = url.rsplit("/", 1)[-1].split("?", 1)[0]
    if not name or not name.replace("_", "").isalnum():
        raise RuntimeError("DATABASE_URL needs a simple database name")
    return name


def ensure_database() -> None:
    """Create the database the first time, when this account is allowed to.

    A server where the database already exists never needs the admin database.
    """
    url = database_url()
    try:
        with psycopg.connect(url, connect_timeout=10):
            return
    except psycopg.OperationalError as first:
        name = _database_name(url)
        admin = url.rsplit("/", 1)[0] + "/postgres"
        try:
            with psycopg.connect(admin, autocommit=True, connect_timeout=10) as conn:
                exists = conn.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
                ).fetchone()
                if exists is None:
                    conn.execute(f'CREATE DATABASE "{name}"')
                    return
        except psycopg.Error:
            pass
        # The database is there, or it could not be created. The first error is the real one.
        raise first


_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def _open_pool() -> ConnectionPool:
    """Connections are opened once and reused. Opening one per query is slow under 64 rooms."""
    global _pool
    with _pool_lock:
        if _pool is None:
            pool = ConnectionPool(
                database_url(),
                kwargs={"row_factory": dict_row},
                min_size=2,
                max_size=max(4, int(setting("HAYALI_DB_POOL", "20") or 20)),
                timeout=10,
                max_idle=300,
                check=ConnectionPool.check_connection,
                name="hayali",
                open=False,
            )
            pool.open(wait=True, timeout=15)
            _pool = pool
            atexit.register(close)
        return _pool


def prepare() -> None:
    ensure_database()
    with psycopg.connect(
        database_url(), autocommit=True, connect_timeout=10, row_factory=dict_row
    ) as conn:
        conn.execute(SCHEMA)
        conn.execute(MIGRATE)
        import reception
        reception.ensure_account(conn)
    _open_pool()


def close() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


@contextmanager
def connection():
    """One transaction. It commits on the way out, and rolls back on an error."""
    pool = _pool or _open_pool()
    with pool.connection() as conn:
        yield conn
