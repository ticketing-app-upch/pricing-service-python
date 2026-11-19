import json
import logging

from fastapi import APIRouter, HTTPException, Request
from psycopg import Error as DatabaseError

from app.auth import require_service_token
from app.services.virtual_queue import QueueError, VirtualQueue
from app.validators import valid_request

router = APIRouter(tags=["turns"])
ACTIONS = {"join", "status", "cancel", "open-payment", "validate", "complete", "advance", "conversion"}


@router.post("/v1/turns/{action}")
async def turns(action: str, request: Request):
    if action not in ACTIONS:
        raise HTTPException(404, {"message": "Ruta no encontrada."})
    require_service_token(request)

    raw = await request.body()
    try:
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(422, {"message": "Solicitud inválida."}) from None

    if not valid_request(action, data):
        raise HTTPException(422, {"message": "Solicitud inválida."})

    queue: VirtualQueue = request.app.state.queue
    try:
        result = queue.conversion(data["eventIds"]) if action == "conversion" else queue.request(action, data)
    except QueueError as error:
        raise HTTPException(error.status, {"message": str(error)}) from None
    except DatabaseError as error:
        logging.error("Queue storage failed (%s): %s", type(error).__name__, error)
        raise HTTPException(503, {"message": "La cola virtual no está disponible."}) from None
    return result
