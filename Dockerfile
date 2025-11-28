# Use standard Python image and install Playwright dependencies manually
# This is more reliable than using specific Playwright image tags
FROM python:3.11-slim-bookworm

WORKDIR /app

# Install system dependencies required by Playwright/Chromium
# These are the libraries Chromium needs to run on Linux
RUN apt-get update && apt-get install -y \
    # Chromium dependencies
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    # Additional dependencies
    libpango-1.0-0 \
    libcairo2 \
    fonts-liberation \
    wget \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (Chromium only to save space)
RUN playwright install chromium

# Copy application code
COPY . .

# Cloud Run uses PORT environment variable
ENV PORT=8080

# Run the Flask server
CMD ["python", "server.py"]
