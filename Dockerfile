FROM python:3.10-slim

ENV PYTHONUNBUFFERED=true

WORKDIR /app

COPY requirements.txt .

RUN pip install --disable-pip-version-check --no-cache-dir --user -r requirements.txt

COPY src/ src/

COPY src/config/permissions.json.default src/config/permissions.json
COPY src/config/config.json.default src/config/config.json

VOLUME /app/src/config

EXPOSE 21025

CMD [ "python", "src/server.py"]
