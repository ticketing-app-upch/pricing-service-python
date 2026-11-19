#!/usr/bin/env python3
"""Simulador anti-scalping: compara compras de bot vs humano contra el motor de riesgo.

Uso:
    python scripts/simulate_bot.py                  # motor local (puro, sin red)
    python scripts/simulate_bot.py --mode http \
        --url https://pricing-service-python.onrender.com \
        --token $VIRTUAL_QUEUE_SERVICE_TOKEN        # endpoint real desplegado

Cada escenario envía time_elapsed_seconds, account_age_hours y tickets_requested
—los mismos tres campos que Laravel POSTea a /api/v1/risk-score— e imprime
risk_score, action (ALLOW/BLOCK) y el desglose SV/SA/SN.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from app.services.anti_scalping import (  # noqa: E402
    WEIGHT_ACCOUNT,
    WEIGHT_SPEED,
    WEIGHT_TICKETS,
    account_score,
    calculate_risk_score,
    speed_score,
    tickets_score,
)


@dataclass(frozen=True)
class Scenario:
    name: str
    profile: str
    time_elapsed_seconds: float
    account_age_hours: float
    tickets_requested: int
    note: str


SCENARIOS: list[Scenario] = [
    Scenario(
        "bot rafaga",
        "BOT",
        0.4,
        2.0,
        6,
        "clic instantaneo, cuenta recien creada, llenado maximo",
    ),
    Scenario(
        "bot rapido",
        "BOT",
        1.5,
        12.0,
        5,
        "menos de 2 s, cuenta de horas, 5 entradas",
    ),
    Scenario(
        "bot en umbral",
        "BOT",
        0.5,
        20.0,
        3,
        "rapido + cuenta nueva + 3 entradas: justo sobre el corte",
    ),
    Scenario(
        "bot con cuenta vieja",
        "BOT",
        0.8,
        8000.0,
        1,
        "velocidad maxima pero cuenta antigua y 1 entrada (SV domina)",
    ),
    Scenario(
        "humano normal",
        "HUMANO",
        45.0,
        7200.0,
        2,
        "45 s leyendo el formulario, cuenta de 10 meses",
    ),
    Scenario(
        "humano cuenta nueva",
        "HUMANO",
        30.0,
        6.0,
        1,
        "cuenta recien registrada pero navega con calma",
    ),
    Scenario(
        "dudoso lento",
        "DUDOSO",
        4.0,
        1.0,
        6,
        "cuenta nueva y llenado maximo, pero dentro de la ventana de 5 s",
    ),
    Scenario(
        "humano dudoso lento",
        "DUDOSO",
        40.0,
        1.0,
        6,
        "cuenta nueva + 6 entradas, pero 40 s: SV anula el riesgo",
    ),
]


def local_result(s: Scenario) -> dict:
    return calculate_risk_score(
        {
            "time_elapsed_seconds": s.time_elapsed_seconds,
            "account_age_hours": s.account_age_hours,
            "tickets_requested": s.tickets_requested,
        }
    )


def http_result(s: Scenario, url: str, token: str) -> dict:
    body = json.dumps(
        {
            "time_elapsed_seconds": s.time_elapsed_seconds,
            "account_age_hours": s.account_age_hours,
            "tickets_requested": s.tickets_requested,
        }
    ).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/api/v1/risk-score",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"HTTP {exc.code} del motor: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"No se pudo contactar al motor en {url}: {exc.reason}") from exc


def breakdown(s: Scenario) -> str:
    sv = speed_score(s.time_elapsed_seconds)
    sa = account_score(s.account_age_hours)
    sn = tickets_score(s.tickets_requested)
    return (
        f"SV={sv:5.1f} SA={sa:5.1f} SN={sn:5.1f}  ->  "
        f"R = {WEIGHT_SPEED:.2f}*{sv:.1f} + {WEIGHT_ACCOUNT:.2f}*{sa:.1f} "
        f"+ {WEIGHT_TICKETS:.2f}*{sn:.1f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Simula compras bot vs humano contra el motor anti-scalping.")
    parser.add_argument("--mode", choices=("local", "http"), default="local")
    parser.add_argument("--url", help="URL base del servicio (modo http)")
    parser.add_argument("--token", help="VIRTUAL_QUEUE_SERVICE_TOKEN (modo http)")
    args = parser.parse_args()

    if args.mode == "http":
        if not args.url or not args.token:
            parser.error("--mode http requiere --url y --token")

    width = max(len(s.name) for s in SCENARIOS) + 2
    print(f"{'ESCENARIO':<{width}} {'PERFIL':<7} {'dt(s)':>6} {'edad(h)':>8} {'tcks':>4}  {'R':>6}  ACTION")
    print("-" * (width + 42))

    blocked = allowed = 0
    for s in SCENARIOS:
        result = local_result(s) if args.mode == "local" else http_result(s, args.url, args.token)
        action = result["action"]
        if action == "BLOCK":
            blocked += 1
        else:
            allowed += 1
        print(
            f"{s.name:<{width}} {s.profile:<7} {s.time_elapsed_seconds:>6.1f} "
            f"{s.account_age_hours:>8.1f} {s.tickets_requested:>4}  "
            f"{result['risk_score']:>6.2f}  {action}"
        )
        print(f"{'':<{width}} {s.note}")
        print(f"{'':<{width}} {breakdown(s)}")
        print()

    print(f"Resumen ({args.mode}): {blocked} BLOCK / {allowed} ALLOW de {len(SCENARIOS)} escenarios.")
    print("Umbral: R >= 70.0 -> BLOCK. El campo time_elapsed_seconds lo manda el cliente;")
    print("si es >= 5 s el SV es 0 y el score maximo alcanzable es 60 (nunca bloquea).")


if __name__ == "__main__":
    main()
