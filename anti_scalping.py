WEIGHT_SPEED = 0.40
WEIGHT_ACCOUNT = 0.30
WEIGHT_TICKETS = 0.30
RISK_THRESHOLD = 70.0
MIN_TICKETS = 1
MAX_TICKETS = 6


def valid_risk_request(data):
    if not isinstance(data, dict):
        return False
    elapsed = data.get("time_elapsed_seconds")
    age = data.get("account_age_hours")
    tickets = data.get("tickets_requested")
    return (
        type(elapsed) in (int, float) and elapsed >= 0
        and type(age) in (int, float) and age >= 0
        and type(tickets) is int and MIN_TICKETS <= tickets <= MAX_TICKETS
    )


def speed_score(time_elapsed_seconds):
    if time_elapsed_seconds < 2.0:
        return 100.0
    if time_elapsed_seconds <= 5.0:
        return 100.0 - ((time_elapsed_seconds - 2.0) / 3.0) * 100.0
    return 0.0


def account_score(account_age_hours):
    if account_age_hours < 24.0:
        return 100.0
    if account_age_hours <= 720.0:
        return 100.0 - ((account_age_hours - 24.0) / 696.0) * 100.0
    return 0.0


def tickets_score(tickets_requested):
    if tickets_requested >= 5:
        return 100.0
    if tickets_requested >= 3:
        return 50.0
    return 0.0


def calculate_risk_score(data):
    risk = (
        WEIGHT_SPEED * speed_score(data["time_elapsed_seconds"])
        + WEIGHT_ACCOUNT * account_score(data["account_age_hours"])
        + WEIGHT_TICKETS * tickets_score(data["tickets_requested"])
    )
    return {
        "risk_score": round(risk, 2),
        "action": "BLOCK" if risk >= RISK_THRESHOLD else "ALLOW",
    }
