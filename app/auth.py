from secrets import compare_digest

from fastapi import HTTPException, Request


def require_service_token(request: Request):
    supplied = request.headers.get("Authorization", "").encode()
    if not compare_digest(supplied, request.app.state.settings.authorization_header):
        raise HTTPException(401, {"message": "Credenciales de servicio inválidas."})
