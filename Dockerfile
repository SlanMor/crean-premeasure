FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Tesseract reads the text on drawings that arrive as pictures (scans)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY engine /app/engine
RUN pip install /app/engine

COPY web/requirements.txt /app/web/requirements.txt
RUN pip install -r /app/web/requirements.txt

COPY web /app/web
WORKDIR /app/web
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

RUN useradd --create-home --uid 1000 app && mkdir -p /data/media && chown -R app /data /app
USER app

EXPOSE 8000
CMD ["gunicorn", "premeasure_web.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
