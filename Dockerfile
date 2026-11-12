# Dealwright - AI quote-to-cash with PayPal
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    sqlite3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI only (no daemon). Lets the in-app sandbox launch ephemeral
# `docker run` audit containers through the mounted host socket.
ARG DOCKER_CLI_VERSION=27.3.1
RUN curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
      | tar -xz --strip-components=1 -C /usr/local/bin docker/docker

# Install python dependencies (fail the build if these cannot be installed)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Expose server port
EXPOSE 8000

ENV SOFTORA_HOST=0.0.0.0
ENV SOFTORA_PORT=8000

# Health check endpoint (GET / is the public UI shell)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:8000/ >/dev/null || exit 1

CMD ["python", "app.py", "8000"]
