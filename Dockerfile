# Base layer.
FROM python:3.12.8 AS base
RUN apt update && apt -y install vim jq bc net-tools curl wget
RUN useradd squad -s /bin/bash -d /home/squad && mkdir -p /home/squad && chown squad:squad /home/squad
RUN mkdir -p /app && chown squad:squad /app
USER squad
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH=$PATH:/home/squad/.local/bin
ADD pyproject.toml /app/
ADD poetry.lock /app/
WORKDIR /app
RUN poetry install --no-root

# Main API.
FROM base AS api
USER root
RUN curl -fsSL -o /usr/local/bin/dbmate https://github.com/amacneil/dbmate/releases/latest/download/dbmate-linux-amd64 && chmod +x /usr/local/bin/dbmate
USER squad
ADD --chown=squad squad /app/squad
ADD --chown=squad migrations /app/migrations
ENV PYTHONPATH=/app
ENTRYPOINT ["poetry", "run", "uvicorn", "squad.api:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# Worker
FROM base AS worker
USER root
RUN apt -y update && apt -y install \
    ffmpeg \
    tesseract-ocr \
    imagemagick \
    exiftool \
    ffmpeg \
    libavcodec-dev \
    libavformat-dev \
    libavutil-dev \
    libswscale-dev \
    libavdevice-dev \
    libavfilter-dev \
    libswresample-dev \
    libcairo2-dev \
    graphviz \
    libgraphviz-dev \
    poppler-utils \
    libpoppler-dev \
    libpoppler-cpp-dev \
    tesseract-ocr \
    libtesseract-dev \
    ghostscript \
    libfreetype6-dev \
    libfontconfig1-dev
USER squad
ADD pyproject-worker.toml /app/pyproject.toml
ADD poetry-worker.lock /app/poetry.lock
RUN poetry install --no-root
RUN poetry run playwright install
ADD --chown=squad squad /app/squad
ADD --chown=squad migrations /app/migrations
ENV PYTHONPATH=/app
