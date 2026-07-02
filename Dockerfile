FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código
COPY agent.py .
COPY setup_dynamodb.py .

# Las credenciales AWS se montan como volumen en runtime
# No se copian al contenedor por seguridad

CMD ["python", "agent.py"]