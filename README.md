# BookMyShow Movie Interest Scraper 🎬

Automated scraper that tracks movie interest counts from BookMyShow Hyderabad daily.

## 🚀 Features

- **Daily automated runs** via GitHub Actions at 6:00 AM IST
- **Fast parallel processing** (3 URLs at once)
- **JSON logging** with timestamps
- **84% success rate** (16/19 movies typically)
- **Cloudflare bypass** with realistic browser simulation

## 📊 Current Data

The scraper tracks interest counts for 19 popular movies in Hyderabad. Results are saved to `bms-interest-log.json` with timestamps.

### Top Movies by Interest:
- **War 2**: 535K+ interested
- **Coolie**: 470K+ interested  
- **Demon Slayer**: 294K+ interested
- **Kantara Chapter 1**: 224K+ interested

## 🛠 Setup

### Local Development
```bash
npm install
npm run browser
```

### GitHub Actions (Automated)
- Runs automatically daily at 6:00 AM IST
- Commits results back to repository
- No manual intervention required

## 📁 File Structure

```
├── scraper-puppeteer.js    # Main scraper with Puppeteer
├── scraper.js              # Basic fetch version (legacy)
├── package.json            # Dependencies
├── bms-interest-log.json   # Historical data
└── .github/workflows/      # GitHub Actions configuration
    └── scraper.yml
```

## 🕒 Schedule

- **Frequency**: Daily
- **Time**: 6:00 AM IST (12:30 AM UTC)
- **Duration**: ~2-3 minutes per run
- **Auto-commit**: Results pushed to repo automatically

## 📈 Data Format

```json
{
  "timestamp": "2025-08-07T04:24:53.935Z",
  "date": "2025-08-07", 
  "totalMovies": 19,
  "successCount": 16,
  "data": [
    {
      "Movie": "War 2",
      "Interests": "535K"
    }
  ]
}
```

## 🔧 Technical Details

- **Engine**: Node.js + Puppeteer
- **Browser**: Headless Chrome
- **Batch processing**: 3 concurrent requests
- **Rate limiting**: 2s delay between batches
- **Error handling**: Graceful failure recovery
- **Anti-detection**: Realistic headers and behavior

---

*Last updated: August 2025*
