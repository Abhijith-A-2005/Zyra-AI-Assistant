import logging
import re
from dataclasses import dataclass
from typing import Optional

import requests

from config import SMART_HOME_BASE_URLS, SMART_HOME_TIMEOUT_SEC

logger = logging.getLogger(__name__)


@dataclass
class SmartHomeResult:
    handled: bool
    response: str
    action: str = ""
    devices: Optional[list[str]] = None


class SmartHomeEngine:
    """
    Direct smart-home command engine for ZYRA.

    This runs after STT and before LLM.
    If a transcript is a relay command, it handles the command directly.
    If not, main.py sends the transcript to the normal LLM pipeline.
    """

    def __init__(self):
        self.base_urls = SMART_HOME_BASE_URLS
        self.timeout = SMART_HOME_TIMEOUT_SEC
        self.active_base_url: Optional[str] = None
        self.session = requests.Session()

        self.devices = {
            "tv": {
                "label": "TV",
                "names": [
                    "tv",
                    "television",
                    "sony tv",
                    "sony television",
                ],
                "on": "/sony/on",
                "off": "/sony/off",
                "toggle": "/sony/toggle",
            },
            "soundbar": {
                "label": "Soundbar",
                "names": [
                    "soundbar",
                    "sound bar",
                    "speaker bar",
                    "bar",
                ],
                "on": "/sb/on",
                "off": "/sb/off",
                "toggle": "/sb/toggle",
            },
            "subwoofer": {
                "label": "Subwoofer",
                "names": [
                    "subwoofer",
                    "sub woofer",
                    "woofer",
                    "sub",
                ],
                "on": "/sub/on",
                "off": "/sub/off",
                "toggle": "/sub/toggle",
            },
            "rear": {
                "label": "Rear speakers",
                "names": [
                    "rear",
                    "back",
                    "rear speaker",
                    "rear speakers",
                    "surround",
                    "surround speaker",
                    "surround speakers",
                    "surround system",
                    "back speaker",
                    "back speakers",
                ],
                "on": "/rear/on",
                "off": "/rear/off",
                "toggle": "/rear/toggle",
            },
        }

        self.groups = {
            "sound_system": {
                "label": "Sound system",
                "names": [
                    "sound system",
                    "audio system",
                    "speaker system",
                ],
                "devices": ["soundbar", "subwoofer","rear"],
            },
            "all_speakers": {
                "label": "All speakers",
                "names": [
                    "all speakers",
                    "speakers",
                    "surround system",
                ],
                "devices": ["soundbar", "subwoofer", "rear"],
            },
            "home_theater": {
                "label": "Home theater",
                "names": [
                    "home theater",
                    "home theatre",
                    "theater",
                    "theatre",
                    "full system",
                    "entire system",
                    "everything",
                    "all devices",
                ],
                "devices": ["tv", "soundbar", "subwoofer", "rear"],
            },
        }

        logger.info(
            "Smart home engine ready — relay URLs: "
            + ", ".join(self.base_urls)
        )

    def handle(self, transcript: str) -> SmartHomeResult:
        """
        Main entry point.

        Returns handled=False if this is not a smart-home command.
        Returns handled=True with a response if smart_home.py handled it.
        """
        text = self._normalize(transcript)

        if not text:
            return SmartHomeResult(False, "")

        # Status questions must be checked before action detection.
        # Example: "Is the TV on?" contains "on", but it is a question,
        # not a command to turn the TV on.
        if self._is_status_question(text):
            return self._handle_status_question(text)

        action = self._detect_action(text)
        if not action:
            return SmartHomeResult(False, "")

        devices = self._detect_devices(text)
        if not devices:
            return SmartHomeResult(False, "")

        return self._execute_action(action, devices)

    def _normalize(self, text: str) -> str:
        text = text.lower().strip()

        replacements = {
            "what's": "what is",
            "whats": "what is",
            "who's": "who is",
            "isn't": "is not",
            "aren't": "are not",
        }

        for old, new in replacements.items():
            text = text.replace(old, new)

        text = re.sub(r"[^a-z0-9\s]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _detect_action(self, text: str) -> Optional[str]:
        off_phrases = [
            "turn off",
            "switch off",
            "power off",
            "shut down",
            "shutdown",
            "disable",
            "deactivate",
            "stop",
            "put off",
        ]

        on_phrases = [
            "turn on",
            "switch on",
            "power on",
            "enable",
            "activate",
            "start",
            "put on",
        ]

        toggle_phrases = [
            "toggle",
            "flip",
        ]

        # OFF first, because phrases like "turn off" contain command words
        # that should not be confused with generic switching.
        for phrase in off_phrases:
            if phrase in text:
                return "off"

        for phrase in on_phrases:
            if phrase in text:
                return "on"

        for phrase in toggle_phrases:
            if phrase in text:
                return "toggle"

        # Short command support:
        # "TV on", "soundbar off", "subwoofer on".
        words = text.split()

        if "off" in words:
            return "off"

        if "on" in words:
            return "on"

        return None

    def _detect_devices(self, text: str) -> list[str]:
        matched: list[str] = []

        # Group detection first.
        # Example: "turn on all speakers" should not only match "rear speakers".
        for group_cfg in self.groups.values():
            for name in group_cfg["names"]:
                if self._contains_phrase(text, name):
                    for device_id in group_cfg["devices"]:
                        if device_id not in matched:
                            matched.append(device_id)
                    return matched

        # Individual devices.
        for device_id, cfg in self.devices.items():
            for name in cfg["names"]:
                if self._contains_phrase(text, name):
                    matched.append(device_id)
                    break

        return matched

    def _contains_phrase(self, text: str, phrase: str) -> bool:
        return re.search(rf"\b{re.escape(phrase)}\b", text) is not None

    def _is_status_question(self, text: str) -> bool:
        status_phrases = [
            "status",
            "device status",
            "what is the device status",
            "what is the status",
            "relay status",
            "home theater status",
            "home theatre status",
            "what is the home theater status",
            "system status",
            "what is the system status",
            "what is on",
            "what all is on",
            "which devices are on",
            "which device is on",
            "what devices are on",
            "what all devices are on",
            "What all devices are currently on",
            "what is currently on",
            "what all is currently on",
            "what devices are currently on",
            "which devices are currently on",
            "what all are off",
            "what is off",
            "what devices are off",
            "which devices are off",
            "what all devices are off",
            "which all devices are off",
            "what is the status",
            "what device is on",
            "what is turned on",
            "what is switched on",
            "check devices",
            "check device",
            "check home theater",
            "check home theatre",
        ]

        if any(phrase in text for phrase in status_phrases):
            return True
        # Device-specific questions:
        # "Is TV on?", "Are rear speakers off?"
        starts_like_question = (
            text.startswith("is ")
            or text.startswith("are ")
            or text.startswith("check ")
        )

        if starts_like_question and self._detect_devices(text):
            return True

        return False

    def _handle_status_question(self, text: str) -> SmartHomeResult:
        devices = self._detect_devices(text)

        status = self.get_status()
        if status is None:
            return SmartHomeResult(
                True,
                "I could not reach the smart extension board.",
                action="status",
                devices=devices,
            )

        if devices:
            response = self._format_specific_status(status, devices)
        else:
            response = self._format_full_status(status)

        return SmartHomeResult(
            True,
            response,
            action="status",
            devices=devices,
        )

    def _execute_action(self, action: str, devices: list[str]) -> SmartHomeResult:
        successful: list[str] = []
        failed: list[str] = []

        for device_id in devices:
            endpoint = self.devices[device_id][action]

            if self._get(endpoint) is not None:
                successful.append(device_id)
            else:
                failed.append(device_id)

        if not successful and failed:
            return SmartHomeResult(
                True,
                "I could not reach the smart extension board.",
                action=action,
                devices=devices,
            )

        # Verify actual state after the command.
        status = self.get_status()

        if status is None:
            return SmartHomeResult(
                True,
                "Command sent, but I could not verify the device state.",
                action=action,
                devices=devices,
            )

        if failed:
            failed_names = self._format_device_list(failed)
            ok_names = self._format_device_list(successful)
            return SmartHomeResult(
                True,
                f"{ok_names} changed, but {failed_names} failed.",
                action=action,
                devices=devices,
            )

        if action == "toggle":
            response = self._format_specific_status(status, devices)
        else:
            response = self._format_action_confirmation(status, devices, action)

        return SmartHomeResult(
            True,
            response,
            action=action,
            devices=devices,
        )

    def _get(self, endpoint: str) -> Optional[str]:
        urls_to_try = []

        if self.active_base_url:
            urls_to_try.append(self.active_base_url)

        for url in self.base_urls:
            if url not in urls_to_try:
                urls_to_try.append(url)

        for base_url in urls_to_try:
            full_url = base_url + endpoint

            try:
                logger.info(f"Smart home GET: {full_url}")

                response = self.session.get(
                    full_url,
                    timeout=self.timeout,
                )

                if response.status_code == 200:
                    self.active_base_url = base_url
                    return response.text.strip()

                logger.warning(
                    f"Smart home HTTP {response.status_code}: {full_url}"
                )

            except requests.RequestException as e:
                logger.warning(f"Smart home request failed: {full_url}: {e}")

        return None

    def get_status(self) -> Optional[dict[str, bool]]:
        payload = self._get("/status")

        if payload is None:
            return None

        return self._parse_status(payload)

    def _parse_status(self, payload: str) -> Optional[dict[str, bool]]:
        payload = payload.strip()
        parts = [p.strip() for p in payload.split(",")]

        if len(parts) != 4:
            logger.error(f"Invalid relay status payload: '{payload}'")
            return None

        if any(part not in {"0", "1"} for part in parts):
            logger.error(f"Invalid relay status tokens: '{payload}'")
            return None

        return {
            "tv": parts[0] == "1",
            "soundbar": parts[1] == "1",
            "subwoofer": parts[2] == "1",
            "rear": parts[3] == "1",
        }

    def _format_action_confirmation(
        self,
        status: dict[str, bool],
        devices: list[str],
        action: str,
    ) -> str:
        desired = action == "on"

        confirmed = [
            device_id
            for device_id in devices
            if status.get(device_id) == desired
        ]

        not_confirmed = [
            device_id
            for device_id in devices
            if status.get(device_id) != desired
        ]

        if not_confirmed and confirmed:
            return (
                f"{self._format_device_list(confirmed)} are {action}, "
                f"but {self._format_device_list(not_confirmed)} did not confirm."
            )

        if not_confirmed and not confirmed:
            return "Command sent, but the state did not change."

        names = self._format_device_list(confirmed)

        if len(confirmed) == 1:
            return f"{names} is {action}."

        return f"{names} are {action}."

    def _format_specific_status(
        self,
        status: dict[str, bool],
        devices: list[str],
    ) -> str:
        parts = []

        for device_id in devices:
            label = self.devices[device_id]["label"]
            state = "on" if status.get(device_id) else "off"
            parts.append(f"{label} is {state}")

        return self._join_sentence_parts(parts) + "."

    def _format_full_status(self, status: dict[str, bool]) -> str:
        on_devices = [
            device_id
            for device_id, is_on in status.items()
            if is_on
        ]

        if not on_devices:
            return "Everything is off."

        if len(on_devices) == len(self.devices):
            return "Everything is on."

        return f"{self._format_device_list(on_devices)} are on."

    def _format_device_list(self, device_ids: list[str]) -> str:
        labels = [self.devices[device_id]["label"] for device_id in device_ids]
        return self._join_sentence_parts(labels)

    def _join_sentence_parts(self, parts: list[str]) -> str:
        if not parts:
            return ""

        if len(parts) == 1:
            return parts[0]

        if len(parts) == 2:
            return f"{parts[0]} and {parts[1]}"

        return ", ".join(parts[:-1]) + f", and {parts[-1]}"