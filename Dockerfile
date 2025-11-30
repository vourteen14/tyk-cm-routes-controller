FROM python:3.11-slim

LABEL maintainer="vourteen14"
LABEL description="Tyk Route Operator - Kubernetes Operator for Tyk API Gateway"
LABEL version="1.0.0"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY operator.py .

RUN set -eux; \
    if ! getent group operator >/dev/null; then \
        groupadd -g 1000 operator; \
    fi; \
    if ! id operator >/dev/null 2>&1; then \
        useradd -m -u 1000 -g operator operator; \
    fi; \
    chown -R operator:operator /app

USER operator

EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8081/healthz')"

CMD ["kopf", "run", "operator.py"]