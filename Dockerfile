# Main API.
FROM python:3.12.8
RUN curl -fsSL -o /usr/local/bin/dbmate https://github.com/amacneil/dbmate/releases/latest/download/dbmate-linux-amd64 && chmod +x /usr/local/bin/dbmate
RUN useradd chutes -s /bin/bash -d /home/chutes && mkdir -p /home/chutes && chown chutes:chutes /home/chutes
RUN mkdir -p /app && chown chutes:chutes /app
USER chutes
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH=$PATH:/home/chutes/.local/bin
ADD pyproject.toml /app/
ADD poetry.lock /app/
WORKDIR /app
RUN poetry install --no-root
RUN poetry run playwright install
ADD --chown=chutes squad /app/squad
ENV PYTHONPATH=/app
ENTRYPOINT ["poetry", "run", "uvicorn", "squad.api:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
