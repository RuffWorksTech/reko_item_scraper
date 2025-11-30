import json
import os
import random
import sys
import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

try:
    import cloudscraper
    USE_CLOUDSCRAPER = True
except ImportError:
    USE_CLOUDSCRAPER = False

# Playwright is used for JavaScript-heavy sites that require browser rendering
# (e.g., Wix, React, Vue, Angular SPAs)
try:
    from playwright.sync_api import sync_playwright
    USE_PLAYWRIGHT = True
except ImportError:
    USE_PLAYWRIGHT = False

# Base headers mimic a modern browser; per-request rotation is layered on top
# inside `build_rotating_headers` to avoid static fingerprints.
# Note: We use 'gzip, deflate' instead of 'gzip, deflate, br' because
# Brotli (br) requires the brotli package to decompress properly.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Curated desktop UA list keeps us aligned with current browser releases and
# allows lightweight rotation without pulling in another dependency.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:123.0) Gecko/20100101 Firefox/123.0",
]

# Detection hints help us pivot to cloudscraper/proxy retries only when needed.
DETECTION_KEYWORDS = [
    "bot detection", "access denied", "just a moment", "captcha",
    "are you human", "verify your identity", "request blocked",
]

# Parse optional proxy list once so we can rotate without reparsing env vars.
def _load_proxy_pool():
    proxies = os.environ.get("SCRAPER_PROXIES", "")
    pool = []
    for raw_proxy in proxies.split(","):
        proxy = raw_proxy.strip()
        if not proxy:
            continue
        if "://" not in proxy:
            proxy = f"http://{proxy}"
        pool.append(proxy)
    return pool


PROXY_POOL = _load_proxy_pool()
CLOUDSCRAPER_SESSION = None
PLAYWRIGHT_BROWSER = None

# JavaScript framework indicators - if these are found in the page, it's likely JS-rendered
JS_FRAMEWORK_INDICATORS = [
    'wix.com', 'wixstatic.com', 'parastorage.com',  # Wix
    '__NEXT_DATA__', '_next/',  # Next.js
    'ng-app', 'ng-controller',  # Angular
    '__NUXT__',  # Nuxt.js
    'data-reactroot', '__REACT_DEVTOOLS',  # React
    'data-v-', 'data-vue',  # Vue
]


def human_delay(min_wait=0.8, max_wait=1.8):
    """Sleep for a human-looking interval so we do not hammer the target."""
    time.sleep(random.uniform(min_wait, max_wait))


def build_rotating_headers(referer=None):
    """Assemble headers that rotate UA + language to reduce fingerprint reuse."""
    headers = HEADERS.copy()
    headers["User-Agent"] = random.choice(USER_AGENTS)
    headers["Accept-Language"] = random.choice(
        ["en-US,en;q=0.9", "en-US,en;q=0.8,fr;q=0.6", "en-GB,en;q=0.9"]
    )
    headers["DNT"] = "1"
    headers["Sec-Fetch-Dest"] = "document"
    headers["Sec-Fetch-Mode"] = "navigate"
    headers["Sec-Fetch-Site"] = "none"
    headers["Sec-Ch-Ua-Mobile"] = "?0"
    headers["Sec-Ch-Ua-Platform"] = random.choice(['"Windows"', '"macOS"', '"Linux"'])
    headers["Referer"] = referer or headers.get("Referer") or "https://www.google.com/"
    return headers


def choose_proxy():
    """Pick a proxy (if provided) to spread requests across multiple exits."""
    if not PROXY_POOL:
        return None
    proxy = random.choice(PROXY_POOL)
    return {"http": proxy, "https": proxy}


def looks_like_bot_block(response):
    """Detect common block patterns so we can escalate to stealthier clients."""
    if response is None:
        return True
    if response.status_code in (403, 429, 503):
        return True
    text = response.text.lower()
    return any(keyword in text for keyword in DETECTION_KEYWORDS)


def build_retry_session():
    """Create a Session with sane retries so transient throttling is absorbed."""
    session = requests.Session()
    retry_cfg = Retry(
        total=int(os.environ.get("SCRAPER_HTTP_RETRIES", "3")),
        backoff_factor=float(os.environ.get("SCRAPER_BACKOFF", "0.7")),
        status_forcelist=[403, 408, 409, 425, 429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_cfg)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def get_cloudscraper_session():
    """Lazy init of cloudscraper so we only pay the cost when sites demand it."""
    global CLOUDSCRAPER_SESSION
    if not USE_CLOUDSCRAPER:
        return None
    if CLOUDSCRAPER_SESSION is None:
        CLOUDSCRAPER_SESSION = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
    return CLOUDSCRAPER_SESSION


def is_js_rendered_site(html_content, url=""):
    """Detect if a site is primarily JavaScript-rendered (like Wix, React SPAs).
    
    Returns True if the page appears to be JS-rendered and needs a browser to scrape.
    """
    html_lower = html_content.lower()
    url_lower = url.lower()
    
    # Check URL patterns first for known JS platforms
    if any(indicator in url_lower for indicator in ['wix.com', 'squarespace.com', 'webflow.io']):
        return True
    
    # Check for framework indicators in the HTML content
    for indicator in JS_FRAMEWORK_INDICATORS:
        if indicator.lower() in html_lower:
            return True
    
    # If the page has very little visible text content but lots of scripts, it's likely JS-rendered
    # Count script tags vs actual content length (rough heuristic)
    script_count = html_lower.count('<script')
    visible_content = len(html_content) - html_lower.count('<script') * 500  # rough estimate
    
    if script_count > 20 and visible_content < 5000:
        return True
    
    return False


def get_playwright_browser():
    """Get or create a persistent Playwright browser instance.
    
    Reuses the same browser across all requests to save memory and startup time.
    Each Chromium launch uses ~200MB, so reusing is critical for efficiency.
    """
    global PLAYWRIGHT_BROWSER, _PLAYWRIGHT_CONTEXT
    
    if not USE_PLAYWRIGHT:
        return None, None
    
    # Create browser only once and reuse it
    if PLAYWRIGHT_BROWSER is None:
        print("🚀 Starting Playwright browser (one-time)...", file=sys.stderr)
        global _PLAYWRIGHT_MANAGER
        _PLAYWRIGHT_MANAGER = sync_playwright().start()
        
        # Launch with minimal memory footprint
        PLAYWRIGHT_BROWSER = _PLAYWRIGHT_MANAGER.chromium.launch(
            headless=True,
            args=[
                '--disable-dev-shm-usage',  # Reduces memory usage in Docker
                '--disable-gpu',  # Not needed for scraping
                '--no-sandbox',  # Required for some Docker environments
                '--disable-setuid-sandbox',
                '--disable-extensions',  # Don't load extensions
                '--disable-background-networking',
                '--disable-sync',
                '--disable-translate',
                '--metrics-recording-only',
                '--mute-audio',
                '--no-first-run',
                '--safebrowsing-disable-auto-update',
            ]
        )
        
        # Create a single context with smaller viewport to save memory
        _PLAYWRIGHT_CONTEXT = PLAYWRIGHT_BROWSER.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1280, "height": 720}  # Smaller viewport = less memory
        )
    
    return PLAYWRIGHT_BROWSER, _PLAYWRIGHT_CONTEXT

# Global context for reuse
_PLAYWRIGHT_CONTEXT = None
_PLAYWRIGHT_MANAGER = None


def fetch_with_playwright(url, timeout=30):
    """Use Playwright to fetch JavaScript-rendered pages.
    
    OPTIMIZED: Reuses a single browser instance across all requests
    instead of launching a new browser for each page (~200MB savings per page).
    """
    if not USE_PLAYWRIGHT:
        print("Playwright not installed - cannot render JS pages", file=sys.stderr)
        return None
    
    try:
        print(f"📄 Rendering: {url}", file=sys.stderr)
        
        # Get or create the persistent browser
        browser, context = get_playwright_browser()
        if not browser or not context:
            return None
        
        # Create a new page (lightweight, ~5MB vs ~200MB for new browser)
        page = context.new_page()
        
        try:
            # Navigate and wait for DOM to be ready (faster than networkidle)
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
            
            # Short wait for JS to render dynamic content
            page.wait_for_timeout(1000)
            
            # Get the fully rendered HTML content
            html_content = page.content()
            
            return html_content
        finally:
            # Always close the page to free memory, but keep browser running
            page.close()
            
    except Exception as e:
        print(f"Playwright error for {url}: {e}", file=sys.stderr)
        return None


def close_playwright():
    """Clean up Playwright browser instance - call when scraping is complete."""
    global PLAYWRIGHT_BROWSER, _PLAYWRIGHT_CONTEXT, _PLAYWRIGHT_MANAGER
    
    if _PLAYWRIGHT_CONTEXT:
        try:
            _PLAYWRIGHT_CONTEXT.close()
        except:
            pass
        _PLAYWRIGHT_CONTEXT = None
    
    if PLAYWRIGHT_BROWSER:
        try:
            PLAYWRIGHT_BROWSER.close()
        except:
            pass
        PLAYWRIGHT_BROWSER = None
    
    if _PLAYWRIGHT_MANAGER:
        try:
            _PLAYWRIGHT_MANAGER.stop()
        except:
            pass
        _PLAYWRIGHT_MANAGER = None
    
    print("🧹 Playwright browser closed", file=sys.stderr)


def fetch_url(session, url, method="GET", timeout=15, allow_cloudscraper=True, referer=None):
    """Perform an HTTP request with rotation + proxy + Cloudflare fallback."""
    max_attempts = max(1, int(os.environ.get("SCRAPER_TOTAL_ATTEMPTS", "3")))
    last_error = None
    for attempt in range(1, max_attempts + 1):
        headers = build_rotating_headers(referer=referer)
        proxies = choose_proxy()
        try:
            response = session.request(
                method,
                url,
                headers=headers,
                timeout=timeout,
                proxies=proxies,
            )
            if response is not None and not looks_like_bot_block(response):
                return response
            print(f"Bot protection triggered on attempt {attempt} for {url}", file=sys.stderr)
            if allow_cloudscraper:
                cloud_session = get_cloudscraper_session()
                if cloud_session:
                    try:
                        cloud_response = cloud_session.request(
                            method,
                            url,
                            headers=headers,
                            timeout=timeout,
                            proxies=proxies,
                        )
                        if cloud_response is not None and not looks_like_bot_block(cloud_response):
                            return cloud_response
                    except Exception as cloud_err:
                        last_error = cloud_err
                        print(f"Cloudscraper error for {url}: {cloud_err}", file=sys.stderr)
        except requests.RequestException as exc:
            last_error = exc
            print(f"Request error for {url}: {exc}", file=sys.stderr)
        human_delay(0.9, 2.2)
    if last_error:
        print(f"Giving up on {url}: {last_error}", file=sys.stderr)
    return None

def detect_category_page(base_url, session):
    """Try to find product listing pages automatically."""
    # Common e-commerce paths
    possible_paths = [
        "/shop/", "/store/", "/products/", "/collections/all/",
        "/category/", "/product-category/", "/catalog/",
        "/all-products/", "/shop-all/", "/items/", "/Shop By Categories/",
        "/browse/", "/search/", "/all/", "/all-products/", "/all-categories/", "/Shop by Category/", "/Collections/"
    ]
    
    print("Searching for product pages...", file=sys.stderr)
    
    for path in possible_paths:
        test_url = urljoin(base_url, path)
        resp = fetch_url(session, test_url, timeout=12, allow_cloudscraper=False, referer=base_url)
        if resp and resp.status_code == 200 and "product" in resp.text.lower():
                print(f"Found product page: {test_url}", file=sys.stderr)
                return test_url
    
    # If no specific path works, try the homepage
    print("No specific product page found, trying homepage...", file=sys.stderr)
    return base_url


def get_product_links_from_sitemap(base_url, session, visited=None):
    """Try to get product links from sitemap.xml."""
    if visited is None:
        visited = set()
    
    product_links = set()
    sitemap_urls = [
        urljoin(base_url, "/sitemap.xml"),
        urljoin(base_url, "/sitemap_index.xml"),
        urljoin(base_url, "/product-sitemap.xml"),
    ]
    
    for sitemap_url in sitemap_urls:
        if sitemap_url in visited:
            continue
        visited.add(sitemap_url)
        
        try:
            print(f"Checking sitemap: {sitemap_url}", file=sys.stderr)
            resp = fetch_url(session, sitemap_url, timeout=15)
            if resp and resp.status_code == 200:
                root = ET.fromstring(resp.content)
                ns = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
                
                # Check if it's a sitemap index
                sitemaps = root.findall('.//ns:sitemap/ns:loc', ns)
                if sitemaps:
                    for sitemap in sitemaps:
                        sitemap_text = sitemap.text
                        if sitemap_text not in visited and 'product' in sitemap_text.lower():
                            product_links.update(get_product_links_from_sitemap(sitemap_text, session, visited))
                
                # Get URLs from sitemap
                urls = root.findall('.//ns:url/ns:loc', ns)
                for url in urls:
                    url_text = url.text
                    # Match various e-commerce URL patterns
                    # Match various e-commerce URL patterns - includes Wix product-page for Wix sites
                    if any(pattern in url_text.lower() for pattern in [
                        '/product/', '/products/', '/p/', '/item/', '/items/',
                        '/shop/', '/store/', '.html', '/buy/', '/pd/', '/product-page/'
                    ]):
                        product_links.add(url_text)
                
                if product_links:
                    print(f"Found {len(product_links)} products in sitemap", file=sys.stderr)
                    return product_links
        except Exception:
            continue
    
    return product_links


def get_product_links(category_url, session, use_playwright=False):
    """Collect product URLs from page (supports ALL e-commerce platforms).
    
    If use_playwright is True or if JS-rendering is detected, uses Playwright
    to render the page before extracting product links.
    """
    product_links = set()
    try:
        html_content = None
        
        # If Playwright mode is explicitly enabled, skip HTTP and go straight to browser
        if use_playwright and USE_PLAYWRIGHT:
            print("🎭 Rendering page with Playwright browser...", file=sys.stderr)
            html_content = fetch_with_playwright(category_url)
            if not html_content:
                print("⚠️ Playwright rendering failed", file=sys.stderr)
                return product_links
        else:
            # Try with standard HTTP request first
            resp = fetch_url(session, category_url, timeout=15)
            if not resp:
                return product_links
            
            html_content = resp.text
            
            # Check if this is a JavaScript-rendered site (like Wix)
            # If so, fall back to Playwright for proper rendering
            if is_js_rendered_site(html_content, category_url):
                print("Detected JavaScript-rendered site, using Playwright...", file=sys.stderr)
                playwright_html = fetch_with_playwright(category_url)
                if playwright_html:
                    html_content = playwright_html
                else:
                    print("Playwright failed, using static HTML (may be incomplete)", file=sys.stderr)
        
        soup = BeautifulSoup(html_content, "html.parser")

        # Universal selectors for all e-commerce platforms (including Wix)
        selectors = [
            "a.woocommerce-LoopProduct-link",  # WooCommerce
            "a.product-item-link",  # Magento
            "a.product-link",  # Generic
            "a[href*='/product/']",  # Generic product URLs
            "a[href*='/products/']",  # Shopify
            "a[href*='/product-page/']",  # Wix product pages
            "a[href*='/p/']",  # Short product URLs
            "a[href*='/item/']",  # Item URLs
            "a[href*='/pd/']",  # Product detail URLs
            ".product-item a",  # Product item links
            ".product a",  # Product links
            "article.product a",  # Article-based products
            "[itemtype*='Product'] a",  # Schema.org markup
            ".grid-product a",  # Grid layouts
            ".product-card a",  # Card layouts
        ]
        
        # URL patterns to match - includes Wix product-page pattern for Wix sites
        url_patterns = ['/product/', '/products/', '/p/', '/item/', '/items/', '/pd/', '/shop/', '.html', '/product-page/']
        
        # Get the base domain to filter out external links
        from urllib.parse import urlparse
        base_domain = urlparse(category_url).netloc
        
        for selector in selectors:
            links = soup.select(selector)
            for a in links:
                href = a.get("href")
                if href and any(pattern in href.lower() for pattern in url_patterns):
                    full_url = urljoin(category_url, href)
                    # Only add if it's from the same domain
                    if urlparse(full_url).netloc == base_domain:
                        product_links.add(full_url)
        
        # If no products found with selectors, try finding ANY links with product patterns
        if not product_links:
            print("🔍 Trying broader search for product links...", file=sys.stderr)
            for a in soup.find_all('a', href=True):
                href = a.get('href')
                if href and any(pattern in href.lower() for pattern in url_patterns):
                    full_url = urljoin(category_url, href)
                    # Only add if it's from the same domain
                    if urlparse(full_url).netloc == base_domain:
                        # Avoid navigation/category links (but allow 'product-page' for Wix)
                        if 'product-page' in href.lower() or not any(skip in href.lower() for skip in ['category', 'collection', 'tag', 'page', 'cart', 'checkout', 'account']):
                            product_links.add(full_url)
        
        next_selectors = ["a.next", ".pagination a[rel='next']", "a[aria-label='Next']"]
        for selector in next_selectors:
            next_link = soup.select_one(selector)
            if next_link:
                next_url = urljoin(category_url, next_link.get("href"))
                product_links.update(get_product_links(next_url, session))
                break
                
    except Exception as e:
        print(f"Error fetching {category_url}: {e}", file=sys.stderr)

    return product_links


def is_simple_product(soup):
    """Check if the product is simple (not grouped, bundle, or configurable) - Universal for all platforms."""
    
    # Wix product options/variants - check for option dropdowns or selectors
    wix_options = soup.select("[data-hook='product-options'] select, [data-hook='product-options'] [role='listbox']")
    if wix_options:
        for opt in wix_options:
            # If the option has multiple choices, it's a variable product
            options = opt.find_all('option') or opt.find_all('[role="option"]')
            if len(options) > 1:
                return False
    
    # WooCommerce variations
    if soup.select("form.variations_form, .variations, table.variations, .single_variation_wrap"):
        return False
    
    # WooCommerce grouped products
    if soup.select(".grouped_form, table.group_table, .woocommerce-grouped-product-list"):
        return False
    
    # WooCommerce bundles
    if soup.select(".bundle_form, .bundled_products, .woocommerce-product-bundle"):
        return False
    
    # Shopify variants - be more lenient, check if there are actual multiple options
    variant_selects = soup.select("select[name='id'], .product-form__variants select, variant-selects select, variant-radios input")
    if variant_selects:
        # Check if it's a real variant selector (more than 1 option) or just a single option
        for select in variant_selects:
            if select.name == 'select':
                options = select.find_all('option')
                if len(options) > 1:
                    return False
            elif select.name == 'input' and select.get('type') == 'radio':
                # Count radio buttons with same name
                name = select.get('name')
                if name:
                    radios = soup.find_all('input', {'type': 'radio', 'name': name})
                    if len(radios) > 1:
                        return False
    
    # Magento configurable products
    if soup.select(".swatch-attribute, .configurable-options, #product-options-wrapper select"):
        return False
    
    # Generic size/color selectors (indicates variants) - but check if they have multiple options
    size_color_selects = soup.select("select[name*='size'], select[name*='color'], select[name*='variant']")
    for select in size_color_selects:
        options = select.find_all('option')
        if len(options) > 1:
            return False
    
    # Check body classes
    body = soup.select_one("body")
    if body:
        classes = body.get("class", [])
        if any(cls in ["product-type-variable", "product-type-grouped", "product-type-bundle", 
                       "product-type-configurable"] for cls in classes):
            return False
    
    return True


def extract_product_data(url, session=None, use_playwright=False):
    """Extract product details (universal e-commerce support).
    
    If use_playwright is True, uses Playwright to render the page before
    extracting product data (needed for JS-rendered sites like Wix).
    """
    try:
        if session is None:
            session = build_retry_session()
        
        html_content = None
        
        # If Playwright mode is enabled, use it to render the page
        if use_playwright and USE_PLAYWRIGHT:
            html_content = fetch_with_playwright(url)
        
        # Fallback to standard HTTP request
        if not html_content:
            resp = fetch_url(session, url, timeout=20)
            if not resp:
                print(f"Skipping {url} after repeated blocks.", file=sys.stderr)
                return None
            html_content = resp.text
        
        soup = BeautifulSoup(html_content, "html.parser")

        if not is_simple_product(soup):
            print(f"Skipping (not simple): {url}", file=sys.stderr)
            return None

        # Name - Universal selectors for all platforms (including Wix)
        name = (soup.select_one("h1.product_title") or  # WooCommerce
                soup.select_one("h1.product-title") or  # Generic
                soup.select_one("h1[itemprop='name']") or  # Schema.org
                soup.select_one("[data-hook='product-title']") or  # Wix stores
                soup.select_one(".product-title") or  # Generic
                soup.select_one("h1.entry-title") or  # WordPress
                soup.select_one(".page-title") or  # Magento
                soup.select_one(".product-name") or  # Generic
                soup.select_one("h1.h2") or  # Shopify
                soup.select_one("h1"))
        
        # Price - Universal extraction for all platforms
        price_text = ""
        import re
        
        # Method 1: Check for sale/current price first (prioritize <ins> over <del>)
        current_price = (soup.select_one("p.price ins .woocommerce-Price-amount") or  # WooCommerce sale price
                        soup.select_one("ins .amount") or  # Generic sale price
                        soup.select_one(".sale-price") or  # Generic sale
                        soup.select_one(".current-price") or  # Current price
                        soup.select_one(".price__sale .price-item--sale") or  # Shopify sale
                        soup.select_one("span.price-item--sale"))  # Shopify sale
        
        if current_price:
            price_text = current_price.get_text(strip=True)
        else:
            # Method 2: Standard e-commerce selectors (if no sale price)
            # Includes Wix-specific data-hook attributes
            price_elem = (soup.select_one("[data-hook='formatted-primary-price']") or  # Wix stores
                          soup.select_one("[data-hook='product-price']") or  # Wix stores
                          soup.select_one("p.price .woocommerce-Price-amount") or  # WooCommerce
                          soup.select_one("span.woocommerce-Price-amount") or  # WooCommerce
                          soup.select_one("p.price") or  # WooCommerce
                          soup.select_one(".product-price") or  # Generic
                          soup.select_one("[itemprop='price']") or  # Schema.org
                          soup.select_one(".price__regular .price-item") or  # Shopify regular
                          soup.select_one(".price") or  # Generic
                          soup.select_one(".price-box .price") or  # Magento
                          soup.select_one("span.money"))  # Shopify
            
            if price_elem:
                price_text = price_elem.get_text(strip=True)
        
        # Clean up price text - extract only the actual price value
        if price_text:
            # Remove extra text like "Regular price", "Sale price", "Unit price", etc.
            price_text = re.sub(r'(Regular price|Sale price|Unit price|per|Sold out)', '', price_text, flags=re.IGNORECASE)
            # Extract all price patterns found
            matches = re.findall(r'(?:Rs\.?\s*|[\$₹€£¥])[\d,]+\.?\d*', price_text)
            if matches:
                # Get the last match (usually the sale/current price)
                price_text = matches[-1].strip()
        
        # Method 3: Look in table cells (for custom platforms)
        if not price_text:
            for td in soup.find_all("td"):
                td_text = td.get_text(strip=True)
                if '$' in td_text and ('=' in td_text or '/lbs' in td_text or 'lb' in td_text):
                    match = re.search(r'\$[\d.]+(?:/lbs)?', td_text)
                    if match:
                        price_text = match.group()
                        break
            
        # Method 4: Search for price patterns anywhere (last resort)
        if not price_text:
                for elem in soup.find_all(['span', 'div', 'p'], class_=re.compile(r'price', re.I)):
                    text = elem.get_text(strip=True)
                    match = re.search(r'[\$₹€£¥][\d,]+\.?\d*', text)
                    if match:
                        price_text = match.group()
                        break
        
        # Description - Universal selectors for all platforms
        desc = (soup.select_one("div.woocommerce-product-details__short-description") or  # WooCommerce
                soup.select_one(".product-description") or  # Generic
                soup.select_one("[itemprop='description']") or  # Schema.org
                soup.select_one(".short-description") or  # Generic
                soup.select_one(".description") or  # Generic
                soup.select_one(".product-short-description") or  # Generic
                soup.select_one(".product-info-description") or  # Magento
                soup.select_one(".product__description"))  # Shopify
                # Note: meta[name='description'] removed - it truncates to 160 chars
        
        # If no description found, search for p tags near product info
        if not desc:
            # Look for p tags that contain substantial product description text
            for p in soup.find_all('p'):
                text = p.get_text(strip=True)
                # Check if it's a product description (reasonable length, not navigation/menu)
                # Skip if it contains common navigation/menu keywords
                skip_keywords = ['cookie', 'copyright', 'menu', 'navigation', 'products -', 'quick view', 'mailing list', 'all products']
                if 50 < len(text) < 1000 and not any(skip in text.lower() for skip in skip_keywords):
                    desc = p
                    break
        
        # Image - Universal selectors for all platforms
        image = (soup.select_one("img.wp-post-image") or  # WooCommerce
                 soup.select_one(".woocommerce-product-gallery__image img") or  # WooCommerce
                 soup.select_one(".product-image img") or  # Generic
                 soup.select_one("[itemprop='image']") or  # Schema.org
                 soup.select_one("img[src*='product']") or  # Generic
                 soup.select_one(".product-gallery img") or  # Generic
                 soup.select_one(".product-media img") or  # Magento
                 soup.select_one(".product__media img") or  # Shopify
                 soup.select_one("meta[property='og:image']") or  # Open Graph
                 soup.select_one(".main-image img"))  # Generic
        
        # Get image URL from various attributes
        image_url = ""
        if image:
            # Try different attributes (data-src, src, content for meta tags)
            src = (image.get("data-src") or 
                   image.get("src") or 
                   image.get("data-lazy-src") or
                   image.get("content") or  # For meta tags
                   "")
            # Make sure it's a full URL
            if src and not src.startswith('http'):
                image_url = urljoin(url, src)
            else:
                image_url = src
        
        # If no image found or it's a placeholder/logo, search for actual product images
        if not image_url or any(skip in image_url.lower() for skip in ['logo', 'transparent', 'placeholder', 'default']):
            # Try to find product images in common containers first
            product_img_containers = soup.select('.product-gallery img, .product-images img, .product-media img, .woocommerce-product-gallery img')
            for img in product_img_containers:
                src = img.get('src', '') or img.get('data-src', '')
                if src and not any(skip in src.lower() for skip in ['logo', 'transparent', 'placeholder', 'stripe', 'payment']):
                    if not src.startswith('http'):
                        image_url = urljoin(url, src)
                    else:
                        image_url = src
                    break
            
            # If still no image, search all images
            if not image_url or any(skip in image_url.lower() for skip in ['logo', 'transparent', 'placeholder', 'default']):
                for img in soup.find_all('img'):
                    src = img.get('src', '') or img.get('data-src', '')
                    # Look for images in common product image paths
                    if any(pattern in src.lower() for pattern in ['/large/', '/medium/', '/product', '/item', '/files/']):
                        # Skip logos and placeholders
                        if not any(skip in src.lower() for skip in ['logo', 'transparent', 'placeholder', 'stripe', 'payment']):
                            if not src.startswith('http'):
                                image_url = urljoin(url, src)
                            else:
                                image_url = src
                            break
        
        # Get description text and clean up
        desc_text = ""
        if desc:
            desc_text = desc.get_text(strip=True)
            # Clean up the description: remove extra whitespace and newlines
            desc_text = ' '.join(desc_text.split())  # Replace multiple spaces/newlines with single space
        
        category = (soup.select_one("span.posted_in a") or
                    soup.select_one(".product-category") or
                    soup.select_one("[rel='tag']"))
        
        stock = "In stock" if soup.select_one(".in-stock, .available, [itemprop='availability']") else "Out of stock"

        return {
            "name": name.get_text(strip=True) if name else "",
            "price": price_text,
            "description": desc_text,
            "imageUrl": image_url,
            "url": url,
        }

    except Exception as e:
        print(f"Error processing {url}: {e}", file=sys.stderr)
        return None


def send_progress_update(api_base_url, agent_token, discovered=None, sent=None, created=None, total=None, phase=None, message=None):
    """Send progress update to the backend API (optional, best-effort).
    
    Args:
        api_base_url: Base URL for the API (e.g., https://staging.rekohub.com/api)
        agent_token: Bearer token for authentication
        discovered: Number of items discovered during scraping
        sent: Number of items POSTed to the API
        created: Number of items confirmed created by backend
        total: Best-effort estimate of total items
        phase: Current phase ("discovery" | "scraping" | "importing" | "complete" | "error")
        message: Human-readable status message
    """
    if not api_base_url or not agent_token:
        return  # Progress updates are optional
    
    try:
        payload = {}
        if discovered is not None:
            payload['discoveredCount'] = discovered
        if sent is not None:
            payload['sentCount'] = sent
        if created is not None:
            payload['createdCount'] = created
        if total is not None:
            payload['totalCount'] = total
        if phase is not None:
            payload['phase'] = phase
        if message is not None:
            payload['message'] = message
        
        # Only send if we have something to report
        if not payload:
            return
        
        progress_url = f"{api_base_url.rstrip('/')}/v4/auto-onboard/progress"
        headers = {
            'Authorization': f'Bearer {agent_token}',
            'Content-Type': 'application/json'
        }
        
        response = requests.post(progress_url, json=payload, headers=headers, timeout=10)
        if response.status_code == 202:
            print(f"📊 Progress update sent: {message or payload}", file=sys.stderr)
        else:
            print(f"⚠️ Progress update failed: {response.status_code}", file=sys.stderr)
    except Exception as e:
        # Don't fail the scrape if progress updates fail
        print(f"⚠️ Progress update error: {e}", file=sys.stderr)


def send_item_to_api(api_base_url, agent_token, item):
    """Send a single item to the backend API immediately after scraping.
    
    Args:
        api_base_url: Base URL for the API (e.g., https://staging.rekohub.com/api)
        agent_token: Bearer token for authentication
        item: Item dict with name, price, description, imageUrl, url
        
    Returns:
        bool: True if successful, False otherwise
    """
    if not api_base_url or not agent_token:
        return False
    
    try:
        items_url = f"{api_base_url.rstrip('/')}/v4/auto-onboard/items"
        headers = {
            'Authorization': f'Bearer {agent_token}',
            'Content-Type': 'application/json'
        }
        
        # Map our item format to the API format
        # The API expects: name, description, price, imageUrl
        # Our item has: name, price, description, imageUrl, url
        payload = {
            'name': item.get('name', ''),
            'description': item.get('description', ''),
            'price': item.get('price', ''),
            'imageUrl': item.get('imageUrl', ''),
            'sourceItemId': item.get('url', '')  # Use source URL as identifier
        }
        
        response = requests.post(items_url, json=payload, headers=headers, timeout=15)
        if response.status_code == 202:
            print(f"✅ Item sent to API: {item.get('name', 'Unknown')[:50]}", file=sys.stderr)
            return True
        else:
            print(f"⚠️ Failed to send item (HTTP {response.status_code}): {item.get('name', 'Unknown')[:50]}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"⚠️ Error sending item to API: {e}", file=sys.stderr)
        return False


def scrape_site(base_url, api_base_url=None, agent_token=None):
    """Main scraping function.
    
    Automatically detects JavaScript-rendered sites (like Wix, React SPAs) and
    uses Playwright browser rendering when needed.
    
    Args:
        base_url: The URL to scrape
        api_base_url: Optional API base URL for progress updates
        agent_token: Optional bearer token for progress updates
    """
    session = build_retry_session()
    category_url = base_url
    use_playwright = False  # Will be set to True if JS-rendering is detected
    
    # STEP 0: Quick check if site is JS-rendered or bot-protected
    # This saves time by avoiding multiple failed requests before trying Playwright
    if USE_PLAYWRIGHT:
        print("🔍 Quick check: Testing if site is JavaScript-rendered...", file=sys.stderr)
        # Do a quick single request (not the full retry loop)
        try:
            quick_resp = session.get(base_url, headers=build_rotating_headers(), timeout=10)
            if quick_resp and quick_resp.status_code == 200:
                if is_js_rendered_site(quick_resp.text, base_url):
                    print("🌐 Detected JavaScript-rendered site (Wix/React/Vue)! Using Playwright...", file=sys.stderr)
                    use_playwright = True
                elif looks_like_bot_block(quick_resp):
                    print("🔒 Bot protection detected! Using Playwright browser...", file=sys.stderr)
                    use_playwright = True
            else:
                # Non-200 response or no response - likely bot protection
                print("🔒 Site may be blocking requests! Using Playwright browser...", file=sys.stderr)
                use_playwright = True
        except Exception as e:
            print(f"⚠️ Quick check failed ({e}), will try Playwright...", file=sys.stderr)
            use_playwright = True
    
    # If Playwright is needed, skip the slow HTTP methods and go straight to browser rendering
    if use_playwright:
        print("🎭 Using Playwright browser to render JavaScript...", file=sys.stderr)
        product_links = get_product_links(base_url, session, use_playwright=True)
    else:
        # Standard scraping flow for non-JS sites
        print("Method 1: Trying sitemap...", file=sys.stderr)
        product_links = get_product_links_from_sitemap(base_url, session)
        
        if not product_links:
            print("Method 2: Searching for product pages...", file=sys.stderr)
            category_url = detect_category_page(base_url, session)
            product_links = get_product_links(category_url, session, use_playwright=False)
        
        # If still no products, try Playwright as last resort
        if not product_links and USE_PLAYWRIGHT:
            print("Method 3: Trying Playwright browser as fallback...", file=sys.stderr)
            use_playwright = True
            product_links = get_product_links(base_url, session, use_playwright=True)
    
    # If still no products, try scraping the homepage directly
    if not product_links and base_url != category_url:
        print("Method 4: Trying homepage...", file=sys.stderr)
        product_links = get_product_links(base_url, session, use_playwright=use_playwright)
    
    print(f"\n🔗 Found {len(product_links)} product links", file=sys.stderr)
    
    # Send initial discovery progress update
    send_progress_update(
        api_base_url, 
        agent_token,
        discovered=len(product_links),
        total=len(product_links),
        phase="discovery",
        message=f"Discovered {len(product_links)} products"
    )
    
    if len(product_links) == 0:
        print("\n❌ No products found!", file=sys.stderr)
        print("Possible reasons:", file=sys.stderr)
        if not USE_PLAYWRIGHT:
            print("  1. The site uses JavaScript - install playwright: pip install playwright && playwright install chromium", file=sys.stderr)
        else:
            print("  1. The site may use a custom JavaScript framework not yet supported", file=sys.stderr)
        print("  2. The site may be blocking automated access", file=sys.stderr)
        print("  3. Try providing a direct category/product listing page URL", file=sys.stderr)
        print("\n💡 Tip: Navigate to a product category page in your browser,", file=sys.stderr)
        print("   copy that URL, and use it with the scraper.\n", file=sys.stderr)

    # Surface what rendering modes are available
    if USE_PLAYWRIGHT:
        print("✅ Playwright browser rendering enabled for JavaScript sites...", file=sys.stderr)
    else:
        print("⚠️ Playwright not installed - JS sites may not work (pip install playwright && playwright install chromium)...", file=sys.stderr)
    
    if USE_CLOUDSCRAPER:
        print("✅ Cloudscraper fallback enabled for tough endpoints...", file=sys.stderr)
    else:
        print("⚠️ Using hardened requests session (install cloudscraper for tougher sites)...", file=sys.stderr)
    
    data = []
    skipped = 0
    sent_count = 0
    total_links = len(product_links)
    has_api_integration = bool(api_base_url and agent_token)
    
    # Send initial scraping phase update
    send_progress_update(
        api_base_url,
        agent_token,
        discovered=total_links,
        sent=0,
        total=total_links,
        phase="scraping",
        message=f"Starting to scrape {total_links} products"
    )
    
    for i, url in enumerate(product_links, 1):
        print(f"Processing {i}/{total_links}: {url}", file=sys.stderr)
        # Use Playwright for product pages if the site was detected as JS-rendered
        item = extract_product_data(url, session, use_playwright=use_playwright)
        
        if item:
            data.append(item)
            print(f"✓ Scraped: {item.get('name', 'Unknown')[:50]}", file=sys.stderr)
            
            # If API integration is enabled, send item immediately
            if has_api_integration:
                success = send_item_to_api(api_base_url, agent_token, item)
                if success:
                    sent_count += 1
            
            # Send progress update after each successful item (for real-time updates)
            send_progress_update(
                api_base_url,
                agent_token,
                discovered=total_links,
                sent=sent_count if has_api_integration else len(data),
                created=sent_count if has_api_integration else len(data),
                total=total_links,
                phase="importing",
                message=f"Imported {sent_count if has_api_integration else len(data)} of {total_links} products ({skipped} skipped)"
            )
        else:
            skipped += 1
            # Send progress update even for skipped items to show activity
            send_progress_update(
                api_base_url,
                agent_token,
                discovered=total_links,
                sent=sent_count if has_api_integration else len(data),
                created=sent_count if has_api_integration else len(data),
                total=total_links,
                phase="importing",
                message=f"Imported {sent_count if has_api_integration else len(data)} of {total_links} products ({skipped} skipped)"
            )
        
        human_delay(1.2, 2.7)  # Jittered delay to avoid rate limiting
    
    # Clean up Playwright browser if it was used
    if use_playwright:
        close_playwright()
    
    has_api_integration = bool(api_base_url and agent_token)
    final_count = sent_count if has_api_integration else len(data)
    
    print(f"\n📊 Summary: {len(data)} simple products scraped, {skipped} skipped", file=sys.stderr)
    if has_api_integration:
        print(f"📤 Sent {sent_count} items to API", file=sys.stderr)
    
    # Send final completion update
    send_progress_update(
        api_base_url,
        agent_token,
        discovered=total_links,
        sent=final_count,
        created=final_count,
        total=total_links,
        phase="complete",
        message=f"Completed: {final_count} products imported, {skipped} skipped"
    )

    # Always output JSON
    output = json.dumps(data, indent=2)
    print(output)
    return data


if __name__ == "__main__":
    print("\n" + "="*60, file=sys.stderr)
    print("Universal E-Commerce Product Scraper", file=sys.stderr)
    print("="*60, file=sys.stderr)
    print("\nThis scraper extracts only simple products.", file=sys.stderr)
    print("It ignores grouped, bundle, and configurable products.", file=sys.stderr)
    print("\nSupports: WooCommerce, Shopify, Magento, and custom platforms", file=sys.stderr)
    print("\nNOTE: For JavaScript-heavy sites (React/Vue/Angular),", file=sys.stderr)
    print("provide a direct product listing/category page URL.", file=sys.stderr)
    print("="*60 + "\n", file=sys.stderr)
    
    if len(sys.argv) > 1:
        url = sys.argv[1].strip()
    else:
        url = input("Enter website URL to scrape: ").strip()
    
    if not url:
        print(" No URL provided. Exiting.", file=sys.stderr)
        sys.exit(1)
    
    print(f"\n Starting scrape for: {url}\n", file=sys.stderr)
    scrape_site(url)
    
def run():
    url = os.environ.get("SCRAPE_URL")
    if not url:
        return {"error": "SCRAPE_URL environment variable not provided"}
    
    return scrape_site(url)