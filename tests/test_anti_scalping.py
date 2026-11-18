import unittest

from app.services.anti_scalping import (
    account_score,
    calculate_risk_score,
    speed_score,
    tickets_score,
    valid_risk_request,
)


def request(time_elapsed_seconds, account_age_hours, tickets_requested):
    return {
        "time_elapsed_seconds": time_elapsed_seconds,
        "account_age_hours": account_age_hours,
        "tickets_requested": tickets_requested,
    }


class AntiScalpingTest(unittest.TestCase):
    def test_speed_score_boundaries(self):
        for dt, expected in [(1.5, 100.0), (2.0, 100.0), (3.5, 50.0), (5.0, 0.0), (6.0, 0.0)]:
            with self.subTest(dt=dt):
                self.assertEqual(speed_score(dt), expected)

    def test_account_score_boundaries(self):
        for age, expected in [(10.0, 100.0), (24.0, 100.0), (372.0, 50.0), (720.0, 0.0), (800.0, 0.0)]:
            with self.subTest(age=age):
                self.assertEqual(account_score(age), expected)

    def test_tickets_score_all_values_in_range(self):
        for tickets, expected in [(1, 0.0), (2, 0.0), (3, 50.0), (4, 50.0), (5, 100.0), (6, 100.0)]:
            with self.subTest(tickets=tickets):
                self.assertEqual(tickets_score(tickets), expected)

    def test_block_at_exact_threshold(self):
        result = calculate_risk_score(request(1.5, 10.0, 1))
        self.assertEqual(result, {"risk_score": 70.0, "action": "BLOCK"})

    def test_allow_just_below_threshold(self):
        result = calculate_risk_score(request(2.0075, 10.0, 1))
        self.assertEqual(result["action"], "ALLOW")
        self.assertEqual(result["risk_score"], 69.9)

    def test_risk_score_is_rounded_to_two_decimals(self):
        result = calculate_risk_score(request(4.0, 100.0, 3))
        risk = result["risk_score"]
        self.assertEqual(risk, round(risk, 2))
        self.assertAlmostEqual(risk, round(
            0.40 * speed_score(4.0) + 0.30 * account_score(100.0) + 0.30 * tickets_score(3), 2,
        ))

    def test_maximum_risk_blocks(self):
        result = calculate_risk_score(request(0.0, 0.0, 6))
        self.assertEqual(result, {"risk_score": 100.0, "action": "BLOCK"})

    def test_minimum_risk_allows(self):
        result = calculate_risk_score(request(60.0, 1000.0, 1))
        self.assertEqual(result, {"risk_score": 0.0, "action": "ALLOW"})

    def test_valid_request_accepts_boundary_values(self):
        self.assertTrue(valid_risk_request(request(0, 0, 1)))
        self.assertTrue(valid_risk_request(request(0.0, 0.0, 6)))
        self.assertTrue(valid_risk_request(request(120.5, 8760.0, 3)))

    def test_valid_request_rejects_out_of_range_tickets(self):
        for tickets in (0, 7, -1, 1.0, True, None):
            with self.subTest(tickets=tickets):
                self.assertFalse(valid_risk_request(request(10.0, 10.0, tickets)))

    def test_valid_request_rejects_negative_numbers(self):
        self.assertFalse(valid_risk_request(request(-0.1, 10.0, 2)))
        self.assertFalse(valid_risk_request(request(10.0, -1.0, 2)))

    def test_valid_request_rejects_missing_or_malformed_fields(self):
        self.assertFalse(valid_risk_request(None))
        self.assertFalse(valid_risk_request([]))
        self.assertFalse(valid_risk_request({}))
        self.assertFalse(valid_risk_request(request(None, 10.0, 2)))
        self.assertFalse(valid_risk_request(request(10.0, None, 2)))
        self.assertFalse(valid_risk_request(request(10.0, 10.0, None)))
        self.assertFalse(valid_risk_request({"time_elapsed_seconds": True, "account_age_hours": 1, "tickets_requested": 1}))
        self.assertFalse(valid_risk_request({"time_elapsed_seconds": 1, "account_age_hours": False, "tickets_requested": 1}))


if __name__ == "__main__":
    unittest.main()
