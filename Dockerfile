# Use Ubuntu 22.04 (which natively uses Python 3.10)
FROM ubuntu:22.04

# Prevent interactive prompts during apt-get
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install system Python, pip, and the pre-compiled libtorrent bindings
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-libtorrent \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the whole project
COPY . .

# Install the standard Python dependencies
RUN pip3 install --no-cache-dir -r backend/requirements.txt gunicorn

# Move into backend to run the server
WORKDIR /app/backend

EXPOSE 8973

# Run the app (using python3/gunicorn)
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8973", "app:app"]