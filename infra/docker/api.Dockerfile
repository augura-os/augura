FROM python:3.12-slim

RUN sed -i 's|http://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

COPY apps/api/requirements.txt /app/apps/api/requirements.txt
COPY packages/graph /app/packages/graph
RUN pip install --no-cache-dir -e /app/packages/graph \
    && pip install --no-cache-dir -r /app/apps/api/requirements.txt

COPY apps/api /app/apps/api

WORKDIR /app/apps/api
ENV PYTHONPATH=/app/apps/api

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
