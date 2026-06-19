import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class RelayHttpClient:
    """
    ESP8266 relay-board HTTP client.

    Used for:
    Mode 2 — Online Intelligent Relay Mode

    It talks to the ESP8266 home Wi-Fi IP directly.
    """

    DEVICE_ENDPOINTS = {
        "tv": {
            "on": "/sony/on",
            "off": "/sony/off",
        },
        "soundbar": {
            "on": "/sb/on",
            "off": "/sb/off",
        },
        "subwoofer": {
            "on": "/sub/on",
            "off": "/sub/off",
        },
        "rear": {
            "on": "/rear/on",
            "off": "/rear/off",
        },
    }

    DEVICE_ORDER = ["tv", "soundbar", "subwoofer", "rear"]

    def __init__(self, base_urls: list[str], timeout: float):
        self.base_urls = [url.rstrip("/") for url in base_urls if url.strip()]
        self.timeout = timeout
        self.active_base_url: Optional[str] = None
        self.session = requests.Session()

    def _candidate_urls(self) -> list[str]:
        urls: list[str] = []

        if self.active_base_url:
            urls.append(self.active_base_url)

        for url in self.base_urls:
            if url not in urls:
                urls.append(url)

        return urls

    def _get(self, endpoint: str) -> Optional[str]:
        """
        GET an ESP8266 relay endpoint with short retries.

        """
        max_attempts = 3
        retry_delay_sec = 0.20

        for attempt in range(1, max_attempts + 1):
            for base_url in self._candidate_urls():
                url = f"{base_url}{endpoint}"

                try:
                    logger.info(
                        "Relay GET attempt %d/%d: %s",
                        attempt,
                        max_attempts,
                        url,
                    )

                    response = self.session.get(
                        url,
                        timeout=self.timeout,
                    )

                    if response.status_code != 200:
                        logger.warning(
                            "Relay HTTP %s for %s: %s",
                            response.status_code,
                            url,
                            response.text,
                        )
                        continue

                    self.active_base_url = base_url
                    return response.text.strip()

                except requests.RequestException as e:
                    logger.warning(
                        "Relay request failed attempt %d/%d: %s: %s",
                        attempt,
                        max_attempts,
                        url,
                        e,
                    )

            if attempt < max_attempts:
                time.sleep(retry_delay_sec)

        return None

    def _parse_status(self, payload: str) -> Optional[dict[str, bool]]:
        if not payload:
            return None

        parts = [part.strip() for part in payload.split(",")]

        if len(parts) != 4:
            logger.error("Invalid relay status payload: %r", payload)
            return None

        states: dict[str, bool] = {}

        for device, raw in zip(self.DEVICE_ORDER, parts):
            if raw not in {"0", "1"}:
                logger.error("Invalid relay status value: %r", payload)
                return None

            states[device] = raw == "1"

        return states

    def get_status(self) -> Optional[dict[str, bool]]:
        payload = self._get("/status")

        if payload is None:
            logger.warning("Relay status unavailable")
            return None

        states = self._parse_status(payload)

        if states is None:
            return None

        logger.info("Relay states: %s", states)
        return states

    def ping(self) -> bool:
        payload = self._get("/ping")

        if payload is None:
            return False

        return payload.strip().lower() == "pong"

    def ping_fast(self) -> bool:
        """
        Fast relay availability check.

        Used only for health/status decisions.
        """
        for base_url in self._candidate_urls():
            url = f"{base_url}/ping"

            try:
                logger.info("Relay fast ping: %s", url)

                response = self.session.get(
                    url,
                    timeout=min(self.timeout, 0.6),
                )

                if response.status_code == 200 and response.text.strip().lower() == "pong":
                    self.active_base_url = base_url
                    return True

            except requests.RequestException as e:
                logger.warning("Relay fast ping failed: %s: %s", url, e)

        return False


    def is_available(self) -> bool:
        return self.ping_fast()

    def set_device(self, device: str, action: str) -> bool:
        if device not in self.DEVICE_ENDPOINTS:
            logger.error("Unsupported relay device: %s", device)
            return False

        if action == "toggle":
            status = self.get_status()

            if status is None:
                return False

            action = "off" if status.get(device) else "on"

        if action not in {"on", "off"}:
            logger.error("Unsupported relay action: %s", action)
            return False

        endpoint = self.DEVICE_ENDPOINTS[device][action]
        response = self._get(endpoint)

        if response is None:
            logger.error("Relay command failed: %s %s", device, action)
            return False

        logger.info("Relay command OK: %s %s", device, action)
        return True