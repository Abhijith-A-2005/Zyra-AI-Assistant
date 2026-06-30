import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)
HA_STATE_NOT_FOUND = "__not_found__"
HA_STATE_REQUEST_FAILED = "__request_failed__"

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

            if state in {"unavailable", "unknown"}:
                logger.warning(
                    "Home Assistant target entity unavailable for %s: %s",
                    entity_id,
                    state,
                )
                return None

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
        
    def get_raw_state(self, device: str) -> Optional[str]:
        """
        Return raw Home Assistant state.

        """
        entity_id = self.entities.get(device)

        if not entity_id:
            logger.error("No Home Assistant entity configured for %s", device)
            return HA_STATE_NOT_FOUND

        try:
            response = self.session.get(
                f"{self.base_url}/api/states/{entity_id}",
                headers=self._headers(),
                timeout=self.timeout,
            )

            if response.status_code == 404:
                logger.error("Home Assistant entity not found: %s", entity_id)
                return HA_STATE_NOT_FOUND

            response.raise_for_status()
            return response.json().get("state")

        except requests.RequestException as e:
            logger.warning(
                "Home Assistant raw state failed for %s: %s",
                entity_id,
                e,
            )
            return HA_STATE_REQUEST_FAILED
        
    def _wait_for_expected_state(
        self,
        device: str,
        expected_state: str,
        attempts: int = 5,
        delay_sec: float = 0.35,
    ) -> tuple[bool, Optional[str]]:
        """
        Wait briefly for HA state update after service call.

        """
        import time

        last_state: Optional[str] = None

        for attempt in range(1, attempts + 1):
            state = self.get_raw_state(device)
            last_state = state

            if state == expected_state:
                return True, state

            if state in {
                HA_STATE_NOT_FOUND,
                HA_STATE_REQUEST_FAILED,
                "unavailable",
                "unknown",
                None,
            }:
                return False, state

            logger.info(
                "Waiting for HA state update for %s: attempt %d/%d state=%s expected=%s",
                device,
                attempt,
                attempts,
                state,
                expected_state,
            )

            time.sleep(delay_sec)

        return False, last_state
        
    def entity_is_unavailable(self, device: str) -> bool:
        """
        True when HA is reachable but the target entity reports unavailable/unknown.

        Why:
        This usually means the physical device/integration behind HA is offline,
        not that Home Assistant itself is down.
        """
        state = self.get_raw_state(device)
        return state in {"unavailable", "unknown"}

    def get_status(self) -> Optional[dict[str, Optional[bool]]]:
        """
        Return state map:
            {"tv": True, "soundbar": False, "subwoofer": None, ...}

        Return None only if Home Assistant itself is unreachable.
        """
        if not self.configured:
            logger.warning("Home Assistant is not configured")
            return None

        if not self.is_available():
            return None

        states: dict[str, Optional[bool]] = {}

        for device in self.entities:
            raw_state = self.get_raw_state(device)

            if raw_state == "on":
                states[device] = True
            elif raw_state == "off":
                states[device] = False
            elif raw_state in {"unavailable", "unknown"}:
                logger.warning(
                    "Home Assistant target entity unavailable for %s: %s",
                    self.entities[device],
                    raw_state,
                )
                states[device] = None
            else:
                logger.warning(
                    "Unsupported Home Assistant state for %s: %s",
                    self.entities[device],
                    raw_state,
                )
                states[device] = None

        return states
    
    def call_service(
        self,
        domain: str,
        service: str,
        data: dict,
    ) -> bool:
        """
        Call any Home Assistant service.

        Media devices require services like:
          media_player.volume_set
          media_player.select_source
          media_player.media_play
          media_player.play_media
        """
        if not self.configured:
            logger.warning("Home Assistant is not configured")
            return False

        try:
            response = self.session.post(
                f"{self.base_url}/api/services/{domain}/{service}",
                headers=self._headers(),
                json=data,
                timeout=self.timeout,
            )

            if response.status_code >= 400:
                logger.error(
                    "Home Assistant service failed HTTP %s for %s.%s: %s",
                    response.status_code,
                    domain,
                    service,
                    response.text,
                )
                return False

            logger.info(
                "Home Assistant service OK: %s.%s data=%s",
                domain,
                service,
                data,
            )
            return True

        except requests.RequestException as e:
            logger.warning(
                "Home Assistant service failed for %s.%s: %s",
                domain,
                service,
                e,
            )
            return False

    def get_entity_raw_state(self, entity_id: str) -> Optional[str]:
        """
        Read raw HA state using entity_id directly.

        Why:
        Registry devices are no longer limited to old logical IDs like tv/soundbar.
        """
        if not self.configured:
            return HA_STATE_REQUEST_FAILED

        try:
            response = self.session.get(
                f"{self.base_url}/api/states/{entity_id}",
                headers=self._headers(),
                timeout=self.timeout,
            )

            if response.status_code == 404:
                logger.error("Home Assistant entity not found: %s", entity_id)
                return HA_STATE_NOT_FOUND

            response.raise_for_status()
            return response.json().get("state")

        except requests.RequestException as e:
            logger.warning(
                "Home Assistant raw state failed for entity %s: %s",
                entity_id,
                e,
            )
            return HA_STATE_REQUEST_FAILED
        
    def get_entity_state_object(self, entity_id: str) -> Optional[dict]:
        """
        Return the full Home Assistant state object for an entity.

        Why:
        Home Assistant can accept a service call even if the actual device does
        not visibly change. Registry commands need to verify real attributes
        like volume_level, source, sound_mode, and mute state.
        """
        if not self.configured:
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
            return response.json()

        except requests.RequestException as e:
            logger.warning(
                "Home Assistant full entity state failed for %s: %s",
                entity_id,
                e,
            )
            return None

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

        domain = entity_id.split(".", 1)[0]

        try:
            response = self.session.post(
                f"{self.base_url}/api/services/{domain}/{service}",
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

            if action in {"on", "off"}:
                ok, final_state = self._wait_for_expected_state(
                    device=device,
                    expected_state=action,
                    attempts=5,
                    delay_sec=0.35,
                )

                if final_state == HA_STATE_NOT_FOUND:
                    logger.error(
                        "Home Assistant accepted command, but target entity does not exist: %s",
                        entity_id,
                    )
                    return False

                if final_state == HA_STATE_REQUEST_FAILED:
                    logger.error(
                        "Home Assistant accepted command, but state verification failed: %s",
                        entity_id,
                    )
                    return False

                if final_state in {"unavailable", "unknown", None}:
                    logger.error(
                        "Home Assistant accepted command, but target entity is %s: %s",
                        final_state,
                        entity_id,
                    )
                    return False

                if not ok:
                    logger.error(
                        "Home Assistant accepted command, but state did not confirm: %s expected=%s last_state=%s",
                        entity_id,
                        action,
                        final_state,
                    )

                    return False

            elif action == "toggle":
                final_state = self.get_raw_state(device)

                if final_state in {"unavailable", "unknown"}:
                    logger.error(
                        "Home Assistant accepted toggle, but target entity is %s: %s",
                        final_state,
                        entity_id,
                    )
                    return False

            logger.info(
                "Home Assistant command OK: %s %s final_state=%s",
                service,
                entity_id,
                final_state,
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