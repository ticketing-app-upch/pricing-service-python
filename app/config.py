import os
from base64 import b64decode

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key


class Settings:
    def __init__(self, service_token, signing_key, database_url, host, port):
        self.service_token = service_token
        self.signing_key = signing_key
        self.database_url = database_url
        self.host = host
        self.port = port

    @property
    def authorization_header(self):
        return f"Bearer {self.service_token}".encode()

    @classmethod
    def from_env(cls):
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
        database_url = os.environ.get("VIRTUAL_QUEUE_DB_URL")
        if not database_url:
            raise SystemExit("Configure VIRTUAL_QUEUE_DB_URL for the shared PostgreSQL database.")
        host = os.environ.get("HOST", "0.0.0.0")
        port = int(os.environ.get("PORT", "8000"))
        return cls(token, key, database_url, host, port)
