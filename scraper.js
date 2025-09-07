import fetch from 'node-fetch';
import { JSDOM } from 'jsdom';
import 'console.table';

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

// 2) The extractor adapted for Node.js
async function extractInterestedCountFromURL(url) {
  // Add delay to avoid rate limiting
  await new Promise(resolve => setTimeout(resolve, Math.random() * 2000 + 1000));
  
  const resp = await fetch(url, {
    headers: {
      'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
      'Accept-Language': 'en-US,en;q=0.9',
      'Accept-Encoding': 'gzip, deflate, br',
      'DNT': '1',
      'Connection': 'keep-alive',
      'Upgrade-Insecure-Requests': '1',
    }
  });
  
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  
  const html = await resp.text();
  const dom = new JSDOM(html);
  const doc = dom.window.document;

  const possibleEls = doc.querySelectorAll('section, div, span, p, h1, h2, h3, h4, h5, h6');
  for (const el of possibleEls) {
    const txt = el.textContent || '';
    if (txt.includes('interested') && /(\d+\.?\d*)K/.test(txt)) {
      const m = txt.match(/(\d+\.?\d*)K/);
      return m ? m[1] + 'K' : null;
    }
  }
  return null;
}

// 3) Run them all and print a table
async function main() {
  console.log('🎬 Fetching BookMyShow movie interest data...\n');
  
  const rows = [];
  
  // Process URLs sequentially to avoid overwhelming the server
  for (let i = 0; i < urls.length; i++) {
    const url = urls[i];
    let count = null;
    
    try {
      console.log(`Processing ${i + 1}/${urls.length}: ${url.split('/').slice(-2, -1)[0]}`);
      count = await extractInterestedCountFromURL(url);
    } catch (e) {
      console.warn(`Failed ${url}:`, e.message);
    }
    
    // slug → human title
    const slug = url.split('/').slice(-2, -1)[0];
    const title = slug
      .split('-')
      .map(w => w.charAt(0).toUpperCase() + w.slice(1))
      .join(' ');
    
    rows.push({ Movie: title, Interests: count || '—' });
  }

  console.log('\n📊 Results:');
  console.table(rows);
}

// Run the script
main().catch(console.error);
