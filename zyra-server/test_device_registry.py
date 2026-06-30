"""
ZYRA device registry and HA service mapper test.

Run from zyra-server:
    python test_device_registry.py

This test verifies:
1. Spoken target -> registry device IDs
2. Device + capability -> correct Home Assistant surface/entity
3. Surface + action -> correct Home Assistant service call

Current architecture:
- power      = relay / mains power
- sleep_wake = actual smart-device soft sleep/wake
"""

from config import DEVICE_REGISTRY_PATH
from device_registry import DeviceRegistry
from ha_service_mapper import HAServiceMapper


TEST_CASES = [
    # TV main power must use relay.
    ("tv", "power", "on", None),
    ("tv", "power", "off", None),

    # TV soft power must use actual TV media_player entity.
    ("tv", "sleep_wake", "wake", None),
    ("tv", "sleep_wake", "sleep", None),

    # TV normal smart controls.
    ("tv", "volume", "volume_up", None),
    ("tv", "mute_switch", "mute_toggle", None),
    ("tv", "source", "select_source", "HDMI 1"),

    # Soundbar main power must use relay.
    ("soundbar", "power", "on", None),
    ("soundbar", "power", "off", None),

    # Soundbar soft power must use actual LG media_player entity.
    ("soundbar", "sleep_wake", "wake", None),
    ("soundbar", "sleep_wake", "sleep", None),

    # Soundbar normal smart controls.
    ("soundbar", "volume", "set_volume", 35),
    ("soundbar", "source", "select_source", "Optical/HDMI ARC"),
    ("soundbar", "sound_mode", "select_sound_mode", "Cinema"),

    # Relay-only devices and groups.
    ("subwoofer", "power", "on", None),
    ("rear speakers", "power", "off", None),
    ("sound system", "power", "off", None),
    ("all speakers", "power", "off", None),
    ("home theater", "power", "off", None),
]


# This is the most important check.
#
# Why:
# The router only outputs:
#   living_room_tv + power + on
#
# This test confirms that the registry converts that to:
#   switch.sony_tv
#
# instead of:
#   media_player.living_room_tv
EXPECTED_ENTITY_BY_DEVICE_CAPABILITY = {
    ("living_room_tv", "power"): "switch.sony_tv",
    ("living_room_tv", "sleep_wake"): "media_player.living_room_tv",

    ("lg_s95tr_soundbar", "power"): "switch.soundbar",
    ("lg_s95tr_soundbar", "sleep_wake"): "media_player.lg_soundbar",

    ("subwoofer", "power"): "switch.subwoofer",
    ("rear_speakers", "power"): "switch.rear_speakers",
}


EXPECTED_SERVICE_BY_DEVICE_CAPABILITY_ACTION = {
    ("living_room_tv", "power", "on"): ("switch", "turn_on"),
    ("living_room_tv", "power", "off"): ("switch", "turn_off"),
    ("living_room_tv", "sleep_wake", "wake"): ("media_player", "turn_on"),
    ("living_room_tv", "sleep_wake", "sleep"): ("media_player", "turn_off"),

    ("lg_s95tr_soundbar", "power", "on"): ("switch", "turn_on"),
    ("lg_s95tr_soundbar", "power", "off"): ("switch", "turn_off"),
    ("lg_s95tr_soundbar", "sleep_wake", "wake"): ("media_player", "turn_on"),
    ("lg_s95tr_soundbar", "sleep_wake", "sleep"): ("media_player", "turn_off"),

    ("subwoofer", "power", "on"): ("switch", "turn_on"),
    ("subwoofer", "power", "off"): ("switch", "turn_off"),
    ("rear_speakers", "power", "on"): ("switch", "turn_on"),
    ("rear_speakers", "power", "off"): ("switch", "turn_off"),
}


def main():
    registry = DeviceRegistry(DEVICE_REGISTRY_PATH)
    mapper = HAServiceMapper()

    print("\n── ZYRA Device Registry Test ─────────────\n")

    print("Loaded devices:")
    for device_id, device in registry.devices.items():
        print(f"  {device_id:22s} -> {device.name}")

    print("\nResolution tests:")

    for spoken_target, capability, action, value in TEST_CASES:
        device_ids = registry.resolve_target(spoken_target)

        if not device_ids:
            raise RuntimeError(
                f"Could not resolve target: {spoken_target!r}"
            )

        for device_id in device_ids:
            surface = registry.resolve_surface(device_id, capability)

            if not surface:
                raise RuntimeError(
                    f"No surface for target={spoken_target!r} "
                    f"device={device_id!r} capability={capability!r}"
                )

            expected_entity = EXPECTED_ENTITY_BY_DEVICE_CAPABILITY.get(
                (device_id, capability)
            )

            if expected_entity and surface.entity_id != expected_entity:
                raise RuntimeError(
                    f"Wrong surface for target={spoken_target!r} "
                    f"device={device_id!r} capability={capability!r}\n"
                    f"Expected: {expected_entity}\n"
                    f"Actual:   {surface.entity_id}"
                )

            service_call = mapper.build(surface, action, value)

            if not service_call:
                raise RuntimeError(
                    f"No HA service mapping for "
                    f"target={spoken_target!r} device={device_id!r} "
                    f"capability={capability!r} action={action!r}"
                )

            expected_service = EXPECTED_SERVICE_BY_DEVICE_CAPABILITY_ACTION.get(
                (device_id, capability, action)
            )

            if expected_service:
                actual_service = (
                    service_call.domain,
                    service_call.service,
                )

                if actual_service != expected_service:
                    raise RuntimeError(
                        f"Wrong service for target={spoken_target!r} "
                        f"device={device_id!r} capability={capability!r} "
                        f"action={action!r}\n"
                        f"Expected: {expected_service[0]}.{expected_service[1]}\n"
                        f"Actual:   {actual_service[0]}.{actual_service[1]}"
                    )

            print(
                f"  {spoken_target:16s} -> {device_id:22s} "
                f"+ {capability:12s} + {action:18s} "
                f"-> {surface.entity_id:34s} "
                f"-> {service_call.domain}.{service_call.service}"
            )

    print("\nSUCCESS — registry and service mapper are valid.")


if __name__ == "__main__":
    main()