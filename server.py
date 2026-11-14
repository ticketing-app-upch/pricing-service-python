from base64 import b64decode
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from secrets import compare_digest
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from virtual_queue import QueueError, VirtualQueue

TURN_ROUTE = "/v1/turns/"
ACTIONS = {"join", "status", "cancel", "open-payment", "validate", "complete", "advance", "conversion"}


def valid_request(action, data):
    if action == "conversion":
        event_ids = data.get("eventIds") if isinstance(data, dict) else None
        return isinstance(event_ids, list) and 0 < len(event_ids) <= 100 and all(map(valid_id, event_ids))

    if not isinstance(data, dict) or not valid_id(data.get("eventId")):
        return False
    if action in {"validate", "complete"}:
        turn_id = data.get("turnId")
        if not isinstance(turn_id, str):
            return False
        try:
            if str(UUID(turn_id)) != turn_id:
                return False
        except ValueError:
            return False
    if action == "complete":
        return isinstance(data.get("soldOut"), bool)
    if action == "open-payment" and type(data.get("openedAt")) is not int:
        return False
    return (
        (action == "advance" or valid_id(data.get("userId")))
        and isinstance(data.get("salesEnabled"), bool)
        and isinstance(data.get("salesClosed"), bool)
    )


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,19}", value) is not None


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"status": "ok"})
        else:
            self.send_json(404, {"message": "Ruta no encontrada."})

    def do_POST(self):
        action = self.path.removeprefix(TURN_ROUTE)
        if not self.path.startswith(TURN_ROUTE) or action not in ACTIONS:
            self.send_json(404, {"message": "Ruta no encontrada."})
            return

        supplied_token = self.headers.get("Authorization", "").encode()
        if not compare_digest(supplied_token, self.server.authorization):
            self.send_json(401, {"message": "Credenciales de servicio inválidas."})
            return

        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096:
                raise ValueError("Invalid body size")
            data = json.loads(self.rfile.read(size))
        except (ValueError, TimeoutError):
            self.send_json(422, {"message": "Solicitud inválida."})
            return

        if not valid_request(action, data):
            self.send_json(422, {"message": "Solicitud inválida."})
            return

        try:
            result = (
                self.server.queue.conversion(data["eventIds"])
                if action == "conversion"
                else self.server.queue.request(action, data)
            )
        except QueueError as error:
            self.send_json(error.status, {"message": str(error)})
            return
        self.send_json(200, result)

    def setup(self):
        self.request.settimeout(5)
        super().setup()

    def send_json(self, status, body):
        encoded = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)


def main():
    token = os.environ.get("VIRTUAL_QUEUE_SERVICE_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("Configure VIRTUAL_QUEUE_SERVICE_TOKEN with at least 32 characters.")
    try:
        pem = b64decode(os.environ["VIRTUAL_QUEUE_PASS_PRIVATE_KEY"], validate=True)
        key = load_pem_private_key(pem, password=None)
    except (KeyError, ValueError, TypeError):
        raise SystemExit("Configure VIRTUAL_QUEUE_PASS_PRIVATE_KEY as Base64-encoded RSA PEM.") from None
    if not isinstance(key, RSAPrivateKey) or key.key_size < 2048:
        raise SystemExit("The purchase pass requires an RSA private key of at least 2048 bits.")

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    with ThreadingHTTPServer((host, port), Handler) as server:
        server.queue = VirtualQueue(key, outcomes_path=os.environ.get("VIRTUAL_QUEUE_OUTCOMES_DB", "queue-outcomes.sqlite3"))
        server.authorization = f"Bearer {token}".encode()
        print(f"Virtual queue listening on {host}:{port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
