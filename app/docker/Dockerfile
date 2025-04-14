# Base image with conda pre-installed
FROM continuumio/miniconda3:latest

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy environment file first for better caching
COPY ./app/docker/environment.yml /app/

# Create conda environment from file
RUN conda env create -f environment.yml

# Copy application code and modules
COPY app/docker/app.py /app/
COPY libmicroview.py /app/
COPY utils.py /app/

# Create logs directory
RUN mkdir -p /app/logs

# Set shell to bash
SHELL ["/bin/bash", "-c"]

# Entry point script
COPY app/docker/entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
# just sleep and wait for the user to exec into the container
# CMD ["sleep", "infinity"]