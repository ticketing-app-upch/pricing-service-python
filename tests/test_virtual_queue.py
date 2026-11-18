import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
import unittest
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.services.virtual_queue import QueueError, VirtualQueue


class VirtualQueueTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_url = os.environ.get("VIRTUAL_QUEUE_TEST_DB_URL")
        if not cls.database_url:
            raise unittest.SkipTest("Set VIRTUAL_QUEUE_TEST_DB_URL to a migrated test database.")
        cls.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.now = 1000
        self.event_id = str((uuid4().int % 10**17) + 10**17)
        self.queue = self.new_service()
        self.addCleanup(self.clean_turns)

    def new_service(self):
        return VirtualQueue(self.signing_key, self.database_url, lambda: self.now)

    def clean_turns(self):
        with self.queue.connect() as db:
            db.execute("DELETE FROM virtual_queue_turns WHERE event_id = %s", (self.event_id,))

    def request(self, action, user="1", enabled=True, closed=False, service=None, **extra):
        return (service or self.queue).request(action, {
            "eventId": self.event_id, "userId": user, "salesEnabled": enabled,
            "salesClosed": closed, **extra,
        })

    def test_waiting_order_and_admission_rate_after_concurrent_joins(self):
        second_service = self.new_service()

        def join(user):
            service = self.queue if int(user) % 2 else second_service
            return self.request("join", user, enabled=False, service=service)

        with ThreadPoolExecutor(max_workers=12) as clients:
            snapshots = list(clients.map(join, map(str, range(1, 13))))
        ordered_users = sorted(range(1, 13), key=lambda user: snapshots[user - 1]["position"])
        self.assertEqual(sorted(snapshot["position"] for snapshot in snapshots), list(range(1, 13)))
        first = str(ordered_users[0])
        fourth = str(ordered_users[3])
        self.assertEqual(self.request("join", first, enabled=False)["position"], 1)

        def status(user):
            service = self.queue if user % 2 else second_service
            return self.request("status", str(user), service=service)

        with ThreadPoolExecutor(max_workers=12) as clients:
            states = list(clients.map(status, ordered_users))
        self.assertEqual([state["status"] for state in states], ["ADMITTED"] * 3 + ["WAITING"] * 9)
        self.assertEqual(states[3]["position"], 1)
        self.request("cancel", first)
        self.assertEqual(self.request("status", fourth, service=second_service)["status"], "WAITING")
        self.now += 5
        self.assertEqual(self.request("status", fourth)["status"], "ADMITTED")

    def test_pass_is_signed_bound_to_turn_and_expires_in_three_minutes(self):
        result = self.request("join")
        header, payload, signature = result["passToken"].split(".")
        self.signing_key.public_key().verify(
            base64.urlsafe_b64decode(signature + "=="),
            f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256(),
        )
        claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
        self.assertEqual((claims["sub"], claims["eventId"]), ("1", self.event_id))
        self.assertEqual(result["queueToken"], claims["jti"])
        self.assertEqual(claims["exp"] - claims["iat"], 180)
        self.assertTrue(self.request("validate", turnId=claims["jti"])["valid"])
        self.request("open-payment", openedAt=self.now)
        self.now += 179
        self.request("open-payment", openedAt=self.now)
        with self.queue.connect() as db:
            turn = db.execute(
                "SELECT payment_opened_at FROM virtual_queue_turns WHERE turn_id = %s",
                (result["queueToken"],),
            ).fetchone()
        self.assertEqual(turn["payment_opened_at"], self.now - 179)
        self.now += 1
        with self.assertRaisesRegex(QueueError, "vencido"):
            self.request("validate", turnId=claims["jti"])
        expired = self.request("status")
        self.assertEqual(expired["status"], "EXPIRED")
        self.assertEqual(expired["queueToken"], result["queueToken"])
        new_turn = self.request("join")
        self.assertNotEqual(new_turn["passToken"], result["passToken"])
        self.assertNotEqual(new_turn["queueToken"], result["queueToken"])

    def test_completion_is_idempotent_and_sellout_closes_other_turns(self):
        snapshots = {user: self.request("join", user) for user in ["1", "2", "3", "4"]}
        self.assertEqual(snapshots["4"]["status"], "WAITING")
        turn_id = snapshots["1"]["queueToken"]
        self.request("complete", turnId=turn_id, soldOut=True)
        self.request("complete", turnId=turn_id, soldOut=True)
        self.assertEqual(self.request("status", closed=True)["status"], "COMPLETED")
        for user in ("2", "3", "4"):
            self.assertEqual(self.request("status", user, closed=True)["status"], "CLOSED")
        self.assertEqual(self.request("join", "5", closed=True)["status"], "CLOSED")

    def test_waiting_turns_and_admitted_passes_survive_service_restart(self):
        admitted = [self.request("join", user) for user in ("1", "2", "3")]
        waiting = self.request("join", "4")
        self.queue = self.new_service()

        self.assertEqual(self.request("status", "4"), waiting)
        self.assertEqual(self.request("status", "1"), admitted[0])
        self.assertTrue(self.request("validate", turnId=admitted[0]["queueToken"])["valid"])
        self.assertEqual(self.request("join", "1"), admitted[0])

    def test_conversion_retains_terminal_outcomes_across_restarts(self):
        expired = self.request("join", "1")["queueToken"]
        cancelled = self.request("join", "2")["queueToken"]
        self.request("cancel", "2")
        self.now += 180
        self.queue = self.new_service()

        outcomes = self.queue.conversion([self.event_id])["outcomes"]
        self.assertEqual({entry["turnId"]: entry["status"] for entry in outcomes}, {
            expired: "EXPIRED",
            cancelled: "CANCELLED",
        })
        self.request("complete", turnId=expired, soldOut=False)
        self.assertEqual(self.queue.conversion([self.event_id]), {
            "outcomes": [{"turnId": cancelled, "status": "CANCELLED"}],
        })


if __name__ == "__main__":
    unittest.main()
