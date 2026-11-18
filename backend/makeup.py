from datetime import datetime, timezone

from db import connection


def _seconds(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds()))


def open_request(conn, room_id: str, source: str = "room", requested_by: str = "") -> None:
    """One open row, with a start time. The room or reception can open it."""
    if source not in ("room", "reception"):
        raise ValueError(source)
    conn.execute(
        """
        INSERT INTO makeup.request (room_id, status, start_time, source, requested_by)
        SELECT %s, 'requested', now(), %s, %s
        WHERE NOT EXISTS (
            SELECT 1 FROM makeup.request
            WHERE room_id = %s AND status = 'requested'
        )
        """,
        (room_id, source, requested_by, room_id),
    )


def close_request(conn, room_id: str, status: str = "canceled") -> None:
    """End the open row. The small screen turns make up room off."""
    if status not in ("acknowledged", "canceled"):
        raise ValueError(status)
    conn.execute(
        """
        UPDATE makeup.request
        SET status = %s, end_time = now()
        WHERE room_id = %s AND status = 'requested'
        """,
        (status, room_id),
    )


def board() -> dict:
    """Open make up calls, plus calls that ended in the last half hour."""
    now = datetime.now(timezone.utc)
    with connection() as conn:
        open_rows = conn.execute(
            """
            SELECT id, room_id, start_time, source
            FROM makeup.request
            WHERE status = 'requested'
            ORDER BY start_time ASC
            """
        ).fetchall()
        recent_rows = conn.execute(
            """
            SELECT id, room_id, status, start_time, end_time
            FROM makeup.request
            WHERE status IN ('acknowledged', 'canceled')
              AND end_time IS NOT NULL
              AND end_time > now() - interval '30 minutes'
            ORDER BY end_time DESC
            LIMIT 12
            """
        ).fetchall()
    return {
        "now": now.isoformat(),
        "open": [
            {
                "id": row["id"],
                "room": row["room_id"],
                "start_time": row["start_time"].isoformat(),
                "waiting_seconds": _seconds(row["start_time"], now),
                "source": row["source"],
            }
            for row in open_rows
        ],
        "recent": [
            {
                "id": row["id"],
                "room": row["room_id"],
                "status": row["status"],
                "start_time": row["start_time"].isoformat(),
                "end_time": row["end_time"].isoformat(),
                "response_seconds": _seconds(row["start_time"], row["end_time"]),
            }
            for row in recent_rows
        ],
    }
