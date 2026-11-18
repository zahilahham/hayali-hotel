import hashlib
import hmac
import secrets
from datetime import date

from db import connection, setting

DESK_USERNAME = "reception"
DESK_PASSWORD = "reception@123"


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return f"{salt.hex()}${digest.hex()}"


def _password_matches(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        digest = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return hmac.compare_digest(check, digest)


def ensure_account(conn) -> None:
    """Create the reception desk account once. Later sign-ins use the stored hash.

    HAYALI_DESK_PASSWORD in backend/.env sets a different password. It is applied
    when the server starts, and the stored hash is replaced only when it differs.
    """
    chosen = setting("HAYALI_DESK_PASSWORD")
    account = conn.execute(
        "SELECT password_hash FROM reception.account WHERE username = %s",
        (DESK_USERNAME,),
    ).fetchone()
    if account is None:
        conn.execute(
            "INSERT INTO reception.account (username, password_hash) VALUES (%s, %s)",
            (DESK_USERNAME, _hash_password(chosen or DESK_PASSWORD)),
        )
        return
    if chosen and not _password_matches(chosen, account["password_hash"]):
        conn.execute(
            "UPDATE reception.account SET password_hash = %s WHERE username = %s",
            (_hash_password(chosen), DESK_USERNAME),
        )
        print("reception: the desk password was set from HAYALI_DESK_PASSWORD", flush=True)


def prune_sessions() -> None:
    """Sign-ins that ended long ago are removed."""
    with connection() as conn:
        conn.execute(
            """
            DELETE FROM reception.session
            WHERE expires_at < now() - interval '7 days'
               OR ended_at < now() - interval '7 days'
            """
        )


def sign_in(username: str, password: str) -> str | None:
    with connection() as conn:
        account = conn.execute(
            "SELECT password_hash FROM reception.account WHERE username = %s",
            (username.strip(),),
        ).fetchone()
        if account is None or not _password_matches(password, account["password_hash"]):
            return None
        token = secrets.token_urlsafe(32)
        conn.execute(
            """
            INSERT INTO reception.session (token, username, expires_at)
            VALUES (%s, %s, now() + interval '16 hours')
            """,
            (token, username.strip()),
        )
        return token


def sign_out(token: str) -> None:
    with connection() as conn:
        conn.execute(
            """
            UPDATE reception.session
            SET ended_at = now()
            WHERE token = %s AND ended_at IS NULL
            """,
            (token,),
        )


def account_for_token(token: str) -> str | None:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT username
            FROM reception.session
            WHERE token = %s AND ended_at IS NULL AND expires_at > now()
            """,
            (token,),
        ).fetchone()
        return row["username"] if row else None


def _reservation(row: dict) -> dict:
    return {
        "id": row["id"],
        "guest_name": row["guest_name"],
        "start_date": row["start_date"].isoformat(),
        "end_date": row["end_date"].isoformat(),
        "message": row["message"],
        "created_at": row["created_at"].isoformat(),
        "created_by": row["created_by"],
    }


def list_reservations() -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT id, guest_name, start_date, end_date, message, created_at, created_by
            FROM reception.reservation
            ORDER BY created_at DESC, id DESC
            """
        ).fetchall()
    return [_reservation(row) for row in rows]


def current_booking(room_id: str) -> dict | None:
    """The stay that is still open for this room. Past departures are hidden."""
    with connection() as conn:
        row = conn.execute(
            """
            SELECT id, guest_name, start_date, end_date, message, created_at, created_by
            FROM reception.reservation
            WHERE room_id = %s AND end_date >= %s
            ORDER BY start_date ASC, id ASC
            LIMIT 1
            """,
            (room_id, date.today()),
        ).fetchone()
    return _reservation(row) if row else None


def current_bookings() -> dict[str, dict]:
    """The open stay of every room, read in one query for the whole desk."""
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT ON (room_id)
                room_id, id, guest_name, start_date, end_date, message, created_at, created_by
            FROM reception.reservation
            WHERE room_id IS NOT NULL AND end_date >= %s
            ORDER BY room_id, start_date ASC, id ASC
            """,
            (date.today(),),
        ).fetchall()
    return {row["room_id"]: _reservation(row) for row in rows}


def book_room(username: str, room_id: str, guest_name: str, start_date: date, end_date: date) -> dict:
    with connection() as conn:
        row = conn.execute(
            """
            INSERT INTO reception.reservation
                (room_id, guest_name, start_date, end_date, message, created_by)
            VALUES (%s, %s, %s, %s, '', %s)
            RETURNING id, guest_name, start_date, end_date, message, created_at, created_by
            """,
            (room_id, guest_name, start_date, end_date, username),
        ).fetchone()
    return _reservation(row)


def cancel_booking(room_id: str) -> dict | None:
    """End the open stay on this room. Past bookings are left alone."""
    with connection() as conn:
        row = conn.execute(
            """
            DELETE FROM reception.reservation
            WHERE id = (
                SELECT id
                FROM reception.reservation
                WHERE room_id = %s AND end_date >= %s
                ORDER BY start_date ASC, id ASC
                LIMIT 1
            )
            RETURNING id, guest_name, start_date, end_date, message, created_at, created_by
            """,
            (room_id, date.today()),
        ).fetchone()
    return _reservation(row) if row else None


def save_reservation(username: str, guest_name: str, start_date: date, end_date: date, message: str) -> dict:
    with connection() as conn:
        row = conn.execute(
            """
            INSERT INTO reception.reservation
                (guest_name, start_date, end_date, message, created_by)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, guest_name, start_date, end_date, message, created_at, created_by
            """,
            (guest_name, start_date, end_date, message, username),
        ).fetchone()
    return _reservation(row)
