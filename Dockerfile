# Use an official Python runtime as the base image
FROM python:3.10-slim

# Set environment variables to prevent Python from buffering output
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    aria2 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy the application code into the container
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Expose the port your app runs on
EXPOSE 8973

# Run the application with Gunicorn
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8973", "app:app"]