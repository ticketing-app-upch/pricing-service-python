import base64
from datetime import datetime, timezone
import json
from time import time
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
import psycopg
from psycopg.rows import dict_row

PASS_SECONDS = 120
BATCH_SIZE = 3
BATCH_SECONDS = 5
ACTIVE_LIMIT = 3


def iso_date(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def encode_segment(content):
    return base64.urlsafe_b64encode(content).rstrip(b"=")


class QueueError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class VirtualQueue:
    def __init__(self, signing_key, database_url, clock=time):
        self.signing_key = signing_key
        self.database_url = database_url
        self.clock = clock

    def connect(self):
        return psycopg.connect(self.database_url, connect_timeout=2, row_factory=dict_row)

    def check_storage(self):
        with self.connect() as db:
            db.execute("SELECT id FROM virtual_queue_turns LIMIT 1").fetchone()

    def request(self, action, data):
        event_id = data["eventId"]
        with self.connect() as db:
            # Laravel holds its own row lock on events while calling this API.
            # Serialize Python's queue operations independently, across processes.
            db.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"alpateck:virtual-queue:{event_id}",),
            )
            now = self.clock()
            if action == "complete":
                return self.complete(db, event_id, data["turnId"], data["soldOut"], now)

            enabled = data["salesEnabled"]
            closed = data["salesClosed"]
            self.advance(db, event_id, now, enabled, closed)
            if action == "advance":
                return {}

            # Keep any expirations and admissions even if this caller has no valid turn.
            try:
                return self.request_turn(db, action, data, now, enabled, closed)
            except QueueError as error:
                failure = error

        raise failure

    def request_turn(self, db, action, data, now, enabled, closed):
        event_id = data["eventId"]
        user_id = data["userId"]
        turn = db.execute(
            """SELECT * FROM virtual_queue_turns
               WHERE event_id = %s AND user_id = %s ORDER BY id DESC LIMIT 1""",
            (event_id, user_id),
        ).fetchone()

        if action == "join":
            if closed and (turn is None or turn["status"] not in {"CLOSED", "COMPLETED"}):
                turn = self.create_turn(db, event_id, user_id, now, closed)
            elif not closed and (turn is None or turn["status"] not in {"WAITING", "ADMITTED"}):
                turn = self.create_turn(db, event_id, user_id, now, closed)
                self.advance(db, event_id, now, enabled, closed)
                turn = db.execute("SELECT * FROM virtual_queue_turns WHERE id = %s", (turn["id"],)).fetchone()
            return self.snapshot(db, turn, enabled)

        if action == "status":
            if turn is None:
                raise QueueError(404, "No encontramos un turno para este evento.")
            return self.snapshot(db, turn, enabled)

        if action == "cancel":
            if turn and turn["status"] == "ADMITTED":
                db.execute("UPDATE virtual_queue_turns SET status = 'CANCELLED' WHERE id = %s", (turn["id"],))
                self.advance(db, event_id, now, enabled, closed)
            return {}

        if turn is None or turn["status"] != "ADMITTED" or not enabled:
            raise QueueError(403, "Tu pase de compra no es válido o ha vencido.")

        if action == "open-payment":
            db.execute(
                """UPDATE virtual_queue_turns SET payment_opened_at = %s
                   WHERE id = %s AND payment_opened_at IS NULL""",
                (data["openedAt"], turn["id"]),
            )
            return {}

        if str(turn["turn_id"]) != data["turnId"]:
            raise QueueError(403, "Tu pase de compra no es válido o ha vencido.")
        return {"valid": True}

    def create_turn(self, db, event_id, user_id, now, closed):
        position = 0
        if not closed:
            position = db.execute(
                """SELECT count(*) + 1 AS position FROM virtual_queue_turns
                   WHERE event_id = %s AND status = 'WAITING'""",
                (event_id,),
            ).fetchone()["position"]
        return db.execute(
            """INSERT INTO virtual_queue_turns
                   (turn_id, event_id, user_id, status, joined_at, initial_position)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
            (uuid4(), event_id, user_id, "CLOSED" if closed else "WAITING", now, position),
        ).fetchone()

    def advance(self, db, event_id, now, enabled, closed):
        db.execute(
            """UPDATE virtual_queue_turns SET status = 'EXPIRED'
               WHERE event_id = %s AND status = 'ADMITTED' AND expires_at <= %s""",
            (event_id, now),
        )
        if closed:
            db.execute(
                """UPDATE virtual_queue_turns SET status = 'CLOSED'
                   WHERE event_id = %s AND status IN ('WAITING', 'ADMITTED')""",
                (event_id,),
            )
            return
        if not enabled:
            return

        counts = db.execute(
            """SELECT
                 (SELECT count(*) FROM virtual_queue_turns
                  WHERE event_id = %s AND status = 'ADMITTED') AS active,
                 (SELECT count(*) FROM virtual_queue_turns
                  WHERE event_id = %s AND admitted_at > %s) AS recent""",
            (event_id, event_id, now - BATCH_SECONDS),
        ).fetchone()
        capacity = min(ACTIVE_LIMIT - counts["active"], BATCH_SIZE - counts["recent"])
        if capacity <= 0:
            return

        db.execute(
            """UPDATE virtual_queue_turns
               SET status = 'ADMITTED', admitted_at = %s, expires_at = %s
               WHERE id IN (
                   SELECT id FROM virtual_queue_turns
                   WHERE event_id = %s AND status = 'WAITING'
                   ORDER BY id LIMIT %s
               )""",
            (now, int(now) + PASS_SECONDS, event_id, capacity),
        )

    def snapshot(self, db, turn, enabled):
        position = 0
        if turn["status"] == "WAITING":
            position = db.execute(
                """SELECT count(*) AS position FROM virtual_queue_turns
                   WHERE event_id = %s AND status = 'WAITING' AND id <= %s""",
                (turn["event_id"], turn["id"]),
            ).fetchone()["position"]

        result = {
            "eventId": turn["event_id"],
            "queueToken": str(turn["turn_id"]),
            "status": turn["status"],
            "position": position,
            "initialPosition": turn["initial_position"],
            "etaSeconds": ((position + BATCH_SIZE - 1) // BATCH_SIZE) * BATCH_SECONDS,
            "joinedAt": iso_date(turn["joined_at"]),
            "salesEnabled": enabled,
        }
        if turn["status"] == "ADMITTED":
            result["passToken"] = self.sign(turn)
            result["passExpiresAt"] = iso_date(turn["expires_at"])
        return result

    def sign(self, turn):
        header = encode_segment(b'{"alg":"RS256","typ":"JWT"}')
        claims = encode_segment(json.dumps({
            "iss": "alpateck-virtual-queue",
            "aud": "alpateck-purchase",
            "sub": turn["user_id"],
            "eventId": turn["event_id"],
            "jti": str(turn["turn_id"]),
            "iat": int(turn["admitted_at"]),
            "exp": turn["expires_at"],
        }, separators=(",", ":")).encode())
        payload = header + b"." + claims
        signature = self.signing_key.sign(payload, padding.PKCS1v15(), hashes.SHA256())
        return (payload + b"." + encode_segment(signature)).decode("ascii")

    def complete(self, db, event_id, turn_id, sold_out, now):
        updated = db.execute(
            """UPDATE virtual_queue_turns SET status = 'COMPLETED'
               WHERE event_id = %s AND turn_id = %s AND status IN ('ADMITTED', 'EXPIRED')""",
            (event_id, turn_id),
        )
        if updated.rowcount and sold_out:
            self.advance(db, event_id, now, True, True)
        return {"recorded": True}

    def conversion(self, event_ids):
        with self.connect() as db:
            rows = db.execute(
                """SELECT turn_id, CASE WHEN status = 'ADMITTED' THEN 'EXPIRED' ELSE status END AS outcome
                   FROM virtual_queue_turns
                   WHERE event_id = ANY(%s) AND (
                       status IN ('EXPIRED', 'CANCELLED')
                       OR (status = 'ADMITTED' AND expires_at <= %s)
                   )""",
                (event_ids, self.clock()),
            )
            return {"outcomes": [{"turnId": str(row["turn_id"]), "status": row["outcome"]} for row in rows]}
