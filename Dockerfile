FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://github.com/mendixlabs/mxcli/releases/download/v0.12.0/mxcli-linux-amd64 \
    -o /usr/local/bin/mxcli \
    && chmod +x /usr/local/bin/mxcli

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
