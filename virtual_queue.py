import base64
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from threading import Lock
from time import time
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

PASS_SECONDS = 180
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


@dataclass
class Turn:
    id: str
    user_id: str
    joined_at: float
    initial_position: int
    status: str = "WAITING"
    expires_at: int | None = None
    pass_token: str | None = None
    payment_opened_at: int | None = None


class EventQueue:
    def __init__(self, event_id, signing_key, outcomes):
        self.event_id = event_id
        self.signing_key = signing_key
        self.outcomes = outcomes
        self.turns = {}
        self.latest = {}
        self.waiting = {}
        self.admitted = {}
        self.recent_admissions = deque()

    def advance(self, now, sales_enabled, sales_closed):
        self.expire(now)
        if sales_closed:
            for turn in (*self.waiting.values(), *self.admitted.values()):
                turn.status = "CLOSED"
                turn.pass_token = None
            self.waiting.clear()
            self.admitted.clear()
            return

        if not sales_enabled:
            return

        while self.recent_admissions and self.recent_admissions[0] <= now - BATCH_SECONDS:
            self.recent_admissions.popleft()

        capacity = min(ACTIVE_LIMIT - len(self.admitted), BATCH_SIZE - len(self.recent_admissions))
        for _ in range(min(capacity, len(self.waiting))):
            turn = self.waiting.pop(next(iter(self.waiting)))
            turn.status = "ADMITTED"
            issued_at = int(now)
            turn.expires_at = issued_at + PASS_SECONDS
            turn.pass_token = self.sign(turn, issued_at)
            self.admitted[turn.id] = turn
            self.recent_admissions.append(now)

    def expire(self, now):
        for turn in list(self.admitted.values()):
            if turn.expires_at <= now:
                self.finish(turn, "EXPIRED")

    def finish(self, turn, status):
        if status in {"EXPIRED", "CANCELLED"}:
            with self.outcomes:
                self.outcomes.execute(
                    "INSERT OR IGNORE INTO queue_outcomes VALUES (?, ?, ?)",
                    (turn.id, self.event_id, status),
                )
        self.admitted.pop(turn.id)
        turn.status = status
        turn.pass_token = None

    def sign(self, turn, now):
        header = encode_segment(b'{"alg":"RS256","typ":"JWT"}')
        claims = encode_segment(json.dumps({
            "iss": "alpateck-virtual-queue",
            "aud": "alpateck-purchase",
            "sub": turn.user_id,
            "eventId": self.event_id,
            "jti": turn.id,
            "iat": now,
            "exp": turn.expires_at,
        }, separators=(",", ":")).encode())
        payload = header + b"." + claims
        signature = self.signing_key.sign(payload, padding.PKCS1v15(), hashes.SHA256())
        return (payload + b"." + encode_segment(signature)).decode("ascii")

    def snapshot(self, turn, sales_enabled):
        position = 0
        if turn.status == "WAITING":
            position = next(index for index, turn_id in enumerate(self.waiting, 1) if turn_id == turn.id)

        result = {
            "eventId": self.event_id,
            "queueToken": turn.id,
            "status": turn.status,
            "position": position,
            "initialPosition": turn.initial_position,
            "etaSeconds": ((position + BATCH_SIZE - 1) // BATCH_SIZE) * BATCH_SECONDS,
            "joinedAt": iso_date(turn.joined_at),
            "salesEnabled": sales_enabled,
        }
        if turn.status == "ADMITTED":
            result["passToken"] = turn.pass_token
            result["passExpiresAt"] = iso_date(turn.expires_at)
        return result


class VirtualQueue:
    def __init__(self, signing_key, clock=time, outcomes_path=":memory:"):
        self.signing_key = signing_key
        self.clock = clock
        self.events = {}
        self.lock = Lock()
        self.outcomes = sqlite3.connect(outcomes_path, check_same_thread=False)
        with self.outcomes:
            self.outcomes.execute("""
                CREATE TABLE IF NOT EXISTS queue_outcomes (
                    turn_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, status TEXT NOT NULL
                )
            """)
            self.outcomes.execute("CREATE INDEX IF NOT EXISTS queue_outcomes_event ON queue_outcomes(event_id)")

    def conversion(self, event_ids):
        with self.lock:
            now = self.clock()
            for event_id in event_ids:
                event = self.events.get(event_id)
                if event is not None:
                    event.expire(now)
            placeholders = ",".join("?" for _ in event_ids)
            rows = self.outcomes.execute(
                f"SELECT turn_id, status FROM queue_outcomes WHERE event_id IN ({placeholders})",
                event_ids,
            )
            return {"outcomes": [{"turnId": turn_id, "status": status} for turn_id, status in rows]}

    def request(self, action, data):
        event_id = data["eventId"]
        with self.lock:
            if action == "complete":
                return self.complete(event_id, data["turnId"], data["soldOut"])

            if event_id not in self.events:
                self.events[event_id] = EventQueue(event_id, self.signing_key, self.outcomes)
            event = self.events[event_id]
            now = self.clock()
            enabled = data["salesEnabled"]
            closed = data["salesClosed"]
            event.advance(now, enabled, closed)
            if action == "advance":
                return {}

            user_id = data["userId"]
            turn = event.latest.get(user_id)

            if action == "join":
                if closed and (turn is None or turn.status not in {"CLOSED", "COMPLETED"}):
                    turn = Turn(str(uuid4()), user_id, now, 0, status="CLOSED")
                    event.latest[user_id] = turn
                elif not closed and (turn is None or turn.status not in {"WAITING", "ADMITTED"}):
                    turn = Turn(str(uuid4()), user_id, now, len(event.waiting) + 1)
                    event.turns[turn.id] = event.latest[user_id] = event.waiting[turn.id] = turn
                    event.advance(now, enabled, closed)
                return event.snapshot(turn, enabled)

            if action == "status":
                if turn is None:
                    raise QueueError(404, "No encontramos un turno para este evento.")
                return event.snapshot(turn, enabled)

            if action == "cancel":
                if turn and turn.status == "ADMITTED":
                    event.finish(turn, "CANCELLED")
                    event.advance(now, enabled, closed)
                return {}

            if turn is None or turn.status != "ADMITTED" or not enabled:
                raise QueueError(403, "Tu pase de compra no es válido o ha vencido.")

            if action == "open-payment":
                if turn.payment_opened_at is None:
                    turn.payment_opened_at = data["openedAt"]
                return {}

            if turn.id != data["turnId"]:
                raise QueueError(403, "Tu pase de compra no es válido o ha vencido.")
            return {"valid": True}

    def complete(self, event_id, turn_id, sold_out):
        with self.outcomes:
            self.outcomes.execute("DELETE FROM queue_outcomes WHERE turn_id = ?", (turn_id,))
        event = self.events.get(event_id)
        if event is None:
            return {"recorded": True}

        turn = event.turns.get(turn_id)
        if turn is not None and turn.status in {"ADMITTED", "EXPIRED"}:
            event.admitted.pop(turn.id, None)
            turn.status = "COMPLETED"
            turn.pass_token = None
            if sold_out:
                event.advance(int(self.clock()), True, True)
        return {"recorded": True}
