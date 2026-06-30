from dataclasses import dataclass
from typing import Any, Optional

from device_registry import RegistrySurface


@dataclass(frozen=True)
class HAServiceCall:
    domain: str
    service: str
    data: dict[str, Any]


class HAServiceMapper:
    """
    Converts ZYRA capability commands into Home Assistant service calls.

    Home Assistant service calls are sent to:
      /api/services/<domain>/<service>

    Home Assistant REST accepts JSON payloads for service execution.
    """

    def build(
        self,
        surface: RegistrySurface,
        action: str,
        value: Optional[Any] = None,
    ) -> Optional[HAServiceCall]:
        domain = surface.domain
        entity_id = surface.entity_id

        if action in {"on", "turn_on", "power_on", "wake"}:
            return HAServiceCall(domain, "turn_on", {"entity_id": entity_id})

        if action in {"off", "turn_off", "power_off", "sleep"}:
            return HAServiceCall(domain, "turn_off", {"entity_id": entity_id})

        if action == "toggle":
            return HAServiceCall(domain, "toggle", {"entity_id": entity_id})

        if domain == "media_player":
            return self._media_player(surface, action, value)

        if domain == "switch":
            return self._switch(surface, action)

        return None

    def _switch(
        self,
        surface: RegistrySurface,
        action: str,
    ) -> Optional[HAServiceCall]:
        entity_id = surface.entity_id

        # For switch.living_room_tv_mute:
        # mute   -> switch.turn_on
        # unmute -> switch.turn_off
        # toggle -> switch.toggle
        if action == "mute":
            return HAServiceCall(
                "switch",
                "turn_on",
                {"entity_id": entity_id},
            )

        if action == "unmute":
            return HAServiceCall(
                "switch",
                "turn_off",
                {"entity_id": entity_id},
            )

        if action == "mute_toggle":
            return HAServiceCall(
                "switch",
                "toggle",
                {"entity_id": entity_id},
            )

        return None

    def _media_player(
        self,
        surface: RegistrySurface,
        action: str,
        value: Optional[Any],
    ) -> Optional[HAServiceCall]:
        entity_id = surface.entity_id

        if action == "play":
            return HAServiceCall(
                "media_player",
                "media_play",
                {"entity_id": entity_id},
            )

        if action == "pause":
            return HAServiceCall(
                "media_player",
                "media_pause",
                {"entity_id": entity_id},
            )

        if action == "stop":
            return HAServiceCall(
                "media_player",
                "media_stop",
                {"entity_id": entity_id},
            )

        if action == "volume_up":
            return HAServiceCall(
                "media_player",
                "volume_up",
                {"entity_id": entity_id},
            )

        if action == "volume_down":
            return HAServiceCall(
                "media_player",
                "volume_down",
                {"entity_id": entity_id},
            )

        if action == "mute":
            return HAServiceCall(
                "media_player",
                "volume_mute",
                {"entity_id": entity_id, "is_volume_muted": True},
            )

        if action == "unmute":
            return HAServiceCall(
                "media_player",
                "volume_mute",
                {"entity_id": entity_id, "is_volume_muted": False},
            )

        if action == "set_volume":
            if value is None:
                return None

            volume = float(value)

            # Accept both 0-100 and 0.0-1.0 inputs.
            if volume > 1:
                volume = volume / 100.0

            volume = max(0.0, min(volume, 1.0))

            return HAServiceCall(
                "media_player",
                "volume_set",
                {"entity_id": entity_id, "volume_level": volume},
            )

        if action == "next":
            return HAServiceCall(
                "media_player",
                "media_next_track",
                {"entity_id": entity_id},
            )

        if action == "previous":
            return HAServiceCall(
                "media_player",
                "media_previous_track",
                {"entity_id": entity_id},
            )

        if action == "select_source":
            if not value:
                return None

            return HAServiceCall(
                "media_player",
                "select_source",
                {"entity_id": entity_id, "source": str(value)},
            )

        if action == "select_sound_mode":
            if not value:
                return None

            return HAServiceCall(
                "media_player",
                "select_sound_mode",
                {"entity_id": entity_id, "sound_mode": str(value)},
            )

        if action == "play_media":
            if not isinstance(value, dict):
                return None

            media_content_id = value.get("media_content_id")
            media_content_type = value.get("media_content_type", "music")

            if not media_content_id:
                return None

            return HAServiceCall(
                "media_player",
                "play_media",
                {
                    "entity_id": entity_id,
                    "media_content_id": media_content_id,
                    "media_content_type": media_content_type,
                },
            )

        return None