"""
Simple seed URL health manager.
Keep only responsive .onion seeds in rotation to avoid slow/failing starts.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

from config import settings
from core.tor_client import tor_client

logger = logging.getLogger(__name__)


class SeedManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._healthy: List[str] = []
        self._all: List[str] = []
        self._last_refresh = 0.0
        self._started = False
        self._refresh_ttl = 600  # seconds between background refreshes
        self._refresh_interval = 900  # background loop interval
        self._check_connect_timeout = 4.0
        self._check_read_timeout = 10.0
        self._max_workers = 4
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._started:
            return
        self._all = list(dict.fromkeys(settings.seed_urls or []))
        if not self._all:
            logger.warning("No seed_urls configured")
            self._started = True
            return
        # Seed with all URLs until we know which ones respond
        with self._lock:
            if not self._healthy:
                self._healthy = list(self._all)
        self._started = True
        self._stop_event.clear()
        # Initial refresh in background so we don't block startup
        t = threading.Thread(target=self._initial_refresh, name="seed-refresh-init", daemon=True)
        t.start()
        # Periodic refresh (allows config overrides, e.g. for slower environments)
        self._refresh_interval = getattr(
            settings, "seed_refresh_interval", self._refresh_interval
        )
        t2 = threading.Thread(target=self._loop, name="seed-refresh-loop", daemon=True)
        t2.start()
        logger.info(f"SeedManager started with {len(self._all)} seed(s)")

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the background loop to exit and wait for it to finish.

        The loop thread is non-daemon from a liveness standpoint only through
        this event: it waits on the event with a timeout instead of sleeping,
        so `stop()` wakes it immediately instead of waiting up to
        `_refresh_interval` seconds for the sleep to expire.
        """
        if not self._started:
            return
        self._stop_event.set()
        loop = next(
            (t for t in threading.enumerate() if t.name == "seed-refresh-loop"),
            None,
        )
        if loop is not None:
            loop.join(timeout=timeout)
            if loop.is_alive():
                logger.warning(
                    "Seed refresh loop did not stop within %.1fs; it is a "
                    "daemon thread and will die with the process",
                    timeout,
                )
        self._started = False

    def _initial_refresh(self) -> None:
        try:
            self.refresh(force=True)
        except Exception as e:
            logger.debug(f"Initial seed refresh failed: {e}")

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            # Event.wait instead of time.sleep so stop() breaks the wait at once
            if self._stop_event.wait(self._refresh_interval):
                break
            try:
                if time.time() - self._last_refresh >= self._refresh_ttl:
                    self.refresh(force=False)
            except Exception as e:
                logger.debug(f"Seed refresh loop error: {e}")

    def _check(self, url: str) -> bool:
        try:
            return bool(
                tor_client.check_reachable(
                    url,
                    connect_timeout=self._check_connect_timeout,
                    read_timeout=self._check_read_timeout,
                    retries=1,
                )
            )
        except Exception:
            return False

    def refresh(self, force: bool = False) -> None:
        if not self._all:
            return
        now = time.time()
        if not force and now - self._last_refresh < self._refresh_ttl and self._healthy:
            return

        alive: List[str] = []
        try:
            with ThreadPoolExecutor(max_workers=min(self._max_workers, len(self._all))) as ex:
                futs = {ex.submit(self._check, u): u for u in self._all}
                for fut in as_completed(futs, timeout=60):
                    u = futs.get(fut)
                    if not u:
                        continue
                    try:
                        if fut.result():
                            alive.append(u)
                    except Exception:
                        continue
        except Exception as e:
            logger.debug(f"Seed refresh executor error: {e}")

        with self._lock:
            if alive:
                # Keep original order from config
                ordered = [u for u in self._all if u in alive]
                self._healthy = ordered
                logger.info(f"Seeds healthy: {len(self._healthy)}/{len(self._all)}")
            else:
                # Don't rotate to empty list on failure; keep last known healthy if any
                if self._healthy:
                    logger.warning(
                        "Seed refresh probed all %d seed(s) and none responded; "
                        "keeping the %d previously-healthy seed(s) in rotation "
                        "(they may also be down — rechecking next interval)",
                        len(self._all),
                        len(self._healthy),
                    )
                else:
                    # Fallback to all so we still try something
                    self._healthy = list(self._all)
                    logger.warning(
                        "Seed refresh probed all %d seed(s) and none responded; "
                        "healthy set was empty so falling back to the full list "
                        "of %d seed(s) — searches may start slow until the next "
                        "refresh finds a responsive seed",
                        len(self._all),
                        len(self._all),
                    )
            self._last_refresh = now

    def get_seed(self) -> Optional[str]:
        with self._lock:
            if self._healthy:
                # simple round-robin
                s = self._healthy.pop(0)
                self._healthy.append(s)
                return s
            if self._all:
                return self._all[0]
            return None


seed_manager = SeedManager()
