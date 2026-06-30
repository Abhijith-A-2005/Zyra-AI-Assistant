import json
import logging
import re
from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import Any, Optional

import requests

from config import OLLAMA_URL, OLLAMA_MODEL, DEVICE_REGISTRY_PATH
from device_registry import DeviceRegistry

logger = logging.getLogger(__name__)


@dataclass
class RegistryCommandItem:
    spoken_target: str
    capability: str
    action: str
    value: Optional[Any] = None


@dataclass
class RegistryVoiceCommand:
    """
    Router result.

    handled=False:
        This is general conversation. Continue normal Zyra LLM.

    handled=True + commands:
        Execute these registry-safe smart-home commands.

    handled=True + error_response:
        It looked like smart-home, but unsafe/unsupported.
    """

    handled: bool
    spoken_target: str = ""
    capability: str = ""
    action: str = ""
    value: Optional[Any] = None
    reason: str = ""
    commands: list[RegistryCommandItem] = field(default_factory=list)
    error_response: str = ""


class RegistryIntentRouter:
    """
    Semantic-frame smart-home router.

    Important design:
    - LLM does semantic understanding.
    - LLM does NOT directly choose Home Assistant entities.
    - LLM does NOT directly choose final executable service calls.
    - Python converts approved semantic operations into registry-safe commands.

    This avoids the bad behavior where the LLM says:
      "mute television" -> power off TV
      "change soundbar input" -> status full
      "action movie" -> smart-home action

    The LLM outputs semantic frames like:
      target=living_room_tv, operation=mute_on
      target=lg_s95tr_soundbar, operation=set_source, value=HDMI ARC

    Then Python maps:
      mute_on + TV -> mute_switch + mute
      set_source + soundbar -> source + select_source
    """

    OPERATIONS = {
        "power_on",
        "power_off",
        "power_toggle",
        "sleep",
        "wake",
        "volume_up",
        "volume_down",
        "set_volume",
        "mute_on",
        "mute_off",
        "mute_toggle",
        "play",
        "pause",
        "stop",
        "next",
        "previous",
        "set_source",
        "set_sound_mode",
        "play_media",
        "launch_app",
        "go_home",
        "status",
    }

    STATUS_DETAILS = {
        "full",
        "power",
        "volume",
        "mute",
        "source",
        "sound_mode",
        "media",
    }

    OPERATION_ALIASES = {
        "on": "power_on",
        "turn_on": "power_on",
        "poweron": "power_on",
        "power_on": "power_on",

        "off": "power_off",
        "turn_off": "power_off",
        "poweroff": "power_off",
        "power_off": "power_off",
        "shutdown": "power_off",
        "shut_down": "power_off",

        "toggle": "power_toggle",
        "power_toggle": "power_toggle",

        "sleep": "sleep",
        "standby": "sleep",
        "put_to_sleep": "sleep",
        "go_to_sleep": "sleep",
        "soft_off": "sleep",

        "wake": "wake",
        "wake_up": "wake",
        "soft_on": "wake",

        "increase_volume": "volume_up",
        "volume_increase": "volume_up",
        "raise_volume": "volume_up",
        "volume_up": "volume_up",
        "louder": "volume_up",

        "decrease_volume": "volume_down",
        "volume_decrease": "volume_down",
        "lower_volume": "volume_down",
        "volume_down": "volume_down",
        "quieter": "volume_down",

        "set_volume": "set_volume",
        "volume_set": "set_volume",

        "mute": "mute_on",
        "mute_on": "mute_on",
        "unmute": "mute_off",
        "mute_off": "mute_off",
        "toggle_mute": "mute_toggle",
        "mute_toggle": "mute_toggle",

        "resume": "play",
        "media_play": "play",
        "play": "play",

        "media_pause": "pause",
        "pause": "pause",

        "media_stop": "stop",
        "stop": "stop",

        "next_track": "next",
        "media_next": "next",
        "next": "next",

        "previous_track": "previous",
        "prev": "previous",
        "media_previous": "previous",
        "previous": "previous",

        "source": "set_source",
        "input": "set_source",
        "set_source": "set_source",
        "select_source": "set_source",
        "change_source": "set_source",
        "change_input": "set_source",
        "set_input": "set_source",

        "sound_mode": "set_sound_mode",
        "set_sound_mode": "set_sound_mode",
        "select_sound_mode": "set_sound_mode",
        "change_sound_mode": "set_sound_mode",

        "stream": "play_media",
        "play_media": "play_media",

        "app": "launch_app",
        "open_app": "launch_app",
        "launch_app": "launch_app",
        "start_app": "launch_app",

        "go_home": "go_home",
        "home": "go_home",
        "home_screen": "go_home",
        "open_home": "go_home",
        "open_home_screen": "go_home",
        "return_home": "go_home",
        "exit_app": "go_home",
        "close_app": "go_home",
        "close": "go_home",
        "exit": "go_home",

        "status": "status",
        "check_status": "status",
        "get_status": "status",
    }

    OPERATION_ACTION_MAP = {
        "power_on": ("power", "on"),
        "power_off": ("power", "off"),
        "power_toggle": ("power", "toggle"),

        "sleep": ("sleep_wake", "sleep"),
        "wake": ("sleep_wake", "wake"),

        "volume_up": ("volume", "volume_up"),
        "volume_down": ("volume", "volume_down"),
        "set_volume": ("volume", "set_volume"),

        # Target-specific capability correction happens later.
        "mute_on": ("mute", "mute"),
        "mute_off": ("mute", "unmute"),
        "mute_toggle": ("mute", "mute_toggle"),

        "play": ("playback", "play"),
        "pause": ("playback", "pause"),
        "stop": ("playback", "stop"),

        "next": ("next_previous", "next"),
        "previous": ("next_previous", "previous"),

        "set_source": ("source", "select_source"),
        "set_sound_mode": ("sound_mode", "select_sound_mode"),
        "play_media": ("stream", "play_media"),

        "launch_app": ("app", "launch_app"),
        "go_home": ("app", "go_home"),

        "status": ("status", "status"),
    }

    ACTION_CAPABILITY_CANDIDATES = {
        "on": ["power"],
        "off": ["power"],
        "toggle": ["power"],

        "sleep": ["sleep_wake"],
        "wake": ["sleep_wake"],

        "volume_up": ["volume"],
        "volume_down": ["volume"],
        "set_volume": ["volume"],

        "mute": ["mute_switch", "mute"],
        "unmute": ["mute_switch", "mute"],
        "mute_toggle": ["mute_switch", "mute"],

        "play": ["playback"],
        "pause": ["playback"],
        "stop": ["playback"],

        "next": ["next_previous"],
        "previous": ["next_previous"],

        "select_source": ["source"],
        "select_sound_mode": ["sound_mode"],
        "play_media": ["stream"],

        "launch_app": ["app"],
        "go_home": ["app"],

        "status": ["status"],
    }

    def __init__(self):
        self.url = f"{OLLAMA_URL}/api/chat"
        self.model = OLLAMA_MODEL
        self.registry = DeviceRegistry(DEVICE_REGISTRY_PATH)

        self.registry_context = self._build_registry_context()
        self.allowed_target_ids = sorted(
            [*self.registry.devices.keys(), *self.registry.groups.keys()]
        )

        logger.info(
            "Semantic-frame registry router ready — model=%s registry=%s",
            self.model,
            DEVICE_REGISTRY_PATH,
        )

    def _route_tv_home_from_text(
        self,
        transcript: str,
    ) -> Optional[RegistryVoiceCommand]:
        """
        Deterministic TV home route.

        Examples:
          go to home
          go to TV home
          open home screen
          exit the app
          close the app
          close Netflix
          exit Prime Video

        Why:
        Google TV home is not an app URL.
        It is the Android TV HOME remote command.
        Also, app-close wording should not fall into normal LLM conversation
        or app launch. It should safely return the TV to the home screen.
        """

        text = self._normalize_text(transcript)

        if not text:
            return None

        home_patterns = [
            r"\bgo\s+(?:to\s+)?(?:tv\s+|google\s+tv\s+)?home(?:\s+screen)?\b",
            r"\bopen\s+(?:the\s+)?(?:tv\s+|google\s+tv\s+)?home(?:\s+screen)?\b",
            r"\breturn\s+(?:to\s+)?(?:tv\s+|google\s+tv\s+)?home(?:\s+screen)?\b",
            r"\bback\s+(?:to\s+)?(?:tv\s+|google\s+tv\s+)?home(?:\s+screen)?\b",
            r"\bshow\s+(?:the\s+)?(?:tv\s+|google\s+tv\s+)?home(?:\s+screen)?\b",
            r"\bexit\s+(?:the\s+)?app\b",
            r"\bclose\s+(?:the\s+)?app\b",
            r"\bexit\s+.+\b",
            r"\bclose\s+.+\b",
        ]

        if not any(re.search(pattern, text) for pattern in home_patterns):
            return None

        # Avoid hijacking non-TV home automation commands.
        # Example: "close the curtains" should not become TV home.
        blocked_targets = {
            "curtain",
            "curtains",
            "door",
            "doors",
            "window",
            "windows",
            "light",
            "lights",
            "fan",
            "ac",
            "air conditioner",
        }

        if any(re.search(rf"\b{re.escape(word)}\b", text) for word in blocked_targets):
            return None

        if not self.registry.resolve_surface("living_room_tv", "app"):
            return RegistryVoiceCommand(
                handled=True,
                reason="TV app/home capability missing from registry",
                error_response=(
                    "I understood the TV home command, but TV remote control "
                    "is not enabled in the device registry yet."
                ),
            )

        command = RegistryCommandItem(
            spoken_target="living_room_tv",
            capability="app",
            action="go_home",
            value=None,
        )

        return RegistryVoiceCommand(
            handled=True,
            spoken_target=command.spoken_target,
            capability=command.capability,
            action=command.action,
            value=command.value,
            reason=f"deterministic TV home navigation: {text}",
            commands=[command],
        )

    def _route_tv_app_launch_from_text(
        self,
        transcript: str,
    ) -> Optional[RegistryVoiceCommand]:
        """
        Deterministic app-launch route.

        Examples:
          open Netflix on TV
          launch Prime Video
          open Jio Hotstar
          start YouTube

        Why:
        App opening is a direct smart-home/media command.
        It should not fall through to general LLM conversation.
        Also, this supports short phrases like "open Netflix" even if the
        user does not explicitly say "TV".
        """

        text = self._normalize_text(transcript)

        if not text:
            return None

        launch_words = {
            "open",
            "launch",
            "start",
            "bring up",
            "put on",
        }

        if not any(word in text for word in launch_words):
            return None

        # Do not hijack soundbar commands.
        if re.search(r"\b(soundbar|sound bar|speaker bar)\b", text):
            return None

        app_aliases = {
            "Netflix": [
                "netflix",
            ],
            "Prime Video": [
                "prime video",
                "amazon prime video",
                "amazon prime",
                "prime",
            ],
            "Jio Hotstar": [
                "jio hotstar",
                "jiohotstar",
                "hotstar",
                "disney hotstar",
                "disney plus hotstar",
            ],
            "YouTube": [
                "youtube",
                "you tube",
            ],
            "Sony LIV": [
                "sony liv",
                "sonyliv",
            ],
            "Sony Pictures Core": [
                "sony pictures core",
                "sony picture core",
                "pictures core",
                "bravia core",
                "sony core",
            ],
            "Spotify": [
                "spotify",
            ],
            "Amazon Music": [
                "amazon music",
                "prime music",
            ],
            "Xstream Play": [
                "xstream play",
                "x stream play",
                "x streamplay",
                "xtream play",
                "x treme play",
                "extreme play",
                "stream play",
                "airtel xstream",
                "airtel xstream play",
                "airtel x streamplay",
                "airtel xtream",
                "airtel xtream play",
                "airtel extreme",
                "airtel extreme play",
                "airtel stream",
                "airtel stream play",
                "airtel streamplay",
            ],
            "ZEE5": [
                "zee5",
                "zee five",
            ],
        }

        selected_app = None

        for app_name, aliases in app_aliases.items():
            if any(re.search(rf"\b{re.escape(alias)}\b", text) for alias in aliases):
                selected_app = app_name
                break

        if not selected_app:
            return None

        # Make sure the registry actually supports TV app launching.
        if not self.registry.resolve_surface("living_room_tv", "app"):
            return RegistryVoiceCommand(
                handled=True,
                reason="TV app capability missing from registry",
                error_response=(
                    "I can recognize that app command, but TV app launching "
                    "is not enabled in the device registry yet."
                ),
            )

        command = RegistryCommandItem(
            spoken_target="living_room_tv",
            capability="app",
            action="launch_app",
            value=selected_app,
        )

        return RegistryVoiceCommand(
            handled=True,
            spoken_target=command.spoken_target,
            capability=command.capability,
            action=command.action,
            value=command.value,
            reason=f"deterministic TV app launch: {text}",
            commands=[command],
        )

    def route(self, transcript: str) -> RegistryVoiceCommand:
        transcript = transcript.strip()

        if not transcript:
            return RegistryVoiceCommand(False, reason="empty transcript")

        tv_home = self._route_tv_home_from_text(transcript)

        if tv_home:
            logger.info("Registry deterministic TV home navigation: %s", tv_home)
            return tv_home

        app_launch = self._route_tv_app_launch_from_text(transcript)

        if app_launch:
            logger.info("Registry deterministic TV app launch: %s", app_launch)
            return app_launch

        mentions_registry_target = self._transcript_mentions_registry_target(transcript)

        try:

            # Safe general veto:
            # If the user does not mention any configured device/group alias,
            # do not waste the smart-home router.
            #
            # This does NOT create commands. It only avoids sending obvious
            # general conversation to the smart-home router.
            if not mentions_registry_target:
                return RegistryVoiceCommand(
                    handled=False,
                    reason="no configured device or group mentioned",
                )

            # Fast LLM path: one semantic-frame call.
            proposal = self._ask_json(
                system_prompt=self._semantic_frame_prompt(),
                user_content=json.dumps(
                    {
                        "transcript": transcript,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                label="semantic_frame_fast",
            )

            result = self._validate_semantic_frame(proposal, transcript)

            if result.handled and result.commands and not result.error_response:
                result = self._repair_group_power_conflicts(result)
                result = self._repair_power_exception_from_text(result, transcript)
                return result

            # Important:
            # If a registry target was explicitly mentioned, do not blindly trust
            # a "general" result from the first LLM pass.
            #
            # This is the LLM-side correction path. We are not creating a command
            # in Python. We ask the LLM auditor to re-check the decision.
            first_error: Optional[RegistryVoiceCommand] = None

            if result.handled and result.error_response:
                first_error = result

            audited = self._ask_json(
                system_prompt=self._semantic_audit_prompt(),
                user_content=json.dumps(
                    {
                        "transcript": transcript,
                        "proposed_frame": proposal,
                        "validation_result": {
                            "handled": result.handled,
                            "reason": result.reason,
                            "error_response": result.error_response,
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                label="semantic_audit_retry",
            )

            audited_result = self._validate_semantic_frame(audited, transcript)

            if audited_result.handled and audited_result.commands and not audited_result.error_response:
                audited_result = self._repair_group_power_conflicts(audited_result)
                audited_result = self._repair_power_exception_from_text(audited_result, transcript)
                return audited_result

            if audited_result.handled and audited_result.error_response:
                first_error = first_error or audited_result

            repair = self._ask_json(
                system_prompt=self._repair_prompt(),
                user_content=json.dumps(
                    {
                        "transcript": transcript,
                        "proposal": proposal,
                        "audit": audited,
                        "validation_error": audited_result.reason,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                label="repair_retry",
            )

            repaired_result = self._validate_semantic_frame(repair, transcript)

            if repaired_result.handled and repaired_result.commands and not repaired_result.error_response:
                repaired_result = self._repair_group_power_conflicts(repaired_result)
                repaired_result = self._repair_power_exception_from_text(repaired_result, transcript)
                return repaired_result

            if first_error:
                return first_error

            return RegistryVoiceCommand(
                handled=False,
                reason="general conversation",
            )

        except Exception as e:
            logger.error("Semantic-frame registry router failed: %s", e)

            if mentions_registry_target:
                return RegistryVoiceCommand(
                    handled=True,
                    reason="semantic router error",
                    error_response=(
                        "I heard a smart-home command, but I could not parse it safely."
                    ),
                )

            return RegistryVoiceCommand(
                handled=False,
                reason="semantic router error",
            )

    def _ask_json(self, system_prompt: str, user_content: str, label: str) -> dict:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            "stream": False,
            "keep_alive": -1,
            "format": "json",
            "options": {
                "temperature": 0.0,
                "top_p": 0.1,

                # Router only needs tiny JSON output.
                "num_predict": 180,

                # Keep router context controlled.
                "num_ctx": 3072,

                # Ask Ollama to offload to GPU.
                "num_gpu": 99,
                "num_thread": 6,
            },
        }

        response = requests.post(
            self.url,
            json=payload,
            timeout=60,
        )
        response.raise_for_status()

        raw = response.json()["message"]["content"].strip()
        logger.info("Registry semantic %s raw: %s", label, raw)

        return self._extract_json(raw)

    def _build_registry_context(self) -> str:
        devices = []

        for device_id, device in self.registry.devices.items():
            surfaces = []

            for surface in device.surfaces.values():
                if surface.fallback_only:
                    continue

                surface_data: dict[str, Any] = {
                    "surface_id": surface.surface_id,
                    "entity_id": surface.entity_id,
                    "domain": surface.domain,
                    "capabilities": surface.capabilities,
                }

                if surface.sources:
                    surface_data["sources"] = surface.sources

                if surface.sound_modes:
                    surface_data["sound_modes"] = surface.sound_modes

                surfaces.append(surface_data)

            devices.append(
                {
                    "id": device_id,
                    "name": device.name,
                    "type": device.type,
                    "room": device.room,
                    "aliases": device.aliases,
                    "legacy_device_id": device.legacy_device_id,
                    "surfaces": surfaces,
                }
            )

        groups = []

        for group_id, group in self.registry.groups.items():
            groups.append(
                {
                    "id": group_id,
                    "name": group.get("name", group_id),
                    "aliases": group.get("aliases", []),
                    "members": group.get("members", []),
                }
            )

        return json.dumps(
            {
                "devices": devices,
                "groups": groups,
            },
            ensure_ascii=False,
            indent=2,
        )

    def _classification_prompt(self) -> str:
        return f"""
You are ZYRA's semantic domain classifier.

Your only job is to decide whether the user is controlling/checking a configured
smart-home device/group, or whether the user is doing general conversation.

Return ONLY valid JSON. No markdown. Do not answer the user.

Device registry:
{self.registry_context}

Allowed target IDs:
{json.dumps(self.allowed_target_ids, indent=2)}

Output schema:
{{
  "domain": "smart_home" or "general",
  "intent": "control" or "status" or "none",
  "target_hint": string or null,
  "confidence": number from 0.0 to 1.0,
  "reason": "short factual reason"
}}

Classify as smart_home when the user wants to:
- control power, volume, mute, source/input, sound mode, playback, or media on a configured device
- check status, power, volume, mute, source/input, sound mode, or playback of a configured device
- control/check a configured group like sound system, all speakers, or home theater

Classify as general when the user wants:
- movie recommendations
- music recommendations
- explanations
- factual Q&A
- casual conversation
- anything not controlling/checking a configured device

Important distinctions:
- "action movie" is a movie genre, not a smart-home action.
- "recommend a good action movie" is general.
- "play a movie recommendation" is general unless the user clearly says to play it on a configured device.
- "explain Bluetooth" is general.
- "change soundbar input to Bluetooth" is smart_home.
""".strip()

    def _semantic_frame_prompt(self) -> str:
        return f"""
You are ZYRA's semantic smart-home frame extractor.

You do not answer the user.
You convert the user's meaning into semantic frames.

Return ONLY valid JSON. No markdown.

Device registry:
{self.registry_context}

Allowed target IDs:
{json.dumps(self.allowed_target_ids, indent=2)}

Allowed semantic operations:
{json.dumps(sorted(self.OPERATIONS), indent=2)}

Output schema:
{{
  "domain": "smart_home" or "general",
  "intent": "control" or "status" or "none",
  "frames": [
    {{
      "target": "one allowed target ID only",
      "operation": "one allowed semantic operation",
      "value": string or number or object or null,
      "detail": string or null
    }}
  ],
  "confidence": number from 0.0 to 1.0,
  "reason": "short factual reason"
}}

Rules:
- You are not a chat assistant here.
- Do not answer the user's question.
- Do not guess the real current device state.
- The "reason" field must explain the routing decision, not the device status.
- If the utterance is general, return domain="general", intent="none", frames=[].
- If the utterance controls, checks, asks status of, asks state of, asks input/source of, asks volume of, asks mute state of, asks sound mode of, or asks playback/media state of a configured device/group, return domain="smart_home".
- If a configured target is mentioned together with a status/check/question about that target, it is smart_home, not general.
- Target must be canonical allowed ID only.
- Never output Home Assistant entity IDs.
- Never output display names.
- Never output surface IDs.

Status extraction rules:
- Broad status/state/condition/overview/summary question -> operation=status, detail="full".
- Input/source/HDMI question -> operation=status, detail="source".
- Volume/level/loudness question -> operation=status, detail="volume".
- Mute/muted question -> operation=status, detail="mute".
- Sound mode/mode question -> operation=status, detail="sound_mode".
- Playing/playback/media/watching question -> operation=status, detail="media".
- Power/on/off question -> operation=status, detail="power".
- Never return general for a status question about a configured target.

Target meanings:
- TV / television / Bravia -> living_room_tv
- soundbar / speaker bar / LG soundbar -> lg_s95tr_soundbar
- subwoofer / woofer / sub -> subwoofer
- rear speakers / surround speakers / surround system -> rear_speakers
- sound system -> sound_system
- all speakers -> all_speakers
- home theater / full system / everything -> home_theater

Operation meanings:
- turn on / power on / switch on main device power -> power_on
- turn off / shut down / power off main device power -> power_off
- sleep / put to sleep / standby actual smart device -> sleep
- wake / wake up actual smart device -> wake
- toggle / flip / switch state -> power_toggle
- louder / volume up / increase volume -> volume_up
- quieter / volume down / decrease volume -> volume_down
- set volume to exact number -> set_volume
- mute -> mute_on
- unmute -> mute_off
- toggle mute -> mute_toggle
- pause media -> pause
- play/resume media -> play
- stop media -> stop
- next track -> next
- previous track -> previous
- change input/source -> set_source
- change sound mode -> set_sound_mode
- open/launch/start an app on TV -> launch_app
- go to home / home screen / exit app / close app / close Netflix -> go_home
- ask status/info/current state -> status

Value rules:
- Do not invent volume values.
- For qualitative volume changes with no number, such as louder, quieter, increase volume, decrease volume, raise volume, or lower volume, use volume_up or volume_down and value=null.
- For relative step volume commands such as "increase volume by 5", "lower volume by 3", or "volume up 5 steps", use volume_up or volume_down and value must be the step count number.
- The numeric value for volume_up or volume_down means relative step count, not final volume.
- For exact final volume commands such as "set volume to 25", use set_volume and value must be the exact number the user gave.
- Never use set_volume unless the user gave an exact final volume number.
- For set_source, value is the requested source.
- For set_sound_mode, value is the requested sound mode.
- For launch_app, value is the requested app name such as Netflix, Prime Video, Jio Hotstar, YouTube, Sony LIV, or ZEE5.
- For status, put requested detail in detail:
  source, volume, mute, sound_mode, media, power, or full.

Important corrections:
- turn on TV -> target=living_room_tv, operation=power_on. This controls relay mains power, not TV soft wake.
- turn off TV -> target=living_room_tv, operation=power_off. This controls relay mains power, not TV soft sleep.
- wake TV -> target=living_room_tv, operation=wake.
- sleep TV -> target=living_room_tv, operation=sleep.
- turn on soundbar -> target=lg_s95tr_soundbar, operation=power_on. This controls relay mains power.
- turn off soundbar -> target=lg_s95tr_soundbar, operation=power_off. This controls relay mains power.
- wake soundbar -> target=lg_s95tr_soundbar, operation=wake.
- sleep soundbar -> target=lg_s95tr_soundbar, operation=sleep.
- mute television -> target=living_room_tv, operation=mute_on.
- mute cannot become power_off.
- pause TV -> target=living_room_tv, operation=pause.
- pause cannot become mute.
- put TV on HDMI 1 -> target=living_room_tv, operation=set_source, value="HDMI 1".
- change soundbar input to HDMI ARC -> target=lg_s95tr_soundbar, operation=set_source, value="HDMI ARC".
- use cinema sound on soundbar -> target=lg_s95tr_soundbar, operation=set_sound_mode, value="Cinema".
- turn off sound system -> target=sound_system, operation=power_off.
- turn off sound system and turn on TV -> two frames, sound_system power_off then living_room_tv power_on.
- shut down home theater except keep TV on -> all_speakers power_off then living_room_tv power_on.
- recommend a good action movie -> general, no frames.
- open Netflix on TV -> target=living_room_tv, operation=launch_app, value="Netflix".
- launch Prime Video -> target=living_room_tv, operation=launch_app, value="Prime Video".
- open Jio Hotstar -> target=living_room_tv, operation=launch_app, value="Jio Hotstar".
- start YouTube on TV -> target=living_room_tv, operation=launch_app, value="YouTube".
- go to TV home -> target=living_room_tv, operation=go_home.
- go to home screen -> target=living_room_tv, operation=go_home.
- exit the app -> target=living_room_tv, operation=go_home.
- close Netflix -> target=living_room_tv, operation=go_home.
- close Prime Video -> target=living_room_tv, operation=go_home.
""".strip()

    def _semantic_audit_prompt(self) -> str:
        return f"""
You are ZYRA's semantic frame auditor.

You receive:
- the original transcript
- the classification
- the proposed semantic frame

Correct the semantic frame if it does not exactly match the user's meaning.

Return ONLY valid JSON. No markdown. Do not answer the user.

Device registry:
{self.registry_context}

Allowed target IDs:
{json.dumps(self.allowed_target_ids, indent=2)}

Allowed semantic operations:
{json.dumps(sorted(self.OPERATIONS), indent=2)}

Output schema:
{{
  "domain": "smart_home" or "general",
  "intent": "control" or "status" or "none",
  "frames": [
    {{
      "target": "one allowed target ID only",
      "operation": "one allowed semantic operation",
      "value": string or number or object or null,
      "detail": string or null
    }}
  ],
  "confidence": number from 0.0 to 1.0,
  "reason": "short factual reason"
}}

Audit rules:
- If the transcript is general Q&A/conversation/recommendation, return general.
- Movie recommendations are general.
- The word "action" in "action movie" is a genre, not smart-home.
- If the transcript controls/checks a configured device, return smart_home.
- Use canonical target IDs only.
- Never output entity IDs.
- Never output display names.

False-general correction:
- If the proposed frame says domain="general" but the transcript mentions a configured device/group and asks about status, state, input, source, volume, mute, sound mode, playback, power, or media, correct it to domain="smart_home".
- Do not answer the user's status question.
- Do not put actual device state in the reason field.
- Extract a status frame instead.
- Broad "status/state/condition/overview/summary" questions use detail="full".

Strict semantic fidelity:
- Turn on/off TV or soundbar means relay mains power, not smart-device soft power.
- Wake TV/soundbar means smart-device soft turn_on.
- Sleep TV/soundbar means smart-device soft turn_off.
- Do not convert wake/sleep into relay power_on/power_off.
- Do not convert turn on/off into wake/sleep.
- mute TV/television must be living_room_tv + mute_on.
- mute/unmute must never become power_on or power_off.
- pause TV must be living_room_tv + pause.
- pause must never become mute.
- source/input change must be set_source.
- source/input change must never become power_on or status.
- sound mode change must be set_sound_mode.
- Qualitative volume changes with no number must be volume_up/volume_down with value=null.
- Relative step volume changes such as "by 5" or "5 steps" must be volume_up/volume_down with value as the step count.
- Exact final volume commands must be set_volume with the exact number.
- set_volume without a number is invalid and must be repaired.
- sound system means sound_system, not all_speakers.
- home theater except keep TV on means all_speakers power_off + living_room_tv power_on.
- go to home, go to home screen, exit app, close app, close Netflix, or close Prime Video must be living_room_tv + go_home.
- close app wording must not become launch_app.
""".strip()

    def _repair_prompt(self) -> str:
        return f"""
You are ZYRA's final semantic-frame repair pass.

The previous frame failed validation or was inconsistent.
Re-read the transcript and output the best registry-safe semantic frame.

Return ONLY valid JSON. No markdown. Do not answer the user.

Device registry:
{self.registry_context}

Allowed target IDs:
{json.dumps(self.allowed_target_ids, indent=2)}

Allowed semantic operations:
{json.dumps(sorted(self.OPERATIONS), indent=2)}

Output schema:
{{
  "domain": "smart_home" or "general",
  "intent": "control" or "status" or "none",
  "frames": [
    {{
      "target": "one allowed target ID only",
      "operation": "one allowed semantic operation",
      "value": string or number or object or null,
      "detail": string or null
    }}
  ],
  "confidence": number from 0.0 to 1.0,
  "reason": "short factual reason"
}}

Rules:
- If the transcript is general conversation/Q&A/recommendation, output general.
- Movie recommendations are general.
- "action movie" is not smart-home.
- If the transcript controls/checks a configured device, output smart_home.
- Use canonical target IDs only.
- Do not invent values.
- Turn on TV: living_room_tv + power_on. This is relay mains power.
- Turn off TV: living_room_tv + power_off. This is relay mains power.
- Wake TV: living_room_tv + wake.
- Sleep TV: living_room_tv + sleep.
- Turn on soundbar: lg_s95tr_soundbar + power_on. This is relay mains power.
- Turn off soundbar: lg_s95tr_soundbar + power_off. This is relay mains power.
- Wake soundbar: lg_s95tr_soundbar + wake.
- Sleep soundbar: lg_s95tr_soundbar + sleep.
- Mute TV: living_room_tv + mute_on.
- Unmute TV: living_room_tv + mute_off.
- Mute soundbar: lg_s95tr_soundbar + mute_on.
- Pause TV: living_room_tv + pause.
- Put TV on HDMI 1: living_room_tv + set_source + HDMI 1.
- Soundbar HDMI ARC: lg_s95tr_soundbar + set_source + HDMI ARC.
- Cinema soundbar: lg_s95tr_soundbar + set_sound_mode + Cinema.
- Open Netflix on TV: living_room_tv + launch_app + Netflix.
- Launch Prime Video: living_room_tv + launch_app + Prime Video.
- Open Jio Hotstar: living_room_tv + launch_app + Jio Hotstar.
- Start YouTube on TV: living_room_tv + launch_app + YouTube.
- Go to TV home: living_room_tv + go_home.
- Go to home screen: living_room_tv + go_home.
- Exit the app: living_room_tv + go_home.
- Close the app: living_room_tv + go_home.
- Close Netflix: living_room_tv + go_home.
- Close Prime Video: living_room_tv + go_home.
- Turn off sound system: sound_system + power_off.
- Home theater except TV on: all_speakers + power_off, then living_room_tv + power_on.
- If the transcript says "increase volume", "raise volume", "louder", "decrease volume", "lower volume", or "quieter" without an exact final number, use volume_up or volume_down with value=null.
- If the transcript says "by N" or "N steps", volume_up or volume_down may keep value=N as a relative step count.
- Exact final volume commands such as "set volume to 25" must become set_volume with that number.
- set_volume without a number is invalid.

False-general repair:
- If the transcript mentions a configured registry target and asks about that target's status/state/input/source/volume/mute/sound mode/playback/power/media, output smart_home.
- Do not answer the device status.
- Output a semantic status frame.
- Broad status/state/condition/overview/summary questions use detail="full".
""".strip()

    def _validate_semantic_frame(
        self,
        data: dict,
        transcript: str = "",
    ) -> RegistryVoiceCommand:
        domain = str(data.get("domain", "general")).strip().lower()
        intent = str(data.get("intent", "none")).strip().lower()
        confidence = self._safe_float(data.get("confidence"), default=0.0)
        reason = str(data.get("reason", "")).strip()

        if domain != "smart_home":
            return RegistryVoiceCommand(
                handled=False,
                reason=reason or "general conversation",
            )

        if confidence < 0.60:
            return RegistryVoiceCommand(
                handled=True,
                reason=reason or "low confidence smart-home parse",
                error_response="I heard a smart-home request, but I could not understand it safely.",
            )

        frames = data.get("frames")

        # Backward compatibility if the model accidentally returns commands.
        if frames is None:
            frames = data.get("commands")

        if not isinstance(frames, list) or not frames:
            return RegistryVoiceCommand(
                handled=True,
                reason=reason or "missing semantic frames",
                error_response="I understood that as a smart-home request, but I could not find a valid device command.",
            )

        commands: list[RegistryCommandItem] = []

        for index, frame in enumerate(frames, start=1):
            if not isinstance(frame, dict):
                return self._invalid_command(
                    f"frame {index} is not an object",
                    "I could not parse that smart-home command safely.",
                )

            parsed = self._frame_to_command(frame, intent, transcript)

            if isinstance(parsed, RegistryVoiceCommand):
                return parsed

            commands.append(parsed)

        commands = self._dedupe_commands(commands)

        if not commands:
            return RegistryVoiceCommand(
                handled=True,
                reason="no executable commands",
                error_response="I understood the smart-home request, but there was nothing safe to execute.",
            )

        first = commands[0]

        return RegistryVoiceCommand(
            handled=True,
            spoken_target=first.spoken_target,
            capability=first.capability,
            action=first.action,
            value=first.value,
            reason=reason or "semantic-frame smart-home command",
            commands=commands,
        )
    
    def _normalize_volume_operation_from_transcript(
        self,
        operation: str,
        value: Any,
        transcript: str,
    ) -> tuple[str, Any]:
        """
        Normalize volume operation using command grammar.

        Why:
        The LLM can incorrectly output:
            operation=volume_up, value=None

        for a command like:
            "set the soundbar volume to 10"

        That is semantically wrong because:
            volume_up / volume_down = relative movement
            set_volume = exact final volume level
        """

        if operation not in {"volume_up", "volume_down", "set_volume"}:
            return operation, value

        text = self._normalize_text(transcript)

        if "volume" not in text:
            return operation, value

        # Repair common LLM mistake:
        # "increase/decrease volume" is a relative command and should become
        # volume_up/volume_down with value=None.
        if operation == "set_volume" and value is None:
            if re.search(
                r"\b(increase|raise|up|louder|higher|boost|turn up)\b",
                text,
            ):
                return "volume_up", None

            if re.search(
                r"\b(decrease|lower|down|quieter|reduce|turn down)\b",
                text,
            ):
                return "volume_down", None
            
        # Relative amount grammar.
        # Examples:
        #   increase TV volume by 5
        #   decrease TV volume by 3
        #   raise TV volume 10 steps
        relative_match = re.search(r"\bby\s+(\d{1,3})\b", text)

        if not relative_match:
            relative_match = re.search(r"\b(\d{1,3})\s+steps?\b", text)

        if relative_match:
            number = self._safe_float(relative_match.group(1), default=None)

            if number is None:
                return operation, value

            steps = max(1, min(round(number), 50))

            if operation in {"volume_up", "volume_down"}:
                return operation, steps

            # Repair possible LLM mistake:
            # "increase volume by 5" should never become set_volume=5.
            if operation == "set_volume":
                if re.search(r"\b(increase|raise|up|louder|turn up)\b", text):
                    return "volume_up", steps

                if re.search(r"\b(decrease|lower|down|quieter|turn down)\b", text):
                    return "volume_down", steps

            return operation, value

        # Exact final volume grammar.
        # Examples:
        #   volume to 10
        #   volume at 10
        #   volume level to 25
        #   soundbar volume 10
        exact_match = re.search(
            r"\bvolume(?:\s+level)?\s+(?:to|at|as)?\s*(\d{1,3})\b",
            text,
        )

        if not exact_match:
            exact_match = re.search(
                r"\b(?:to|at)\s+(\d{1,3})\b.*\bvolume\b",
                text,
            )

        if not exact_match:
            return operation, value

        number = self._safe_float(exact_match.group(1), default=None)

        if number is None:
            return operation, value

        number = max(0, min(round(number), 100))

        return "set_volume", number
    
    def _normalize_sleep_wake_operation_from_transcript(
        self,
        operation: str,
        transcript: str,
        device_ids: list[str],
    ) -> str:
        """
        Normalize soft sleep/wake commands using semantic command grammar.

        """

        if operation not in {"power_on", "power_off"}:
            return operation

        text = self._normalize_text(transcript)

        if not text:
            return operation

        # Only convert if every resolved target supports sleep_wake.
        #
        # Why:
        # "wake TV" and "wake soundbar" are valid.
        # But "wake home theater" should not blindly become sleep_wake,
        # because subwoofer/rear speakers do not have soft sleep/wake.
        if not device_ids:
            return operation

        if not all(
            self.registry.resolve_surface(device_id, "sleep_wake")
            for device_id in device_ids
        ):
            return operation

        if re.search(r"\b(wake|wake up|wakeup|soft on)\b", text):
            return "wake"

        if re.search(r"\b(sleep|standby|stand by|soft off)\b", text):
            return "sleep"

        return operation

    def _frame_to_command(
        self,
        frame: dict,
        intent: str,
        transcript: str = "",
    ) -> RegistryCommandItem | RegistryVoiceCommand:
        raw_target = str(frame.get("target") or "").strip()

        if not raw_target:
            return self._invalid_command(
                "missing target",
                "I could not identify which device you meant.",
            )

        canonical_target, device_ids = self._canonicalize_target(raw_target)

        if not canonical_target or not device_ids:
            return self._invalid_command(
                f"target not in registry: {raw_target}",
                f"I could not find {raw_target} in the device registry.",
            )

        raw_operation = (
            frame.get("operation")
            or frame.get("op")
            or frame.get("action")
        )

        operation = self._normalize_operation(raw_operation)
        intent_token = self._normalize_token(intent)

        # Do not blindly let intent: status override operation: sleep.
        if intent_token == "status" and operation not in self.OPERATIONS:
            operation = "status"

        # Generic semantic normalization.
        raw_value = frame.get("value")

        operation, raw_value = self._normalize_volume_operation_from_transcript(
            operation=operation,
            value=raw_value,
            transcript=transcript,
        )

        operation = self._normalize_sleep_wake_operation_from_transcript(
            operation=operation,
            transcript=transcript,
            device_ids=device_ids,
        )

        if operation not in self.OPERATIONS:
            return self._invalid_command(
                f"unsupported operation: {operation}",
                "I understood the device, but I do not support that smart-home action yet.",
            )

        # Preserve registry groups mentioned in the actual transcript.
        # Example:
        #   "turn off the sound system and turn on the TV"
        canonical_target, device_ids = self._prefer_spoken_group_target(
            raw_target=canonical_target,
            device_ids=device_ids,
            operation=operation,
            transcript=transcript,
        )

        capability, action = self.OPERATION_ACTION_MAP[operation]

        if action == "status":
            detail = frame.get("detail")
            value = frame.get("value")

            if detail is None:
                detail = value

            # First trust the actual user transcript, not the LLM's guessed detail.
            # The user asked for broad status, so we must return full.
            transcript_detail = self._infer_status_detail_from_text(transcript)

            if self._is_broad_status_query(transcript):
                status_detail = "full"
            elif transcript_detail != "full":
                status_detail = transcript_detail
            else:
                status_detail = self._normalize_status_detail(detail)

            return RegistryCommandItem(
                spoken_target=canonical_target,
                capability="status",
                action="status",
                value=status_detail,
            )

        value = raw_value

        # Semantic consistency guard.
        # volume_up / volume_down may carry a number
        if action in {"volume_up", "volume_down"} and value is not None:
            text = self._normalize_text(transcript)

            has_relative_step_amount = (
                re.search(r"\bby\s+\d{1,3}\b", text)
                or re.search(r"\b\d{1,3}\s+steps?\b", text)
            )

            if not has_relative_step_amount:
                return self._invalid_command(
                    (
                        "relative volume action received a non-step value: "
                        f"action={action} value={value}"
                    ),
                    (
                        "I understood a volume command, but the parsed command mixed "
                        "relative and exact volume semantics."
                    ),
                )

        capability = self._choose_valid_capability(
            device_ids=device_ids,
            requested_capability=capability,
            action=action,
        )

        if not capability:
            return self._invalid_command(
                f"capability unavailable target={canonical_target} action={action}",
                f"{canonical_target} does not support that action in the current registry.",
            )

        value = self._normalize_value(
            device_ids=device_ids,
            capability=capability,
            action=action,
            value=value,
        )

        if action in {
            "set_volume",
            "select_source",
            "select_sound_mode",
            "play_media",
            "launch_app",
        } and value is None:
            return self._invalid_command(
                f"missing value for action={action}",
                "I understood the command, but the required value was missing.",
            )

        return RegistryCommandItem(
            spoken_target=canonical_target,
            capability=capability,
            action=action,
            value=value,
        )
    
    def _transcript_mentions_registry_target(self, transcript: str) -> bool:
        """
        Return True only if the transcript explicitly mentions a configured
        registry device or group alias.

        Why:
        This prevents general requests like:
            "recommend a good action movie"

        from being turned into smart-home commands by an over-eager LLM repair pass.

        This is not sentence hardcoding. It reads aliases from device_registry.json.
        """

        text = self._normalize_text(transcript)

        if not text:
            return False

        aliases: list[str] = []

        for device_id, device in self.registry.devices.items():
            aliases.extend(
                [
                    device_id,
                    device.name,
                    device.legacy_device_id or "",
                    *device.aliases,
                ]
            )

        for group_id, group in self.registry.groups.items():
            aliases.extend(
                [
                    group_id,
                    group.get("name", ""),
                    *group.get("aliases", []),
                ]
            )

        # Longest aliases first prevents weak short aliases from dominating.
        aliases = sorted(
            {
                self._normalize_text(alias)
                for alias in aliases
                if alias and self._normalize_text(alias)
            },
            key=len,
            reverse=True,
        )

        for alias in aliases:
            if re.search(rf"\b{re.escape(alias)}\b", text):
                return True

        return False

    def _canonicalize_target(self, raw_target: str) -> tuple[str, list[str]]:
        raw_norm = self._normalize_text(raw_target)

        for device_id, device in self.registry.devices.items():
            candidates = [
                device_id,
                device.name,
                device.legacy_device_id or "",
                *device.aliases,
            ]

            for surface in device.surfaces.values():
                candidates.append(surface.surface_id)
                candidates.append(surface.entity_id)

            for candidate in candidates:
                if candidate and self._normalize_text(candidate) == raw_norm:
                    return device_id, [device_id]

        for group_id, group in self.registry.groups.items():
            candidates = [
                group_id,
                group.get("name", ""),
                *group.get("aliases", []),
            ]

            for candidate in candidates:
                if candidate and self._normalize_text(candidate) == raw_norm:
                    members = list(group.get("members", []))
                    return group_id, members

        resolved = self.registry.resolve_target(raw_target)

        if not resolved:
            return "", []

        if len(resolved) == 1:
            return resolved[0], resolved

        resolved_set = set(resolved)

        for group_id, group in self.registry.groups.items():
            members = list(group.get("members", []))

            if set(members) == resolved_set:
                return group_id, members

        return "", resolved
    
    def _prefer_spoken_group_target(
        self,
        raw_target: str,
        device_ids: list[str],
        operation: str,
        transcript: str,
    ) -> tuple[str, list[str]]:
        """
        Preserve group targets when the user's transcript explicitly mentions a
        registry group alias.

        Why:
        The LLM may reduce "sound system" to only "soundbar".
        But in device_registry.json, sound_system = soundbar + subwoofer.

        This is not phrase hardcoding. It reads group names and aliases from the
        registry and uses them as the source of truth.
        """

        if not transcript:
            return raw_target, device_ids

        # Groups in the current registry mainly support power/status semantics.
        # Do not upgrade groups for source, volume, sound mode, etc.
        if operation not in {"power_on", "power_off", "power_toggle", "status"}:
            return raw_target, device_ids

        # If already a group, keep it.
        if raw_target in self.registry.groups:
            return raw_target, device_ids

        transcript_norm = self._normalize_text(transcript)
        current_members = set(device_ids)

        candidates: list[tuple[int, str, list[str]]] = []

        for group_id, group in self.registry.groups.items():
            members = list(group.get("members", []))

            if not members:
                continue

            # Only consider groups that include the LLM-chosen device.
            # Example:
            #   raw target lg_s95tr_soundbar can be upgraded to sound_system
            #   because sound_system contains lg_s95tr_soundbar.
            if current_members and not current_members.issubset(set(members)):
                continue

            aliases = [
                group_id,
                group.get("name", ""),
                *group.get("aliases", []),
            ]

            for alias in aliases:
                alias_norm = self._normalize_text(alias)

                if not alias_norm:
                    continue

                if re.search(rf"\b{re.escape(alias_norm)}\b", transcript_norm):
                    candidates.append(
                        (
                            len(alias_norm),
                            group_id,
                            members,
                        )
                    )

        if not candidates:
            return raw_target, device_ids

        # Prefer the longest explicit group alias.
        # Example: "home theater" beats "everything" if both appear.
        candidates.sort(reverse=True, key=lambda item: item[0])

        _, group_id, members = candidates[0]
        return group_id, members

    def _choose_valid_capability(
        self,
        device_ids: list[str],
        requested_capability: str,
        action: str,
    ) -> str:
        candidates: list[str] = []

        for candidate in self.ACTION_CAPABILITY_CANDIDATES.get(action, []):
            if candidate not in candidates:
                candidates.append(candidate)

        if requested_capability and requested_capability not in candidates:
            candidates.append(requested_capability)

        for capability in candidates:
            if self._all_devices_support_capability(device_ids, capability):
                return capability

        if len(device_ids) == 1:
            for capability in candidates:
                if self.registry.resolve_surface(device_ids[0], capability):
                    return capability

        return ""

    def _all_devices_support_capability(
        self,
        device_ids: list[str],
        capability: str,
    ) -> bool:
        if not capability:
            return False

        for device_id in device_ids:
            if not self.registry.resolve_surface(device_id, capability):
                return False

        return True

    def _normalize_value(
        self,
        device_ids: list[str],
        capability: str,
        action: str,
        value: Any,
    ):
        if value is None:
            return None

        if action == "set_volume":
            number = self._safe_float(value, default=None)

            if number is None:
                return None

            return max(0, min(round(number), 100))

        if action == "select_source":
            return self._closest_registry_value(
                device_ids=device_ids,
                capability=capability,
                attr_name="sources",
                value=value,
            )

        if action == "select_sound_mode":
            return self._closest_registry_value(
                device_ids=device_ids,
                capability=capability,
                attr_name="sound_modes",
                value=value,
            )

        if action == "play_media":
            return value if isinstance(value, dict) else None

        if action == "launch_app":
            raw = str(value or "").strip().lower()

            app_aliases = {
                "netflix": "Netflix",

                "prime": "Prime Video",
                "prime video": "Prime Video",
                "amazon prime": "Prime Video",
                "amazon prime video": "Prime Video",

                "hotstar": "Jio Hotstar",
                "jio hotstar": "Jio Hotstar",
                "jiohotstar": "Jio Hotstar",
                "disney hotstar": "Jio Hotstar",
                "disney plus hotstar": "Jio Hotstar",

                "youtube": "YouTube",
                "you tube": "YouTube",

                "sony liv": "Sony LIV",
                "sonyliv": "Sony LIV",

                "sony pictures core": "Sony Pictures Core",
                "sony picture core": "Sony Pictures Core",
                "pictures core": "Sony Pictures Core",
                "bravia core": "Sony Pictures Core",
                "sony core": "Sony Pictures Core",

                "spotify": "Spotify",

                "amazon music": "Amazon Music",
                "prime music": "Amazon Music",

                "xstream play": "Xstream Play",
                "x stream play": "Xstream Play",
                "x streamplay": "Xstream Play",
                "xtream play": "Xstream Play",
                "x treme play": "Xstream Play",
                "extreme play": "Xstream Play",
                "stream play": "Xstream Play",
                "airtel xstream": "Xstream Play",
                "airtel xstream play": "Xstream Play",
                "airtel xstreamplay": "Xstream Play",
                "airtel x streamplay": "Xstream Play",
                "airtel xtream": "Xstream Play",
                "airtel xtream play": "Xstream Play",
                "airtel extreme": "Xstream Play",
                "airtel extreme play": "Xstream Play",
                "airtel stream": "Xstream Play",
                "airtel streamplay": "Xstream Play",
                "airtel stream play": "Xstream Play",

                "zee5": "ZEE5",
                "zee five": "ZEE5",
            }

            return app_aliases.get(raw, str(value).strip())

        return value

    def _closest_registry_value(
        self,
        device_ids: list[str],
        capability: str,
        attr_name: str,
        value: Any,
    ) -> Optional[str]:
        raw_value = str(value or "").strip()

        if not raw_value:
            return None

        allowed_values: list[str] = []

        for device_id in device_ids:
            device = self.registry.get_device(device_id)

            if not device:
                continue

            for surface in device.surfaces.values():
                if capability not in surface.capabilities:
                    continue

                values = getattr(surface, attr_name, None) or []

                for item in values:
                    if item not in allowed_values:
                        allowed_values.append(item)

        if not allowed_values:
            return raw_value

        raw_norm = self._normalize_text(raw_value)

        for allowed in allowed_values:
            if self._normalize_text(allowed) == raw_norm:
                return allowed

        containing = [
            allowed
            for allowed in allowed_values
            if raw_norm and raw_norm in self._normalize_text(allowed)
        ]

        if containing:
            # For HDMI ARC, this chooses "Optical/HDMI ARC" over "HDMI".
            return max(containing, key=len)

        normalized_map = {
            self._normalize_text(allowed): allowed
            for allowed in allowed_values
        }

        match = get_close_matches(
            raw_norm,
            normalized_map.keys(),
            n=1,
            cutoff=0.55,
        )

        if match:
            return normalized_map[match[0]]

        return raw_value
    
    def _repair_power_exception_from_text(
        self,
        route: RegistryVoiceCommand,
        transcript: str,
    ) -> RegistryVoiceCommand:
        """
        Registry-based repair for dangerous group exception commands.

        Example:
            "shut down the home theater except keep the TV on"

        Meaning:
            home_theater members - living_room_tv = all_speakers
            then keep living_room_tv on

        This is not exact sentence hardcoding. It uses:
        - registry group aliases
        - registry device aliases
        - registry group membership algebra

        Why this exists:
        Broad group commands are dangerous. If the LLM returns home_theater on/off
        incorrectly, this repair protects explicitly mentioned exception devices.
        """

        if not transcript:
            return route

        text = self._normalize_text(transcript)

        exception_markers = {
            "except",
            "other than",
            "apart from",
            "excluding",
            "but keep",
            "keep",
            "leave",
        }

        if not any(marker in text for marker in exception_markers):
            return route

        base_action = self._infer_power_action_from_text(text)

        if base_action not in {"on", "off"}:
            return route

        mentioned_group = self._find_mentioned_group(text)

        if not mentioned_group:
            return route

        protected_devices = self._find_mentioned_devices_in_exception_part(text)

        if not protected_devices:
            return route

        group_members = self._members_for_target(mentioned_group)

        if not group_members:
            return route

        protected_members = set(protected_devices)
        remaining_members = sorted(group_members - protected_members)

        if not remaining_members:
            return route

        replacement_group = self._group_for_members(remaining_members)

        repaired_commands: list[RegistryCommandItem] = []

        if replacement_group:
            repaired_commands.append(
                RegistryCommandItem(
                    spoken_target=replacement_group,
                    capability="power",
                    action=base_action,
                    value=None,
                )
            )
        else:
            for device_id in remaining_members:
                repaired_commands.append(
                    RegistryCommandItem(
                        spoken_target=device_id,
                        capability="power",
                        action=base_action,
                        value=None,
                    )
                )

        protected_action = self._infer_protected_power_action_from_text(text)

        if protected_action:
            for device_id in sorted(protected_members):
                repaired_commands.append(
                    RegistryCommandItem(
                        spoken_target=device_id,
                        capability="power",
                        action=protected_action,
                        value=None,
                    )
                )

        repaired_commands = self._dedupe_commands(repaired_commands)

        if not repaired_commands:
            return route

        first = repaired_commands[0]

        return RegistryVoiceCommand(
            handled=True,
            spoken_target=first.spoken_target,
            capability=first.capability,
            action=first.action,
            value=first.value,
            reason="registry group exception repair",
            commands=repaired_commands,
            error_response="",
        )
    
    def _infer_power_action_from_text(self, text: str) -> Optional[str]:
        """
        Infer only broad power direction for exception safety repair.
        This is not the main command router.
        """

        off_words = {
            "off",
            "shutdown",
            "shut down",
            "turn off",
            "switch off",
            "power off",
            "disable",
            "deactivate",
            "kill",
        }

        on_words = {
            "on",
            "turn on",
            "switch on",
            "power on",
            "enable",
            "activate",
            "start",
        }

        if any(word in text for word in off_words):
            return "off"

        if any(word in text for word in on_words):
            return "on"

        return None


    def _infer_protected_power_action_from_text(self, text: str) -> Optional[str]:
        """
        Infer whether the exception device should be explicitly kept on/off.

        Example:
        keep TV on -> on
        leave TV off -> off
        """

        if "keep" not in text and "leave" not in text:
            return None

        if re.search(r"\b(keep|leave)\b.+\bon\b", text):
            return "on"

        if re.search(r"\b(keep|leave)\b.+\boff\b", text):
            return "off"

        return None


    def _find_mentioned_group(self, text: str) -> Optional[str]:
        """
        Find the most specific registry group explicitly mentioned in transcript.
        """

        candidates: list[tuple[int, str]] = []

        for group_id, group in self.registry.groups.items():
            aliases = [
                group_id,
                group.get("name", ""),
                *group.get("aliases", []),
            ]

            for alias in aliases:
                alias_norm = self._normalize_text(alias)

                if not alias_norm:
                    continue

                if re.search(rf"\b{re.escape(alias_norm)}\b", text):
                    candidates.append((len(alias_norm), group_id))

        if not candidates:
            return None

        candidates.sort(reverse=True, key=lambda item: item[0])
        return candidates[0][1]


    def _find_mentioned_devices_in_exception_part(self, text: str) -> list[str]:
        """
        Find device aliases mentioned after an exception/keep marker.
        """

        split_match = re.search(
            r"\b(?:except|other than|apart from|excluding|but keep|keep|leave)\b",
            text,
        )

        if split_match:
            exception_part = text[split_match.start():]
        else:
            exception_part = text

        found: list[str] = []

        for device_id, device in self.registry.devices.items():
            aliases = [
                device_id,
                device.name,
                device.legacy_device_id or "",
                *device.aliases,
            ]

            for alias in aliases:
                alias_norm = self._normalize_text(alias)

                if not alias_norm:
                    continue

                if re.search(rf"\b{re.escape(alias_norm)}\b", exception_part):
                    if device_id not in found:
                        found.append(device_id)

                    break

        return found

    def _repair_group_power_conflicts(
            self,
            route: RegistryVoiceCommand,
        ) -> RegistryVoiceCommand:
            """
            Registry membership algebra, not phrase matching.

            Example:
                home_theater off + living_room_tv on

            Since home_theater contains TV, replace broad home_theater off with
            all_speakers off.
            """

            commands = route.commands

            later_power_on_members: set[str] = set()

            for command in commands:
                if command.capability == "power" and command.action == "on":
                    later_power_on_members.update(
                        self._members_for_target(command.spoken_target)
                    )

            repaired: list[RegistryCommandItem] = []

            for command in commands:
                if command.capability != "power" or command.action != "off":
                    repaired.append(command)
                    continue

                members = self._members_for_target(command.spoken_target)

                if not members:
                    repaired.append(command)
                    continue

                conflict = members.intersection(later_power_on_members)

                if not conflict:
                    repaired.append(command)
                    continue

                remaining = sorted(members - conflict)

                if not remaining:
                    continue

                replacement_group = self._group_for_members(remaining)

                if replacement_group:
                    repaired.append(
                        RegistryCommandItem(
                            spoken_target=replacement_group,
                            capability="power",
                            action="off",
                            value=None,
                        )
                    )
                else:
                    for device_id in remaining:
                        repaired.append(
                            RegistryCommandItem(
                                spoken_target=device_id,
                                capability="power",
                                action="off",
                                value=None,
                            )
                        )

            # Preserve explicit ON commands at the end, without duplicating.
            for command in commands:
                if command.capability == "power" and command.action == "on":
                    repaired.append(command)

            repaired = self._dedupe_commands(repaired)

            if not repaired:
                return route

            first = repaired[0]

            return RegistryVoiceCommand(
                handled=True,
                spoken_target=first.spoken_target,
                capability=first.capability,
                action=first.action,
                value=first.value,
                reason=route.reason,
                commands=repaired,
                error_response="",
            )

    def _members_for_target(self, target: str) -> set[str]:
        if target in self.registry.devices:
            return {target}

        group = self.registry.groups.get(target)

        if group:
            return set(group.get("members", []))

        return set()

    def _group_for_members(self, members: list[str]) -> Optional[str]:
        member_set = set(members)

        for group_id, group in self.registry.groups.items():
            if set(group.get("members", [])) == member_set:
                return group_id

        return None

    def _dedupe_commands(
        self,
        commands: list[RegistryCommandItem],
    ) -> list[RegistryCommandItem]:
        seen = set()
        out: list[RegistryCommandItem] = []

        for command in commands:
            key = (
                command.spoken_target,
                command.capability,
                command.action,
                json.dumps(command.value, sort_keys=True, default=str),
            )

            if key in seen:
                continue

            seen.add(key)
            out.append(command)

        return out

    def _normalize_operation(self, value: Any) -> str:
        op = self._normalize_token(value)
        return self.OPERATION_ALIASES.get(op, op)

    def _normalize_status_detail(self, value: Any) -> str:
        detail = self._normalize_token(value).replace("_state", "")

        if not detail:
            return "full"

        if detail in self.STATUS_DETAILS:
            return detail

        if "source" in detail or "input" in detail or "hdmi" in detail:
            return "source"

        if "volume" in detail or "loudness" in detail:
            return "volume"

        if "mute" in detail:
            return "mute"

        if "sound_mode" in detail or "soundmode" in detail:
            return "sound_mode"

        if detail == "mode":
            return "sound_mode"

        if "playback" in detail or "media" in detail or "playing" in detail:
            return "media"

        if "power" in detail or detail in {"on", "off"}:
            return "power"

        match = get_close_matches(
            detail,
            self.STATUS_DETAILS,
            n=1,
            cutoff=0.72,
        )

        return match[0] if match else "full"
    
    def _is_broad_status_query(self, text: str) -> bool:
        """
        Detect broad status queries.

        Broad:
          "what is the status of my TV"
          "how is my soundbar"
          "check TV status"

        Not broad:
          "what input is the TV using"
          "what is the soundbar volume"
          "is the TV muted"
          "what sound mode is the soundbar using"
        """

        normalized = self._normalize_text(text)
        tokens = set(normalized.split())

        broad_words = {
            "status",
            "state",
            "condition",
            "overview",
            "summary",
        }

        specific_words = {
            "input",
            "source",
            "hdmi",
            "volume",
            "loudness",
            "level",
            "mute",
            "muted",
            "sound",
            "mode",
            "playing",
            "playback",
            "media",
            "watching",
            "power",
        }

        if not tokens.intersection(broad_words):
            return False

        if tokens.intersection(specific_words):
            return False

        return True
    
    def _infer_status_detail_from_text(self, text: str) -> str:
        normalized = self._normalize_text(text)

        tokens = set(normalized.split())

        if tokens.intersection({"input", "source", "hdmi"}):
            return "source"

        if tokens.intersection({"volume", "loudness", "level"}):
            return "volume"

        if tokens.intersection({"mute", "muted"}):
            return "mute"

        if "sound mode" in normalized or tokens.intersection({"mode", "cinema", "atmos"}):
            return "sound_mode"

        if tokens.intersection({"playing", "playback", "media", "watching"}):
            return "media"

        if tokens.intersection({"power", "on", "off", "state"}):
            return "power"

        return "full"

    def _normalize_text(self, value: Any) -> str:
        text = str(value or "").lower().strip()
        text = re.sub(r"[^a-z0-9\s]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _normalize_token(self, value: Any) -> str:
        text = str(value or "").lower().strip()
        text = re.sub(r"[^a-z0-9\s_]", " ", text)
        text = re.sub(r"\s+", "_", text)
        text = re.sub(r"_+", "_", text)
        return text.strip("_")

    def _extract_json(self, raw: str) -> dict:
        raw = raw.strip()

        if raw.startswith("{") and raw.endswith("}"):
            return json.loads(raw)

        match = re.search(r"\{.*\}", raw, re.DOTALL)

        if not match:
            raise ValueError(f"No JSON object found: {raw}")

        return json.loads(match.group(0))

    def _invalid_command(self, reason: str, response: str) -> RegistryVoiceCommand:
        return RegistryVoiceCommand(
            handled=True,
            reason=reason,
            error_response=response,
        )

    def _safe_float(
        self,
        value: Any,
        default: Optional[float],
    ) -> Optional[float]:
        try:
            return float(value)
        except Exception:
            return default