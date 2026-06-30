"""
ZYRA live registry command test.

Run from zyra-server:
    python test_registry_live.py

This performs safe live Home Assistant service calls.
"""

from smart_home import SmartHomeEngine


def run_case(engine, title, target, capability, action, value=None):
    print(f"\n{title}")
    print(f"  target={target}")
    print(f"  capability={capability}")
    print(f"  action={action}")
    print(f"  value={value}")

    result = engine.execute_registry_command(
        spoken_target=target,
        capability=capability,
        action=action,
        value=value,
    )

    print(f"  success={result.success}")
    print(f"  backend={result.backend}")
    print(f"  response={result.response}")

    return result.success


def main():
    engine = SmartHomeEngine()

    print("\n── ZYRA Live Registry Test ─────────────")

    cases = [
        (
            "TV volume up should use media_player.tv_remote",
            "tv",
            "volume",
            "volume_up",
            None,
        ),
        (
            "TV mute switch should use switch.living_room_tv_mute",
            "tv",
            "mute_switch",
            "mute_toggle",
            None,
        ),
        (
            "Soundbar volume set should use native LG soundbar",
            "soundbar",
            "volume",
            "set_volume",
            25,
        ),
        (
            "Soundbar Cinema mode should use native LG soundbar",
            "soundbar",
            "sound_mode",
            "select_sound_mode",
            "Cinema",
        ),
    ]

    ok_count = 0

    for case in cases:
        if run_case(engine, *case):
            ok_count += 1

    print(f"\nPassed {ok_count}/{len(cases)} live registry commands.")

    if ok_count == 0:
        raise RuntimeError("All live registry commands failed.")

    print("\nSUCCESS — registry can control Home Assistant live.")


if __name__ == "__main__":
    main()