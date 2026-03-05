FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir .

COPY app /app/app
COPY locale /app/locale

RUN useradd --create-home --uid 10001 appuser
USER appuser

CMD ["python", "-m", "app.main"]
