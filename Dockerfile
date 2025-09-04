# --- Builder: install deps into a local venv (cache friendly) ---
FROM python:3.12-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 \
    VENV=/opt/venv
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
RUN python -m venv $VENV
ENV PATH="$VENV/bin:$PATH"
WORKDIR /app
# Separate layer for requirements to maximize cache
COPY requirements.txt .
RUN pip install -r requirements.txt

# --- Runtime image: copy venv + source, run as non-root ---
FROM python:3.12-slim
ENV VENV=/opt/venv
ENV PATH="$VENV/bin:$PATH"
# Create a non-root user
RUN useradd -m appuser
# Copy virtualenv from builder
COPY --from=builder $VENV $VENV
WORKDIR /app
# Copy source last to leverage Docker layer cache
COPY app ./app
# Expose port and default envs
ENV PORT=8000
EXPOSE 8000
USER appuser
# Start uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]