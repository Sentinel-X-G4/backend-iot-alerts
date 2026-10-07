FROM python:3.12-slim

# Versions de l'application Orange qui produit les .pkcls (Aide → À propos) : un modèle
# picklé ne se recharge de façon fiable qu'avec les mêmes orange3 et scikit-learn.
# Orange 3.40.0 (macOS) embarque Python 3.12 et scikit-learn 1.5.2.
ARG ORANGE_VERSION=3.40.0
ARG SKLEARN_VERSION=1.5.2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . "orange3==${ORANGE_VERSION}" "scikit-learn==${SKLEARN_VERSION}"

COPY tools ./tools

RUN useradd --create-home --uid 1000 app && mkdir -p /models && chown app /models
USER app

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["detection-service"]
