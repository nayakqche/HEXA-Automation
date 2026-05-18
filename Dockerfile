FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Kolkata \
    PORT=5000 \
    DATA_DIR=/app/data

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && ln -fs /usr/share/zoneinfo/$TZ /etc/localtime

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY hexa_agent ./hexa_agent
COPY webapp ./webapp
COPY main.py ./

RUN useradd --create-home --shell /bin/bash hexa \
 && mkdir -p /app/data && chown -R hexa:hexa /app
USER hexa

VOLUME ["/app/data"]
EXPOSE 5000

# Default command: serve the Flask dashboard via gunicorn on $PORT
# (this single process also runs the embedded daily scheduler).
# Override with `python main.py schedule` if you only want the
# headless CLI scheduler.
CMD ["sh", "-c", "exec gunicorn -w 1 -k gthread --threads 4 --timeout 120 -b 0.0.0.0:${PORT:-5000} webapp.app:app"]
