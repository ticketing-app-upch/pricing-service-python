# Ticketing Pricing Microservice (Python)
> Microservicio analítico para el cálculo y ajuste dinámico de tarifas en tiempo real.

![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)
![Uvicorn](https://img.shields.io/badge/Uvicorn-499848?style=for-the-badge&logo=gunicorn&logoColor=white)

## 📌 Responsabilidades del Servicio
* Exponer una API REST ligera para consultar el precio ajustado de una zona en tiempo real.
* Ejecutar las reglas de tarificación dinámica:
  * **Incremento (+20%):** Si el aforo disponible es menor al 15% o si faltan menos de 3 días para el evento.
  * **Descuento (-10%):** Si la publicación del evento supera los 30 días de antigüedad y las ventas acumuladas no alcanzan el 10% del aforo total.
* Proveer pruebas unitarias con `pytest` para verificar la precisión de las reglas de negocio.

## 👥 Responsables
* **Líder de Integración / Developer Backend 2:** Camilo Silva y Jaren Pazmiño
* **Supervisión de Arquitectura:** Vanesa Rioja

## 🛠️ Requisitos Previos
* Python 3.10 o superior
* `pip` y entorno virtual (`venv`)

## 🚀 Puesta en Marcha Local

1. **Crear y activar entorno virtual:**
   ```bash
   # En Ubuntu / macOS
   python3 -m venv venv
   source venv/bin/activate

   # En Windows
   python -m venv venv
   venv\Scripts\activate