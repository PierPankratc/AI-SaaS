FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

# Запуск от непривилегированного пользователя
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/.vkbottle \
    && chown -R appuser:appuser /app

USER appuser

CMD ["python", "-m", "src.main"]
