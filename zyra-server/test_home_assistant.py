"""
Smart-home backend test for ZYRA.

Run from zyra-server:
    python test_home_assistant.py

This checks:
1. Home Assistant backend
2. ESP8266 relay-home fallback backend
3. SmartHomeEngine selected status
"""

from smart_home import SmartHomeEngine


def print_states(title: str, states):
    print(f"\n{title}")

    if states is None:
        print("  unavailable")
        return

    for device, state in states.items():
        if state is True:
            text = "on"
        elif state is False:
            text = "off"
        else:
            text = "unknown"

        print(f"  {device:10s} {text}")


def main():
    smart_home = SmartHomeEngine()

    print("\n── ZYRA Smart Home Backend Test ─────────────")

    print("\nConfigured Home Assistant entities:")
    for device, meta in smart_home.devices.items():
        print(f"  {device:10s} {meta['entity_id']}")

    ha_status = smart_home.ha.get_status()
    relay_status = smart_home.relay.get_status()
    selected_status = smart_home.get_status()

    print_states("Home Assistant status:", ha_status)
    print_states("Relay home-IP status:", relay_status)
    print_states("Selected smart-home status:", selected_status)

    print("\nHealth snapshot:")
    health = smart_home.health_snapshot()

    print(f"  preferred_mode: {health['preferred_mode']}")
    print(f"  active_backend: {health['active_backend']}")
    print(f"  ha_available:   {health['home_assistant']['available']}")
    print(f"  relay_available:{health['relay_home']['available']}")
    print(f"  relay_active:   {health['relay_home']['active_url']}")

    if selected_status is None:
        raise RuntimeError(
            "Neither Home Assistant nor ESP8266 relay home IP is reachable."
        )

    print("\nSUCCESS — at least one smart-home backend is reachable.")


if __name__ == "__main__":
    main()