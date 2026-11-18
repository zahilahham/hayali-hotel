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
        INSERT INTO laundry.request (room_id, status, start_time, source, requested_by)
        SELECT %s, 'requested', now(), %s, %s
        WHERE NOT EXISTS (
            SELECT 1 FROM laundry.request
            WHERE room_id = %s AND status IN ('requested', 'accepted')
        )
        """,
        (room_id, source, requested_by, room_id),
    )


def close_request(conn, room_id: str, status: str = "canceled") -> None:
    """End the open row. The small screen turns laundry off."""
    if status not in ("acknowledged", "canceled"):
        raise ValueError(status)
    conn.execute(
        """
        UPDATE laundry.request
        SET status = %s, end_time = now()
        WHERE room_id = %s AND status IN ('requested', 'accepted')
        """,
        (status, room_id),
    )


def take(room_id: str) -> bool:
    """Laundry accepts the order. The room still turns it off."""
    with connection() as conn:
        row = conn.execute(
            """
            UPDATE laundry.request
            SET status = 'accepted'
            WHERE room_id = %s AND status = 'requested'
            RETURNING id
            """,
            (room_id,),
        ).fetchone()
    return row is not None


def board() -> dict:
    """Open pickups, plus calls that ended in the last half hour."""
    now = datetime.now(timezone.utc)
    with connection() as conn:
        open_rows = conn.execute(
            """
            SELECT id, room_id, start_time, source, status
            FROM laundry.request
            WHERE status IN ('requested', 'accepted')
            ORDER BY start_time ASC
            """
        ).fetchall()
        recent_rows = conn.execute(
            """
            SELECT id, room_id, status, start_time, end_time
            FROM laundry.request
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
                "stage": "taken" if row["status"] == "accepted" else "new",
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
