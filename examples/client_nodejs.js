/**
 * Node.js Example - Calling the Reko Item Scraper
 * 
 * Installation: npm install axios
 * Usage: node client_nodejs.js
 */

const axios = require('axios');

// Configuration: Switch between local and production
const SCRAPER_URL = process.env.NODE_ENV === 'production'
  ? 'https://your-cloud-run-url.run.app'  // Replace with your Cloud Run URL
  : 'http://localhost:8080';

/**
 * Scrape a website and return product data
 * @param {string} url - The URL of the e-commerce site to scrape
 * @param {Object} options - Optional configuration
 * @param {string} options.apiBaseUrl - API base URL for progress updates
 * @param {string} options.agentToken - Agent token for progress updates
 * @returns {Promise<Array>} - Array of product objects
 */
async function scrapeWebsite(url, options = {}) {
  try {
    console.log(`🔍 Scraping: ${url}`);
    console.log(`📡 Using scraper at: ${SCRAPER_URL}`);
    
    const payload = { URL: url };
    
    // Add progress update parameters if provided
    if (options.apiBaseUrl && options.agentToken) {
      payload.apiBaseUrl = options.apiBaseUrl;
      payload.agentToken = options.agentToken;
      console.log(`📊 Progress updates enabled`);
    }
    
    const response = await axios.post(SCRAPER_URL, payload, {
      // Scraping can take 2-5 minutes for large sites
      timeout: 300000,  // 5 minutes
      headers: {
        'Content-Type': 'application/json'
      }
    });
    
    if (response.data.status === 'ok') {
      const products = response.data.result;
      console.log(`✅ Found ${products.length} products`);
      return products;
    } else {
      throw new Error('Scraper returned non-ok status');
    }
  } catch (error) {
    if (error.code === 'ECONNREFUSED') {
      console.error('❌ Error: Cannot connect to scraper service.');
      console.error('   Make sure the scraper is running:');
      console.error('   docker-compose up -d');
    } else if (error.code === 'ECONNABORTED') {
      console.error('❌ Error: Scraper timeout. The site may be too large or slow to scrape.');
    } else {
      console.error('❌ Scraper error:', error.message);
    }
    throw error;
  }
}

/**
 * Save products to your database (example)
 * Replace this with your actual database logic
 */
async function saveProductsToDatabase(products) {
  // Example: Save to MongoDB, PostgreSQL, etc.
  for (const product of products) {
    console.log(`💾 Saving: ${product.name}`);
    // await db.products.insert(product);
  }
}

// Main execution
async function main() {
  // Example URL - replace with the actual site you want to scrape
  const siteUrl = process.argv[2] || 'https://example.com/shop';
  
  try {
    // Scrape the website
    // Optional: Add progress updates for auto-onboarding
    const products = await scrapeWebsite(siteUrl, {
      // apiBaseUrl: 'https://staging.rekohub.com/api',
      // agentToken: 'your-agent-token-from-start-endpoint'
    });
    
    // Display results
    console.log('\n📦 Products found:');
    products.slice(0, 5).forEach((product, index) => {
      console.log(`\n${index + 1}. ${product.name}`);
      console.log(`   Price: ${product.price}`);
      console.log(`   URL: ${product.url}`);
      console.log(`   Description: ${product.description.substring(0, 100)}...`);
    });
    
    if (products.length > 5) {
      console.log(`\n... and ${products.length - 5} more products`);
    }
    
    // Save to database (optional)
    // await saveProductsToDatabase(products);
    
    console.log('\n✅ Scraping complete!');
  } catch (error) {
    console.error('\n❌ Scraping failed:', error.message);
    process.exit(1);
  }
}

// Run the script
if (require.main === module) {
  main();
}

// Export for use in other modules
module.exports = { scrapeWebsite };

