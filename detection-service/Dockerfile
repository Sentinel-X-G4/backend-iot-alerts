FROM python:3.12-slim

# INSTALL_ORANGE=true installe orange3 (lourd) pour charger directement les .pkcls.
# Sinon, convertir les modèles avec tools/export_orange_to_joblib.py et PREDICTOR=sklearn.
ARG INSTALL_ORANGE=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN if [ "$INSTALL_ORANGE" = "true" ]; then EXTRAS="sklearn,orange"; else EXTRAS="sklearn"; fi \
    && pip install ".[${EXTRAS}]"

COPY tools ./tools

RUN useradd --create-home --uid 1000 app && mkdir -p /models && chown app /models
USER app

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["detection-service"]
