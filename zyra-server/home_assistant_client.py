import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class HomeAssistantClient:
    """
    Home Assistant REST client for ZYRA.

    Used for:
    Mode 1 — Online Intelligent HA Mode
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float,
        entities: dict[str, str],
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.timeout = timeout
        self.entities = entities
        self.session = requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def is_available(self) -> bool:
        """
        Lightweight Home Assistant availability check.

        Why:
        Before sending physical-device commands, Zyra should know whether
        HA is alive. If HA is down, the server can safely use relay fallback.
        """
        if not self.configured:
            return False

        try:
            response = self.session.get(
                f"{self.base_url}/api/",
                headers=self._headers(),
                timeout=self.timeout,
            )
            return response.status_code == 200

        except requests.RequestException as e:
            logger.warning("Home Assistant availability check failed: %s", e)
            return False

    def get_state(self, device: str) -> Optional[bool]:
        entity_id = self.entities.get(device)

        if not entity_id:
            logger.error("No Home Assistant entity configured for %s", device)
            return None

        try:
            response = self.session.get(
                f"{self.base_url}/api/states/{entity_id}",
                headers=self._headers(),
                timeout=self.timeout,
            )

            if response.status_code == 404:
                logger.error("Home Assistant entity not found: %s", entity_id)
                return None

            response.raise_for_status()

            state = response.json().get("state")

            if state == "on":
                return True

            if state == "off":
                return False

            logger.warning(
                "Unsupported Home Assistant state for %s: %s",
                entity_id,
                state,
            )
            return None

        except Exception as e:
            logger.warning(
                "Home Assistant state failed for %s: %s",
                entity_id,
                e,
            )
            return None

    def get_status(self) -> Optional[dict[str, Optional[bool]]]:
        """
        Return state map:
            {"tv": True, "soundbar": False, ...}

        Returns None only if HA is completely unavailable.
        """
        if not self.configured:
            logger.warning("Home Assistant is not configured")
            return None

        if not self.is_available():
            return None

        states: dict[str, Optional[bool]] = {}

        for device in self.entities:
            states[device] = self.get_state(device)

        # If every entity failed, treat HA backend as unusable.
        if all(value is None for value in states.values()):
            return None

        return states

    def set_device(self, device: str, action: str) -> bool:
        entity_id = self.entities.get(device)

        if not entity_id:
            logger.error("No Home Assistant entity configured for %s", device)
            return False

        service_map = {
            "on": "turn_on",
            "off": "turn_off",
            "toggle": "toggle",
        }

        service = service_map.get(action)

        if not service:
            logger.error("Unsupported Home Assistant action: %s", action)
            return False

        try:
            response = self.session.post(
                f"{self.base_url}/api/services/switch/{service}",
                headers=self._headers(),
                json={"entity_id": entity_id},
                timeout=self.timeout,
            )

            if response.status_code >= 400:
                logger.error(
                    "Home Assistant command failed HTTP %s for %s.%s: %s",
                    response.status_code,
                    device,
                    action,
                    response.text,
                )
                return False

            logger.info(
                "Home Assistant command OK: %s %s",
                service,
                entity_id,
            )
            return True

        except requests.RequestException as e:
            logger.warning(
                "Home Assistant command failed for %s.%s: %s",
                device,
                action,
                e,
            )
            return False