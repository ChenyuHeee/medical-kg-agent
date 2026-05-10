FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app

# system deps for PyMuPDF / pdfplumber / sentence-transformers
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# CPU-only torch to keep image size manageable for sentence-transformers
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu torch==2.4.1 \
 && pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY data ./data
COPY textbooks ./textbooks

EXPOSE 8000
CMD ["sh", "-c", "uvicorn src.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
