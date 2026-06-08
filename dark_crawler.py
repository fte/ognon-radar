#!/usr/bin/env python3
from bs4 import BeautifulSoup
from stem import Signal
from stem.control import Controller
from urllib.parse import urljoin, urlparse
from core.constants import BLACKLIST_PATHS, ONION_URL_REGEX
from core.crawler import extract_onion_links, is_valid_onion_url
from core.tor_client import TorClient
import time
import json
import csv
import re
import logging
import sys
import os
import argparse
import hashlib
from datetime import datetime
import nltk
from nltk.corpus import stopwords
from nltk.tokenize import word_tokenize
from collections import Counter
from PIL import Image
import io

# Download NLTK data if not already present
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')

try:
    nltk.data.find('corpora/stopwords')
except LookupError:
    nltk.download('stopwords')

# ANSI color codes for colored output
class Colors:
    RED = '\033[1;31m'        # Bold Red
    NEON_GREEN = '\033[1;92m' # Neon Green
    YELLOW = '\033[1;33m'     # Yellow
    BLUE = '\033[1;34m'       # Blue
    PURPLE = '\033[1;35m'     # Purple
    CYAN = '\033[1;36m'       # Cyan
    RESET = '\033[0m'         # Reset color

# Tool banner and descriptions with colors
def print_banner():
    banner = f"""{Colors.RED}
███████                       ░██            ░██████                                        ░██                     
░██   ░██                      ░██           ░██   ░██                                       ░██                     
░██    ░██  ░██████   ░██░████ ░██    ░██   ░██        ░██░████  ░██████   ░██    ░██    ░██ ░██  ░███████  ░██░████ 
░██    ░██       ░██  ░███     ░██   ░██    ░██        ░███           ░██  ░██    ░██    ░██ ░██ ░██    ░██ ░███     
░██    ░██  ░███████  ░██      ░███████     ░██        ░██       ░███████   ░██  ░████  ░██  ░██ ░█████████ ░██      
░██   ░██  ░██   ░██  ░██      ░██   ░██     ░██   ░██ ░██      ░██   ░██    ░██░██ ░██░██   ░██ ░██        ░██      
░███████    ░█████░██ ░██      ░██    ░██     ░██████  ░██       ░█████░██    ░███   ░███    ░██  ░███████  ░██      
{Colors.RESET}"""
    
    creator = f"{Colors.YELLOW}Creator: Cyber Threat Intelligence Team{Colors.RESET}"
    version = f"{Colors.PURPLE}Version: 2.4 (Enhanced with Image Capture & Market Analysis){Colors.RESET}"
    print(banner)
    print()
    print(creator)
    print(version)
    print()


def print_help():
    """Display help information"""
    help_text = f"""
{Colors.CYAN}DarkCrawler Elite - Dark Web Intelligence Tool{Colors.RESET}
{Colors.YELLOW}================================================{Colors.RESET}

{Colors.NEON_GREEN}USAGE:{Colors.RESET}
    python {sys.argv[0]} [OPTIONS]

{Colors.NEON_GREEN}OPTIONS:{Colors.RESET}
    -h, --help           Show this help message
    -u URL, --url URL    Single .onion URL to crawl
    -f FILE, --file FILE File containing list of .onion URLs (one per line)
    -d DEPTH, --depth DEPTH
                        Maximum crawl depth (default: 3)
    -p PAGES, --pages PAGES
                        Maximum pages per site (default: 50)
    -o OUTPUT, --output OUTPUT
                        Output directory for reports (default: current directory)
    --images             Download images from crawled pages
    --images-only        Download ONLY images, no text/content analysis
    --image-extensions EXT1,EXT2,...
                        Image extensions to download (default: jpg,jpeg,png,gif,bmp,webp)
    --max-images PER_PAGE
                        Maximum images to download per page (default: 10)
    --no-tor-check       Skip Tor connection test
    --json               Output only JSON format
    --csv                Output only CSV format
    --all                Generate all report formats (JSON, CSV)

{Colors.NEON_GREEN}EXAMPLES:{Colors.RESET}
    1. Crawl with image capture:
       python {sys.argv[0]} -u http://marketplace.onion --images

    2. Download only images from marketplace:
       python {sys.argv[0]} -u http://marketplace.onion --images-only

    3. Custom image extensions and limits:
       python {sys.argv[0]} -u http://marketplace.onion --images --image-extensions jpg,png --max-images 5

    4. Comprehensive analysis with images:
       python {sys.argv[0]} -u http://marketplace.onion --images --all -o ./reports/

{Colors.NEON_GREEN}OUTPUT FORMATS:{Colors.RESET}
    • JSON: Complete structured data with all analysis results
    • CSV: Spreadsheet-friendly format with threat analysis and marketplace data
    • Images: Downloaded to 'images/' subdirectory with metadata

{Colors.NEON_GREEN}IMPORTANT NOTES:{Colors.RESET}
    • Tor must be running on port 9050
    • Use only for authorized security research
    • Never access illegal content
    • Image downloading increases bandwidth usage
    • Large images may slow down crawling

{Colors.RED}LEGAL DISCLAIMER:{Colors.RESET}
    The user assumes full responsibility for all actions taken with this tool.
    Image downloading may constitute copyright infringement in some jurisdictions.
    Always ensure you have proper legal authorization for image capture.
    """
    print(help_text)


# Setup logging for traceability
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

# Constants
# Use 'tor' hostname when running in Docker, '127.0.0.1' for local
TOR_SOCKS_PROXY = os.getenv('TOR_PROXY', 'socks5h://tor:9050')  # Use socks5h for DNS over Tor
TOR_CONTROL_PORT = 9051
DEFAULT_CRAWL_DELAY = 7          # seconds delay between requests
DEFAULT_MAX_DEPTH = 3            # default maximum crawl depth
DEFAULT_MAX_PAGES = 50           # default max pages per site
RETRY_COUNT = 3                  # retry attempts on failure
BACKOFF_FACTOR = 4               # backoff multiplier in seconds
RENEW_CIRCUIT_EVERY = 5          # renew Tor circuit more frequently
MAX_IMAGE_SIZE_MB = 5            # Maximum image size to download in MB

# Default image extensions - will be updated from command line
DEFAULT_IMAGE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp']
image_hash_cache = set()

# Keywords for dangerous content detection with severity levels
DANGEROUS_KEYWORDS = {
    'high': [
        'assassination', 'terrorism', 'child abuse', 'child pornography', 'human trafficking',
        'weapons for sale', 'firearms for sale', 'explosives for sale', 'hitman', 'contract killing',
        'drug trafficking', 'hack for hire', 'ddos for hire', 'blackmail', 'extortion', 'ransomware',
        'credit card fraud', 'identity theft', 'bank credentials', 'stolen data', 'data breach'
    ],
    'medium': [
        'drugs for sale', 'weed for sale', 'cocaine', 'heroin', 'meth', 'firearms', 'weapons',
        'hacking tools', 'malware', 'ransomware', 'phishing', 'scam', 'fake documents', 'fake id',
        'stolen credit cards', 'carding', 'account takeover', 'doxing', 'doxxing'
    ],
    'low': [
        'counterfeit', 'fake money', 'pirated software', 'cracked accounts', 'netflix account',
        'premium accounts', 'bypass security', 'hacking tutorial', 'carding tutorial'
    ]
}

# Marketplace goods categories for analysis
MARKETPLACE_CATEGORIES = {
    'drugs': ['cocaine', 'heroin', 'meth', 'weed', 'marijuana', 'lsd', 'ecstasy', 'mdma', 'opioids', 'fentanyl'],
    'weapons': ['firearm', 'gun', 'rifle', 'pistol', 'ammunition', 'explosives', 'grenade', 'silencer'],
    'digital_goods': ['credit cards', 'accounts', 'credentials', 'database', 'malware', 'ransomware', 'botnet'],
    'fraud': ['fake id', 'counterfeit', 'passport', 'driver license', 'documents', 'scam'],
    'services': ['hacking', 'ddos', 'phishing', 'carding', 'doxing', 'hitman', 'assassination']
}


def renew_tor_identity(password):
    """Signal Tor to get new identity (new circuit)"""
    try:
        with Controller.from_port(port=TOR_CONTROL_PORT) as controller:
            controller.authenticate(password=password)
            controller.signal(Signal.NEWNYM)
            logging.info("Tor circuit renewed for anonymity")
            time.sleep(5)  # Wait for new circuit establishment
    except Exception as e:
        logging.error(f"Failed to renew Tor identity: {e}")


_tor = TorClient(proxy_url=TOR_SOCKS_PROXY)



def download_image(session, image_url, output_dir, page_url, image_extensions, max_size_mb=MAX_IMAGE_SIZE_MB):
    """Download an image and save it with metadata"""
    try:
        # Check if URL looks like an image
        parsed = urlparse(image_url)
        if not any(parsed.path.lower().endswith(ext) for ext in image_extensions):
            return None
        
        # Download image
        response = session.get(image_url, timeout=15, stream=True)
        response.raise_for_status()
        
        # Check content type
        content_type = response.headers.get('content-type', '').lower()
        if not any(img_type in content_type for img_type in ['image/jpeg', 'image/png', 'image/gif', 'image/bmp', 'image/webp']):
            logging.debug(f"Not an image content type: {content_type}")
            return None
        
        chunks = []
        total = 0
        size_limit = max_size_mb * 1024 * 1024
        for chunk in response.iter_content(chunk_size=8192):
            chunks.append(chunk)
            total += len(chunk)
            if total > size_limit:
                logging.warning(f"Image too large, skipping: {image_url}")
                return None
        image_data = b''.join(chunks)
        
        # Calculate hash for deduplication
        image_hash = hashlib.sha256(image_data).hexdigest()
        global image_hash_cache
        if image_hash in image_hash_cache:
            logging.debug(f"Duplicate image skipped (hash: {image_hash[:8]})")
            return None
        image_hash_cache.add(image_hash)
        
        # Open image to get metadata
        img = Image.open(io.BytesIO(image_data))
        width, height = img.size
        format = img.format or 'unknown'
        
        # Generate filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        hash_short = image_hash[:8]
        ext = parsed.path.split('.')[-1].lower() if '.' in parsed.path else format.lower()
        if ext not in image_extensions:
            ext = 'jpg'  # Default extension
        
        filename = f"image_{timestamp}_{hash_short}.{ext}"
        filepath = os.path.join(output_dir, filename)
        
        # Save image
        with open(filepath, 'wb') as f:
            f.write(image_data)
        
        # Get file size
        size_kb = len(image_data) / 1024
        
        logging.info(f"Downloaded image: {filename} ({size_kb:.1f} KB, {width}x{height})")
        
        return {
            'filename': filename,
            'local_path': filepath,
            'image_url': image_url,
            'source_url': page_url,
            'size_kb': size_kb,
            'width': width,
            'height': height,
            'format': format,
            'hash': image_hash
        }
        
    except Exception as e:
        logging.debug(f"Failed to download image {image_url}: {e}")
        return None


def extract_and_download_images(session, url, soup, output_dir, image_extensions, max_images_per_page=10):
    """Extract and download images from a page"""
    images_info = []
    image_count = 0
    
    images_dir = os.path.join(output_dir, 'images')
    os.makedirs(images_dir, exist_ok=True)
    
    # Find all image tags
    for img_tag in soup.find_all('img'):
        if image_count >= max_images_per_page:
            break
            
        img_src = img_tag.get('src')
        if not img_src:
            continue
        
        # Resolve relative URLs
        img_url = urljoin(url, img_src)
        
        # Download image
        img_info = download_image(session, img_url, images_dir, url, image_extensions)
        if img_info:
            images_info.append(img_info)
            image_count += 1
    
    # Also check for images in links (common in marketplaces)
    for a_tag in soup.find_all('a', href=True):
        if image_count >= max_images_per_page:
            break
            
        href = a_tag['href']
        # Check if link might be an image
        parsed = urlparse(href)
        path_lower = parsed.path.lower()
        if any(path_lower.endswith(ext) for ext in image_extensions):
            img_url = urljoin(url, href)
            img_info = download_image(session, img_url, images_dir, url, image_extensions)
            if img_info:
                images_info.append(img_info)
                image_count += 1
    
    return images_info




def analyze_content(text, url):
    """Analyze content for dangerous keywords and return threat assessment"""
    threats = {'high': [], 'medium': [], 'low': []}
    text_lower = text.lower()
    
    for severity, keywords in DANGEROUS_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in text_lower:
                threats[severity].append(keyword)
    
    # Additional analysis for specific patterns
    # Email addresses
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    emails = re.findall(email_pattern, text)
    if emails and any(word in text_lower for word in ['sell', 'buy', 'leak', 'dump', 'database']):
        threats['medium'].append('Potential email database leak')
    
    # Phone numbers
    phone_pattern = r'(\+\d{1,3}[-\.\s]??|\d{1,4}[-\.\s]??)?(\(\d{1,4}\)[-\.\s]??)?\d{1,4}[-\.\s]??\d{1,4}[-\.\s]??\d{1,9}'
    phones = re.findall(phone_pattern, text)
    if phones and any(word in text_lower for word in ['sell', 'buy', 'leak', 'database']):
        threats['medium'].append('Potential phone number database leak')
    
    # Credit card patterns
    cc_pattern = r'\b(?:\d{4}[- ]?){3}\d{4}\b'
    ccs = re.findall(cc_pattern, text)
    if ccs and any(word in text_lower for word in ['sell', 'buy', 'credit', 'card']):
        threats['high'].append('Potential credit card data')
    
    # Cryptocurrency addresses
    btc_pattern = r'\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b'
    btc_addresses = re.findall(btc_pattern, text)
    if btc_addresses and any(word in text_lower for word in ['payment', 'send', 'bitcoin', 'donate']):
        threats['medium'].append('Cryptocurrency payment request')
    
    return threats


def analyze_marketplace_goods(text):
    """Analyze text for marketplace goods and return categorized counts"""
    goods = {category: Counter() for category in MARKETPLACE_CATEGORIES.keys()}
    text_lower = text.lower()
    
    for category, keywords in MARKETPLACE_CATEGORIES.items():
        for keyword in keywords:
            # Count occurrences of each keyword
            count = text_lower.count(keyword.lower())
            if count > 0:
                goods[category][keyword] += count
    
    return goods


def scrape_onion_url(url, session, download_images=False, images_output_dir=None, image_extensions=None, max_images_per_page=10):
    """Scrape a single .onion URL, return title, text, soup, and images"""
    try:
        parsed_path = urlparse(url).path
        if any(parsed_path.startswith(path) for path in BLACKLIST_PATHS):
            logging.info(f"Skipping blacklisted URL path: {url}")
            return None, None, None, []

        logging.info(f"Fetching URL: {url}")
        response = _tor.get_with_retries(url)
        soup = BeautifulSoup(response.text, 'lxml')
        title = soup.title.string.strip() if soup.title and soup.title.string else 'No Title Found'
        
        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()
            
        text = soup.get_text(separator='\n', strip=True)
        
        # Extract images if requested
        images_info = []
        if download_images and images_output_dir and image_extensions:
            images_info = extract_and_download_images(session, url, soup, images_output_dir, image_extensions, max_images_per_page)
            if images_info:
                logging.info(f"Downloaded {len(images_info)} image(s) from {url}")
        
        return title, text, soup, images_info
    except Exception as e:
        logging.error(f"Failed to scrape {url}: {e}")
        return None, None, None, []


def crawl_site(start_url, session, max_depth, max_pages, output_dir, 
               tor_password=None, crawl_delay=DEFAULT_CRAWL_DELAY,
               download_images=False, image_extensions=None, max_images_per_page=10, images_only=False):
    """Breadth-first crawl on .onion site up to max_depth and max_pages"""
    from collections import deque
    crawled = set()
    to_crawl = deque([(start_url, 0)])
    results = []
    threat_findings = []
    all_images_info = []

    while to_crawl and len(crawled) < max_pages:
        current_url, depth = to_crawl.popleft()
        if current_url in crawled or depth > max_depth:
            continue

        title, text, soup, images_info = scrape_onion_url(
            current_url, session, download_images, output_dir, image_extensions, max_images_per_page
        )
        
        if images_only:
            crawled.add(current_url)
            if images_info:
                all_images_info.extend(images_info)
                logging.info(f"Image-only: collected {len(images_info)} image(s) from {current_url}")
        elif title and text:
            # Analyze content for threats
            threats = analyze_content(text, current_url)
            # Analyze marketplace goods
            marketplace_goods = analyze_marketplace_goods(text)
            
            results.append({
                'url': current_url,
                'title': title,
                'text': text,
                'threats': threats,
                'marketplace_goods': marketplace_goods,
                'images_count': len(images_info),
                'image_files': [img['filename'] for img in images_info] if images_info else []
            })
            
            # Record if any threats found
            if any(threats.values()):
                threat_findings.append({
                    'url': current_url,
                    'title': title,
                    'content': text,
                    'threats': threats,
                    'marketplace_goods': marketplace_goods,
                    'images': images_info
                })
                logging.warning(f"{Colors.RED}Threat detected on {current_url}: {threats}{Colors.RESET}")

            if images_info:
                all_images_info.extend(images_info)

            crawled.add(current_url)

            if depth < max_depth and soup:
                links = extract_onion_links(current_url, soup)
                for link in links:
                    if link not in crawled:
                        to_crawl.append((link, depth + 1))

            logging.info(f"Crawled {len(crawled)} page(s) so far.")

        # Rate limit delay
        time.sleep(crawl_delay)

        # Periodically renew Tor circuit for anonymity (if control port is available)
        if tor_password and len(crawled) % RENEW_CIRCUIT_EVERY == 0 and len(crawled) > 0:
            try:
                renew_tor_identity(tor_password)
            except Exception:
                logging.warning("Cannot renew Tor circuit - control port unavailable")

    return results, threat_findings, all_images_info


def save_results_json(results, output_dir, filename="darkweb_crawl_results.json"):
    try:
        output_path = os.path.join(output_dir, filename)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=4, ensure_ascii=False)
        logging.info(f"Results saved to JSON file: {output_path}")
        return output_path
    except Exception as e:
        logging.error(f"Failed to save JSON results: {e}")
        return None


def save_results_csv(results, output_dir, filename="darkweb_crawl_results.csv"):
    try:
        output_path = os.path.join(output_dir, filename)
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['URL', 'Title', 'Content', 'Threats', 'Marketplace Goods', 'Images Count', 'Image Files'])
            for item in results:
                # Replace newlines in text with spaces for CSV readability
                clean_text = item['text'].replace('\n', ' ').replace('\r', ' ')[:500] + '...' if len(item['text']) > 500 else item['text']
                # Convert marketplace goods to string representation
                goods_str = "; ".join([f"{cat}: {', '.join([f'{k}({v})' for k, v in items.items()])}" 
                                     for cat, items in item['marketplace_goods'].items() if items])
                # Convert image files list to string
                image_files_str = "; ".join(item['image_files']) if item['image_files'] else "None"
                writer.writerow([
                    item['url'], 
                    item['title'], 
                    clean_text, 
                    str(item['threats']), 
                    goods_str,
                    item['images_count'],
                    image_files_str
                ])
        logging.info(f"Results saved to CSV file: {output_path}")
        return output_path
    except Exception as e:
        logging.error(f"Failed to save CSV results: {e}")
        return None


def generate_summary_report(all_results, all_threats, all_images_info, output_dir):
    """Generate a simple text summary report"""
    try:
        output_path = os.path.join(output_dir, 'darkweb_analysis_summary.txt')
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("=" * 70 + "\n")
            f.write("DARKWEB CRAWL ANALYSIS SUMMARY\n")
            f.write("=" * 70 + "\n\n")
            
            f.write(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            f.write("OVERALL STATISTICS:\n")
            f.write("-" * 40 + "\n")
            f.write(f"Total pages crawled: {len(all_results)}\n")
            f.write(f"Total threats detected: {len(all_threats)}\n")
            
            if all_images_info:
                f.write(f"Total images captured: {len(all_images_info)}\n")
                total_size_mb = sum(img['size_kb'] for img in all_images_info) / 1024
                f.write(f"Total image data: {total_size_mb:.2f} MB\n")
            
            # Threat breakdown
            if all_threats:
                f.write("\nTHREAT BREAKDOWN:\n")
                f.write("-" * 40 + "\n")
                high_count = sum(1 for finding in all_threats if finding['threats']['high'])
                medium_count = sum(1 for finding in all_threats if finding['threats']['medium'])
                low_count = sum(1 for finding in all_threats if finding['threats']['low'])
                
                f.write(f"High severity threats: {high_count}\n")
                f.write(f"Medium severity threats: {medium_count}\n")
                f.write(f"Low severity threats: {low_count}\n")
            
            # Marketplace analysis
            marketplace_stats = {category: 0 for category in MARKETPLACE_CATEGORIES.keys()}
            for result in all_results:
                for category, items in result['marketplace_goods'].items():
                    category_count = sum(items.values())
                    marketplace_stats[category] += category_count
            
            f.write("\nMARKETPLACE GOODS ANALYSIS:\n")
            f.write("-" * 40 + "\n")
            total_goods = sum(marketplace_stats.values())
            f.write(f"Total marketplace goods detected: {total_goods}\n")
            
            for category, count in sorted(marketplace_stats.items(), key=lambda x: x[1], reverse=True):
                if count > 0:
                    f.write(f"  {category.replace('_', ' ').title()}: {count} items\n")
            
            f.write("\nTOP THREAT FINDINGS:\n")
            f.write("-" * 40 + "\n")
            
            # Sort by threat severity
            sorted_threats = sorted(all_threats, 
                                  key=lambda x: (len(x['threats']['high']), len(x['threats']['medium']), len(x['threats']['low'])), 
                                  reverse=True)
            
            for i, finding in enumerate(sorted_threats[:5], 1):
                f.write(f"\n{i}. {finding['title'][:50]}...\n")
                f.write(f"   URL: {finding['url']}\n")
                
                threat_summary = []
                if finding['threats']['high']:
                    threat_summary.append(f"High: {len(finding['threats']['high'])}")
                if finding['threats']['medium']:
                    threat_summary.append(f"Medium: {len(finding['threats']['medium'])}")
                if finding['threats']['low']:
                    threat_summary.append(f"Low: {len(finding['threats']['low'])}")
                
                if threat_summary:
                    f.write(f"   Threats: {', '.join(threat_summary)}\n")
            
            f.write("\n" + "=" * 70 + "\n")
            f.write("END OF REPORT\n")
            f.write("=" * 70 + "\n")
        
        logging.info(f"Summary report saved to: {output_path}")
        return output_path
    except Exception as e:
        logging.error(f"Failed to generate summary report: {e}")
        return None




def read_urls_from_file(file_path):
    """Read URLs from a text file"""
    urls = []
    try:
        with open(file_path, 'r') as f:
            for line in f:
                url = line.strip()
                if url and not url.startswith('#'):
                    urls.append(url)
        return urls
    except Exception as e:
        logging.error(f"Failed to read URLs from file: {e}")
        return []


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='DarkCrawler Elite - Dark Web Intelligence Tool', add_help=False)
    parser.add_argument('-h', '--help', action='store_true', help='Show this help message')
    parser.add_argument('-u', '--url', help='Single .onion URL to crawl')
    parser.add_argument('-f', '--file', help='File containing list of .onion URLs (one per line)')
    parser.add_argument('-d', '--depth', type=int, default=DEFAULT_MAX_DEPTH, 
                       help=f'Maximum crawl depth (default: {DEFAULT_MAX_DEPTH})')
    parser.add_argument('-p', '--pages', type=int, default=DEFAULT_MAX_PAGES,
                       help=f'Maximum pages per site (default: {DEFAULT_MAX_PAGES})')
    parser.add_argument('-o', '--output', default='./', help='Output directory for reports (default: current directory)')
    parser.add_argument('--images', action='store_true', help='Download images from crawled pages')
    parser.add_argument('--images-only', action='store_true', help='Download ONLY images, no text/content analysis')
    parser.add_argument('--image-extensions', default=','.join(DEFAULT_IMAGE_EXTENSIONS),
                       help=f'Image extensions to download (default: {",".join(DEFAULT_IMAGE_EXTENSIONS)})')
    parser.add_argument('--max-images', type=int, default=10,
                       help='Maximum images to download per page (default: 10)')
    parser.add_argument('--no-tor-check', action='store_true', help='Skip Tor connection test')
    parser.add_argument('--json', action='store_true', help='Output only JSON format')
    parser.add_argument('--csv', action='store_true', help='Output only CSV format')
    parser.add_argument('--all', action='store_true', help='Generate all report formats (JSON, CSV, summary)')
    
    args = parser.parse_args()
    
    # Show help if requested
    if args.help or (not args.url and not args.file):
        print_banner()
        print_help()
        sys.exit(0)
    
    # Print banner
    print_banner()
    
    output_dir = args.output
    os.makedirs(output_dir, exist_ok=True)
    
    # Parse image extensions
    image_extensions = [ext.strip().lower() for ext in args.image_extensions.split(',')]
    
    # Check for image-only mode
    if args.images_only:
        args.images = True  # Enable image download
        logging.info(f"{Colors.YELLOW}Image-only mode enabled. Text analysis will be skipped.{Colors.RESET}")
    
    # Collect URLs
    onion_start_urls = []
    
    if args.url:
        onion_start_urls.append(args.url)
    
    if args.file:
        file_urls = read_urls_from_file(args.file)
        onion_start_urls.extend(file_urls)
    
    # Remove duplicates
    onion_start_urls = list(set(onion_start_urls))
    
    if not onion_start_urls:
        logging.error("No valid URLs provided. Use -u or -f to specify URLs.")
        sys.exit(1)
    
    # Validate URLs
    valid_urls = []
    for url in onion_start_urls:
        if is_valid_onion_url(url):
            valid_urls.append(url)
        else:
            logging.warning(f"Invalid .onion URL format, skipping: {url}")
    
    if not valid_urls:
        logging.error("No valid .onion URLs provided.")
        logging.info("Example URL format: http://3g2upl4pq6kufc4m.onion (DuckDuckGo)")
        sys.exit(1)
    
    logging.info(f"Starting crawl with {len(valid_urls)} URL(s)")
    logging.info(f"Crawl depth: {args.depth}, Max pages per site: {args.pages}")
    if args.images:
        logging.info(f"Image capture: ENABLED (max {args.max_images} per page, extensions: {', '.join(image_extensions)})")
    if args.images_only:
        logging.info(f"Image-only mode: ENABLED (text analysis disabled)")
    
    logging.info("Basic anonymity maintained through SOCKS proxy")

    session = _tor.create_session()
    all_results = []
    all_threats = []
    all_images_info = []

    # Test Tor connection unless disabled
    if not args.no_tor_check:
        _tor.test_connection()
    else:
        logging.info("Skipping Tor connection test as requested")

    # Crawl each URL
    for url in valid_urls:
        logging.info(f"Starting crawl on: {url}")
        # Pass None for password since control port isn't available
        site_data, threat_data, images_info = crawl_site(
            url, session, args.depth, args.pages, output_dir, 
            tor_password=None,
            download_images=args.images,
            image_extensions=image_extensions,
            max_images_per_page=args.max_images,
            images_only=args.images_only
        )
        all_results.extend(site_data)
        all_threats.extend(threat_data)
        all_images_info.extend(images_info)
    
    if args.images_only:
        # In images-only mode, we only care about images
        if not all_images_info:
            logging.error("No images were captured. Check if the site contains images.")
            sys.exit(1)
        
        # Create image manifest
        manifest_path = os.path.join(output_dir, 'image_manifest.json')
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(all_images_info, f, indent=4, ensure_ascii=False)
        logging.info(f"Image manifest saved to: {manifest_path}")
        
        # Create summary
        total_size_mb = sum(img['size_kb'] for img in all_images_info) / 1024
        print(f"\n{Colors.NEON_GREEN}Image Capture Complete!{Colors.RESET}")
        print(f"  • Total images captured: {len(all_images_info)}")
        print(f"  • Total image data: {total_size_mb:.2f} MB")
        print(f"  • Images saved in: {os.path.join(output_dir, 'images/')}")
        print(f"  • Manifest file: {manifest_path}")
        sys.exit(0)
    
    if not all_results:
        logging.error("No content was scraped. Check Tor connection and URLs.")
        sys.exit(1)
    
    # Determine output formats
    output_json = args.json or args.all or (not args.json and not args.csv)
    output_csv = args.csv or args.all or (not args.json and not args.csv)
    
    # Save results
    json_file = None
    csv_file = None
    summary_file = None
    
    if output_json:
        json_file = save_results_json(all_results, output_dir)
    
    if output_csv:
        csv_file = save_results_csv(all_results, output_dir)
    
    # Always generate summary report when using --all
    if args.all or (output_json and output_csv):
        summary_file = generate_summary_report(all_results, all_threats, all_images_info, output_dir)
    
    # Summary
    print(f"\n{Colors.NEON_GREEN}Crawling complete! Summary:{Colors.RESET}")
    print(f"  • Total pages crawled: {len(all_results)}")
    print(f"  • Threats detected: {len(all_threats)}")
    
    if all_threats:
        high_count = sum(1 for finding in all_threats if finding['threats']['high'])
        medium_count = sum(1 for finding in all_threats if finding['threats']['medium'])
        low_count = sum(1 for finding in all_threats if finding['threats']['low'])
        
        if high_count > 0:
            print(f"  {Colors.RED}• High severity threats: {high_count}{Colors.RESET}")
        if medium_count > 0:
            print(f"  {Colors.YELLOW}• Medium severity threats: {medium_count}{Colors.RESET}")
        if low_count > 0:
            print(f"  • Low severity threats: {low_count}")
    
    if args.images:
        print(f"  • Images captured: {len(all_images_info)}")
        if all_images_info:
            total_size_mb = sum(img['size_kb'] for img in all_images_info) / 1024
            print(f"  • Total image data: {total_size_mb:.2f} MB")
        print(f"  • Images saved in: {os.path.join(output_dir, 'images/')}")
    
    if json_file:
        print(f"  • JSON report: {json_file}")
    if csv_file:
        print(f"  • CSV report: {csv_file}")
    if summary_file:
        print(f"  • Summary report: {summary_file}")
    
    print()


if __name__ == "__main__":
    main()