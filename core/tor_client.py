"""
Tor client for SOCKS5 proxy communication.
Handles session creation, circuit renewal, and connectivity testing.
"""
import time
import requests
import logging
from typing import Optional
from requests.exceptions import RequestException

from config import settings

logger = logging.getLogger(__name__)


class TorClient:
    """Manages Tor SOCKS5 proxy connections and session handling."""
    
    def __init__(self, proxy_url: Optional[str] = None):
        """Initialize Tor client with proxy configuration."""
        self.proxy_url = proxy_url or settings.tor_proxy
        self.session: Optional[requests.Session] = None
        
    def create_session(self) -> requests.Session:
        """
        Create a new requests session configured for Tor SOCKS5 proxy.
        
        Returns:
            Configured requests.Session instance
        """
        session = requests.Session()
        session.proxies = {
            'http': self.proxy_url,
            'https': self.proxy_url
        }
        session.headers.update({
            'User-Agent': settings.user_agent,
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Connection': 'keep-alive',
            'DNT': '1',
            'Upgrade-Insecure-Requests': '1'
        })
        
        self.session = session
        logger.info("Created new Tor session with SOCKS5 proxy")
        return session
    
    def test_connection(self) -> bool:
        """
        Test if Tor connection is working by checking torproject.org.
        
        Returns:
            True if connected through Tor, False otherwise
        """
        if not self.session:
            self.create_session()
        
        try:
            logger.info("Testing Tor connection...")
            response = self.session.get(
                settings.tor_check_url,
                timeout=settings.default_timeout
            )
            
            if "Congratulations" in response.text:
                logger.info("✓ Tor connection successful - anonymity enabled")
                return True
            else:
                logger.warning("Tor connection test returned unexpected response")
                return False
                
        except RequestException as e:
            logger.error(f"Tor connection test failed: {e}")
            return False
    
    def get_with_retries(
        self,
        url: str,
        retries: Optional[int] = None,
        timeout: Optional[int] = None
    ) -> requests.Response:
        """
        HTTP GET with retry logic and exponential backoff.
        
        Args:
            url: URL to fetch
            retries: Number of retry attempts (uses config default if None)
            timeout: Request timeout in seconds (uses config default if None)
            
        Returns:
            Response object if successful
            
        Raises:
            RequestException: If all retry attempts fail
        """
        if not self.session:
            self.create_session()
        
        retries = retries if retries is not None else settings.retry_count
        timeout = timeout if timeout is not None else settings.default_timeout
        last_exception: Optional[Exception] = None
        
        for attempt in range(1, retries + 1):
            try:
                logger.debug(f"Attempt {attempt}/{retries} for {url}")
                response = self.session.get(url, timeout=timeout)
                response.raise_for_status()
                return response
                
            except RequestException as e:
                logger.warning(f"Attempt {attempt} failed for {url}: {e}")
                last_exception = e
                
                if attempt < retries:
                    sleep_time = settings.backoff_factor * (2 ** (attempt - 1))
                    logger.info(f"Retrying in {sleep_time}s...")
                    time.sleep(sleep_time)
        
        logger.error(f"All {retries} attempts failed for {url}")
        raise last_exception
    
    def close(self):
        """Close the session and cleanup resources."""
        if self.session:
            self.session.close()
            logger.info("Tor session closed")


# Global Tor client instance
tor_client = TorClient()
