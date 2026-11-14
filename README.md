# Cola virtual de AlpaTeck

Laravel autentica al usuario, controla la apertura de ventas y confirma compras. Python mantiene el orden de llegada, aplica hasta tres admisiones en una ventana de cinco segundos y permite tres turnos admitidos simultáneos por evento. Cada pase firmado dura tres minutos.

Ejecuta una sola instancia y un solo proceso. Un reinicio borra las posiciones activas. Los asistentes deben volver a la cola. Laravel conserva las órdenes y los pases consumidos. Python guarda los turnos vencidos y cancelados para Mercado en SQLite. Los estados avanzan al habilitar ventas y cuando la API opera sobre la cola.

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
| VIRTUAL_QUEUE_OUTCOMES_DB | Python | Archivo SQLite de resultados. Por defecto, queue-outcomes.sqlite3 o /data/queue-outcomes.sqlite3 en Docker. Monta un volumen persistente en /data. |

Configura los secretos en el entorno de ejecución. Conecta los servicios en una red privada o mediante un proxy TLS de acceso restringido. La ruta GET /health sirve como comprobación de arranque.

~~~sh
python -m pip install -r requirements.txt
python -m unittest -v
python server.py
~~~

Para usar la imagen de Python 3.12 del Dockerfile, construye con docker build -t alpateck-queue . En Docker, coloca ambos contenedores en una red compartida y usa el nombre del contenedor Python en la URL interna. Ejecuta las migraciones de Laravel y vuelve a generar la caché de configuración si corresponde.

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

Angular usa las rutas Laravel y envía el pase como queuePass al pagar. La evaluación anti-scalping queda pendiente.
