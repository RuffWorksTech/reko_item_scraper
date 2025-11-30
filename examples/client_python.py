"""
Python Example - Calling the Reko Item Scraper

Installation: pip install requests
Usage: python client_python.py https://example.com/shop
"""

import os
import sys
import requests
from typing import List, Dict, Optional

# Configuration: Switch between local and production
SCRAPER_URL = (
    "https://your-cloud-run-url.run.app"  # Replace with your Cloud Run URL
    if os.environ.get("ENV") == "production"
    else "http://localhost:8080"
)


def scrape_website(url: str) -> List[Dict]:
    """
    Scrape a website and return product data.
    
    Args:
        url: The URL of the e-commerce site to scrape
        
    Returns:
        List of product dictionaries
        
    Raises:
        requests.RequestException: If the scraper request fails
    """
    print(f"🔍 Scraping: {url}")
    print(f"📡 Using scraper at: {SCRAPER_URL}")
    
    try:
        response = requests.post(
            SCRAPER_URL,
            json={"URL": url},
            timeout=300,  # 5 minutes (scraping can be slow)
            headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()
        
        data = response.json()
        if data.get("status") == "ok":
            products = data.get("result", [])
            print(f"✅ Found {len(products)} products")
            return products
        else:
            raise ValueError("Scraper returned non-ok status")
            
    except requests.exceptions.ConnectionError:
        print("❌ Error: Cannot connect to scraper service.")
        print("   Make sure the scraper is running:")
        print("   docker-compose up -d")
        raise
    except requests.exceptions.Timeout:
        print("❌ Error: Scraper timeout. The site may be too large or slow.")
        raise
    except requests.exceptions.RequestException as e:
        print(f"❌ Scraper error: {e}")
        raise


def save_products_to_database(products: List[Dict]) -> None:
    """
    Save products to your database (example).
    Replace this with your actual database logic.
    
    Args:
        products: List of product dictionaries
    """
    # Example: Save to PostgreSQL, MongoDB, etc.
    for product in products:
        print(f"💾 Saving: {product['name']}")
        # db.session.add(Product(**product))
        # db.session.commit()


def main():
    """Main execution function."""
    # Get URL from command line or use default
    site_url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com/shop"
    
    try:
        # Scrape the website
        products = scrape_website(site_url)
        
        # Display results
        print("\n📦 Products found:")
        for i, product in enumerate(products[:5], 1):
            print(f"\n{i}. {product['name']}")
            print(f"   Price: {product['price']}")
            print(f"   URL: {product['url']}")
            desc = product.get('description', '')
            print(f"   Description: {desc[:100]}...")
        
        if len(products) > 5:
            print(f"\n... and {len(products) - 5} more products")
        
        # Save to database (optional)
        # save_products_to_database(products)
        
        print("\n✅ Scraping complete!")
        return products
        
    except Exception as e:
        print(f"\n❌ Scraping failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()





