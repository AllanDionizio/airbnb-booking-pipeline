# syntax=docker/dockerfile:1
# The HTTP service (web.py) under gunicorn: Cloud Run in production,
# `make serve` locally over the sample inbox.

# Build the wheels of the package and its dependencies
FROM python:3.14-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip wheel --no-cache-dir --wheel-dir /wheels ".[web]"

# Install them alone: no compiler, no source tree, no pip cache
FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080
RUN useradd --create-home --uid 10001 app
RUN --mount=type=bind,from=build,source=/wheels,target=/wheels \
    pip install --no-cache-dir --no-index --find-links /wheels "airbnb-booking-pipeline[web]"
USER app
EXPOSE 8080
# One worker: syncs are rare and idempotent, and a second thread keeps GET /
# answering during a long one. No worker timeout: Cloud Run enforces the
# request timeout itself.
CMD ["sh", "-c", "exec gunicorn --bind \":$PORT\" --workers 1 --threads 2 --timeout 0 'airbnb_booking_pipeline.web:create_app()'"]
