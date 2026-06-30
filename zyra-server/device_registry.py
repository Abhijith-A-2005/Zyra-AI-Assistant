import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegistrySurface:
    device_id: str
    surface_id: str
    entity_id: str
    domain: str
    capabilities: list[str]
    fallback_only: bool = False
    sources: Optional[list[str]] = None
    sound_modes: Optional[list[str]] = None


@dataclass(frozen=True)
class RegistryDevice:
    device_id: str
    name: str
    type: str
    room: str
    primary_entity: str
    aliases: list[str]
    legacy_device_id: Optional[str]
    surfaces: dict[str, RegistrySurface]


class DeviceRegistry:
    """
    Capability-aware smart-home registry.

    Why:
    A real smart device can expose multiple Home Assistant entities.
    Example:
      TV power/source      -> media_player.living_room_tv
      TV volume/remote     -> media_player.tv_remote
      TV cast streaming    -> media_player.google_tv_cast
      TV mute switch       -> switch.living_room_tv_mute

    So ZYRA must resolve:
      spoken target + requested capability -> correct HA entity.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.raw = self._load_json(self.path)
        self.devices = self._load_devices()
        self.groups = self.raw.get("groups", {})
        self.alias_index = self._build_alias_index()
        self.group_alias_index = self._build_group_alias_index()

        logger.info(
            "Device registry loaded — %d devices, %d groups",
            len(self.devices),
            len(self.groups),
        )

    def _load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"Device registry not found: {path}")

        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _normalize(self, text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"[^a-z0-9\s]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _load_devices(self) -> dict[str, RegistryDevice]:
        devices: dict[str, RegistryDevice] = {}

        for device_id, data in self.raw.get("devices", {}).items():
            surfaces: dict[str, RegistrySurface] = {}

            for surface_id, surface_data in data.get("surfaces", {}).items():
                surfaces[surface_id] = RegistrySurface(
                    device_id=device_id,
                    surface_id=surface_id,
                    entity_id=surface_data["entity_id"],
                    domain=surface_data["domain"],
                    capabilities=list(surface_data.get("capabilities", [])),
                    fallback_only=bool(surface_data.get("fallback_only", False)),
                    sources=surface_data.get("sources"),
                    sound_modes=surface_data.get("sound_modes"),
                )

            devices[device_id] = RegistryDevice(
                device_id=device_id,
                name=data["name"],
                type=data["type"],
                room=data["room"],
                primary_entity=data["primary_entity"],
                aliases=list(data.get("aliases", [])),
                legacy_device_id=data.get("legacy_device_id"),
                surfaces=surfaces,
            )

        return devices

    def _build_alias_index(self) -> dict[str, str]:
        index: dict[str, str] = {}

        for device_id, device in self.devices.items():
            values = [device_id, device.name, *device.aliases]

            if device.legacy_device_id:
                values.append(device.legacy_device_id)

            for value in values:
                index[self._normalize(value)] = device_id

        return index

    def _build_group_alias_index(self) -> dict[str, str]:
        index: dict[str, str] = {}

        for group_id, group in self.groups.items():
            values = [group_id, group.get("name", ""), *group.get("aliases", [])]

            for value in values:
                index[self._normalize(value)] = group_id

        return index

    def resolve_target(self, spoken_target: str) -> list[str]:
        """
        Resolve spoken target to one or more registry device IDs.

        Examples:
          "tv"              -> ["living_room_tv"]
          "sound system"    -> ["lg_s95tr_soundbar", "subwoofer"]
          "all speakers"    -> ["lg_s95tr_soundbar", "subwoofer", "rear_speakers"]
        """
        target = self._normalize(spoken_target)

        if target in self.group_alias_index:
            group_id = self.group_alias_index[target]
            return list(self.groups[group_id].get("members", []))

        if target in self.alias_index:
            return [self.alias_index[target]]

        # Phrase-contained fallback.
        # Why:
        # STT may output "the living room tv" or "my soundbar".
        for alias, device_id in sorted(
            self.alias_index.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            if re.search(rf"\b{re.escape(alias)}\b", target):
                return [device_id]

        for alias, group_id in sorted(
            self.group_alias_index.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            if re.search(rf"\b{re.escape(alias)}\b", target):
                return list(self.groups[group_id].get("members", []))

        return []

    def get_device(self, device_id: str) -> Optional[RegistryDevice]:
        return self.devices.get(device_id)

    def get_legacy_device_id(self, device_id: str) -> Optional[str]:
        device = self.devices.get(device_id)
        return device.legacy_device_id if device else None

    def resolve_surface(
        self,
        device_id: str,
        capability: str,
        allow_fallback_only: bool = False,
    ) -> Optional[RegistrySurface]:
        """
        Resolve device + capability to the best HA control surface.
        """
        device = self.devices.get(device_id)

        if not device:
            return None

        capability = self._normalize(capability).replace(" ", "_")

        for surface in device.surfaces.values():
            if surface.fallback_only and not allow_fallback_only:
                continue

            if capability in surface.capabilities:
                return surface

        return None

    def all_status_entities(self) -> dict[str, str]:
        """
        Return primary entity IDs for status display.

        Why:
        ZYRA should show one status per physical device, not one per HA entity.
        """
        return {
            device_id: device.primary_entity
            for device_id, device in self.devices.items()
        }

    def describe(self) -> dict[str, Any]:
        return {
            "devices": {
                device_id: {
                    "name": device.name,
                    "type": device.type,
                    "room": device.room,
                    "primary_entity": device.primary_entity,
                    "aliases": device.aliases,
                    "legacy_device_id": device.legacy_device_id,
                    "surfaces": {
                        surface_id: {
                            "entity_id": surface.entity_id,
                            "domain": surface.domain,
                            "capabilities": surface.capabilities,
                            "fallback_only": surface.fallback_only,
                        }
                        for surface_id, surface in device.surfaces.items()
                    },
                }
                for device_id, device in self.devices.items()
            },
            "groups": self.groups,
        }