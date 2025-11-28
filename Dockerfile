# Use Playwright's official base image which includes all browser dependencies
# This image has Chromium, Firefox, and WebKit pre-installed with all system libs
FROM mcr.microsoft.com/playwright/python:v1.56.0-noble

WORKDIR /app

# Copy requirements first for better Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install only Chromium browser (smaller than installing all browsers)
# The base image has system deps, but we need to install the actual browser
RUN playwright install chromium

# Copy application code
COPY . .

# Cloud Run uses PORT environment variable
ENV PORT=8080

# Run the Flask server
CMD ["python", "server.py"]
