from dataclasses import dataclass
from enum import Enum
from typing import Optional


class SmartHomeBackend(str, Enum):
    HOME_ASSISTANT = "home_assistant"
    RELAY_HOME = "relay_home"
    NONE = "none"


@dataclass
class BackendCommandResult:
    ok: bool
    backend: SmartHomeBackend
    error: str = ""


@dataclass
class BackendStatusResult:
    ok: bool
    backend: SmartHomeBackend
    states: Optional[dict[str, Optional[bool]]] = None
    error: str = ""