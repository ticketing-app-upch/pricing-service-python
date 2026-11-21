import uvicorn
from psycopg import Error as DatabaseError

from app.config import Settings
from app.main import create_app
from app.services.virtual_queue import VirtualQueue


def main():
    settings = Settings.from_env()
    queue = VirtualQueue(settings.signing_key, settings.database_url)
    try:
        queue.check_storage()
    except DatabaseError as error:
        raise SystemExit(
            f"Queue storage unavailable ({type(error).__name__}). Check the database and Laravel migrations."
        ) from None
    app = create_app(settings, queue)
    print(f"Virtual queue listening on {settings.host}:{settings.port}", flush=True)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
