import re
from uuid import UUID


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,19}", value) is not None


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
