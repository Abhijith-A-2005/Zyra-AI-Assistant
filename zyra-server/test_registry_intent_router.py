"""
ZYRA semantic registry intent router test.

Run from zyra-server:
    python test_registry_intent_router.py

This test does not only check handled=True.
It verifies canonical registry-safe output.
"""

from registry_intent_router import RegistryIntentRouter


SMART_HOME_EXPECTED = [

    (
        "turn on the TV",
        [("living_room_tv", "power", "on", None)],
    ),
    (
        "turn off the TV",
        [("living_room_tv", "power", "off", None)],
    ),
    (
        "wake the TV",
        [("living_room_tv", "sleep_wake", "wake", None)],
    ),
    (
        "sleep the TV",
        [("living_room_tv", "sleep_wake", "sleep", None)],
    ),
    (
        "turn on the soundbar",
        [("lg_s95tr_soundbar", "power", "on", None)],
    ),
    (
        "turn off the soundbar",
        [("lg_s95tr_soundbar", "power", "off", None)],
    ),
    (
        "wake the soundbar",
        [("lg_s95tr_soundbar", "sleep_wake", "wake", None)],
    ),
    (
        "sleep the soundbar",
        [("lg_s95tr_soundbar", "sleep_wake", "sleep", None)],
    ),
    (
        "set the soundbar volume to 10",
        [("lg_s95tr_soundbar", "volume", "set_volume", 10)],
    ),
    (
        "make the speaker bar quieter",
        [("lg_s95tr_soundbar", "volume", "volume_down", None)],
    ),
    (
        "bring the TV volume up a little",
        [("living_room_tv", "volume", "volume_up", None)],
    ),
    (
        "decrease my TV volume",
        [("living_room_tv", "volume", "volume_down", None)],
    ),
    (
        "mute the television",
        [("living_room_tv", "mute_switch", "mute", None)],
    ),
    (
        "put the TV on HDMI 1",
        [("living_room_tv", "source", "select_source", "HDMI 1")],
    ),
    (
        "change the soundbar input to HDMI ARC",
        [("lg_s95tr_soundbar", "source", "select_source", "Optical/HDMI ARC")],
    ),
    (
        "use cinema sound on the soundbar",
        [("lg_s95tr_soundbar", "sound_mode", "select_sound_mode", "Cinema")],
    ),
    (
        "pause whatever is playing on the TV",
        [("living_room_tv", "playback", "pause", None)],
    ),
    (
        "open Netflix on TV",
        [("living_room_tv", "app", "launch_app", "Netflix")],
    ),
    (
        "launch Prime Video",
        [("living_room_tv", "app", "launch_app", "Prime Video")],
    ),
    (
        "open Jio Hotstar",
        [("living_room_tv", "app", "launch_app", "Jio Hotstar")],
    ),
    (
        "start YouTube on TV",
        [("living_room_tv", "app", "launch_app", "YouTube")],
    ),
    (
        "open Sony Pictures Core on TV",
        [("living_room_tv", "app", "launch_app", "Sony Pictures Core")],
    ),
    (
        "open Spotify on TV",
        [("living_room_tv", "app", "launch_app", "Spotify")],
    ),
    (
        "launch Amazon Music",
        [("living_room_tv", "app", "launch_app", "Amazon Music")],
    ),
    (
        "open Xstream Play",
        [("living_room_tv", "app", "launch_app", "Xstream Play")],
    ),
    (
        "go to TV home",
        [("living_room_tv", "app", "go_home", None)],
    ),
    (
        "go to home screen",
        [("living_room_tv", "app", "go_home", None)],
    ),
    (
        "exit the app",
        [("living_room_tv", "app", "go_home", None)],
    ),
    (
        "close Netflix",
        [("living_room_tv", "app", "go_home", None)],
    ),
    (
        "close Prime Video",
        [("living_room_tv", "app", "go_home", None)],
    ),
    (
        "what input is the TV using right now",
        [("living_room_tv", "status", "status", "source")],
    ),
    (
        "what is the soundbar volume",
        [("lg_s95tr_soundbar", "status", "status", "volume")],
    ),
    (
        "turn off the sound system and turn on the TV",
        [
            ("sound_system", "power", "off", None),
            ("living_room_tv", "power", "on", None),
        ],
    ),
    (
        "shut down the home theater except keep the TV on",
        [
            ("all_speakers", "power", "off", None),
            ("living_room_tv", "power", "on", None),
        ],
    ),
]

GENERAL_TESTS = [
    "what are the best horror movies",
    "how are you",
    "explain how Bluetooth works",
    "recommend a good action movie",
]


def command_tuple(command):
    return (
        command.spoken_target,
        command.capability,
        command.action,
        command.value,
    )


def print_route(text, result):
    print(f"\n{text}")
    print(f"  handled={result.handled}")
    print(f"  reason={result.reason}")
    print(f"  error_response={result.error_response}")

    for index, command in enumerate(result.commands, start=1):
        print(
            f"  command_{index}: "
            f"target={command.spoken_target} "
            f"capability={command.capability} "
            f"action={command.action} "
            f"value={command.value}"
        )


def main():
    router = RegistryIntentRouter()

    print("\n── ZYRA Semantic Registry Intent Router Test ─────────────")

    for text, expected in SMART_HOME_EXPECTED:
        result = router.route(text)
        print_route(text, result)

        if not result.handled:
            raise RuntimeError(
                f"Smart-home phrase was classified as general: {text}"
            )

        if result.error_response:
            raise RuntimeError(
                f"Smart-home phrase produced error_response: "
                f"{text} -> {result.error_response}"
            )

        actual = [command_tuple(command) for command in result.commands]

        if actual != expected:
            raise RuntimeError(
                f"Unexpected route for: {text}\n"
                f"Expected: {expected}\n"
                f"Actual:   {actual}"
            )

    for text in GENERAL_TESTS:
        result = router.route(text)
        print_route(text, result)

        if result.handled:
            raise RuntimeError(
                f"General phrase was classified as smart-home: {text}"
            )

    print("\nSUCCESS — semantic registry routing is canonical and safe.")


if __name__ == "__main__":
    main()