# components/anthropic/anthropic_helper.py
import json
import logging

_LOGGER = logging.getLogger(__name__)


class AnthropicHelper:
    def __init__(self, hass, client=None):
        self.hass = hass
        self._client = client
        self.mock_mode = True
        self.demo_scenarios = {
            "light.kitchen": {
                "response": {
                    "actions": [
                        {
                            "entity": "light.kitchen",
                            "service_domain": "light",
                            "service_name": "turn_on",
                            "params": {"brightness": 200},
                            "explanation": "User activity detected in kitchen - lighting takes priority over schedule",
                        }
                    ],
                    "confidence": 0.92,
                }
            },
            "media_player.spotify": {
                "response": {
                    "actions": [
                        {
                            "entity": "media_player.spotify",
                            "service_domain": "media_player",
                            "service_name": "media_pause",
                            "params": {},
                            "explanation": "User explicitly requested pause - immediate action takes precedence",
                        }
                    ],
                    "confidence": 0.88,
                }
            },
            "media_player.demo": {
                "response": {
                    "actions": [
                        {
                            "entity": "media_player.demo",
                            "service_domain": "media_player",
                            "service_name": "volume_set",
                            "params": {"volume_level": 0.5},
                            "explanation": "Compromise between party mode and quiet hours - medium volume",
                        }
                    ],
                    "confidence": 0.85,
                }
            },
        }

    async def async_call_anthropic(self, prompt: str) -> str:
        """Call Anthropic with context-aware mock responses for demo."""
        if self.mock_mode:
            # Parse the prompt to determine which scenario we're in
            if (
                "light.kitchen" in prompt
                and "turn_off" in prompt
                and "turn_on" in prompt
            ):
                return json.dumps(self.demo_scenarios["light.kitchen"]["response"])
            if "media_player.spotify" in prompt and (
                "media_play" in prompt or "media_pause" in prompt
            ):
                return json.dumps(
                    self.demo_scenarios["media_player.spotify"]["response"]
                )
            if "media_player.demo" in prompt and "volume" in prompt:
                return json.dumps(self.demo_scenarios["media_player.demo"]["response"])
            # Default response
            return json.dumps(
                {
                    "actions": [
                        {
                            "entity": "light.living_room",
                            "service_domain": "light",
                            "service_name": "turn_on",
                            "params": {"brightness": 150},
                            "explanation": "Default resolution: favoring user activity over automation",
                        }
                    ],
                    "confidence": 0.75,
                }
            )
        _LOGGER.debug(
            "AnthropicHelper: would call Anthropic with prompt length %d", len(prompt)
        )
        return '{"actions": []}'
