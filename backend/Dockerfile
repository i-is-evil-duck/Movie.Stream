# Use Ubuntu 22.04 (which natively uses Python 3.10)
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install system Python, pip, and libtorrent
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-libtorrent \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# We set the working directory to /app/backend to match our config.py paths
WORKDIR /app/backend

# Copy ONLY the backend files (since this Dockerfile is now in the backend folder)
COPY . .

# Install Python dependencies (no longer needs 'backend/' prefix)
RUN pip3 install --no-cache-dir -r requirements.txt gunicorn

EXPOSE 8973

# Run the app
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8973", "app:app"]