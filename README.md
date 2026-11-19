# Cola virtual y anti-scalping de AlpaTeck

Laravel autentica al usuario, controla la apertura de ventas y confirma compras. Python mantiene el orden de llegada, aplica hasta tres admisiones en una ventana de cinco segundos y permite tres turnos admitidos simultáneos por evento. Cada pase firmado dura tres minutos.

Python guarda los turnos y las admisiones en `virtual_queue_turns`, en el mismo PostgreSQL que usa Laravel. Un bloqueo por evento coordina las operaciones de distintas instancias de Python. La posición y los pases vigentes sobreviven a un reinicio del servicio si se conserva la clave privada RSA. Los estados avanzan al habilitar ventas y cuando la API opera sobre la cola.

Además, Python expone un motor de riesgo anti-scalping sin estado: calcula un score de 0 a 100 a partir del tiempo de compra, la antigüedad de la cuenta y la cantidad de entradas, y devuelve ALLOW o BLOCK. Laravel consulta ese endpoint antes de cobrar y emite el HTTP 429 al cliente cuando corresponde.

La API está construida con FastAPI (uvicorn como servidor ASGI). Documentación interactiva en `GET /docs` (OpenAPI).

## Estructura

~~~
app/
├── main.py                 # create_app: middleware, handlers de error, routers
├── config.py               # Settings.from_env (token, RSA, DB, host/port)
├── auth.py                 # require_service_token (Bearer)
├── validators.py           # valid_request / valid_id de la cola
├── routers/
│   ├── health.py           # GET /health
│   ├── turns.py            # POST /v1/turns/{acción}
│   └── risk_score.py       # POST /api/v1/risk-score
├── schemas/
│   └── risk.py             # RiskScoreRequest / RiskScoreResponse (Pydantic)
└── services/
    ├── virtual_queue.py    # lógica de la cola (sin dependencias web)
    └── anti_scalping.py    # cálculo de riesgo (puro, sin estado)
tests/
├── test_api.py             # contratos HTTP (TestClient, sin DB)
├── test_virtual_queue.py   # integración de cola (requiere DB)
└── test_anti_scalping.py   # algoritmo anti-scalping
scripts/
└── simulate_bot.py         # simulador bot vs humano (local o contra el endpoint real)
server.py                   # valida entorno, verifica storage, arranca uvicorn
~~~

Cada funcionalidad vive aislada en su router + servicio: cambiar la cola o el motor de riesgo no toca el otro ni el cableado HTTP.

## Configuración

Genera una clave RSA y un token de servicio con OpenSSL:

~~~sh
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out queue-private.pem
openssl pkey -in queue-private.pem -pubout -out queue-public.pem
openssl base64 -A -in queue-private.pem
openssl base64 -A -in queue-public.pem
openssl rand -hex 32
~~~

| Variable | Dónde | Valor |
|---|---|---|
| VIRTUAL_QUEUE_SERVICE_TOKEN | Python y Laravel | El mismo token aleatorio. |
| VIRTUAL_QUEUE_PASS_PRIVATE_KEY | Python | PEM privado en Base64. |
| VIRTUAL_QUEUE_PASS_PUBLIC_KEY | Laravel | PEM público en Base64. |
| VIRTUAL_QUEUE_URL | Laravel | URL interna accesible desde la API Laravel. |
| HOST / PORT | Python | 0.0.0.0 y 8000 por defecto. |
| VIRTUAL_QUEUE_DB_URL | Python | URL PostgreSQL de la base de Laravel. En Render, usa la conexión interna si ambos recursos están en la misma región y workspace. |

El endpoint anti-scalping no requiere variables adicionales: reutiliza `VIRTUAL_QUEUE_SERVICE_TOKEN` y el mismo `Authorization: Bearer` de los endpoints de cola. No usa base de datos.

Configura los secretos en el entorno de ejecución. Usa un usuario PostgreSQL con acceso al esquema, permisos SELECT, INSERT y UPDATE en `virtual_queue_turns` y USAGE en `virtual_queue_turns_id_seq`.

~~~sh
python -m pip install -r requirements.txt
python server.py
~~~

En Render, despliega primero la migración `2026_11_14_000001_create_virtual_queue_turns_table.php` de Laravel. Después configura `VIRTUAL_QUEUE_DB_URL` y despliega Python. Python verifica que la tabla esté disponible antes de arrancar. Comprueba `GET /health` y el ingreso a la cola desde Laravel. Los turnos activos en memoria y los resultados históricos del SQLite anterior quedan fuera de esta migración. Si hay una cola activa durante el cambio, sus asistentes tendrán que volver a entrar.

Para ejecutar la suite completa:

~~~sh
python -m unittest discover -s tests -v
~~~

Las pruebas de integración de la cola (`test_virtual_queue.py`) necesitan `VIRTUAL_QUEUE_TEST_DB_URL` con una base PostgreSQL desechable migrada; si no está configurada se omiten. Las pruebas de API (`test_api.py`) y anti-scalping (`test_anti_scalping.py`) no requieren base de datos y corren siempre.

## Endpoints

Todos los POST aceptan y devuelven JSON. El acceso autenticado usa el mismo encabezado en ambos casos:

~~~http
Authorization: Bearer <VIRTUAL_QUEUE_SERVICE_TOKEN>
~~~

| Método y ruta | Acceso | Función |
|---|---|---|
| `GET /health` | Público | Sonda de salud para el orquestador. Responde `200` con `{"status":"ok"}` sin tocar la base. |
| `GET /docs` | Público | Documentación OpenAPI generada por FastAPI (prueba interactiva de los endpoints). |
| `POST /v1/turns/{acción}` | Bearer de servicio | API interna de la cola virtual. Opera turnos por evento según la acción (`join`, `status`, `cancel`, `open-payment`, `validate`, `complete`, `advance`, `conversion`). Laravel es el único cliente. Detalle en [Contrato entre servicios](#contrato-entre-servicios). |
| `POST /api/v1/risk-score` | Bearer de servicio | Motor anti-scalping. Calcula el riesgo de la transacción actual y responde `ALLOW` o `BLOCK` (siempre `200` si el body es válido). Laravel lo consulta antes de cobrar. Detalle en [Motor anti-scalping](#motor-anti-scalping). |

Códigos comunes:

| Status | Cuándo |
|---|---|
| `200` | Operación exitosa. |
| `401` | Token ausente o distinto de `VIRTUAL_QUEUE_SERVICE_TOKEN`. |
| `404` | Ruta o acción de cola desconocida (se evalúa antes que la autenticación en `/v1/turns/{acción}`). |
| `422` | Body inválido, fuera de rango o mayor a 4096 bytes: `{"message":"Solicitud inválida."}`. |
| `503` | PostgreSQL no disponible al operar la cola: `{"message":"La cola virtual no está disponible."}`. |

## Contrato entre servicios

Las operaciones internas usan POST /v1/turns/{acción} y Authorization: Bearer con el token de servicio.

| Acción | Campos JSON además de eventId |
|---|---|
| join, status, cancel | userId, salesEnabled, salesClosed |
| open-payment | userId, salesEnabled, salesClosed, openedAt en segundos Unix según Laravel. |
| validate | userId, salesEnabled, salesClosed, turnId leído del JWT verificado. |
| advance | salesEnabled, salesClosed. Laravel lo llama al habilitar compras. |
| complete | turnId, soldOut. Laravel lo llama tras el commit y reintenta al volver a operar la cola si falla. |
| conversion | eventIds, hasta 100 por petición. Devuelve turnId y estado por turno vencido o cancelado. Laravel combina estos resultados con las compras confirmadas y devuelve solo métricas agregadas. |

Los identificadores de usuario y evento viajan como cadenas. join y status responden con eventId, queueToken, status, position, initialPosition, etaSeconds, joinedAt y salesEnabled. Un turno ADMITTED agrega passToken y passExpiresAt. Los estados finales son COMPLETED, EXPIRED, CANCELLED y CLOSED.

Angular usa las rutas Laravel y envía el pase como queuePass al pagar.

## Motor anti-scalping

`POST /api/v1/risk-score` evalúa el riesgo de scalping en la transacción actual. Es un cálculo puro y sin estado: no toca la cola virtual, no persiste nada y siempre responde HTTP 200 cuando la solicitud es válida. Laravel es quien traduce `BLOCK` en un HTTP 429 hacia el cliente.

### Contrato

Mismo `Authorization: Bearer` con `VIRTUAL_QUEUE_SERVICE_TOKEN`. Cuerpo JSON (máximo 4096 bytes):

| Campo | Tipo | Rango |
|---|---|---|
| time_elapsed_seconds | number | ≥ 0. Segundos desde que el usuario entró al formulario hasta presionar Pagar (Δt). |
| account_age_hours | number | ≥ 0. Antigüedad de la cuenta en horas desde su registro (A). |
| tickets_requested | integer | 1 a 6. Entradas solicitadas en la transacción (N). |

Fuera de rango o campo ausente responde 422 con `{"message":"Solicitud inválida."}`. Token incorrecto responde 401.

Respuesta 200:

~~~json
{"risk_score":75.5,"action":"BLOCK"}
~~~

`risk_score` es `R` redondeado a 2 decimales. `action` es `BLOCK` si `R >= 70.0` (sobre el valor exacto, antes de redondear) o `ALLOW` en caso contrario. No existe REVIEW.

### Algoritmo

Cada sub-score va de 0 a 100:

| Sub-score | Pesos de R | Reglas |
|---|---|---|
| SV velocidad (Δt) | 0.40 | Δt < 2 s → 100; entre 2 y 5 s → lineal de 100 a 0; > 5 s → 0. |
| SA antigüedad (A) | 0.30 | A < 24 h → 100; entre 24 y 720 h → lineal de 100 a 0; > 720 h → 0. |
| SN cantidad (N) | 0.30 | N ≥ 5 → 100; N 3 o 4 → 50; N ≤ 2 → 0. |

`R = 0.40·SV + 0.30·SA + 0.30·SN`. Compra sospechosa = muy rápido, cuenta nueva y muchas entradas.

Ejemplo con Δt = 1.5 s, A = 10 h, N = 1: SV = 100, SA = 100, SN = 0 → R = 70.0 → BLOCK.

### Simulador

`scripts/simulate_bot.py` corre ocho escenarios (bots, humanos y dudosos) contra el motor y muestra `R`, `action` y el desglose SV/SA/SN:

~~~sh
python scripts/simulate_bot.py                                   # motor local (puro)
python scripts/simulate_bot.py --mode http \
  --url https://pricing-service-python.onrender.com \
  --token "$VIRTUAL_QUEUE_SERVICE_TOKEN"                         # endpoint real
~~~
