FROM python:3.12-slim

WORKDIR /app

# System deps para sa pycryptodome build (kung walang prebuilt wheel)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps muna (para cached sa next builds)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code + entrypoint
COPY main.py .
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Railway injects $PORT — health server mo gagamit nito
ENV PORT=8080

CMD ["/app/entrypoint.sh"]
