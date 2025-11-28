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

# Base headers mimic a modern browser; per-request rotation is layered on top
# inside `build_rotating_headers` to avoid static fingerprints.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
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
                    if any(pattern in url_text.lower() for pattern in [
                        '/product/', '/products/', '/p/', '/item/', '/items/',
                        '/shop/', '/store/', '.html', '/buy/', '/pd/'
                    ]):
                        product_links.add(url_text)
                
                if product_links:
                    print(f"Found {len(product_links)} products in sitemap", file=sys.stderr)
                    return product_links
        except Exception:
            continue
    
    return product_links


def get_product_links(category_url, session):
    """Collect product URLs from page (supports ALL e-commerce platforms)."""
    product_links = set()
    try:
        resp = fetch_url(session, category_url, timeout=15)
        if not resp:
            return product_links
        soup = BeautifulSoup(resp.text, "html.parser")

        # Universal selectors for all e-commerce platforms
        selectors = [
            "a.woocommerce-LoopProduct-link",  # WooCommerce
            "a.product-item-link",  # Magento
            "a.product-link",  # Generic
            "a[href*='/product/']",  # Generic product URLs
            "a[href*='/products/']",  # Shopify
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
        
        # URL patterns to match
        url_patterns = ['/product/', '/products/', '/p/', '/item/', '/items/', '/pd/', '/shop/', '.html']
        
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
                        # Avoid navigation/category links
                        if not any(skip in href.lower() for skip in ['category', 'collection', 'tag', 'page', 'cart', 'checkout', 'account']):
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


def extract_product_data(url, session=None):
    """Extract product details (universal e-commerce support)."""
    try:
        if session is None:
            session = build_retry_session()
        
        resp = fetch_url(session, url, timeout=20)
        if not resp:
            print(f"Skipping {url} after repeated blocks.", file=sys.stderr)
            return None
        
        soup = BeautifulSoup(resp.text, "html.parser")

        if not is_simple_product(soup):
            print(f"Skipping (not simple): {url}", file=sys.stderr)
            return None

        # Name - Universal selectors for all platforms
        name = (soup.select_one("h1.product_title") or  # WooCommerce
                soup.select_one("h1.product-title") or  # Generic
                soup.select_one("h1[itemprop='name']") or  # Schema.org
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
            price_elem = (soup.select_one("p.price .woocommerce-Price-amount") or  # WooCommerce
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


def scrape_site(base_url):
    """Main scraping function."""
    session = build_retry_session()
    category_url = base_url
    print("Method 1: Trying sitemap...", file=sys.stderr)
    product_links = get_product_links_from_sitemap(base_url, session)
    
    if not product_links:
        print("Method 2: Searching for product pages...", file=sys.stderr)
        category_url = detect_category_page(base_url, session)
        product_links = get_product_links(category_url, session)
    
    # If still no products, try scraping the homepage directly
    if not product_links and base_url != category_url:
        print("Method 3: Trying homepage...", file=sys.stderr)
        product_links = get_product_links(base_url, session)
    
    print(f"\n🔗 Found {len(product_links)} product links", file=sys.stderr)
    
    if len(product_links) == 0:
        print("\n No products found!", file=sys.stderr)
        print("Possible reasons:", file=sys.stderr)
        print("  1. The site uses JavaScript to load products (React/Vue/Angular)", file=sys.stderr)
        print("  2. The site may be blocking automated access", file=sys.stderr)
        print("  3. Try providing a direct category/product listing page URL", file=sys.stderr)
        print("\n Tip: Navigate to a product category page in your browser,", file=sys.stderr)
        print("   copy that URL, and use it with the scraper.\n", file=sys.stderr)

    # Surface whether the stealth fallback is available so operators know expectations.
    if USE_CLOUDSCRAPER:
        print(" Cloudscraper fallback enabled for tough endpoints...", file=sys.stderr)
    else:
        print(" Using hardened requests session (install cloudscraper for tougher sites)...", file=sys.stderr)
    
    data = []
    skipped = 0
    for i, url in enumerate(product_links, 1):
        print(f"Processing {i}/{len(product_links)}: {url}", file=sys.stderr)
        item = extract_product_data(url, session)
        if item:
            data.append(item)
            print(f"Added: {item.get('name', 'Unknown')[:50]}", file=sys.stderr)
        else:
            skipped += 1
        human_delay(1.2, 2.7)  # Jittered delay to avoid rate limiting
    
    print(f"\nSummary: {len(data)} simple products, {skipped} skipped", file=sys.stderr)

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
