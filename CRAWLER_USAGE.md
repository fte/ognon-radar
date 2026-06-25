# Running Dark Crawler in Docker

## 🚀 Quick Start

### 1. Build and start Tor service
```bash
docker-compose up -d tor
```

### 2. Run Dark Crawler

**Basic crawl with JSON output:**
```bash
docker-compose --profile crawler run --rm crawler \
  python dark_crawler.py \
  -u http://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion \
  --json -o /app/output
```

**Search for specific term:**
```bash
docker-compose --profile crawler run --rm crawler \
  python dark_crawler.py \
  -u http://zqktlwiuavvvqqt4ybvgvi7tyo4hjl5xgfuvpdf6otjiycgwqbym2qad.onion \
  -d 2 -p 20 --all -o /app/output
```

**With image capture:**
```bash
docker-compose --profile crawler run --rm crawler \
  python dark_crawler.py \
  -u http://xxx.onion \
  --images --max-images 5 --json -o /app/output
```

**Batch crawl from file:**
```bash
# Create a file with .onion URLs (one per line)
cat > onion_urls.txt << EOF
http://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion
http://p53lf57qovyuvwsc6xnrppyply3vtqm7l6pcobkmyqsiofyeznfu5uqd.onion
EOF

docker-compose --profile crawler run --rm crawler \
  python dark_crawler.py \
  -f /app/onion_urls.txt --json -o /app/output
```

## 📋 Command Options

```
-h, --help              Show help message
-u URL, --url URL       Single .onion URL to crawl
-f FILE, --file FILE    File with .onion URLs (one per line)
-d DEPTH                Maximum crawl depth (default: 3)
-p PAGES                Maximum pages per site (default: 50)
-o OUTPUT               Output directory (default: current directory)
--images                Download images from pages
--images-only           Download ONLY images, no text analysis
--image-extensions      Image types to download (default: jpg,jpeg,png,gif,bmp,webp)
--max-images            Max images per page (default: 10)
--no-tor-check          Skip Tor connection test
--json                  Output JSON format
--csv                   Output CSV format
--all                   Generate all formats (JSON, CSV, summary)
```

## 📁 Output Files

Results are saved in `crawler_output/` directory:

```
crawler_output/
├── darkweb_crawl_results.json       # JSON results
├── darkweb_crawl_results.csv        # CSV results
├── darkweb_analysis_summary.txt     # Text summary
└── images/                          # Downloaded images (if --images)
    ├── image_20251213_101520_abc123.jpg
    └── ...
```

## 🔍 View Results

```bash
# View JSON results
cat crawler_output/darkweb_crawl_results.json | jq

# View CSV in readable format
column -t -s',' crawler_output/darkweb_crawl_results.csv | less -S

# View summary
cat crawler_output/darkweb_analysis_summary.txt
```

## 🐳 Docker Commands Reference

**Start Tor only:**
```bash
docker-compose up -d tor
```

**Check Tor is running:**
```bash
docker-compose ps
docker-compose logs tor
```

**Run crawler (one-off):**
```bash
docker-compose --profile crawler run --rm crawler python dark_crawler.py -u <URL> --json
```

**Clean up:**
```bash
docker-compose down
```

**Rebuild after dependencies change:**
```bash
docker-compose build
```

## ⚙️ Environment Variables

The script automatically detects Docker environment:
- In Docker: Uses `tor:9050` (service name)
- Locally: Uses `127.0.0.1:9050`

> **Note:** `dark_crawler.py` imports from `core/` which requires a `config.yaml` file in the
> working directory at import time. When running outside Docker, copy `config.yaml` to your
> working directory and set `tor.proxy` to `socks5h://127.0.0.1:9050` before running.

Override with environment variable:
```bash
docker-compose --profile crawler run --rm \
  -e TOR_PROXY=socks5h://tor:9050 \
  crawler python dark_crawler.py -u <URL>
```

## 🔄 Comparison: API vs CLI

| Feature | FastAPI (api service) | Dark Crawler (crawler service) |
|---------|----------------------|-------------------------------|
| Interface | REST API | Command-line |
| Usage | `curl` / HTTP clients | Docker run command |
| Output | JSON via HTTP | Files (JSON/CSV/TXT) |
| Start URL | Required in request | Required as argument |
| Best for | Integration, automation | One-off analysis, reports |

**Use API for:**
- Building web applications
- API integrations
- Real-time queries

**Use CLI for:**
- Detailed threat analysis
- Report generation
- Batch processing

## 📝 Examples

### Example 1: Quick test with DuckDuckGo
```bash
docker-compose --profile crawler run --rm crawler \
  python dark_crawler.py \
  -u http://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion \
  -d 1 -p 5 --json -o /app/output
```

### Example 2: Deep crawl with all outputs
```bash
docker-compose --profile crawler run --rm crawler \
  python dark_crawler.py \
  -u http://zqktlwiuavvvqqt4ybvgvi7tyo4hjl5xgfuvpdf6otjiycgwqbym2qad.onion \
  -d 3 -p 50 --all -o /app/output
```

### Example 3: Image-only capture
```bash
docker-compose --profile crawler run --rm crawler \
  python dark_crawler.py \
  -u http://xxx.onion \
  --images-only --image-extensions jpg,png --max-images 20 -o /app/output
```

## 🚨 Important Notes

- Tor must be running (`docker-compose up -d tor`)
- First run may be slow while Tor establishes circuits
- .onion sites are inherently slow (10-30s per page)
- Results saved in `crawler_output/` directory
- Use `--rm` flag to remove container after run
- Use `-d` to limit depth for faster results

## 🔧 Troubleshooting

**Tor connection fails:**
```bash
# Check Tor is running
docker-compose ps tor

# View Tor logs
docker-compose logs tor

# Restart Tor
docker-compose restart tor
```

**Output directory not found:**
```bash
# Create output directory
mkdir -p crawler_output
```

**NLTK download errors:**
The script auto-downloads required NLTK data on first run. This is normal and only happens once.
