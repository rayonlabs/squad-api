# Main API.
FROM python:3.12.8
RUN curl -fsSL -o /usr/local/bin/dbmate https://github.com/amacneil/dbmate/releases/latest/download/dbmate-linux-amd64 && chmod +x /usr/local/bin/dbmate
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
RUN poetry run playwright install
ADD --chown=squad squad /app/squad
ADD --chown=squad migrations /app/migrations
ENV PYTHONPATH=/app
ENTRYPOINT ["poetry", "run", "uvicorn", "squad.api:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
