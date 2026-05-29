# Working .onion URLs for Testing

## ⚠️ Important Notes
- These URLs may change or become unavailable
- Always verify URLs are legitimate before crawling
- Some sites may be slow to respond (this is normal for .onion)
- For authorized security research only

## 🔍 Legitimate .onion Sites (December 2025)

### Search Engines
```
http://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion
```
DuckDuckGo - Privacy-focused search engine

### News & Media
```
http://p53lf57qovyuvwsc6xnrppyply3vtqm7l6pcobkmyqsiofyeznfu5uqd.onion
```
ProPublica - Investigative journalism

```
http://bbcnewsd73hkzno2ini43t4gblxvycyac5aw4gnv7t2rccijh7745uqd.onion
```
BBC News - International news

```
http://www.nytimesn7cgmftshazwhfgzm37qxb44r64ytbb2dj3x62d2lljsciiyd.onion
```
New York Times - News and articles

### Directories
```
http://zqktlwiuavvvqqt4ybvgvi7tyo4hjl5xgfuvpdf6otjiycgwqbym2qad.onion
```
The Hidden Wiki - Directory of .onion sites

### Privacy Tools
```
http://thehiddenwiki.com/index.php/Main_Page
```
Various privacy-focused resources

## 📝 How to Use These URLs

### Option 1: In API Request
```bash
curl -X POST http://localhost:8337/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "term": "privacy",
    "start_url": "http://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion",
    "max_results": 5
  }'
```

### Option 2: In config.yaml
```yaml
crawling:
  seed_urls:
    - "http://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion"
    - "http://p53lf57qovyuvwsc6xnrppyply3vtqm7l6pcobkmyqsiofyeznfu5uqd.onion"
```

Then call API without start_url:
```bash
curl -X POST http://localhost:8337/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "term": "privacy",
    "max_results": 5
  }'
```

## 🔧 Verifying URLs

To test if a URL is accessible:
```bash
# Start API
docker-compose up

# Check health
curl http://localhost:8337/api/v1/health

# Test search with a known URL
curl -X POST http://localhost:8337/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "term": "search",
    "start_url": "http://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion",
    "max_depth": 1,
    "max_pages": 5
  }'
```

## ⏰ Expected Response Times
- Initial Tor connection: 10-30 seconds
- Per page crawl: 10-20 seconds
- Complete search (5-10 pages): 2-5 minutes

This is normal for .onion sites due to Tor's multi-hop routing.

## 🚨 Legal Reminder
These are **legitimate, public-facing .onion sites**. Always ensure you have proper authorization before crawling any sites.
