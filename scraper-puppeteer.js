import puppeteer from 'puppeteer';
import 'console.table';
import fs from 'fs';
import path from 'path';

// 1) Your URL list
const urls = [
  "https://in.bookmyshow.com/movies/hyderabad/war-2/ET00356501",
  "https://in.bookmyshow.com/movies/hyderabad/coolie/ET00395817",
  "https://in.bookmyshow.com/movies/hyderabad/avatar-fire-and-ash/ET00407893",
  "https://in.bookmyshow.com/movies/hyderabad/kantara-a-legend-chapter1/ET00377351",
  "https://in.bookmyshow.com/movies/hyderabad/the-raja-saab/ET00383697",
  "https://in.bookmyshow.com/movies/hyderabad/they-call-him-og/ET00369074",
  "https://in.bookmyshow.com/movies/hyderabad/baahubali-the-epic/ET00453094",
  "https://in.bookmyshow.com/movies/hyderabad/toxic-a-fairy-tale-for-grownups/ET00378770",
  "https://in.bookmyshow.com/movies/hyderabad/coolie-the-powerhouse/ET00452035",
  "https://in.bookmyshow.com/movies/hyderabad/param-sundari/ET00426409",
  "https://in.bookmyshow.com/movies/hyderabad/mirai/ET00395402",
  "https://in.bookmyshow.com/movies/hyderabad/baaghi-4/ET00420244",
  "https://in.bookmyshow.com/movies/hyderabad/peddi/ET00439772",
  "https://in.bookmyshow.com/movies/hyderabad/bhooth-bangla/ET00411383",
  "https://in.bookmyshow.com/movies/hyderabad/akhanda-2/ET00416621",
  "https://in.bookmyshow.com/movies/hyderabad/the-paradise/ET00436621",
  "https://in.bookmyshow.com/movies/hyderabad/border-2/ET00401449",
  "https://in.bookmyshow.com/movies/hyderabad/jolly-llb-3/ET00450799",
  "https://in.bookmyshow.com/movies/hyderabad/spirit/ET00452121",
  "https://in.bookmyshow.com/movies/hyderabad/ramayana/ET00451914",
  "https://in.bookmyshow.com/movies/hyderabad/rajini-the-jailer-2/ET00429211",
  "https://in.bookmyshow.com/movies/hyderabad/king/ET00455480"
];

// 2) The extractor using Puppeteer (optimized for speed)
async function extractInterestedCountFromURL(page, url) {
  try {
    await page.goto(url, { 
      waitUntil: 'domcontentloaded', // Faster than networkidle2
      timeout: 15000 // Reduced timeout
    });
    
    // Quick Cloudflare check
    const title = await page.title();
    if (title.includes('Cloudflare') || title.includes('Attention Required')) {
      console.log(`⚠️  Cloudflare detected for ${url.split('/').slice(-2, -1)[0]}`);
      throw new Error('Cloudflare challenge');
    }
    
    // Reduced wait time for dynamic content
    await new Promise(resolve => setTimeout(resolve, 1000));
    
    // Simplified extraction (removed debug overhead)
    const interestedCount = await page.evaluate(() => {
      const elements = document.querySelectorAll('section, div, span, p, h1, h2, h3, h4, h5, h6');
      
      for (const el of elements) {
        const text = el.textContent || '';
        
        // Primary pattern: "interested" with K numbers
        if (text.includes('interested') && /(\d+\.?\d*)K/.test(text)) {
          const match = text.match(/(\d+\.?\d*)K/);
          return match ? match[1] + 'K' : null;
        }
      }
      
      // Secondary pattern: "interested" with regular numbers
      for (const el of elements) {
        const text = el.textContent || '';
        if (text.toLowerCase().includes('interested') && /(\d+[\d,]*)/.test(text)) {
          const match = text.match(/(\d+[\d,]*)/);
          if (match) {
            const num = parseInt(match[1].replace(/,/g, ''));
            if (num > 1000) {
              return Math.round(num / 1000 * 10) / 10 + 'K';
            }
            return match[1];
          }
        }
      }
      
      return null;
    });
    
    return interestedCount;
  } catch (error) {
    throw new Error(`Failed to process ${url}: ${error.message}`);
  }
}

// 3) Main function
async function main() {
  console.log('🎬 Starting Puppeteer browser...\n');
  
  const browser = await puppeteer.launch({
    headless: true,
    args: [
      '--no-sandbox', 
      '--disable-setuid-sandbox',
      '--disable-blink-features=AutomationControlled',
      '--disable-features=VizDisplayCompositor',
      '--disable-dev-shm-usage',
      '--disable-web-security',
      '--disable-features=site-per-process'
    ]
  });

  console.log('🎬 Fetching BookMyShow movie interest data...\n');
  
  // Process URLs in parallel batches (3 at a time to avoid overwhelming server)
  const BATCH_SIZE = 3;
  const results = [];
  
  for (let i = 0; i < urls.length; i += BATCH_SIZE) {
    const batch = urls.slice(i, i + BATCH_SIZE);
    console.log(`\n📦 Processing batch ${Math.floor(i/BATCH_SIZE) + 1}/${Math.ceil(urls.length/BATCH_SIZE)} (${batch.length} URLs)`);
    
    const batchPromises = batch.map(async (url, batchIndex) => {
      const page = await browser.newPage();
      
      // Set realistic browser properties
      await page.setUserAgent('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');
      
      // Remove automation indicators
      await page.evaluateOnNewDocument(() => {
        Object.defineProperty(navigator, 'webdriver', {
          get: () => undefined,
        });
      });
      
      // Set viewport and headers
      await page.setViewport({ width: 1366, height: 768 });
      await page.setExtraHTTPHeaders({
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
      });
      
      let count = null;
      const globalIndex = i + batchIndex;
      
      try {
        console.log(`  ${globalIndex + 1}/${urls.length}: ${url.split('/').slice(-2, -1)[0]}`);
        count = await extractInterestedCountFromURL(page, url);
      } catch (e) {
        console.warn(`  ❌ Failed ${url.split('/').slice(-2, -1)[0]}: ${e.message}`);
      }
      
      await page.close();
      
      // slug → human title
      const slug = url.split('/').slice(-2, -1)[0];
      const title = slug
        .split('-')
        .map(w => w.charAt(0).toUpperCase() + w.slice(1))
        .join(' ');
      
      return { Movie: title, Interests: count || '—' };
    });
    
    const batchResults = await Promise.all(batchPromises);
    results.push(...batchResults);
    
    // Small delay between batches
    if (i + BATCH_SIZE < urls.length) {
      await new Promise(resolve => setTimeout(resolve, 2000));
    }
  }

  await browser.close();
  
  // Save results to JSON log file
  const finalResults = {
    timestamp: new Date().toISOString(),
    date: new Date().toISOString().split('T')[0], // YYYY-MM-DD
    totalMovies: results.length,
    successCount: results.filter(r => r.Interests !== '—').length,
    data: results
  };
  
  const logFile = path.join(process.cwd(), 'bms-interest-log.json');
  
  try {
    let logData = [];
    
    // Read existing log if it exists
    if (fs.existsSync(logFile)) {
      const existingData = fs.readFileSync(logFile, 'utf8');
      logData = JSON.parse(existingData);
    }
    
    // Append new results
    logData.push(finalResults);
    
    // Write back to file
    fs.writeFileSync(logFile, JSON.stringify(logData, null, 2));
    
    console.log(`\n💾 Results saved to: ${logFile}`);
    console.log(`📊 Run summary: ${finalResults.successCount}/${finalResults.totalMovies} movies with data`);
  } catch (error) {
    console.error('Failed to save results:', error);
  }
  
  console.log('\n📊 Results:');
  console.table(results);
}

// Run the script
main().catch(console.error);
