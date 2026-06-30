"""
Configuration management for DarkWeb API.
All settings are loaded from YAML configuration file.
"""
import os
import yaml
from pathlib import Path
from typing import List


class Settings:
    """Application settings loaded from YAML config file."""
    
    def __init__(self, config_path: str = "config.yaml"):
        """
        Load configuration from YAML file.
        
        Args:
            config_path: Path to YAML config file
        """
        config_file = Path(config_path)
        
        if not config_file.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
        
        # API Configuration
        api_config = config.get('api', {})
        self.api_title: str = api_config.get('title', 'DarkWeb Search API')
        self.api_version: str = api_config.get('version', '1.0.0')
        self.api_description: str = api_config.get('description', 'REST API for searching .onion sites')
        self.api_host: str = api_config.get('host', '0.0.0.0')
        self.api_port: int = api_config.get('port', 8000)
        
        # Tor Configuration
        tor_config = config.get('tor', {})
        self.tor_proxy: str = tor_config.get('proxy', 'socks5h://tor:9050')
        self.tor_check_url: str = tor_config.get('check_url', 'http://check.torproject.org/')
        
        # Crawling Configuration
        crawl_config = config.get('crawling', {})
        self.crawl_delay: int = crawl_config.get('delay', 7)
        self.default_max_depth: int = crawl_config.get('max_depth', 2)
        self.default_max_pages: int = crawl_config.get('max_pages', 50)
        self.default_max_results: int = crawl_config.get('max_results', 10)
        self.default_timeout: int = crawl_config.get('timeout', 30)
        self.seed_urls: List[str] = crawl_config.get('seed_urls', [])
        
        # Retry Configuration
        retry_config = config.get('retry', {})
        self.retry_count: int = retry_config.get('count', 3)
        self.backoff_factor: int = retry_config.get('backoff_factor', 4)
        
        # Security
        security_config = config.get('security', {})
        self.user_agent: str = security_config.get(
            'user_agent',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        self.api_key: str = security_config.get('api_key', '')
        
        # CORS
        cors_config = config.get('cors', {})
        self.cors_origins: List[str] = cors_config.get('origins', ['*'])

        # Job Queue
        jobs_config = config.get('jobs', {})
        self.job_max_workers: int = jobs_config.get('max_workers', 2)
        self.job_db_path: str = jobs_config.get('db_path', '/app/data/jobs.db')

        # Webhook
        webhook_config = config.get('webhook', {})
        self.webhook_max_attempts: int = webhook_config.get('max_attempts', 3)
        self.webhook_retry_delay: int = webhook_config.get('retry_delay', 5)
        self.webhook_timeout: float = webhook_config.get('timeout', 10)
        self.webhook_db_path: str = webhook_config.get('db_path', '/app/data/webhooks.db')
        self.webhook_allow_insecure_urls: bool = webhook_config.get('allow_insecure_urls', False)

        # Capture
        capture_config = config.get('capture', {})
        self.capture: dict = capture_config  # kept for backward compat with warc_provider
        self.capture_backend: str = capture_config.get('backend', 'warc')
        self.capture_output_dir: str = capture_config.get('output_dir', '/app/data/captures')
        self.capture_max_pages: int = capture_config.get('max_pages', 50)
        self.capture_max_size_mb: int = capture_config.get('max_size_mb', 500)


# Global settings instance
settings = Settings(os.getenv('APP_CONFIG_PATH', 'config.yaml'))
