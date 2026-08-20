# ==============================================================================
# Build stage
# ==============================================================================
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ==============================================================================
# Runtime stage
# ==============================================================================
FROM python:3.12-slim AS runtime

# Create non-root user
RUN groupadd -g 10001 balancer && \
    useradd -u 10001 -g balancer -s /bin/sh -m balancer

WORKDIR /app

# Copy installed wheels/packages from builder
COPY --from=builder /install /usr/local

# Copy application source and project metadata
COPY pyproject.toml .
COPY sungrow_battery_balancer/ ./sungrow_battery_balancer/

# Install the application package
RUN pip install --no-cache-dir --no-deps .

USER balancer

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["sungrow-battery-balancer"]
CMD []
