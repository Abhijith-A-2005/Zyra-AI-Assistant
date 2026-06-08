import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import requests

from config import OLLAMA_URL, OLLAMA_MODEL

logger = logging.getLogger(__name__)


@dataclass
class RoutedCommand:
    action: str
    devices: list[str]


@dataclass
class RoutedIntent:
    domain: str
    intent: str
    action: Optional[str]
    devices: list[str]
    confidence: float
    reason: str = ""
    commands: list[RoutedCommand] = field(default_factory=list)


class IntentRouter:
    """
    Converts natural speech transcripts into strict machine intents.

    This router supports both:
    - single action: turn on TV
    - multi action: turn off TV and turn on everything else
    """

    def __init__(self):
        self.url = f"{OLLAMA_URL}/api/chat"
        self.model = OLLAMA_MODEL
        logger.info(f"Intent router ready — model: {self.model}")

    def route(self, transcript: str) -> RoutedIntent:
        transcript = transcript.strip()

        if not transcript:
            return RoutedIntent(
                "general", "none", None, [], 0.0,
                "empty transcript", []
            )

        messages = [
            {
                "role": "system",
                "content": self._router_prompt(),
            },
            {
                "role": "user",
                "content": transcript,
            },
        ]

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": -1,
            "format": "json",
            "options": {
                "temperature": 0.0,
                "top_p": 0.1,
                "num_predict": 220,
                "num_gpu": 99,
                "num_thread": 6,
            },
        }

        try:
            response = requests.post(
                self.url,
                json=payload,
                timeout=20,
            )
            response.raise_for_status()

            raw = response.json()["message"]["content"].strip()
            logger.info(f"Intent router raw: {raw}")

            data = self._extract_json(raw)
            intent = self._validate(data, transcript)

            logger.info(f"Intent router parsed: {intent}")
            return intent

        except Exception as e:
            logger.error(f"Intent router error: {e}")

            text = self._normalize_text(transcript)

            if self._looks_like_exact_known_command(text):
                return RoutedIntent(
                    domain="smart_home",
                    intent="control",
                    action=None,
                    devices=[],
                    confidence=0.60,
                    reason="router failed on likely smart-home command",
                    commands=[],
                )

            return RoutedIntent(
                domain="general",
                intent="none",
                action=None,
                devices=[],
                confidence=0.0,
                reason="router error",
                commands=[],
            )

    def _router_prompt(self) -> str:
        return """
You are ZYRA's smart-home intent router.

Your only job is to convert the user's transcript into strict JSON.

Return ONLY valid JSON.
No markdown.
No explanation outside JSON.

Allowed JSON schema:
{
  "domain": "smart_home" or "general",
  "intent": "control" or "status" or "none",
  "action": "on" or "off" or "toggle" or null,
  "devices": ["tv", "soundbar", "subwoofer", "rear"] or ["all"] or [],
  "commands": [
    {
      "action": "on" or "off" or "toggle",
      "devices": ["tv", "soundbar", "subwoofer", "rear"] or ["all"]
    }
  ],
  "confidence": number from 0.0 to 1.0,
  "reason": "short reason"
}

Important:
- For control commands, always fill the "commands" list.
- For one action, commands has one item.
- For mixed actions OR multiple device clauses, commands must include every clause. Example: "turn off sound system and turn off TV" has two command items.
- Keep "action" and "devices" as a summary of the first command only.
- For status questions, commands must be [].

Device meanings:
- tv = TV, Sony TV, television
- soundbar = soundbar, sound bar, speaker bar
- subwoofer = subwoofer, woofer, sub
- rear = rear speakers, surround speakers, back speakers, surround system, rear system
- sound system/audio system = soundbar + subwoofer
- all speakers/speakers/speaker system = soundbar + subwoofer + rear. Never include TV for speaker-only groups.
- surround system/surround speakers/rear system/back speakers = rear only.
- home theater/home theatre/full system/everything/all devices = tv + soundbar + subwoofer + rear
- "all" as a device list means every device including TV. Use ["all"] only for all devices/home theater/everything, never for all speakers.

Rules:
- If the user asks what is on/off, which devices are on/off, status, or asks "is/are device on/off", intent must be "status". Do NOT use control.
- If the user asks to turn/switch/power/activate/start/fire up something, intent is "control".
- If the user asks to shut down/disable/deactivate/kill/stop/turn off something, action is "off".
- If the user asks to fire up/turn on/start/activate something, action is "on".
- If the user asks to toggle/flip/switch state, action is "toggle".
- "everything else" means all devices except the device already mentioned in the opposite command.
- If this is not a smart-home device request, use domain "general", intent "none", action null, devices [], commands [].
- If uncertain, set confidence below 0.65.

Examples:

User: "Which all devices are on?"
JSON:
{"domain":"smart_home","intent":"status","action":null,"devices":["all"],"commands":[],"confidence":0.98,"reason":"user asks device status"}

User: "Can you turn on my TV?"
JSON:
{"domain":"smart_home","intent":"control","action":"on","devices":["tv"],"commands":[{"action":"on","devices":["tv"]}],"confidence":0.96,"reason":"turn on TV"}

User: "Fire up the sound system"
JSON:
{"domain":"smart_home","intent":"control","action":"on","devices":["soundbar","subwoofer"],"commands":[{"action":"on","devices":["soundbar","subwoofer"]}],"confidence":0.94,"reason":"sound system means soundbar and subwoofer"}

User: "Turn off the sound system and turn off the TV"
JSON:
{"domain":"smart_home","intent":"control","action":"off","devices":["soundbar","subwoofer"],"commands":[{"action":"off","devices":["soundbar","subwoofer"]},{"action":"off","devices":["tv"]}],"confidence":0.96,"reason":"turn off sound system and TV"}

User: "Turn off all speakers"
JSON:
{"domain":"smart_home","intent":"control","action":"off","devices":["soundbar","subwoofer","rear"],"commands":[{"action":"off","devices":["soundbar","subwoofer","rear"]}],"confidence":0.96,"reason":"all speakers group"}

User: "Turn off the surround system"
JSON:
{"domain":"smart_home","intent":"control","action":"off","devices":["rear"],"commands":[{"action":"off","devices":["rear"]}],"confidence":0.96,"reason":"surround system means rear speakers"}

User: "Can you turn on my TV and the surround system?"
JSON:
{"domain":"smart_home","intent":"control","action":"on","devices":["tv","rear"],"commands":[{"action":"on","devices":["tv","rear"]}],"confidence":0.96,"reason":"turn on TV and rear speakers"}

User: "Will you turn off my TV and turn on everything else?"
JSON:
{"domain":"smart_home","intent":"control","action":"off","devices":["tv"],"commands":[{"action":"off","devices":["tv"]},{"action":"on","devices":["soundbar","subwoofer","rear"]}],"confidence":0.94,"reason":"turn off TV and turn on all other devices"}

User: "Shut down everything except the TV"
JSON:
{"domain":"smart_home","intent":"control","action":"off","devices":["soundbar","subwoofer","rear"],"commands":[{"action":"off","devices":["soundbar","subwoofer","rear"]}],"confidence":0.90,"reason":"everything except TV"}

User: "How are you?"
JSON:
{"domain":"general","intent":"none","action":null,"devices":[],"commands":[],"confidence":0.99,"reason":"general conversation"}
""".strip()

    def _extract_json(self, raw: str) -> dict:
        raw = raw.strip()

        if raw.startswith("{") and raw.endswith("}"):
            return json.loads(raw)

        match = re.search(r"\{.*\}", raw, re.DOTALL)

        if not match:
            raise ValueError(f"No JSON object found in router output: {raw}")

        return json.loads(match.group(0))

    def _normalize_text(self, transcript: str) -> str:
        text = transcript.lower().strip()
        text = re.sub(r"[^a-z0-9\s]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _normalize_device_list(self, devices: list, text: str) -> list[str]:
        """
        Converts LLM device aliases into physical device IDs.

        This is intentionally deterministic because relay control must be safe.
        """
        if not isinstance(devices, list):
            devices = []

        normalized: list[str] = []

        alias_map = {
            "tv": ["tv", "television", "sony tv", "sony television"],
            "soundbar": ["soundbar", "sound bar", "speaker bar", "bar"],
            "subwoofer": ["subwoofer", "sub woofer", "woofer", "sub"],
            "rear": [
                "rear",
                "rear speaker",
                "rear speakers",
                "surround",
                "surround speaker",
                "surround speakers",
                "surround system",
                "rear system",
                "back speaker",
                "back speakers",
            ],
            "all": [
                "all",
                "all devices",
                "everything",
                "home theater",
                "home theatre",
                "full system",
                "entire system",
            ],
        }

        group_aliases = {
            "sound_system": [
                "sound system",
                "audio system",
            ],
            "all_speakers": [
                "all speakers",
                "the speakers",
                "speakers",
                "speaker system",
            ],
            "rear_only": [
                "surround system",
                "surround speakers",
                "rear system",
                "rear speakers",
                "back speakers",
            ],
        }

        def add(device_id: str):
            if device_id not in normalized:
                normalized.append(device_id)

        for raw_device in devices:
            device_text = str(raw_device).lower().strip()
            device_text = re.sub(r"[^a-z0-9\s]", " ", device_text)
            device_text = re.sub(r"\s+", " ", device_text)

            if device_text in group_aliases["rear_only"]:
                add("rear")
                continue

            if device_text in group_aliases["sound_system"]:
                add("soundbar")
                add("subwoofer")
                continue

            if device_text in group_aliases["all_speakers"]:
                add("soundbar")
                add("subwoofer")
                add("rear")
                continue

            matched = False

            for device_id, aliases in alias_map.items():
                if device_text in aliases:
                    add(device_id)
                    matched = True
                    break

            if matched:
                continue

        # If LLM gave an empty/weak device list, use transcript phrases.
        # This is a fallback only; it does not overwrite already-correct lists.
        if not normalized:
            if any(phrase in text for phrase in group_aliases["rear_only"]):
                normalized = ["rear"]

            elif any(phrase in text for phrase in group_aliases["sound_system"]):
                normalized = ["soundbar", "subwoofer"]

            elif any(phrase in text for phrase in group_aliases["all_speakers"]):
                normalized = ["soundbar", "subwoofer", "rear"]

            elif any(phrase in text for phrase in alias_map["all"]):
                normalized = ["all"]

            else:
                for device_id, aliases in alias_map.items():
                    if device_id == "all":
                        continue

                    for alias in aliases:
                        if re.search(rf"\b{re.escape(alias)}\b", text):
                            add(device_id)
                            break

        return normalized

    def _looks_like_exact_known_command(self, text: str) -> bool:
        action_words = [
            "turn on", "switch on", "power on", "fire up",
            "activate", "start", "turn off", "switch off",
            "power off", "shut down", "shutdown", "kill",
            "disable", "deactivate", "stop", "toggle", "flip"
        ]

        device_words = [
            "tv", "television", "soundbar", "sound bar",
            "subwoofer", "woofer", "rear", "rear speakers",
            "surround system", "surround speakers", "back speakers",
            "sound system", "audio system", "all speakers", "home theater",
            "home theatre", "everything", "all devices"
        ]

        has_action = any(word in text for word in action_words)
        has_device = any(word in text for word in device_words)

        return has_action and has_device
    
    def _extract_clause_commands(self, text: str) -> list[RoutedCommand]:
        """
        Deterministic repair layer.

        Finds obvious action-device clauses that the LLM may have missed.
        Example:
        'turn off the sound system and turn off the TV'
        -> off soundbar+subwoofer, off tv
        """
        commands: list[RoutedCommand] = []

        # Split natural joined commands.
        clauses = re.split(
            r"\b(?:and then|then|also|and)\b",
            text
        )

        last_action: Optional[str] = None

        for clause in clauses:
            clause = clause.strip()

            if not clause:
                continue

            # Skip special "everything else" clauses here.
            # Existing everything-else repair handles that separately.
            if "everything else" in clause or "all else" in clause:
                continue

            action = None

            if re.search(
                r"\b(turn off|switch off|power off|shut down|shutdown|kill|disable|deactivate|stop)\b",
                clause,
            ):
                action = "off"

            elif re.search(
                r"\b(turn on|switch on|power on|fire up|activate|enable|start)\b",
                clause,
            ):
                action = "on"

            elif re.search(r"\b(toggle|flip)\b", clause):
                action = "toggle"

            # If the second clause is like:
            # "and the TV"
            # reuse the previous action.
            if action is None:
                action = last_action

            if action is None:
                continue

            devices = self._normalize_device_list([], clause)

            if not devices:
                continue

            last_action = action
            commands.append(RoutedCommand(action, devices))

        return commands


    def _merge_command_repairs(
        self,
        llm_commands: list[RoutedCommand],
        repair_commands: list[RoutedCommand],
    ) -> list[RoutedCommand]:
        """
        Merge deterministic repairs into LLM commands without duplicating devices.
        """
        merged: list[RoutedCommand] = []

        def add_or_merge(new_command: RoutedCommand):
            for existing in merged:
                if existing.action == new_command.action:
                    for device in new_command.devices:
                        if device not in existing.devices:
                            existing.devices.append(device)
                    return

            merged.append(
                RoutedCommand(
                    new_command.action,
                    list(new_command.devices)
                )
            )

        for command in llm_commands:
            add_or_merge(command)

        for command in repair_commands:
            add_or_merge(command)

        return merged

    def _fix_everything_else_commands(
        self,
        commands: list[RoutedCommand],
        text: str,
    ) -> list[RoutedCommand]:
        """
        Handles:
        'turn off TV and turn on everything else'

        If the LLM makes a weak command list, this deterministic correction
        protects the relay behavior.
        """
        if "everything else" not in text and "all else" not in text:
            return commands

        mentioned = []

        if re.search(r"\btv\b|\btelevision\b", text):
            mentioned.append("tv")

        if "soundbar" in text or "sound bar" in text:
            mentioned.append("soundbar")

        if "subwoofer" in text or "woofer" in text:
            mentioned.append("subwoofer")

        if (
            "rear" in text
            or "surround" in text
            or "back speakers" in text
        ):
            mentioned.append("rear")

        all_devices = ["tv", "soundbar", "subwoofer", "rear"]
        else_devices = [d for d in all_devices if d not in mentioned]

        if not mentioned or not else_devices:
            return commands

        if "turn off" in text or "switch off" in text or "power off" in text:
            first_action = "off"
            second_action = "on" if (
                "turn on everything else" in text
                or "switch on everything else" in text
                or "power on everything else" in text
            ) else None
        else:
            first_action = None
            second_action = None

        if first_action and second_action:
            return [
                RoutedCommand(first_action, mentioned),
                RoutedCommand(second_action, else_devices),
            ]

        return commands

    def _validate(self, data: dict, transcript: str) -> RoutedIntent:
        allowed_domains = {"smart_home", "general"}
        allowed_intents = {"control", "status", "none"}
        allowed_actions = {"on", "off", "toggle", None}

        text = self._normalize_text(transcript)

        domain = data.get("domain", "general")
        intent = data.get("intent", "none")
        action = data.get("action", None)
        devices = data.get("devices", [])
        commands_raw = data.get("commands", [])
        confidence = float(data.get("confidence", 0.0))
        reason = str(data.get("reason", ""))

        if domain not in allowed_domains:
            domain = "general"

        if intent not in allowed_intents:
            intent = "none"

        if action not in allowed_actions:
            action = None

        if domain != "smart_home":
            return RoutedIntent(
                "general", "none", None, [], confidence, reason, []
            )

        # Status intents never execute commands.
        if intent == "status":
            clean_devices = self._normalize_device_list(devices, text)

            return RoutedIntent(
                "smart_home",
                "status",
                None,
                clean_devices or ["all"],
                max(confidence, 0.90),
                reason,
                [],
            )

        if intent != "control":
            return RoutedIntent(
                "general", "none", None, [], confidence, reason, []
            )

        clean_commands: list[RoutedCommand] = []

        # New schema path: commands list.
        if isinstance(commands_raw, list):
            for item in commands_raw:
                if not isinstance(item, dict):
                    continue

                cmd_action = item.get("action")
                cmd_devices = item.get("devices", [])

                if cmd_action not in {"on", "off", "toggle"}:
                    continue

                clean_devices = self._normalize_device_list(cmd_devices, text)

                if not clean_devices:
                    continue

                clean_commands.append(
                    RoutedCommand(cmd_action, clean_devices)
                )

        # Legacy fallback: old action/devices schema.
        if not clean_commands and action in {"on", "off", "toggle"}:
            clean_devices = self._normalize_device_list(devices, text)

            if clean_devices:
                clean_commands.append(
                    RoutedCommand(action, clean_devices)
                )

        # Repair common missed clauses like:
        # "turn off the sound system and turn off the TV"
        clause_commands = self._extract_clause_commands(text)

        if clause_commands:
            clean_commands = self._merge_command_repairs(
                clean_commands,
                clause_commands,
            )

        clean_commands = self._fix_everything_else_commands(
            clean_commands,
            text,
        )

        if not clean_commands:
            return RoutedIntent(
                "smart_home",
                "none",
                None,
                [],
                0.0,
                "control intent missing valid command",
                [],
            )

        if self._looks_like_exact_known_command(text):
            confidence = max(confidence, 0.88)

        first = clean_commands[0]

        return RoutedIntent(
            "smart_home",
            "control",
            first.action,
            first.devices,
            confidence,
            reason,
            clean_commands,
        )