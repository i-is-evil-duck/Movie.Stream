# Use Alpine Linux for small image size
FROM python:3.10-alpine

# Set environment variables to prevent Python from buffering output
ENV PYTHONUNBUFFERED=1

# Install system dependencies (aria2 for torrents, wget for healthcheck)
RUN apk add --no-cache aria2 wget

# Set the working directory in the container
WORKDIR /app

# Copy the application code into the container
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir flask requests flask-limiter python-dotenv gunicorn

# Create necessary directories
RUN mkdir -p media tmp logs

# Expose the port your app runs on
EXPOSE 8973

# Run the application with Gunicorn
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8973", "app:app"]
