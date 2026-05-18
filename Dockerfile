FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Kolkata

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && ln -fs /usr/share/zoneinfo/$TZ /etc/localtime

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY hexa_agent ./hexa_agent
COPY main.py ./

RUN useradd --create-home --shell /bin/bash hexa \
 && mkdir -p /app/data && chown -R hexa:hexa /app
USER hexa

VOLUME ["/app/data"]

CMD ["python", "main.py", "schedule"]
