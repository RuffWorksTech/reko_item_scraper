# Reko Item Scraper

Universal e-commerce product scraper with JavaScript rendering support (Wix, Shopify, WooCommerce, Magento, React/Vue SPAs).

## Setup

Install Python dependencies in a virtual environment (first time only):

```bash
yarn setup
```

This creates a virtual environment and installs all Python dependencies including Playwright.

## Start Server

```bash
yarn start
```

Server will be available at `http://localhost:8080`

Press `Ctrl+C` to stop.

## API Usage

### GET Request
```bash
curl "http://localhost:8080/?url=https://example.com/shop"
```

### POST Request
```bash
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{"URL": "https://example.com/shop"}'
```

### POST with Progress Updates (Auto-Onboarding)
```bash
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{
    "URL": "https://example.com/shop",
    "apiBaseUrl": "https://staging.rekohub.com/api",
    "agentToken": "your-agent-token-here"
  }'
```

When `apiBaseUrl` and `agentToken` are provided, the scraper will send real-time progress updates to `POST /v4/auto-onboard/progress` during scraping.

### Response Format
```json
{
  "status": "ok",
  "result": [
    {
      "name": "Product Name",
      "price": "$29.99",
      "description": "Product description...",
      "imageUrl": "https://example.com/image.jpg",
      "url": "https://example.com/product/item"
    }
  ]
}
```

## Calling from Your Backend

### Node.js
```javascript
const axios = require('axios');

async function scrapeWebsite(url) {
  const response = await axios.post('http://localhost:8080', {
    URL: url
  }, {
    timeout: 300000  // 5 minutes - scraping takes time!
  });
  
  return response.data.result;
}

// Usage
const products = await scrapeWebsite('https://example.com/shop');
```

### Python
```python
import requests

def scrape_website(url: str):
    response = requests.post(
        'http://localhost:8080',
        json={'URL': url},
        timeout=300  # 5 minutes
    )
    return response.json()['result']

# Usage
products = scrape_website('https://example.com/shop')
```

### Environment-Based URL (Production vs Development)

**Node.js:**
```javascript
const SCRAPER_URL = process.env.NODE_ENV === 'production'
  ? 'https://your-cloud-run-url.run.app'
  : 'http://localhost:8080';

const response = await axios.post(SCRAPER_URL, { URL: url });
```

**Python:**
```python
import os

SCRAPER_URL = (
    "https://your-cloud-run-url.run.app"
    if os.environ.get("ENV") == "production"
    else "http://localhost:8080"
)

response = requests.post(SCRAPER_URL, json={'URL': url})
```

## Client Examples

Ready-to-use client code available in `/examples`:
- `client_nodejs.js` - Node.js integration
- `client_python.py` - Python integration
- `client_curl.sh` - Shell script example

Copy to your backend project:
```bash
cp examples/client_nodejs.js ../your-backend/lib/scraper-client.js
# or
cp examples/client_python.py ../your-backend/scraper_client.py
```

## Important Notes

- ⏱️ **Timeouts:** Scraping takes 2-5 minutes for large sites. Use 300+ second timeouts.
- 🔄 **Auto-detection:** Automatically detects and handles JavaScript-rendered sites (Wix, React, Vue).
- 📦 **Simple Products Only:** Returns only simple products (skips variants, bundles, grouped products).
- 📊 **Progress Updates:** When `apiBaseUrl` and `agentToken` are provided, sends real-time progress updates during scraping.

## Real-Time Item Creation & Progress Updates

When `apiBaseUrl` and `agentToken` are provided, the scraper operates in **real-time mode**:

1. **Items are sent immediately** - Each item is POSTed to `/v4/auto-onboard/items` as soon as it's scraped
2. **Progress updates after each item** - Updates sent to `/v4/auto-onboard/progress` after every item
3. **Live client updates** - Your app receives SSE events and can show items appearing in real-time

### How It Works

```
For each product URL:
  1. Scrape product data
  2. POST to /v4/auto-onboard/items          ← Item created immediately
  3. POST to /v4/auto-onboard/progress       ← Progress update sent
  4. Your backend emits SSE refresh event    ← Client updates in real-time
  5. Repeat for next product
```

### Progress Phases

1. **Discovery** - After finding product links
   ```json
   {
     "discoveredCount": 50,
     "totalCount": 50,
     "phase": "discovery",
     "message": "Discovered 50 products"
   }
   ```

2. **Scraping** - When starting to process items
   ```json
   {
     "discoveredCount": 50,
     "sentCount": 0,
     "totalCount": 50,
     "phase": "scraping",
     "message": "Starting to scrape 50 products"
   }
   ```

3. **Importing** - After **each item** (real-time updates)
   ```json
   {
     "discoveredCount": 50,
     "sentCount": 25,
     "createdCount": 25,
     "totalCount": 50,
     "phase": "importing",
     "message": "Imported 25 of 50 products (2 skipped)"
   }
   ```

4. **Complete** - When finished
   ```json
   {
     "discoveredCount": 50,
     "sentCount": 48,
     "createdCount": 48,
     "totalCount": 50,
     "phase": "complete",
     "message": "Completed: 48 products imported, 2 skipped"
   }
   ```

### Integration

**Node.js:**
```javascript
const response = await axios.post('http://localhost:8080', {
  URL: 'https://example.com/shop',
  apiBaseUrl: 'https://staging.rekohub.com/api',
  agentToken: agentToken  // From your /v4/auto-onboard/start endpoint
}, { timeout: 300000 });
```

**Python:**
```python
response = requests.post(
    'http://localhost:8080',
    json={
        'URL': 'https://example.com/shop',
        'apiBaseUrl': 'https://staging.rekohub.com/api',
        'agentToken': agent_token
    },
    timeout=300
)
```

### API Calls Made

When `apiBaseUrl` and `agentToken` are provided, the scraper makes these API calls:

1. **Per Item (immediate):**
   ```
   POST {apiBaseUrl}/v4/auto-onboard/items
   Authorization: Bearer {agentToken}
   Content-Type: application/json
   
   {
     "name": "Product Name",
     "description": "Product description",
     "price": "$3.00 ea",
     "imageUrl": "https://...",
     "sourceItemId": "https://source-url"
   }
   ```

2. **Progress Update (after each item):**
   ```
   POST {apiBaseUrl}/v4/auto-onboard/progress
   Authorization: Bearer {agentToken}
   Content-Type: application/json
   
   {
     "discoveredCount": 50,
     "sentCount": 25,
     "createdCount": 25,
     "totalCount": 50,
     "phase": "importing",
     "message": "Imported 25 of 50 products"
   }
   ```

### Logs Output

When running in real-time mode, you'll see:
```
✓ Scraped: Product Name
✅ Item sent to API: Product Name
📊 Progress update sent: Imported 1 of 50 products (0 skipped)
✓ Scraped: Another Product
✅ Item sent to API: Another Product
📊 Progress update sent: Imported 2 of 50 products (0 skipped)
...
```

### Error Handling

- Item POSTs and progress updates are best-effort
- Failures are logged but don't stop the scrape
- The scraper still returns all items in the response (even if some API calls fail)
- Without `apiBaseUrl`/`agentToken`, works in offline mode (returns JSON only)

## Available Commands

```bash
yarn setup          # Setup virtual environment & install dependencies (first time)
yarn start          # Start the server
yarn test           # Test the service
```

## Production Deployment

Deploy to Google Cloud Run (already configured):

```bash
gcloud builds submit --config cloudbuild.yaml
```

Then update your backend to use the Cloud Run URL in production (see environment-based URL example above).

## Troubleshooting

**"Module not found" or "externally-managed-environment":**
```bash
yarn setup
```

**Port 8080 already in use:**
```bash
# Check what's using it
lsof -i :8080

# Or change port in start_dev.py
export PORT=8081
yarn start
```

**Slow scraping:**
- Normal! Use 300+ second timeouts in your backend
- View progress in terminal logs

## Docker (Optional)

For production-like testing:
```bash
docker-compose up -d
docker-compose logs -f scraper
docker-compose down
```
