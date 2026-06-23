import logging
from dataclasses import dataclass
from typing import Optional

from config import (
    HOME_ASSISTANT_URL,
    HOME_ASSISTANT_TOKEN,
    HOME_ASSISTANT_TIMEOUT_SEC,
    HOME_ASSISTANT_ENTITIES,
    RELAY_HOME_BASE_URLS,
    RELAY_HTTP_TIMEOUT_SEC,
)

from home_assistant_client import HomeAssistantClient
from relay_http_client import RelayHttpClient
from smart_home_backends import SmartHomeBackend
from intent_router import RoutedIntent, RoutedCommand

logger = logging.getLogger(__name__)


@dataclass
class SmartHomeResult:
    handled: bool
    response: str
    action: str = ""
    devices: Optional[list[str]] = None
    backend: str = ""
    success: bool = True

class SmartHomeEngine:
    """
    Executes validated smart-home intents.

    Mode 1:
        Zyra Server → Home Assistant → MQTT → ESP8266

    Mode 2:
        Zyra Server → ESP8266 Home IP direct relay fallback

    This class does not understand natural language.
    It only receives structured intents from IntentRouter.
    """

    DEVICE_ORDER = ["tv", "soundbar", "subwoofer", "rear"]

    def __init__(self):
        self.ha = HomeAssistantClient(
            base_url=HOME_ASSISTANT_URL,
            token=HOME_ASSISTANT_TOKEN,
            timeout=HOME_ASSISTANT_TIMEOUT_SEC,
            entities=HOME_ASSISTANT_ENTITIES,
        )

        self.relay = RelayHttpClient(
            base_urls=RELAY_HOME_BASE_URLS,
            timeout=RELAY_HTTP_TIMEOUT_SEC,
        )

        self.active_backend = SmartHomeBackend.NONE

        # Kept for compatibility with older /health code and older tests.
        self.base_urls = self.relay.base_urls
        self.active_base_url = self.relay.active_base_url

        self.devices = {
            "tv": {
                "label": "TV",
                "plural": False,
                "entity_id": HOME_ASSISTANT_ENTITIES["tv"],
            },
            "soundbar": {
                "label": "Soundbar",
                "plural": False,
                "entity_id": HOME_ASSISTANT_ENTITIES["soundbar"],
            },
            "subwoofer": {
                "label": "Subwoofer",
                "plural": False,
                "entity_id": HOME_ASSISTANT_ENTITIES["subwoofer"],
            },
            "rear": {
                "label": "Rear speakers",
                "plural": True,
                "entity_id": HOME_ASSISTANT_ENTITIES["rear"],
            },
        }

        logger.info(
            "Smart home engine ready — HA=%s Relay=%s",
            HOME_ASSISTANT_URL,
            ", ".join(RELAY_HOME_BASE_URLS),
        )

    # ── Public API ─────────────────────────────────

    def handle_intent(self, routed: RoutedIntent) -> SmartHomeResult:
        if routed.domain != "smart_home":
            return SmartHomeResult(False, "")

        if routed.confidence < 0.65:
            return SmartHomeResult(
                True,
                "I heard a device command, but I could not parse it safely.",
            )

        if routed.intent == "status":
            return self._handle_status(routed.devices)

        if routed.intent == "control":
            return self._handle_control(routed)

        return SmartHomeResult(False, "")

    def get_status(self) -> Optional[dict[str, Optional[bool]]]:
        """
        Return current status from best available backend.

        HA is preferred.
        Relay home IP is fallback.
        """
        ha_status = self.ha.get_status()

        if ha_status is not None:
            self.active_backend = SmartHomeBackend.HOME_ASSISTANT
            return ha_status

        relay_status = self.relay.get_status()

        if relay_status is not None:
            self.active_backend = SmartHomeBackend.RELAY_HOME
            self.active_base_url = self.relay.active_base_url
            return relay_status

        self.active_backend = SmartHomeBackend.NONE
        return None

    def health_snapshot(self) -> dict:
        ha_available = self.ha.is_available()
        relay_available = self.relay.is_available()

        self.active_base_url = self.relay.active_base_url

        if ha_available:
            preferred_mode = "online_intelligent_ha"
        elif relay_available:
            preferred_mode = "online_intelligent_relay"
        else:
            preferred_mode = "smart_home_unavailable"

        return {
            "preferred_mode": preferred_mode,
            "active_backend": self.active_backend.value,
            "home_assistant": {
                "url": self.ha.base_url,
                "configured": self.ha.configured,
                "available": ha_available,
                "entities": dict(self.ha.entities),
            },
            "relay_home": {
                "configured_urls": self.relay.base_urls,
                "active_url": self.relay.active_base_url,
                "available": relay_available,
            },
        }
    
    def _first_unavailable_ha_devices(
        self,
        commands: list[RoutedCommand],
    ) -> list[str]:
        """
        Return devices whose HA entities are unavailable/unknown.

        Why:
        If HA is alive but a target entity is unavailable, the problem is the
        physical target/integration, not Home Assistant itself.
        """
        unavailable: list[str] = []

        for command in commands:
            for device in self._expand_devices(command.devices):
                if self.ha.entity_is_unavailable(device):
                    if device not in unavailable:
                        unavailable.append(device)

        return unavailable
    
    def _ha_entity_unavailable_response(self, devices: list[str]) -> str:
        """
        Build a truthful response when HA is reachable but the target HA
        entity/entities are unavailable.

        """
        clean_devices = self._expand_devices(devices)

        if not clean_devices:
            return (
                "I can reach Home Assistant, but the target device is unavailable."
            )

        relay_backed_devices = {
            "tv",
            "soundbar",
            "subwoofer",
            "rear",
        }

        all_are_relay_backed = all(
            device in relay_backed_devices
            for device in clean_devices
        )

        target_name = self._friendly_target_name(clean_devices)

        if all_are_relay_backed:
            return (
                f"I can reach Home Assistant, but I can't reach the smart relay board "
                f"for {target_name}."
            )

        labels = [
            self.devices[device]["label"]
            for device in clean_devices
            if device in self.devices
        ]

        if not labels:
            return (
                "I can reach Home Assistant, but the target device is unavailable."
            )

        if len(labels) == 1:
            label = labels[0]
            plural = self.devices[clean_devices[0]].get("plural", False)
            verb = "are" if plural else "is"

            return (
                f"I can reach Home Assistant, but {label} {verb} unavailable right now."
            )

        joined = self._join_sentence_parts(labels)

        return (
            f"I can reach Home Assistant, but these devices are unavailable right now: "
            f"{joined}."
        )
    
    def _friendly_target_name(self, devices: list[str]) -> str:
        """
        Convert device sets into natural group names.

        Why:
        If the user asked for a grouped command, Zyra should say
        'sound system' or 'home theater' instead of listing every relay.
        """
        clean = set(devices)

        if clean == {"tv"}:
            return "the TV"

        if clean == {"soundbar"}:
            return "the soundbar"

        if clean == {"subwoofer"}:
            return "the subwoofer"

        if clean == {"rear"}:
            return "the rear speakers"

        if clean == {"soundbar", "subwoofer"}:
            return "the sound system"

        if clean == {"soundbar", "subwoofer", "rear"}:
            return "the speaker system"

        if clean == {"tv", "soundbar", "subwoofer", "rear"}:
            return "the home theater system"

        # Mixed custom set fallback.
        names = self._format_device_list(devices)

        if not names:
            return "the target device"

        return names

    # ── Control handling ───────────────────────────

    def _handle_control(self, routed: RoutedIntent) -> SmartHomeResult:
        commands = self._commands_from_routed_intent(routed)

        if not commands:
            return SmartHomeResult(
                True,
                "I understood it as a smart-home command, but no valid device was found.",
            )

        # Mode 1: try Home Assistant if the HA hub is reachable.
        ha_available = self.ha.is_available()

        if ha_available:
            if self._execute_commands_on_backend(
                backend=SmartHomeBackend.HOME_ASSISTANT,
                commands=commands,
            ):
                self.active_backend = SmartHomeBackend.HOME_ASSISTANT
                return SmartHomeResult(
                    True,
                    self._control_success_response(commands),
                    action=commands[0].action,
                    devices=commands[0].devices,
                    backend=self.active_backend.value,
                )

            # HA hub is reachable, but command failed.
            # This usually means the requested HA entity/device is unavailable.
            failed_devices = self._first_unavailable_ha_devices(commands)

            if failed_devices:
                self.active_backend = SmartHomeBackend.HOME_ASSISTANT
                return SmartHomeResult(
                    True,
                    self._ha_entity_unavailable_response(failed_devices),
                    action=commands[0].action,
                    devices=failed_devices,
                    backend=self.active_backend.value,
                    success=False,
                )

            logger.warning(
                "Home Assistant hub is reachable, but HA command path failed. Trying relay fallback."
            )

        # Mode 2: fallback to ESP8266 home IP.
        if self._execute_commands_on_backend(
            backend=SmartHomeBackend.RELAY_HOME,
            commands=commands,
        ):
            self.active_backend = SmartHomeBackend.RELAY_HOME
            self.active_base_url = self.relay.active_base_url

            return SmartHomeResult(
                True,
                self._control_success_response(
                    commands,
                    fallback=True,
                ),
                action=commands[0].action,
                devices=commands[0].devices,
                backend=self.active_backend.value,
            )

        self.active_backend = SmartHomeBackend.NONE

        if ha_available:
            response = (
                "I can reach Home Assistant, but smart relay board is unreachable "
                "and the target device command failed."
            )
        else:
            response = (
                "I understood the device command, but Home Assistant and the smart relay board "
                "are both unreachable."
            )

        return SmartHomeResult(
            True,
            response,
            action=commands[0].action,
            devices=commands[0].devices,
            backend=self.active_backend.value,
            success=False,
        )

    def _execute_commands_on_backend(
        self,
        backend: SmartHomeBackend,
        commands: list[RoutedCommand],
    ) -> bool:
        for command in commands:
            devices = self._expand_devices(command.devices)

            for device in devices:
                if backend == SmartHomeBackend.HOME_ASSISTANT:
                    ok = self.ha.set_device(device, command.action)
                elif backend == SmartHomeBackend.RELAY_HOME:
                    ok = self.relay.set_device(device, command.action)
                else:
                    ok = False

                if not ok:
                    logger.error(
                        "Smart-home command failed on %s: %s %s",
                        backend.value,
                        device,
                        command.action,
                    )
                    return False

        return True

    def _commands_from_routed_intent(
        self,
        routed: RoutedIntent,
    ) -> list[RoutedCommand]:
        commands = list(routed.commands or [])

        if not commands and routed.action and routed.devices:
            commands = [
                RoutedCommand(
                    action=routed.action,
                    devices=routed.devices,
                )
            ]

        clean_commands: list[RoutedCommand] = []

        for command in commands:
            if command.action not in {"on", "off", "toggle"}:
                continue

            devices = self._expand_devices(command.devices)

            if not devices:
                continue

            clean_commands.append(
                RoutedCommand(
                    action=command.action,
                    devices=devices,
                )
            )

        return clean_commands

    # ── Status handling ────────────────────────────

    def _handle_status(self, requested_devices: list[str]) -> SmartHomeResult:
        status = self.get_status()

        if status is None:
            return SmartHomeResult(
                True,
                "I could not read the smart-home status. Home Assistant and the relay board are both unreachable.",
                backend=SmartHomeBackend.NONE.value,
                success=False,
            )

        devices = self._expand_devices(requested_devices or ["all"])
        response = self._status_response(status, devices)

        return SmartHomeResult(
            True,
            response,
            action="status",
            devices=devices,
            backend=self.active_backend.value,
        )

    def _status_response(
        self,
        status: dict[str, Optional[bool]],
        devices: list[str],
    ) -> str:
        if not devices:
            devices = list(self.DEVICE_ORDER)

        unknown_devices = [
            device for device in devices
            if status.get(device) is None
        ]

        on_devices = [
            device for device in devices
            if status.get(device) is True
        ]

        off_devices = [
            device for device in devices
            if status.get(device) is False
        ]

        if len(devices) == 1:
            device = devices[0]
            label = self.devices[device]["label"]
            plural = self.devices[device]["plural"]
            state = status.get(device)

            if state is True:
                verb = "are" if plural else "is"
                return f"{label} {verb} on."

            if state is False:
                verb = "are" if plural else "is"
                return f"{label} {verb} off."

            return f"I could not read {label}."

        if on_devices:
            names = self._format_device_list(on_devices)
            base = f"{names} are on."
        else:
            base = "All devices are off."

        if off_devices and on_devices:
            off_names = self._format_device_list(off_devices)
            base += f" {off_names} are off."

        if unknown_devices:
            unknown_names = self._format_device_list(unknown_devices)
            base += f" I could not read {unknown_names}."

        return base

    # ── Helpers ────────────────────────────────────

    def _expand_devices(self, devices: list[str]) -> list[str]:
        if not devices or "all" in devices:
            return list(self.DEVICE_ORDER)

        clean: list[str] = []

        for device in devices:
            if device in self.DEVICE_ORDER and device not in clean:
                clean.append(device)

        return clean

    def _control_success_response(
        self,
        commands: list[RoutedCommand],
        fallback: bool = False,
    ) -> str:
        parts: list[str] = []

        for command in commands:
            devices = self._expand_devices(command.devices)
            names = self._format_device_list(devices)

            if command.action == "on":
                parts.append(f"turned on {names}")
            elif command.action == "off":
                parts.append(f"turned off {names}")
            elif command.action == "toggle":
                parts.append(f"toggled {names}")

        if not parts:
            return "Done."

        response = "Done, I " + self._join_sentence_parts(parts) + "."

        if fallback:
            response += " Home Assistant was unavailable, so I used direct relay control."

        return response

    def _format_device_list(self, device_ids: list[str]) -> str:
        labels = [
            self.devices[device_id]["label"]
            for device_id in device_ids
            if device_id in self.devices
        ]
        return self._join_sentence_parts(labels)

    def _join_sentence_parts(self, parts: list[str]) -> str:
        if not parts:
            return ""

        if len(parts) == 1:
            return parts[0]

        if len(parts) == 2:
            return f"{parts[0]} and {parts[1]}"

        return ", ".join(parts[:-1]) + f", and {parts[-1]}"