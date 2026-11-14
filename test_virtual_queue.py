import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from virtual_queue import QueueError, VirtualQueue


class VirtualQueueTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.now = 1000
        self.queue = VirtualQueue(self.signing_key, lambda: self.now)

    def request(self, action, user="1", enabled=True, closed=False, **extra):
        return self.queue.request(action, {
            "eventId": "42", "userId": user, "salesEnabled": enabled,
            "salesClosed": closed, **extra,
        })

    def test_waiting_order_and_admission_rate_after_concurrent_joins(self):
        with ThreadPoolExecutor(max_workers=12) as clients:
            snapshots = list(clients.map(lambda user: self.request("join", user, enabled=False), map(str, range(1, 13))))
        ordered_users = sorted(range(1, 13), key=lambda user: snapshots[user - 1]["position"])
        self.assertEqual(sorted(snapshot["position"] for snapshot in snapshots), list(range(1, 13)))
        first = str(ordered_users[0])
        fourth = str(ordered_users[3])
        self.assertEqual(self.request("join", first, enabled=False)["position"], 1)
        self.assertEqual(self.request("status", fourth)["position"], 1)
        self.assertEqual(self.request("status", first)["status"], "ADMITTED")
        self.request("cancel", first)
        self.assertEqual(self.request("status", fourth)["status"], "WAITING")
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
        self.assertEqual((claims["sub"], claims["eventId"]), ("1", "42"))
        self.assertEqual(result["queueToken"], claims["jti"])
        self.assertEqual(claims["exp"] - claims["iat"], 180)
        self.assertTrue(self.request("validate", turnId=claims["jti"])["valid"])
        self.request("open-payment", openedAt=self.now)
        self.now += 179
        self.request("open-payment", openedAt=self.now)
        self.assertEqual(self.queue.events["42"].latest["1"].payment_opened_at, self.now - 179)
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
        turn_id = self.queue.events["42"].latest["1"].id
        self.request("complete", turnId=turn_id, soldOut=True)
        self.request("complete", turnId=turn_id, soldOut=True)
        self.assertEqual(self.request("status", closed=True)["status"], "COMPLETED")
        for user in ("2", "3", "4"):
            self.assertEqual(self.request("status", user, closed=True)["status"], "CLOSED")
        self.assertEqual(self.request("join", "5", closed=True)["status"], "CLOSED")

    def test_conversion_retains_terminal_outcomes_across_restarts(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "outcomes.sqlite3"
            self.queue = VirtualQueue(self.signing_key, lambda: self.now, path)
            expired = self.request("join", "1")["queueToken"]
            cancelled = self.request("join", "2")["queueToken"]
            self.request("cancel", "2")
            self.now += 180
            self.queue.conversion(["42"])
            self.queue.outcomes.close()

            restarted = VirtualQueue(self.signing_key, lambda: self.now, path)
            self.assertEqual(restarted.conversion(["99"]), {"outcomes": []})
            counts = {entry["turnId"]: entry["status"] for entry in restarted.conversion(["42"])["outcomes"]}
            self.assertEqual(counts, {expired: "EXPIRED", cancelled: "CANCELLED"})
            restarted.request("complete", {"eventId": "42", "turnId": expired, "soldOut": False})
            self.assertEqual(restarted.conversion(["42"]), {"outcomes": [{"turnId": cancelled, "status": "CANCELLED"}]})
            restarted.outcomes.close()


if __name__ == "__main__":
    unittest.main()
