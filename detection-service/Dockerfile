# --- Construction : dépendances installées dans /opt/venv -----------------------------------
FROM python:3.12-slim AS build

# Versions de l'application Orange qui produit les .pkcls (Aide → À propos) : un modèle
# picklé ne se recharge de façon fiable qu'avec les mêmes orange3 et scikit-learn.
# Orange 3.40.0 (macOS) embarque Python 3.12 et scikit-learn 1.5.2.
ARG ORANGE_VERSION=3.40.0
ARG SKLEARN_VERSION=1.5.2

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PATH=/opt/venv/bin:$PATH

# openTSNE (dépendance d'orange3) n'a pas de wheel linux/arm64 (Mac Apple Silicon) : compilé
# ici avec g++, qui ne va pas dans l'image finale
RUN apt-get update && apt-get install -y --no-install-recommends g++ && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv

# Dépendances lourdes avant le code : couche en cache tant que les versions ne changent pas
RUN pip install "orange3==${ORANGE_VERSION}" "scikit-learn==${SKLEARN_VERSION}"

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# --- Image finale ----------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

# Bibliothèque OpenMP chargée par openTSNE (compilé dans l'étape build)
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/venv /opt/venv

WORKDIR /app
COPY tools ./tools

RUN useradd --create-home --uid 1000 app && mkdir -p /models && chown app /models
USER app

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["detection-service"]
