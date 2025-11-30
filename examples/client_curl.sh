#!/bin/bash

# Bash/cURL Example - Calling the Reko Item Scraper
# 
# Usage: ./client_curl.sh https://example.com/shop

# Configuration: Switch between local and production
if [ "$ENV" = "production" ]; then
    SCRAPER_URL="https://your-cloud-run-url.run.app"
else
    SCRAPER_URL="http://localhost:8080"
fi

# Get URL from command line argument or use default
SITE_URL="${1:-https://example.com/shop}"

echo "🔍 Scraping: $SITE_URL"
echo "📡 Using scraper at: $SCRAPER_URL"
echo ""

# Make the request (using POST with JSON)
response=$(curl -s -w "\n%{http_code}" -X POST "$SCRAPER_URL" \
    -H "Content-Type: application/json" \
    -d "{\"URL\": \"$SITE_URL\"}" \
    --max-time 300)

# Extract HTTP status code (last line)
http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')

# Check if request was successful
if [ "$http_code" -eq 200 ]; then
    echo "✅ Scraping successful!"
    echo ""
    echo "📦 Results:"
    echo "$body" | python3 -m json.tool
    
    # Count products (requires jq installed, optional)
    if command -v jq &> /dev/null; then
        count=$(echo "$body" | jq '.result | length')
        echo ""
        echo "Found $count products"
    fi
else
    echo "❌ Scraper request failed with status: $http_code"
    echo ""
    echo "Response:"
    echo "$body"
    exit 1
fi





