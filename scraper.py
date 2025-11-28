import requests
from bs4 import BeautifulSoup
import json
import time
import sys
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

try:
    import cloudscraper
    USE_CLOUDSCRAPER = True
except ImportError:
    USE_CLOUDSCRAPER = False

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

def detect_category_page(base_url):
    """Try to find product listing pages automatically."""
    # Common e-commerce paths
    possible_paths = [
        "/shop/", "/store/", "/products/", "/collections/all/",
        "/category/", "/product-category/", "/catalog/", "/All-products/"
        "/all-products/", "/shop-all/", "/items/", "/Shop By Categories/",
        "/browse/", "/search/", "/all/", "/all-products/", "/all-categories/", "/Shop by Category/", "/Collections/"
    ]
    
    for path in possible_paths:
        test_url = urljoin(base_url, path)
        try:
            r = requests.get(test_url, headers=HEADERS, timeout=10)
            if r.status_code == 200 and "product" in r.text.lower():
                return test_url
        except requests.RequestException:
            continue
    return base_url


def get_product_links_from_sitemap(base_url, visited=None):
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
            resp = requests.get(sitemap_url, headers=HEADERS, timeout=10)
            if resp.status_code == 200:
                root = ET.fromstring(resp.content)
                ns = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
                
                # Check if it's a sitemap index
                sitemaps = root.findall('.//ns:sitemap/ns:loc', ns)
                if sitemaps:
                    for sitemap in sitemaps:
                        sitemap_text = sitemap.text
                        if sitemap_text not in visited and 'product' in sitemap_text.lower():
                            product_links.update(get_product_links_from_sitemap(sitemap_text, visited))
                
                # Get URLs from sitemap
                urls = root.findall('.//ns:url/ns:loc', ns)
                for url in urls:
                    url_text = url.text
                    # Match various e-commerce URL patterns
                    if any(pattern in url_text.lower() for pattern in [
                        '/product/', '/products/', '/p/', '/item/', '/items/',
                        '/shop/', '/store/', '.html', '/buy/', '/pd/', '/myproducts/',
                    ]):
                        product_links.add(url_text)
                
                # If no products found with standard patterns, try category-based patterns
                # Common for sites like earthytales.in that use /vegetables/, /fruits/, etc.
                if not product_links:
                    for url in urls:
                        url_text = url.text
                        # Exclude common non-product pages
                        exclude_patterns = [
                            '/blog', '/about', '/contact', '/faq', '/policy', '/policies',
                            '/terms', '/privacy', '/cart', '/checkout', '/account', '/login',
                            '/register', '/my-', '/testimonial', '/team', '/story', '/refer',
                            '/sitemap', '/search', '/tag', '/category', '/collection'
                        ]
                        # Check if URL has a path (not just homepage) and doesn't match exclude patterns
                        path = url_text.replace(base_url, '').strip('/')
                        if path and '/' in url_text and not any(ex in url_text.lower() for ex in exclude_patterns):
                            # Likely a product page
                            product_links.add(url_text)
                
                if product_links:
                    return product_links
        except Exception:
            continue
    
    return product_links


def detect_js_framework(html_content):
    """Detect if page uses JavaScript frameworks."""
    html_lower = html_content.lower()
    frameworks = {
        'react': 'react' in html_lower or '__react' in html_lower or 'reactdom' in html_lower,
        'vue': 'vue' in html_lower or '__vue' in html_lower or 'vue.js' in html_lower,
        'angular': 'angular' in html_lower or 'ng-' in html_lower or '@angular' in html_lower,
        'nextjs': '__next' in html_lower or 'next.js' in html_lower or '_next' in html_lower,
    }
    return any(frameworks.values()), frameworks


def get_product_links(category_url, driver=None, max_pages=10):
    """Collect product URLs from page (supports ALL e-commerce platforms including JS frameworks)."""
    product_links = set()
    visited_pages = set()
    pages_to_visit = [category_url]
    
    while pages_to_visit and len(visited_pages) < max_pages:
        current_url = pages_to_visit.pop(0)
        if current_url in visited_pages:
            continue
        visited_pages.add(current_url)
        
        try:
            # First try with regular requests
            resp = requests.get(current_url, headers=HEADERS, timeout=10)
            html_content = resp.text
            soup = BeautifulSoup(html_content, "html.parser")

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
            
            # URL patterns to match (including limeroad pattern: -p followed by digits and /scrap/)
            url_patterns = ['/product/', '/products/', '/p/', '/item/', '/items/', '/pd/', '/shop/', '.html', r'-p\d+', '/scrap/']
            
            # Get the base domain to filter out external links
            from urllib.parse import urlparse
            base_domain = urlparse(current_url).netloc
            
            for selector in selectors:
                links = soup.select(selector)
                for a in links:
                    href = a.get("href")
                    if href and any(pattern in href.lower() for pattern in url_patterns):
                        full_url = urljoin(current_url, href)
                        # Only add if it's from the same domain
                        if urlparse(full_url).netloc == base_domain:
                            product_links.add(full_url)
            
            # If no products found with selectors, check if it's a JS framework site
            if not product_links:
                uses_js, frameworks = detect_js_framework(html_content)
                
                if uses_js and SELENIUM_AVAILABLE:
                    detected = [name for name, found in frameworks.items() if found]
                    
                    # Create driver if not provided
                    if driver is None:
                        chrome_options = Options()
                        chrome_options.add_argument('--headless')
                        chrome_options.add_argument('--no-sandbox')
                        chrome_options.add_argument('--disable-dev-shm-usage')
                        chrome_options.add_argument('--disable-gpu')
                        chrome_options.add_argument('--window-size=1920,1080')
                        chrome_options.add_argument(f'user-agent={HEADERS["User-Agent"]}')
                        
                        # Stealth mode to avoid bot detection
                        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
                        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
                        chrome_options.add_experimental_option('useAutomationExtension', False)
                        
                        try:
                            from selenium import webdriver
                            from selenium.webdriver.chrome.service import Service
                            from selenium.webdriver.support.ui import WebDriverWait
                            from selenium.webdriver.support import expected_conditions as EC
                            from selenium.webdriver.common.by import By
                            from webdriver_manager.chrome import ChromeDriverManager
                            
                            service = Service(ChromeDriverManager().install())
                            driver = webdriver.Chrome(service=service, options=chrome_options)
                            driver.set_page_load_timeout(60)
                            should_quit_driver = True
                        except Exception as e:
                            driver = None
                            should_quit_driver = False
                    else:
                        should_quit_driver = False
                    
                    if driver:
                        try:
                            from selenium.webdriver.support.ui import WebDriverWait
                            from selenium.webdriver.support import expected_conditions as EC
                            from selenium.webdriver.common.by import By
                            
                            driver.get(current_url)
                            
                            # Wait for product links to appear (up to 20 seconds)
                            try:
                                wait = WebDriverWait(driver, 20)
                                wait.until(EC.presence_of_element_located((
                                    By.CSS_SELECTOR, 
                                    "a[href*='/product/'], a[href*='/scrap/'], a[href*='/p/'], .product-item a, .product a"
                                )))
                            except:
                                pass
                            
                            # Additional wait for AJAX calls
                            time.sleep(10)
                            
                            # Scroll to trigger lazy loading
                            driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
                            time.sleep(2)
                            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                            time.sleep(3)
                            driver.execute_script("window.scrollTo(0, 0);")
                            time.sleep(2)
                            
                            html_content = driver.page_source
                            soup = BeautifulSoup(html_content, "html.parser")
                        except Exception as e:
                            pass
                        finally:
                            if should_quit_driver:
                                driver.quit()

                for a in soup.find_all('a', href=True):
                    href = a.get('href')
                    # Use regex for -p\d+ pattern
                    import re
                    has_pattern = (any(pattern in href.lower() for pattern in url_patterns if not pattern.startswith('r')) or 
                                  re.search(r'-p\d+', href))
                    
                    if href and has_pattern:
                        full_url = urljoin(current_url, href)
                        # Only add if it's from the same domain
                        if urlparse(full_url).netloc == base_domain:
                            # Avoid navigation/category links
                            if not any(skip in href.lower() for skip in ['category', 'collection', 'tag', 'page', 'cart', 'checkout', 'account']):
                                product_links.add(full_url)
            
            # Look for pagination/next page links
            next_selectors = [
                "a.next", 
                ".pagination a[rel='next']", 
                "a[aria-label='Next']",
                "a[aria-label='Next page']",
                ".pagination__item--next a",
                "a.pagination__next",
                "link[rel='next']"
            ]
            
            for selector in next_selectors:
                next_link = soup.select_one(selector)
                if next_link:
                    next_href = next_link.get("href")
                    if next_href:
                        next_url = urljoin(current_url, next_href)
                        if next_url not in visited_pages and next_url not in pages_to_visit:
                            pages_to_visit.append(next_url)
                        break
            
            # Also check for page number links (e.g., ?page=2, ?page=3)
            page_links = soup.select(".pagination a[href*='page']")
            for link in page_links:
                page_href = link.get("href")
                if page_href:
                    page_url = urljoin(current_url, page_href)
                    if page_url not in visited_pages and page_url not in pages_to_visit:
                        pages_to_visit.append(page_url)
                    
        except Exception as e:
            pass

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
    
    # Multiple "Add to Cart" forms (indicates multiple variants/packages)
    # This catches custom platforms that list variants as separate forms on the same page
    add_to_cart_forms = soup.find_all('form', action=lambda x: x and ('cart' in x.lower() or 'add' in x.lower()))
    if len(add_to_cart_forms) > 1:
        # Check if they have different product IDs (indicating variants)
        product_ids = set()
        for form in add_to_cart_forms:
            product_id_input = form.find('input', attrs={'name': lambda x: x and 'product' in x.lower()})
            if product_id_input:
                product_ids.add(product_id_input.get('value'))
        # If multiple unique product IDs, it's a configurable product
        if len(product_ids) > 1:
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
            session = requests.Session()
        
        resp = session.get(url, headers=HEADERS, timeout=15)
        
        # Check if blocked by Cloudflare
        if resp.status_code == 403 or "Just a moment" in resp.text:
            time.sleep(3)
            resp = session.get(url, headers=HEADERS, timeout=15)
        
        soup = BeautifulSoup(resp.text, "html.parser")

        if not is_simple_product(soup):
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
        return None


def scrape_site(base_url):
    product_links = get_product_links_from_sitemap(base_url)
    
    if not product_links:
        category_url = detect_category_page(base_url)
        product_links = get_product_links(category_url)
    
    # If still no products, try scraping the homepage directly
    if not product_links and base_url != category_url:
        product_links = get_product_links(base_url)

    # Use cloudscraper if available, otherwise regular session
    if USE_CLOUDSCRAPER:
        session = cloudscraper.create_scraper()
    else:
        session = requests.Session()
    
    data = []
    for url in product_links:
        item = extract_product_data(url, session)
        if item:
            data.append(item)
        time.sleep(1)  # Delay to avoid rate limiting

    # Always output JSON
    print(json.dumps(data, indent=2))



if __name__ == "__main__":
    
    if len(sys.argv) > 1:
        url = sys.argv[1].strip()
    else:
        url = input("Enter website URL to scrape: ").strip()
    
    if not url:
        sys.exit(1)
    scrape_site(url)
