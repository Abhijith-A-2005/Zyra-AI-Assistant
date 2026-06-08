import logging
from dataclasses import dataclass
from typing import Optional

import requests

from config import SMART_HOME_BASE_URLS, SMART_HOME_TIMEOUT_SEC
from intent_router import RoutedIntent, RoutedCommand

logger = logging.getLogger(__name__)


@dataclass
class SmartHomeResult:
    handled: bool
    response: str
    action: str = ""
    devices: Optional[list[str]] = None


class SmartHomeEngine:
    """
    Executes validated smart-home intents.

    This class does not understand natural language.
    It only receives structured intents and talks to the ESP8266 relay board.
    """

    def __init__(self):
        self.base_urls = SMART_HOME_BASE_URLS
        self.timeout = SMART_HOME_TIMEOUT_SEC
        self.active_base_url: Optional[str] = None
        self.session = requests.Session()

        self.devices = {
            "tv": {
                "label": "TV",
                "plural": False,
                "on": "/sony/on",
                "off": "/sony/off",
                "toggle": "/sony/toggle",
            },
            "soundbar": {
                "label": "Soundbar",
                "plural": False,
                "on": "/sb/on",
                "off": "/sb/off",
                "toggle": "/sb/toggle",
            },
            "subwoofer": {
                "label": "Subwoofer",
                "plural": False,
                "on": "/sub/on",
                "off": "/sub/off",
                "toggle": "/sub/toggle",
            },
            "rear": {
                "label": "Rear speakers",
                "plural": True,
                "on": "/rear/on",
                "off": "/rear/off",
                "toggle": "/rear/toggle",
            },
        }

        logger.info(
            "Smart home engine ready — relay URLs: "
            + ", ".join(self.base_urls)
        )

    def handle_intent(self, routed: RoutedIntent) -> SmartHomeResult:
        """
        Executes a routed smart-home intent.

        Safety rule:
        Low-confidence control commands are not executed.
        """
        if routed.domain != "smart_home":
            return SmartHomeResult(False, "")

        if routed.confidence < 0.65:
            return SmartHomeResult(
                True,
                "I heard a device command, but I could not parse it safely.",
                action="clarify",
                devices=routed.devices,
            )

        if routed.intent == "status":
            return self._handle_status(routed.devices)

        if routed.intent == "control":
            if routed.confidence < 0.75:
                return SmartHomeResult(
                    True,
                    "I heard a device command, but I am not confident enough to execute it.",
                    action="clarify",
                    devices=routed.devices,
                )

            return self._execute_command_batch(routed.commands)

        return SmartHomeResult(False, "")

    def _expand_devices(self, devices: list[str]) -> list[str]:
        if not devices or "all" in devices:
            return ["tv", "soundbar", "subwoofer", "rear"]

        expanded = []

        for device in devices:
            if device in self.devices and device not in expanded:
                expanded.append(device)

        return expanded

    def _handle_status(self, devices: list[str]) -> SmartHomeResult:
        status = self.get_status()

        if status is None:
            return SmartHomeResult(
                True,
                "I could not reach the smart extension board.",
                action="status",
                devices=devices,
            )

        expanded = self._expand_devices(devices)

        if not devices or "all" in devices:
            response = self._format_full_status(status)
        else:
            response = self._format_specific_status(status, expanded)

        return SmartHomeResult(
            True,
            response,
            action="status",
            devices=expanded,
        )

    def _execute_command_batch(
        self,
        commands: list[RoutedCommand],
    ) -> SmartHomeResult:
        if not commands:
            return SmartHomeResult(
                True,
                "I understood a command, but not the device action.",
                action="clarify",
                devices=[],
            )

        executed_groups = []
        failed_devices = []

        for command in commands:
            action = command.action
            devices = self._expand_devices(command.devices)

            for device_id in devices:
                endpoint = self.devices[device_id][action]

                if self._get(endpoint) is not None:
                    executed_groups.append((action, device_id))
                else:
                    failed_devices.append(device_id)

        if not executed_groups and failed_devices:
            return SmartHomeResult(
                True,
                "I could not reach the smart extension board.",
                action="batch",
                devices=failed_devices,
            )

        status = self.get_status()

        if status is None:
            return SmartHomeResult(
                True,
                "Command sent, but I could not verify the device state.",
                action="batch",
                devices=[device for _, device in executed_groups],
            )

        if failed_devices:
            fail_names = self._format_device_list(failed_devices)
            return SmartHomeResult(
                True,
                f"Some devices changed, but {fail_names} failed.",
                action="batch",
                devices=[device for _, device in executed_groups],
            )

        response = self._format_batch_confirmation(commands, status)

        return SmartHomeResult(
            True,
            response,
            action="batch",
            devices=[device for _, device in executed_groups],
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

    def _format_batch_confirmation(
        self,
        commands: list[RoutedCommand],
        status: dict[str, bool],
    ) -> str:
        parts = []

        for command in commands:
            action = command.action
            devices = self._expand_devices(command.devices)

            if action == "toggle":
                parts.append(self._format_specific_status(status, devices))
                continue

            desired_state = action == "on"

            confirmed = [
                device_id
                for device_id in devices
                if status.get(device_id) == desired_state
            ]

            not_confirmed = [
                device_id
                for device_id in devices
                if status.get(device_id) != desired_state
            ]

            if confirmed:
                names = self._format_device_list(confirmed)

                if len(confirmed) == 1:
                    verb = "are" if self.devices[confirmed[0]]["plural"] else "is"
                    parts.append(f"{names} {verb} {action}")
                else:
                    parts.append(f"{names} are {action}")

            if not_confirmed:
                names = self._format_device_list(not_confirmed)
                parts.append(f"{names} did not confirm")

        if not parts:
            return "Command sent, but the state did not change."

        return ". ".join(parts) + "."

    def _format_specific_status(
        self,
        status: dict[str, bool],
        devices: list[str],
    ) -> str:
        parts = []

        for device_id in devices:
            label = self.devices[device_id]["label"]
            state = "on" if status.get(device_id) else "off"
            verb = "are" if self.devices[device_id]["plural"] else "is"
            parts.append(f"{label} {verb} {state}")

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

        names = self._format_device_list(on_devices)

        if len(on_devices) == 1:
            verb = "are" if self.devices[on_devices[0]]["plural"] else "is"
            return f"{names} {verb} on."

        return f"{names} are on."

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