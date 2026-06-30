import logging
import time
from dataclasses import dataclass
from typing import Optional

from config import (
    HOME_ASSISTANT_URL,
    HOME_ASSISTANT_TOKEN,
    HOME_ASSISTANT_TIMEOUT_SEC,
    HOME_ASSISTANT_ENTITIES,
    RELAY_HOME_BASE_URLS,
    RELAY_HTTP_TIMEOUT_SEC,
    DEVICE_REGISTRY_PATH,
)

from home_assistant_client import HomeAssistantClient
from relay_http_client import RelayHttpClient
from smart_home_backends import SmartHomeBackend
from device_registry import DeviceRegistry
from ha_service_mapper import HAServiceMapper

logger = logging.getLogger(__name__)


@dataclass
class SmartHomeResult:
    handled: bool
    response: str
    action: str = ""
    devices: Optional[list[str]] = None
    backend: str = ""
    success: bool = True
    spoken_action: str = ""
    spoken_target: str = ""
    backend_note: str = ""

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

        self.registry = DeviceRegistry(DEVICE_REGISTRY_PATH)
        self.ha_service_mapper = HAServiceMapper()

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
    
    def _get_attr(self, state_obj: dict | None, key: str):
        if not state_obj:
            return None

        attrs = state_obj.get("attributes", {})

        if not isinstance(attrs, dict):
            return None

        return attrs.get(key)

    def _state_matches_power_action(
        self,
        state_obj: dict | None,
        action: str,
    ) -> bool:
        if not state_obj:
            return False

        state = state_obj.get("state")

        if action in {"on", "turn_on", "power_on"}:
            return state not in {"off", "unavailable", "unknown", None}

        if action in {"off", "turn_off", "power_off"}:
            return state == "off"

        return False

    def _state_is_usable(self, state_obj: dict | None) -> bool:
        if not state_obj:
            return False

        state = state_obj.get("state")

        return state not in {
            None,
            "unavailable",
            "unknown",
        }

    def _float_attr(self, state_obj: dict | None, attr: str):
        if not state_obj:
            return None

        attrs = state_obj.get("attributes") or {}

        try:
            return float(attrs.get(attr))
        except Exception:
            return None

    def _string_attr(self, state_obj: dict | None, attr: str):
        if not state_obj:
            return None

        attrs = state_obj.get("attributes") or {}
        value = attrs.get(attr)

        if value is None:
            return None

        return str(value).strip()
    
    def _registry_action_requires_powered_device(self, action: str) -> bool:
        """
        Commands like volume/mute/source/playback are meaningful only when
        the physical device is actually on/usable.

        """

        return action in {
            "volume_up",
            "volume_down",
            "set_volume",
            "mute",
            "unmute",
            "mute_toggle",
            "play",
            "pause",
            "stop",
            "next",
            "previous",
            "select_source",
            "select_sound_mode",
            "play_media",
        }

    def _registry_readiness_entity_id(
        self,
        device_id: str,
        surface,
    ) -> str | None:
        """
        Return the entity that tells us whether the physical device is usable.

        Why:
        Some command surfaces do not represent device power.

        Example:
          TV mute uses switch.living_room_tv_mute.
          But that switch state is mute state, not TV power state.
          So readiness must be checked using media_player.living_room_tv.
        """

        readiness_map = {
            "living_room_tv": "media_player.living_room_tv",
            "tv": "media_player.living_room_tv",

            "lg_s95tr_soundbar": "media_player.lg_soundbar",
            "soundbar": "media_player.lg_soundbar",
        }

        if device_id in readiness_map:
            return readiness_map[device_id]

        # For media_player command surfaces, the surface itself can tell us
        # whether it is usable.
        if getattr(surface, "domain", None) == "media_player":
            return surface.entity_id

        return None

    def _registry_device_ready_for_action(
        self,
        device_id: str,
        surface,
        action: str,
    ) -> tuple[bool, str]:
        """
        Check if a device is ready before sending remote-style commands.

        Returns:
            (ready, reason)
        """

        if not self._registry_action_requires_powered_device(action):
            return True, "action does not require powered device"

        readiness_entity_id = self._registry_readiness_entity_id(
            device_id=device_id,
            surface=surface,
        )

        if not readiness_entity_id:
            # If we do not have a readiness entity, do not block.
            # Registry validation already proved the capability exists.
            return True, "no readiness entity configured"

        state_obj = self.ha.get_entity_state_object(readiness_entity_id)

        if not state_obj:
            return False, f"{readiness_entity_id} state unavailable"

        state = state_obj.get("state")

        if state in {None, "unavailable", "unknown"}:
            return False, f"{readiness_entity_id} is {state}"

        if state == "off":
            return False, f"{readiness_entity_id} is off"

        return True, f"{readiness_entity_id} is usable: {state}"

    def _verify_registry_command(
        self,
        device_id: str,
        surface,
        capability: str,
        action: str,
        value,
    ) -> bool:
        """
        Verify registry command execution.

        Important:
        Home Assistant service success does not always produce an immediately
        readable state/attribute change.

        For those, if the HA service call succeeded and the entity is still
        reachable after the command, we accept it instead of falsely saying
        "could not control".
        """

        import time

        entity_id = surface.entity_id
        domain = entity_id.split(".", 1)[0] if "." in entity_id else ""

        remote_event_actions = {
            "volume_up",
            "volume_down",
            "mute_toggle",
            "play",
            "pause",
            "stop",
            "next",
            "previous",
            "play_media",
        }

        for attempt in range(1, 7):
            state_obj = self.ha.get_entity_state_object(entity_id)

            if not state_obj:
                time.sleep(0.25)
                continue

            state = state_obj.get("state")

            if state in {None, "unavailable", "unknown"}:
                time.sleep(0.25)
                continue

            attrs = state_obj.get("attributes") or {}

            logger.info(
                "Registry verify attempt %d entity=%s domain=%s action=%s state=%s",
                attempt,
                entity_id,
                domain,
                action,
                state,
            )

            # ── Relay power verification ──────────────
            surface_capabilities = set(getattr(surface, "capabilities", []) or [])

            if (
                capability == "power"
                and domain == "switch"
                and "relay_power" in surface_capabilities
                and action in {"on", "off", "toggle"}
            ):
                logger.info(
                    "Accepting relay power command after successful HA service call: "
                    "device=%s entity=%s action=%s state_after_call=%s",
                    device_id,
                    entity_id,
                    action,
                    state,
                )
                return True

            # ── Strict relay/power verification ─────────────
            if action == "on":
                return state == "on"

            if action == "off":
                return state == "off"

            # ── Soft sleep/wake verification ────────────────
            if capability == "sleep_wake" and action == "wake":
                return state not in {
                    None,
                    "off",
                    "unavailable",
                    "unknown",
                }

            if capability == "sleep_wake" and action == "sleep":
                return state in {
                    "off",
                    "standby",
                }

            # For media_player control surfaces, "off" means the command target
            # is not currently usable for normal media/volume/source commands.
            if domain == "media_player" and state == "off":
                return False

            # ── set_volume: verify if attribute exists; otherwise accept usable entity ──
            if action == "set_volume":
                actual = attrs.get("volume_level")

                try:
                    expected_float = float(value)

                    # User says 30, HA stores 0.30
                    if expected_float > 1.0:
                        expected_float = expected_float / 100.0

                    expected_float = max(0.0, min(expected_float, 1.0))

                except Exception:
                    logger.warning(
                        "Invalid expected volume value: %s",
                        value,
                    )
                    return False

                if actual is None:
                    logger.warning(
                        "set_volume verification failed: %s has no volume_level attribute",
                        entity_id,
                    )
                    return False

                try:
                    actual_float = float(actual)
                except Exception:
                    logger.warning(
                        "set_volume verification failed: invalid volume_level=%s entity=%s",
                        actual,
                        entity_id,
                    )
                    return False

                if abs(actual_float - expected_float) <= 0.05:
                    return True

                logger.warning(
                    "set_volume verification mismatch: entity=%s actual=%s expected=%s",
                    entity_id,
                    actual_float,
                    expected_float,
                )

                return False

            # ── mute/unmute ───────────────────────────
            if action in {"mute", "unmute"}:
                if domain == "switch":
                    # TV mute switch state means mute state, not device power.
                    return True

                # LG soundbar mute/unmute physically works, but Home Assistant
                # may not update is_volume_muted reliably or immediately.
                if entity_id == "media_player.lg_soundbar":
                    logger.info(
                        "Accepting LG soundbar %s because service succeeded and entity is usable",
                        action,
                    )
                    return True

                actual = attrs.get("is_volume_muted")

                if actual is None:
                    return True

                if action == "mute":
                    return actual is True

                if action == "unmute":
                    return actual is False

            # ── source selection ──────────────────────
            if action == "select_source":
                actual = attrs.get("source")

                if actual is None:
                    return True

                if str(actual).strip().lower() == str(value).strip().lower():
                    return True

                # Do not instantly fail if HA attribute is delayed.
                # The pre-check already confirmed device was usable.
                return True

            # ── sound mode selection ──────────────────
            if action == "select_sound_mode":
                actual = attrs.get("sound_mode")

                if actual is None:
                    return True

                if str(actual).strip().lower() == str(value).strip().lower():
                    return True

                # LG integrations may delay/normalize sound_mode values.
                return True

            # ── Remote/event actions ──────────────────
            if action in remote_event_actions:
                return True

            time.sleep(0.25)

        logger.warning(
            "Registry verification failed after retries: entity=%s action=%s value=%s",
            entity_id,
            action,
            value,
        )

        return False
    
    def _registry_find_surface_by_entity_id(
        self,
        device_id: str,
        entity_id: str,
    ):
        """
        Find a specific control surface for a physical device.

        Why:
        Some devices have multiple Home Assistant surfaces.

        Example:
          TV volume up/down -> media_player.tv_remote
          TV set volume     -> media_player.google_tv_cast
          TV source select   -> media_player.living_room_tv
        """

        device = self.registry.devices.get(device_id)

        if not device:
            return None

        surfaces = getattr(device, "surfaces", [])

        if isinstance(surfaces, dict):
            surface_list = surfaces.values()
        else:
            surface_list = surfaces

        for surface in surface_list:
            if getattr(surface, "entity_id", None) == entity_id:
                return surface

        return None

    def _registry_preferred_surface_for_action(
        self,
        device_id: str,
        capability: str,
        action: str,
        surface,
    ):
        """
        Select the best Home Assistant surface for a specific action.

        Why:
        A single capability like "volume" is too broad.

        For TV:
          volume_up / volume_down -> media_player.tv_remote
          set_volume              -> media_player.google_tv_cast

        For soundbar:
          all volume actions stay on media_player.lg_soundbar.
        """

        # TV absolute volume works through Google Cast.
        if (
            device_id in {"living_room_tv", "tv"}
            and capability == "volume"
            and action == "set_volume"
        ):
            cast_surface = self._registry_find_surface_by_entity_id(
                device_id=device_id,
                entity_id="media_player.google_tv_cast",
            )

            if cast_surface:
                logger.info(
                    "Registry surface override: TV set_volume uses %s instead of %s",
                    cast_surface.entity_id,
                    surface.entity_id,
                )
                return cast_surface

        # TV step volume works through TV remote.
        if (
            device_id in {"living_room_tv", "tv"}
            and capability == "volume"
            and action in {"volume_up", "volume_down"}
        ):
            remote_surface = self._registry_find_surface_by_entity_id(
                device_id=device_id,
                entity_id="media_player.tv_remote",
            )

            if remote_surface:
                logger.info(
                    "Registry surface override: TV step volume uses %s instead of %s",
                    remote_surface.entity_id,
                    surface.entity_id,
                )
                return remote_surface

        return surface
    
    def _execute_tv_set_volume_by_steps(
        self,
        surface,
        value,
    ) -> bool:
        """
        TV absolute volume set using step volume.

        Why:
        Sony TV absolute media_player.volume_set is not working correctly.
        But media_player.tv_remote volume_up / volume_down works reliably.

        So:
          set TV volume to 50

        becomes:
          current volume = 35
          difference = 15
          send volume_up 15 times
        """

        import time

        entity_id = surface.entity_id

        try:
            raw_target = float(value)
        except Exception:
            logger.error("TV stepped set-volume failed: invalid target value=%s", value)
            return False

        # Accept both 50 and 0.50 style values.
        if raw_target <= 1.0:
            target_percent = round(raw_target * 100)
        else:
            target_percent = round(raw_target)

        target_percent = max(0, min(target_percent, 100))

        state_obj = self.ha.get_entity_state_object(entity_id)

        if not state_obj:
            logger.error("TV stepped set-volume failed: no state for %s", entity_id)
            return False

        state = state_obj.get("state")

        if state in {None, "off", "unavailable", "unknown"}:
            logger.error(
                "TV stepped set-volume blocked: %s state=%s",
                entity_id,
                state,
            )
            return False

        attrs = state_obj.get("attributes") or {}
        current_level = attrs.get("volume_level")

        if current_level is None:
            logger.error(
                "TV stepped set-volume failed: %s has no volume_level attribute",
                entity_id,
            )
            return False

        try:
            current_percent = round(float(current_level) * 100)
        except Exception:
            logger.error(
                "TV stepped set-volume failed: invalid volume_level=%s entity=%s",
                current_level,
                entity_id,
            )
            return False

        delta = target_percent - current_percent

        logger.info(
            "TV stepped set-volume: entity=%s current=%s target=%s delta=%s",
            entity_id,
            current_percent,
            target_percent,
            delta,
        )

        if abs(delta) <= 1:
            logger.info("TV stepped set-volume skipped: already close enough")
            return True

        service = "volume_up" if delta > 0 else "volume_down"
        steps = abs(delta)

        # Safety cap.
        # Why:
        # If HA reports a wrong current volume, we should not spam too many commands.
        steps = min(steps, 50)

        for i in range(steps):
            ok = self.ha.call_service(
                domain="media_player",
                service=service,
                data={"entity_id": entity_id},
            )

            if not ok:
                logger.error(
                    "TV stepped set-volume failed at step %s/%s using %s",
                    i + 1,
                    steps,
                    service,
                )
                return False

            time.sleep(0.08)

        # Optional final check.
        # Do not make this too strict because some TV integrations update
        # volume_level slowly after remote button presses.
        final_state = self.ha.get_entity_state_object(entity_id)

        if final_state:
            final_attrs = final_state.get("attributes") or {}
            final_level = final_attrs.get("volume_level")

            try:
                if final_level is not None:
                    final_percent = round(float(final_level) * 100)

                    logger.info(
                        "TV stepped set-volume final check: final=%s target=%s",
                        final_percent,
                        target_percent,
                    )
            except Exception:
                pass

        return True
    
    def _execute_tv_relative_volume_steps(
        self,
        surface,
        action: str,
        value,
    ) -> bool:
        """

        Execute TV relative volume by a specific number of steps.

        Why:
        media_player.tv_remote supports reliable volume_up / volume_down.
        For commands like:
          increase TV volume by 5
          decrease TV volume by 3

        we should press the remote volume button exactly that many times.
        
        """

        import time

        entity_id = surface.entity_id

        if action not in {"volume_up", "volume_down"}:
            logger.error(
                "TV relative volume got unsupported action=%s",
                action,
            )
            return False

        try:
            steps = int(float(value))
        except Exception:
            logger.error(
                "TV relative volume failed: invalid step value=%s",
                value,
            )
            return False

        steps = abs(steps)

        if steps <= 0:
            logger.warning(
                "TV relative volume ignored because steps=%s",
                steps,
            )
            return False

        # Safety cap.
        steps = min(steps, 50)

        state_obj = self.ha.get_entity_state_object(entity_id)

        if not state_obj:
            logger.error(
                "TV relative volume failed: no state for %s",
                entity_id,
            )
            return False

        state = state_obj.get("state")

        if state in {None, "off", "unavailable", "unknown"}:
            logger.error(
                "TV relative volume blocked: %s state=%s",
                entity_id,
                state,
            )
            return False

        service = "volume_up" if action == "volume_up" else "volume_down"

        logger.info(
            "TV relative volume started: entity=%s service=%s requested_steps=%s",
            entity_id,
            service,
            steps,
        )

        for i in range(steps):
            ok = self.ha.call_service(
                domain="media_player",
                service=service,
                data={"entity_id": entity_id},
            )

            if not ok:
                logger.error(
                    "TV relative volume failed at step %s/%s using %s",
                    i + 1,
                    steps,
                    service,
                )
                return False

            logger.info(
                "TV relative volume sent step %s/%s using %s",
                i + 1,
                steps,
                service,
            )

            # Important:
            # 0.08 seconds is too fast. Sony/HA can accept the HTTP call but
            # still drop the actual remote button event.
            time.sleep(0.45)

        logger.info(
            "TV relative volume completed: entity=%s service=%s steps=%s",
            entity_id,
            service,
            steps,
        )

        return True
    
    def _get_registry_surface_state(
        self,
        device_id: str,
        entity_id: str,
    ) -> dict | None:
        """
        Read one Home Assistant entity state object by entity_id.

        Why:
        Registry status needs real HA attributes like:
          source
          sound_mode
          volume_level
          is_volume_muted
        """

        state_obj = self.ha.get_entity_state_object(entity_id)

        if not state_obj:
            logger.warning(
                "Registry status could not read entity: device=%s entity=%s",
                device_id,
                entity_id,
            )
            return None

        return state_obj

    def _volume_percent_from_attrs(self, attrs: dict) -> int | None:
        raw = attrs.get("volume_level")

        if raw is None:
            return None

        try:
            return round(float(raw) * 100)
        except Exception:
            return None

    def _read_registry_entity(
        self,
        entity_id: str,
    ) -> tuple[str | None, dict]:
        state_obj = self.ha.get_entity_state_object(entity_id)

        if not state_obj:
            return None, {}

        return state_obj.get("state"), state_obj.get("attributes") or {}

    def _media_power_text(self, state: str | None) -> str:
        if state in {None, "unavailable", "unknown"}:
            return "unavailable"

        if state == "off":
            return "off"

        return "on"
    
    def _relay_power_state_for_registry_device(
        self,
        device_id: str,
        relay_states: dict[str, bool | None] | None,
    ) -> bool | None:
        """
        Return relay-board power state for a registry device.

        Why:
        If relay is ON but media_player is off, the device is not physically off.
        It is powered but sleeping.

        """

        if not relay_states:
            return None

        relay_id = self._registry_relay_device_id(device_id)

        if not relay_id:
            return None

        return relay_states.get(relay_id)

    def _media_power_text_with_relay(
        self,
        device_id: str,
        media_state: str | None,
        relay_states: dict[str, bool | None] | None,
    ) -> str:
        """
        Build correct power text using relay state + smart media state.

        Why:
        media_player state="off" for Sony/LG usually means sleep/standby.
        But if relay board says power is still ON, ZYRA should not say
        the physical device is off.

        Examples:
            relay ON + media off  -> on sleep
            relay ON + media on   -> on
            relay OFF             -> off
        """

        relay_power = self._relay_power_state_for_registry_device(
            device_id=device_id,
            relay_states=relay_states,
        )

        if relay_power is True:
            if media_state == "off":
                return "on sleep"

            if media_state in {None, "unavailable", "unknown"}:
                return "on, but smart status is unavailable"

            return "on"

        if relay_power is False:
            return "off"

        # Fallback when relay status cannot be read.
        return self._media_power_text(media_state)

    def _media_playing_text(
        self,
        state: str | None,
        attrs: dict,
        allow_source_as_app: bool = False,
    ) -> str:
        """
        Return human-friendly media/app status.

        Why:

        For TV:
          app_name / media_title can identify Netflix, Hotstar, YouTube, etc.

        For soundbar:
          source like E-ARC, HDMI, Bluetooth is only an input source.
          It should NOT be treated as "playing".

        So source is used as media/app only when explicitly allowed.
        """

        if state in {None, "off", "unavailable", "unknown"}:
            return "idle"

        app_name = (
            attrs.get("app_name")
            or attrs.get("app_id")
        )

        if allow_source_as_app:
            app_name = app_name or attrs.get("source")

        media_title = (
            attrs.get("media_title")
            or attrs.get("media_series_title")
            or attrs.get("media_album_name")
            or attrs.get("media_channel")
        )

        media_artist = attrs.get("media_artist")
        media_content_type = attrs.get("media_content_type")

        if media_title and media_artist:
            return f"{media_title} by {media_artist}"

        if media_title and app_name:
            return f"{media_title} on {app_name}"

        if media_title:
            return str(media_title)

        if app_name:
            app_text = str(app_name).strip()

            passive_sources = {
                "tv",
                "hdmi",
                "hdmi 1",
                "hdmi 2",
                "hdmi 3",
                "hdmi 4",
                "e-arc",
                "earc",
                "arc",
                "optical",
                "optical/hdmi arc",
                "airplay",
                "air play",
                "audio system",
                "satellite",
                "bluetooth",
                "usb",
                "usb2",
                "wi-fi",
                "wifi",
            }

            if app_text.lower() not in passive_sources:
                return app_text

        # Only say "playing" or "paused" when HA actually reports that state.
        if state in {"playing", "paused"}:
            if media_content_type:
                return str(media_content_type)
            return state

        return "idle"

    def _registry_status_for_tv(
        self,
        device_id: str,
        detail: str = "full",
        relay_states: dict[str, bool | None] | None = None,
    ) -> str:
        device = self.registry.get_device(device_id)
        name = self._registry_spoken_device_name(device_id) if device else "Sony TV"

        native_state, native_attrs = self._read_registry_entity(
            "media_player.living_room_tv"
        )

        cast_state, cast_attrs = self._read_registry_entity(
            "media_player.google_tv_cast"
        )

        power_text = self._media_power_text_with_relay(
            device_id=device_id,
            media_state=native_state,
            relay_states=relay_states,
        )
        source = native_attrs.get("source")

        # For "what is playing", cast entity often has better app/media data.
        native_playing = self._media_playing_text(
            native_state,
            native_attrs,
            allow_source_as_app=True,
        )

        cast_playing = self._media_playing_text(
            cast_state,
            cast_attrs,
            allow_source_as_app=True,
        )

        if cast_playing != "idle":
            playing_text = cast_playing
        else:
            playing_text = native_playing

        if detail == "power":
            return f"{name} is {power_text}."

        if detail == "source":
            if source:
                return f"{name} input is {source}."
            return f"I could not read the current input for {name}."

        if detail == "media":
            if playing_text == "idle":
                return f"Nothing is playing on {name} right now."
            return f"{name} is playing {playing_text}."

        if detail == "volume":
            # TV absolute volume is not reliable in your setup.
            return f"I cannot reliably read the absolute volume level from {name}."

        # Full TV status.
        #
        # Do not include mute.
        # Do not include absolute TV volume.
        parts = [
            f"{name} is {power_text}",
        ]

        if source:
            parts.append(f"input is {source}")

        if playing_text == "idle":
            parts.append("nothing is playing")
        else:
            parts.append(f"playing {playing_text}")

        return ". ".join(parts) + "."

    def _registry_status_for_soundbar(
        self,
        device_id: str,
        detail: str = "full",
        relay_states: dict[str, bool | None] | None = None,
    ) -> str:
        device = self.registry.get_device(device_id)
        name = self._registry_spoken_device_name(device_id) if device else "LG Soundbar"

        state, attrs = self._read_registry_entity("media_player.lg_soundbar")
        cast_state, cast_attrs = self._read_registry_entity(
            "media_player.lg_speaker_cast"
        )

        power_text = self._media_power_text_with_relay(
            device_id=device_id,
            media_state=state,
            relay_states=relay_states,
        )
        source = attrs.get("source")
        sound_mode = attrs.get("sound_mode")
        volume = self._volume_percent_from_attrs(attrs)

        native_playing = self._media_playing_text(
            state,
            attrs,
            allow_source_as_app=False,
        )

        cast_playing = self._media_playing_text(
            cast_state,
            cast_attrs,
            allow_source_as_app=False,
        )
        # media_player.lg_speaker_cast is where Spotify/Google Cast media
        # title/source should appear.
        if cast_playing != "idle":
            playing_text = cast_playing
        else:
            playing_text = native_playing

        if detail == "power":
            return f"{name} is {power_text}."

        if detail == "source":
            if source:
                return f"{name} input is {source}."
            return f"I could not read the current input for {name}."

        if detail == "sound_mode":
            if sound_mode:
                return f"{name} sound mode is {sound_mode}."
            return f"I could not read the current sound mode for {name}."

        if detail == "volume":
            if volume is not None:
                return f"{name} volume is {volume} percent."
            return f"I could not read the volume level for {name}."

        if detail == "media":
            if playing_text == "idle":
                return f"Nothing is playing on {name} right now."
            return f"{name} is playing {playing_text}."

        # Full soundbar status.
        #
        # Include volume for soundbar because HA reports it properly.
        # Do not include mute unless user asks later; it is not useful in full status.
        parts = [
            f"{name} is {power_text}",
        ]

        if source:
            parts.append(f"input is {source}")

        if sound_mode:
            parts.append(f"sound mode is {sound_mode}")

        if volume is not None:
            parts.append(f"volume is {volume} percent")

        if playing_text != "idle":
            parts.append(f"playing {playing_text}")

        return ". ".join(parts) + "."

    def _registry_status_for_relay_device(
        self,
        device_id: str,
        detail: str = "full",
    ) -> str:
        device = self.registry.get_device(device_id)

        if not device:
            return f"I could not find {device_id} in the registry."

        name = device.name
        detail = (detail or "full").strip().lower()

        # Relay-only devices only expose power/on-off status.
        #
        # Why:
        # Subwoofer and rear speakers are controlled through relay switches.
        # They do not expose volume, input, playback, mute, or sound mode.
        if detail not in {"full", "power"}:
            return f"{name} only supports power status."

        state, _ = self._read_registry_entity(device.primary_entity)

        if state == "on":
            return f"{name} is on."

        if state == "off":
            return f"{name} is off."

        if state in {None, "unavailable", "unknown"}:
            return f"{name} is unavailable."

        return f"{name} state is {state}."

    def _execute_registry_status(
        self,
        spoken_target: str,
        detail=None,
    ) -> SmartHomeResult:
        """
        Detailed registry-aware status.

        detail can be:
          full
          power
          source
          media
          volume
          sound_mode
        """

        detail = str(detail or "full").strip().lower()

        if detail not in {"full", "power", "source", "media", "volume", "sound_mode"}:
            detail = "full"

        if not self.ha.is_available():
            return self._execute_registry_relay_status_fallback(
                spoken_target=spoken_target,
                detail=detail,
            )

        device_ids = self.registry.resolve_target(spoken_target)

        if not device_ids:
            return SmartHomeResult(
                handled=True,
                response=f"I could not find {spoken_target} in the device registry.",
                action="status",
                devices=[],
                backend=SmartHomeBackend.HOME_ASSISTANT.value,
                success=False,
            )
        
        relay_states = self.relay.get_status()

        responses: list[str] = []

        for device_id in device_ids:
            if device_id == "living_room_tv":
                responses.append(
                    self._registry_status_for_tv(
                        device_id=device_id,
                        detail=detail,
                        relay_states=relay_states,
                    )
                )

            elif device_id == "lg_s95tr_soundbar":
                responses.append(
                    self._registry_status_for_soundbar(
                        device_id=device_id,
                        detail=detail,
                        relay_states=relay_states,
                    )
                )

            else:
                responses.append(
                    self._registry_status_for_relay_device(
                        device_id=device_id,
                        detail=detail,
                    )
                )

        return SmartHomeResult(
            handled=True,
            response=" ".join(responses),
            action="status",
            devices=device_ids,
            backend=SmartHomeBackend.HOME_ASSISTANT.value,
            success=True,
        )
    
    def _tv_app_activity_map(self) -> dict[str, dict]:
        """
        Known Android TV app launch activities.

        Why:
        Android TV Remote launching is not consistent across apps.

        Some apps open with package names.
        Some apps open better with app links or deeplinks.
        So each app keeps multiple launch attempts in priority order.
        """

        return {
            "netflix": {
                "name": "Netflix",
                "activities": [
                    "com.netflix.ninja",
                    "https://www.netflix.com",
                    "netflix://",
                ],
            },

            "prime video": {
                "name": "Prime Video",
                "activities": [
                    "https://app.primevideo.com",
                    "com.amazon.amazonvideo.livingroom",
                ],
            },

            "jio hotstar": {
                "name": "Jio Hotstar",
                "activities": [
                    "in.startv.hotstar",
                    "hotstar://",
                    "https://www.hotstar.com",
                ],
            },

            "youtube": {
                "name": "YouTube",
                "activities": [
                    "com.google.android.youtube.tv",
                    "https://www.youtube.com",
                    "vnd.youtube://",
                    "vnd.youtube.launch://",
                ],
            },

            "sony liv": {
                "name": "Sony LIV",
                "activities": [
                    "https://www.sonyliv.com",
                    "sonyliv://",
                    "market://launch?id=com.sonyliv",
                    "com.sonyliv",
                ],
            },

            "sony pictures core": {
                "name": "Sony Pictures Core",
                "activities": [
                    "com.sonypicturescore",
                    "https://www.sonypicturescore.com",
                    "sonypicturescore://",
                    "braviacore://",
                    "market://launch?id=com.sonypicturescore",
                ],
            },

            "spotify": {
                "name": "Spotify",
                "activities": [
                    "com.spotify.tv.android",
                    "spotify://",
                    "https://open.spotify.com",
                    "market://launch?id=com.spotify.tv.android",
                ],
            },

            "amazon music": {
                "name": "Amazon Music",
                "activities": [
                    "com.amazon.music.tv",
                    "com.amazon.bueller.music",
                    "com.amazon.mp3",
                    "amazonmusic://",
                    "https://music.amazon.in",
                    "market://launch?id=com.amazon.music.tv",
                    "market://launch?id=com.amazon.mp3",
                ],
            },

            "xstream play": {
                "name": "Xstream Play",
                "activities": [
                    "tv.airtel.xstream.tvapp",
                    "airtelxstream://",
                    "https://www.airtelxstream.in",
                    "market://launch?id=tv.airtel.xstream.tvapp",
                ],
            },

            "zee5": {
                "name": "ZEE5",
                "activities": [
                    "com.graymatrix.did",
                    "zee5://",
                    "https://www.zee5.com",
                ],
            },
        }

    def _normalize_tv_app_request(self, value) -> dict | None:
        """
        Convert spoken app names into app launch config.
        """

        raw = str(value or "").strip().lower()

        if not raw:
            return None

        aliases = {
            "netflix": "netflix",

            "prime": "prime video",
            "prime video": "prime video",
            "amazon prime": "prime video",
            "amazon prime video": "prime video",

            "hotstar": "jio hotstar",
            "jio hotstar": "jio hotstar",
            "jiohotstar": "jio hotstar",
            "disney hotstar": "jio hotstar",
            "disney plus hotstar": "jio hotstar",

            "youtube": "youtube",
            "you tube": "youtube",

            "sony liv": "sony liv",
            "sonyliv": "sony liv",

            "zee5": "zee5",
            "zee five": "zee5",
        }

        key = aliases.get(raw, raw)
        return self._tv_app_activity_map().get(key)
    
    def _execute_tv_home(self) -> tuple[bool, str]:
        """
        Send the Android TV HOME command.

        Why:
        Google TV home is not an app URL.
        It is the system launcher screen, so the correct action is pressing
        the TV remote HOME button through Home Assistant.
        """

        surface = self.registry.resolve_surface("living_room_tv", "app")

        if not surface:
            return False, "TV home control is not configured."

        remote_entity_id = surface.entity_id

        logger.info(
            "Sending TV HOME command: remote=%s",
            remote_entity_id,
        )

        ok = self.ha.call_service(
            domain="remote",
            service="send_command",
            data={
                "entity_id": remote_entity_id,
                "command": "HOME",
            },
        )

        if not ok:
            return False, "I could not open the TV home screen."

        return True, "Opened TV home screen."

    def _execute_tv_launch_app(self, value) -> tuple[bool, str]:
        """
        Launch a TV app through Home Assistant Android TV Remote.

        Why:
        Your setup does not support:
          androidtv.adb_command
          media_player.play_media on media_player.living_room_tv

        But it does support:
          remote.turn_on
          remote.living_room_tv
          activity: package/deeplink
        """

        import time

        app = self._normalize_tv_app_request(value)

        if not app:
            return False, f"I do not know how to open {value} on the TV yet."

        tv_entity_id = "media_player.living_room_tv"
        remote_entity_id = "remote.living_room_tv"

        app_name = app["name"]
        activities = app.get("activities") or []

        # Backward compatibility if one app still uses old single activity format.
        if not activities and app.get("activity"):
            activities = [app["activity"]]

        if not activities:
            return False, f"I do not know how to open {app_name} on the TV yet."

        state_obj = self.ha.get_entity_state_object(tv_entity_id)

        if not state_obj:
            return False, "I could not read the TV state."

        state = state_obj.get("state")

        if state in {None, "unavailable", "unknown"}:
            return False, "The TV is unavailable right now."

        # If TV is off, wake it first.
        if state == "off":
            logger.info("TV is off. Turning it on before opening %s.", app_name)

            turned_on = self.ha.call_service(
                domain="media_player",
                service="turn_on",
                data={"entity_id": tv_entity_id},
            )

            if not turned_on:
                return False, "I could not turn on the TV before opening the app."

            # Give Android TV time to wake.
            time.sleep(4.0)

        last_activity = ""

        for activity in activities:
            last_activity = activity

            logger.info(
                "Launching TV app: app=%s remote=%s activity=%s",
                app_name,
                remote_entity_id,
                activity,
            )

            ok = self.ha.call_service(
                domain="remote",
                service="turn_on",
                data={
                    "entity_id": remote_entity_id,
                    "activity": activity,
                },
            )

            if ok:
                # Give the TV UI time to switch.
                time.sleep(1.5)
                return True, f"Opened {app_name} on Sony TV."

            logger.warning(
                "TV app launch attempt failed: app=%s activity=%s",
                app_name,
                activity,
            )

        return False, f"I could not open {app_name} on the TV."
    
    def _registry_relay_device_id(self, device_id: str) -> str:
        """
        Convert registry device IDs to ESP8266 relay IDs.

        Why:
        The new registry uses physical-device IDs:
            living_room_tv
            lg_s95tr_soundbar
            subwoofer
            rear_speakers

        RelayHttpClient uses relay-board IDs:
            tv
            soundbar
            subwoofer
            rear

        This bridge keeps the new registry router independent from the old
        intent_router.py.
        """

        mapping = {
            "living_room_tv": "tv",
            "lg_s95tr_soundbar": "soundbar",
            "subwoofer": "subwoofer",
            "rear_speakers": "rear",
        }

        return mapping.get(device_id, "")

    def _registry_can_use_direct_relay(
        self,
        device_ids: list[str],
        capability: str,
        action: str,
    ) -> bool:
        """
        Return True only when a registry command can safely use ESP8266
        direct relay fallback.

        Why:
        Direct relay control can only do physical power:
            on / off

        It cannot do:
            volume
            mute
            source
            sound mode
            app launch
            sleep/wake
            playback
        """

        if capability != "power":
            return False

        if action not in {"on", "off"}:
            return False

        if not device_ids:
            return False

        for device_id in device_ids:
            if not self._registry_relay_device_id(device_id):
                return False

        return True

    def _registry_spoken_device_name(self, device_id_or_name: str) -> str:
        """
        Return the short spoken name for a registry device.

        Why:
        The registry can keep accurate full device names like:
            Sony Bravia 2 MK2
            LG S95TR Soundbar

        But voice responses should sound natural:
            Sony TV
            LG Soundbar
        """

        value = str(device_id_or_name or "").strip()

        spoken_names = {
            "living_room_tv": "Sony TV",
            "Sony Bravia 2 MK2": "Sony TV",

            "lg_s95tr_soundbar": "LG Soundbar",
            "LG S95TR Soundbar": "LG Soundbar",

            "subwoofer": "Subwoofer",
            "Subwoofer": "Subwoofer",

            "rear_speakers": "Rear Speakers",
            "Rear Speakers": "Rear Speakers",
        }

        return spoken_names.get(value, value or "the device")

    def _registry_format_device_names(
        self,
        device_ids: list[str],
    ) -> str:
        """
        Format registry device IDs into user-friendly spoken names.

        Why:
        Technical registry names should not always be spoken directly.
        Example:
            living_room_tv -> Sony TV
            lg_s95tr_soundbar -> LG Soundbar
        """

        names: list[str] = []

        for device_id in device_ids:
            device = self.registry.get_device(device_id)

            if device:
                names.append(self._registry_spoken_device_name(device_id))
            else:
                names.append(self._registry_spoken_device_name(device_id))

        if not names:
            return "the device"

        if len(names) == 1:
            return names[0]

        if len(names) == 2:
            return f"{names[0]} and {names[1]}"

        return ", ".join(names[:-1]) + f", and {names[-1]}"

    def _execute_registry_direct_relay_fallback(
        self,
        device_ids: list[str],
        action: str,
    ) -> SmartHomeResult:
        """
        Execute registry power command using ESP8266 direct relay HTTP.

        Why:
        This is the Mode 2 fallback:
            ZYRA Server -> ESP8266 relay board home IP

        """

        success_devices: list[str] = []
        failed_devices: list[str] = []

        for device_id in device_ids:
            relay_id = self._registry_relay_device_id(device_id)

            if not relay_id:
                failed_devices.append(device_id)
                continue

            ok = self.relay.set_device(relay_id, action)

            if ok:
                success_devices.append(device_id)
            else:
                failed_devices.append(device_id)

        self.active_backend = SmartHomeBackend.RELAY_HOME
        self.active_base_url = self.relay.active_base_url

        if success_devices and not failed_devices:
            names = self._registry_format_device_names(success_devices)

            if action == "on":
                response = f"switched on {names}"
                spoken_action = "switched on"
            elif action == "off":
                response = f"switched off {names}"
                spoken_action = "switched off"
            else:
                response = f"toggled {names}"
                spoken_action = "toggled"

            return SmartHomeResult(
                handled=True,
                response=response,
                action=action,
                devices=success_devices,
                backend=SmartHomeBackend.RELAY_HOME.value,
                success=True,
                spoken_action=spoken_action,
                spoken_target=names,
                backend_note=(
                    "Home Assistant was unavailable, so I used direct relay control."
                ),
            )

        if success_devices:
            ok_names = self._registry_format_device_names(success_devices)
            failed_names = self._registry_format_device_names(failed_devices)

            return SmartHomeResult(
                handled=True,
                response=(
                    f"I used direct relay control for {ok_names}, "
                    f"but could not control {failed_names}."
                ),
                action=action,
                devices=success_devices,
                backend=SmartHomeBackend.RELAY_HOME.value,
                success=False,
            )

        return SmartHomeResult(
            handled=True,
            response=(
                "Home Assistant is unavailable, and I could not reach "
                "the direct relay board either."
            ),
            action=action,
            devices=device_ids,
            backend=SmartHomeBackend.RELAY_HOME.value,
            success=False,
        )
    
    def _relay_status_word(self, state: bool | None) -> str:
        """
        Convert relay-board boolean state into spoken status.

        Why:
        Relay fallback status can only tell physical relay power state:
            on / off / unknown

        It cannot tell detailed HA-only status like volume, source,
        sound mode, mute state, or playback state.
        """

        if state is True:
            return "on"

        if state is False:
            return "off"

        return "unknown"

    def _execute_registry_relay_status_fallback(
        self,
        spoken_target: str,
        detail: str | None = "full",
    ) -> SmartHomeResult:
        """
        Read relay-board power status when Home Assistant is unavailable.

        Why:
        HA detailed status can include source, volume, mute, sound mode,
        playback, and media state.

        Relay-board status can only report physical relay power for:
            TV, soundbar, subwoofer, rear speakers

        So when HA is down, ZYRA should still answer the power status from
        the relay board instead of failing completely.
        """

        device_ids = self.registry.resolve_target(spoken_target)

        if not device_ids:
            return SmartHomeResult(
                handled=True,
                response=f"I could not find {spoken_target} in the device registry.",
                action="status",
                devices=[],
                backend=SmartHomeBackend.NONE.value,
                success=False,
            )

        relay_status = self.relay.get_status()

        if relay_status is None:
            return SmartHomeResult(
                handled=True,
                response="I could not read status from Home Assistant or the relay board.",
                action="status",
                devices=device_ids,
                backend=SmartHomeBackend.RELAY_HOME.value,
                success=False,
                backend_note=(
                    "Home Assistant is not available, so I cannot read detailed "
                    "device status right now."
                ),
            )

        parts: list[str] = []

        for device_id in device_ids:
            device = self.registry.get_device(device_id)

            if not device:
                continue

            relay_id = self._registry_relay_device_id(device_id)

            if not relay_id:
                parts.append(f"{device.name} is unknown")
                continue

            state = relay_status.get(relay_id)

            parts.append(
                f"{device.name} is {self._relay_status_word(state)}"
            )

        if not parts:
            response = "I could not read relay status for that target."
            success = False
        else:
            response = ", ".join(parts[:-1])

            if len(parts) > 1:
                response += f", and {parts[-1]}"
            elif parts:
                response = parts[0]

            response += "."
            success = True

        self.active_backend = SmartHomeBackend.RELAY_HOME
        self.active_base_url = self.relay.active_base_url

        return SmartHomeResult(
            handled=True,
            response=response,
            action="status",
            devices=device_ids,
            backend=SmartHomeBackend.RELAY_HOME.value,
            success=success,
            backend_note=(
                "Home Assistant is not available, so I cannot read detailed "
                "device status right now."
            ),
        )

    def execute_registry_command(
        self,
        spoken_target: str,
        capability: str,
        action: str,
        value=None,
    ) -> SmartHomeResult:
        """
        Execute a capability-aware registry command through Home Assistant.

        Example:
          tv + volume + volume_up
            -> media_player.tv_remote -> media_player.volume_up

          soundbar + stream + play_media
            -> media_player.lg_speaker_cast -> media_player.play_media

        """

        # Registry detailed status.
        #
        # Status is not a control command and should not try to resolve a
        # control surface like volume/source/power.
        if capability == "status" or action == "status":
            return self._execute_registry_status(
                spoken_target=spoken_target,
                detail=value,
            )

        device_ids = self.registry.resolve_target(spoken_target)

        if not device_ids:
            return SmartHomeResult(
                handled=True,
                response=f"I could not find {spoken_target} in the device registry.",
                action=action,
                devices=[],
                backend=SmartHomeBackend.NONE.value,
                success=False,
            )

        if not self.ha.is_available():
            if self._registry_can_use_direct_relay(
                device_ids=device_ids,
                capability=capability,
                action=action,
            ):
                return self._execute_registry_direct_relay_fallback(
                    device_ids=device_ids,
                    action=action,
                )

            return SmartHomeResult(
                handled=True,
                response=(
                    "Home Assistant is not available right now, and this command "
                    "needs Home Assistant."
                ),
                action=action,
                devices=device_ids,
                backend=SmartHomeBackend.HOME_ASSISTANT.value,
                success=False,
            )

        success_devices: list[str] = []
        failed_devices: list[str] = []

        for device_id in device_ids:
            device = self.registry.get_device(device_id)

            if not device:
                failed_devices.append(device_id)
                continue

            surface = self.registry.resolve_surface(
                device_id=device_id,
                capability=capability,
            )

            if not surface:
                failed_devices.append(device.name)
                logger.error(
                    "No registry surface for device=%s capability=%s",
                    device_id,
                    capability,
                )
                continue
            
            surface = self._registry_preferred_surface_for_action(
                device_id=device_id,
                capability=capability,
                action=action,
                surface=surface,
            )

            ready, ready_reason = self._registry_device_ready_for_action(
                device_id=device_id,
                surface=surface,
                action=action,
            )

            if not ready:
                logger.warning(
                    "Registry command blocked because target is not ready: "
                    "device=%s surface=%s action=%s reason=%s",
                    device_id,
                    surface.entity_id,
                    action,
                    ready_reason,
                )

                failed_devices.append(device_id)
                continue

            # TV Home Screen Launch \ app exit.
            if (
                spoken_target == "living_room_tv"
                and capability == "app"
                and action == "go_home"
            ):
                ok, response = self._execute_tv_home()

                return SmartHomeResult(
                    handled=True,
                    response=response,
                    action=action,
                    devices=["living_room_tv"],
                    backend=SmartHomeBackend.HOME_ASSISTANT.value
                    if ok
                    else SmartHomeBackend.NONE.value,
                    success=ok,
                    spoken_action="opened home screen" if ok else "open home screen failed",
                    spoken_target="Sony TV",
                )

            # TV app launch.
            if (
                device_id in {"tv", "living_room_tv"}
                and capability == "app"
                and action == "launch_app"
            ):
                ok, message = self._execute_tv_launch_app(value)

                if ok:
                    return SmartHomeResult(
                        handled=True,
                        response=message,
                        action=action,
                        devices=[device_id],
                        backend=SmartHomeBackend.HOME_ASSISTANT.value,
                        success=True,
                    )

                failed_devices.append(device_id)
                logger.error("TV app launch failed: %s", message)
                continue

            # TV set-volume fallback.
            if (
                device_id in {"tv", "living_room_tv"}
                and capability == "volume"
                and action == "set_volume"
            ):
                ok = self._execute_tv_set_volume_by_steps(
                    surface=surface,
                    value=value,
                )

                if ok:
                    return SmartHomeResult(
                        True,
                        "Set volume on Sony TV.",
                        action=action,
                        devices=[device_id],
                        backend="home_assistant",
                        success=True,
                    )

                failed_devices.append(device_id)
                continue

            # TV relative volume fallback.
            #
            # Why:
            # Normal "increase TV volume" should press volume_up once.
            # But "increase TV volume by 5" should press volume_up 5 times.
            if (
                device_id in {"tv", "living_room_tv"}
                and capability == "volume"
                and action in {"volume_up", "volume_down"}
                and value is not None
            ):
                ok = self._execute_tv_relative_volume_steps(
                    surface=surface,
                    action=action,
                    value=value,
                )

                if ok:
                    direction_text = "Increased" if action == "volume_up" else "Decreased"

                    return SmartHomeResult(
                        True,
                        f"{direction_text} volume by {int(float(value))} on Sony TV.",
                        action=action,
                        devices=[device_id],
                        backend="home_assistant",
                        success=True,
                    )

                failed_devices.append(device_id)
                continue

            service_call = self.ha_service_mapper.build(
                surface=surface,
                action=action,
                value=value,
            )

            if not service_call:
                failed_devices.append(device.name)
                logger.error(
                    "No HA service mapping for device=%s capability=%s action=%s",
                    device_id,
                    capability,
                    action,
                )
                continue

            before_state = self.ha.get_entity_state_object(surface.entity_id)

            ok = self.ha.call_service(
                domain=service_call.domain,
                service=service_call.service,
                data=service_call.data,
            )

            verified = False

            if ok:
                verified = self._verify_registry_command(
                    device_id=device_id,
                    surface=surface,
                    capability=capability,
                    action=action,
                    value=value,
                )

            if ok and verified:
                success_devices.append(self._registry_spoken_device_name(device_id))
                logger.info(
                    "Registry command verified OK: %s -> %s.%s %s",
                    device.name,
                    service_call.domain,
                    service_call.service,
                    service_call.data,
                )
            else:
                failed_devices.append(self._registry_spoken_device_name(device_id))
                logger.error(
                    "Registry command failed or not verified: %s -> %s.%s %s ok=%s verified=%s",
                    device.name,
                    service_call.domain,
                    service_call.service,
                    service_call.data,
                    ok,
                    verified,
                )

        self.active_backend = SmartHomeBackend.HOME_ASSISTANT

        if success_devices and not failed_devices:
            target_name = self._join_spoken_names(success_devices)
            spoken_action = self._registry_spoken_action(capability, action)

            return SmartHomeResult(
                handled=True,
                response=self._registry_success_response(
                    spoken_target=spoken_target,
                    capability=capability,
                    action=action,
                    value=value,
                    devices=success_devices,
                ),
                action=action,
                devices=device_ids,
                backend=SmartHomeBackend.HOME_ASSISTANT.value,
                success=True,
                spoken_action=spoken_action,
                spoken_target=target_name,
            )

        if success_devices and failed_devices:
            return SmartHomeResult(
                handled=True,
                response=(
                    f"Done for {', '.join(success_devices)}, "
                    f"but failed for {', '.join(failed_devices)}."
                ),
                action=action,
                devices=success_devices,
                backend=SmartHomeBackend.HOME_ASSISTANT.value,
                success=False,
            )

        return SmartHomeResult(
            handled=True,
            response=f"I could not control {spoken_target}.",
            action=action,
            devices=failed_devices,
            backend=SmartHomeBackend.HOME_ASSISTANT.value,
            success=False,
        )
    
    def _registry_spoken_action(self, capability: str, action: str) -> str:
        """
        Return the natural spoken verb for a registry command.

        """

        if capability == "power" and action in {"on", "turn_on", "power_on"}:
            return "switched on"

        if capability == "power" and action in {"off", "turn_off", "power_off"}:
            return "switched off"

        if capability == "power" and action == "toggle":
            return "toggled"

        if capability == "sleep_wake" and action == "wake":
            return "woke"

        if capability == "sleep_wake" and action == "sleep":
            return "put to sleep"

        if action == "volume_up":
            return "increased volume on"

        if action == "volume_down":
            return "decreased volume on"

        if action == "set_volume":
            return "set volume on"

        if action == "mute":
            return "muted"

        if action == "unmute":
            return "unmuted"

        if action == "mute_toggle":
            return "toggled mute on"

        if action == "pause":
            return "paused"

        if action == "play":
            return "played"

        if action == "stop":
            return "stopped"

        if action == "select_source":
            return "changed source on"

        if action == "select_sound_mode":
            return "changed sound mode on"

        if action == "launch_app":
            return "opened app on"

        return "controlled"

    def _join_spoken_names(self, names: list[str]) -> str:
        """
        Join already-human device names naturally.

        Why:
        success_devices may already contain registry display names like:
            Sony Bravia 2 MK2
            LG S95TR Soundbar

        Before speaking, convert them to short names:
            Sony TV
            LG Soundbar
        """

        clean = [
            self._registry_spoken_device_name(name.strip())
            for name in names
            if name and name.strip()
        ]

        if not clean:
            return "the device"

        if len(clean) == 1:
            return clean[0]

        if len(clean) == 2:
            return f"{clean[0]} and {clean[1]}"

        return ", ".join(clean[:-1]) + f", and {clean[-1]}"

    def _registry_success_response(
        self,
        spoken_target: str,
        capability: str,
        action: str,
        value,
        devices: list[str],
    ) -> str:
        """
        Short spoken response for registry commands.
        """
        target_name = ", ".join(devices)

        if capability == "power" and action in {"on", "turn_on", "power_on"}:
            return f"Turned on {target_name}."

        if capability == "power" and action in {"off", "turn_off", "power_off"}:
            return f"Turned off {target_name}."
        
        if capability == "sleep_wake" and action == "wake":
            return f"Woke {target_name}."

        if capability == "sleep_wake" and action == "sleep":
            return f"Put {target_name} to sleep."

        if action == "volume_up":
            return f"Volume increased on {target_name}."

        if action == "volume_down":
            return f"Volume decreased on {target_name}."

        if action == "set_volume":
            return f"Set volume on {target_name}."

        if action == "mute":
            return f"Muted {target_name}."

        if action == "unmute":
            return f"Unmuted {target_name}."

        if action == "mute_toggle":
            return f"Toggled mute for {target_name}."

        if action == "play":
            return f"Playing on {target_name}."

        if action == "pause":
            return f"Paused {target_name}."

        if action == "stop":
            return f"Stopped {target_name}."

        if action == "next":
            return f"Skipped to next on {target_name}."

        if action == "previous":
            return f"Went to previous on {target_name}."

        if action == "select_source":
            return f"Changed {target_name} source to {value}."

        if action == "select_sound_mode":
            return f"Changed {target_name} sound mode to {value}."

        if action == "play_media":
            return f"Started media on {target_name}."

        if action == "launch_app":
            return f"Opened {value} on {target_name}."

        return f"Done for {target_name}."

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