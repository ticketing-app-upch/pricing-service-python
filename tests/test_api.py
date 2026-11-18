import unittest
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.virtual_queue import QueueError

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def build_client(queue=None):
    settings = Settings(TOKEN, None, "postgresql://test", "127.0.0.1", 0)
    app = create_app(settings, queue or MagicMock())
    return TestClient(app, raise_server_exceptions=False)


class HealthTest(unittest.TestCase):
    def test_health(self):
        response = build_client().get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


class TurnsAuthTest(unittest.TestCase):
    def test_missing_token_returns_401(self):
        response = build_client().post("/v1/turns/join", json={})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"message": "Credenciales de servicio inválidas."})

    def test_unknown_action_returns_404_before_auth(self):
        response = build_client().post("/v1/turns/nope", json={})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"message": "Ruta no encontrada."})

    def test_unknown_route_returns_404(self):
        response = build_client().post("/api/v1/other", json={}, headers=AUTH)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"message": "Ruta no encontrada."})


class TurnsValidationTest(unittest.TestCase):
    def setUp(self):
        self.queue = MagicMock()
        self.client = build_client(self.queue)

    def post(self, action, body):
        return self.client.post(f"/v1/turns/{action}", json=body, headers=AUTH)

    def test_invalid_json_body_returns_422(self):
        response = self.client.post(
            "/v1/turns/join", content=b"{", headers={**AUTH, "Content-Type": "application/json"}
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {"message": "Solicitud inválida."})

    def test_invalid_event_id_returns_422(self):
        response = self.post("join", {"eventId": "0", "userId": "1", "salesEnabled": True, "salesClosed": False})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {"message": "Solicitud inválida."})
        self.queue.request.assert_not_called()

    def test_oversized_body_returns_422(self):
        body = {"eventId": "1", "userId": "1", "salesEnabled": True, "salesClosed": False, "pad": "x" * 5000}
        response = self.post("join", body)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {"message": "Solicitud inválida."})

    def test_queue_error_maps_status_and_message(self):
        self.queue.request.side_effect = QueueError(403, "Tu pase de compra no es válido o ha vencido.")
        response = self.post("join", {"eventId": "1", "userId": "1", "salesEnabled": True, "salesClosed": False})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"message": "Tu pase de compra no es válido o ha vencido."})

    def test_join_success_returns_queue_payload(self):
        self.queue.request.return_value = {"status": "WAITING", "position": 1}
        response = self.post("join", {"eventId": "1", "userId": "1", "salesEnabled": True, "salesClosed": False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "WAITING", "position": 1})
        self.queue.request.assert_called_once_with("join", {
            "eventId": "1", "userId": "1", "salesEnabled": True, "salesClosed": False,
        })

    def test_conversion_uses_conversion_method(self):
        self.queue.conversion.return_value = {"outcomes": []}
        response = self.post("conversion", {"eventIds": ["1"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"outcomes": []})
        self.queue.conversion.assert_called_once_with(["1"])


class RiskScoreTest(unittest.TestCase):
    def setUp(self):
        self.client = build_client()

    def post(self, body, headers=AUTH):
        return self.client.post("/api/v1/risk-score", json=body, headers=headers)

    def test_block_at_threshold(self):
        response = self.post({"time_elapsed_seconds": 1.5, "account_age_hours": 10.0, "tickets_requested": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"risk_score": 70.0, "action": "BLOCK"})

    def test_allow_below_threshold(self):
        response = self.post({"time_elapsed_seconds": 60.0, "account_age_hours": 1000.0, "tickets_requested": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"risk_score": 0.0, "action": "ALLOW"})

    def test_requires_service_token(self):
        response = self.post(
            {"time_elapsed_seconds": 1.0, "account_age_hours": 1.0, "tickets_requested": 1}, headers={}
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"message": "Credenciales de servicio inválidas."})

    def test_rejects_tickets_out_of_range(self):
        response = self.post({"time_elapsed_seconds": 1.0, "account_age_hours": 1.0, "tickets_requested": 7})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {"message": "Solicitud inválida."})

    def test_rejects_negative_values(self):
        response = self.post({"time_elapsed_seconds": -1.0, "account_age_hours": 1.0, "tickets_requested": 1})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {"message": "Solicitud inválida."})

    def test_rejects_missing_fields(self):
        response = self.post({})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {"message": "Solicitud inválida."})


if __name__ == "__main__":
    unittest.main()
