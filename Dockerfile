FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=Asia/Kolkata

WORKDIR /app

# Install system dependencies (tzdata for IST timezone, CA certificates)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY . /app

# Install certifi for robust TLS certificate verification
RUN pip install --no-cache-dir certifi

# Create directory for persistent data if using a subfolder
RUN mkdir -p /app/data

# Default port
EXPOSE 8000

# Healthcheck hitting the live posts.json endpoint
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/posts.json || exit 1

# Launch pipeline in server mode with auto-updater daemon
CMD ["python", "pipeline.py", "--serve", "8000", "--auto-update", "30"]
