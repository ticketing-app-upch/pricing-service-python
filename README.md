# Cola virtual y anti-scalping de AlpaTeck

Laravel autentica al usuario, controla la apertura de ventas y confirma compras. Python mantiene el orden de llegada, aplica hasta tres admisiones en una ventana de cinco segundos y permite tres turnos admitidos simultáneos por evento. Cada pase firmado dura tres minutos.

Python guarda los turnos y las admisiones en `virtual_queue_turns`, en el mismo PostgreSQL que usa Laravel. Un bloqueo por evento coordina las operaciones de distintas instancias de Python. La posición y los pases vigentes sobreviven a un reinicio del servicio si se conserva la clave privada RSA. Los estados avanzan al habilitar ventas y cuando la API opera sobre la cola.

Además, Python expone un motor de riesgo anti-scalping sin estado: calcula un score de 0 a 100 a partir del tiempo de compra, la antigüedad de la cuenta y la cantidad de entradas, y devuelve ALLOW o BLOCK. Laravel consulta ese endpoint antes de cobrar y emite el HTTP 429 al cliente cuando corresponde.

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

Para ejecutar `python -m unittest -v`, configura `VIRTUAL_QUEUE_TEST_DB_URL` con una base PostgreSQL desechable que tenga aplicada la migración. Las pruebas crean y eliminan turnos de eventos de prueba en esa base. Las pruebas anti-scalping (`test_anti_scalping.py`) no necesitan base de datos y corren siempre.

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
