"""Anthropic Conflict Resolver for Home Assistant.

This module is intentionally conservative:
- register_intent(intent) returns True if resolver will handle execution,
  otherwise False and caller should proceed to run the requested service.
- The resolver listens for intents, aggregates close-in-time intents per entity,
  and when a conflict is detected it calls Anthropic (via AnthropicHelper)
  to obtain a JSON-formatted resolution.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

DEFAULT_WINDOW_SECONDS = 2
DEFAULT_CACHE_SECONDS = 120


class AIConflictResolver:
    def __init__(self, hass, anth_helper, window_seconds: int = DEFAULT_WINDOW_SECONDS):
        self.hass = hass
        self.anth = anth_helper
        self._window = timedelta(seconds=window_seconds)
        # entity_id -> list[intent]
        self._intents: dict[str, list[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()
        # simple cache: fingerprint -> (response_dict, expiry_dt)
        self._cache: dict[str, Any] = {}

    async def register_intent(self, intent: dict[str, Any]) -> bool:
        """Register an intent.
        Returns True if resolver claimed the execution (i.e. caller should NOT execute);
        Returns False if no conflict and caller may proceed to run the service now.
        Intent format expected:
        {
            "entity_id": "media_player.spotify",
            "source": "automation.morning_playlist",
            "service_domain": "media_player",
            "service_name": "media_play",
            "params": { ... },
            "reason": "7:00 AM schedule",
            "timestamp": "2025-10-12T07:00:32Z"  # optional
        }
        """
        entity = intent.get("entity_id")
        if not entity:
            _LOGGER.debug("Intent without entity_id: %s", intent)
            return False

        now = datetime.now(UTC)
        if "timestamp" not in intent:
            intent["timestamp"] = now.isoformat()

        async with self._lock:
            self._intents.setdefault(entity, []).append(intent)
            self._prune(entity, now)
            if self._is_conflict(entity):
                # collect and clear current intents for this entity
                conflicts = list(self._intents.get(entity, []))
                self._intents[entity] = []
                # spawn background task to resolve and execute
                asyncio.create_task(self._handle_conflict(entity, conflicts))
                # indicate to caller: resolver will handle execution
                _LOGGER.debug(
                    "Conflict detected for %s: delegating to resolver", entity
                )
                return True

        # no conflict: caller should proceed to execute immediately
        return False

    def _prune(self, entity: str, now: datetime) -> None:
        if entity not in self._intents:
            return
        # keep intents only within the configured window
        kept = []
        for i in self._intents[entity]:
            try:
                ts = datetime.fromisoformat(i["timestamp"])
            except Exception:
                ts = now
            if now - ts <= self._window:
                kept.append(i)
        self._intents[entity] = kept

    def _is_conflict(self, entity: str) -> bool:
        return len(self._intents.get(entity, [])) >= 2

    async def _handle_conflict(
        self, entity: str, conflicts: list[dict[str, Any]]
    ) -> None:
        """Aggregate conflicts, call Anthropic, parse response, execute or fallback."""
        try:
            fp = self._fingerprint(entity, conflicts)
            # consult cache
            cached = self._cache.get(fp)
            if cached:
                response, expiry = cached
                if datetime.now(UTC) < expiry:
                    _LOGGER.debug("Using cached decision for %s", entity)
                    await self._apply_decision(response)
                    return
                self._cache.pop(fp, None)

            prompt = self._build_prompt(entity, conflicts)
            _LOGGER.debug("Conflict prompt:\n%s", prompt)
            # call Anthropic via helper
            raw = await self.anth.async_call_anthropic(prompt)
            _LOGGER.debug("Anthropic reply: %s", raw)
            parsed = self._parse_response(raw)
            if parsed and parsed.get("actions"):
                # cache short-lived
                expiry = datetime.now(UTC) + timedelta(seconds=DEFAULT_CACHE_SECONDS)
                self._cache[fp] = (parsed, expiry)
                await self._apply_decision(parsed)
            else:
                _LOGGER.warning(
                    "Anthropic returned no actions; using fallback for %s", entity
                )
                await self._fallback_resolution(conflicts)
        except Exception as exc:  # broad catch to keep HA stable
            _LOGGER.exception(
                "Exception while resolving conflict for %s: %s", entity, exc
            )
            await self._fallback_resolution(conflicts)

    def _fingerprint(self, entity: str, conflicts: list[dict[str, Any]]) -> str:
        """Simple fingerprint for caching decisions (stable ordering)."""
        try:
            stable = json.dumps(
                {
                    "entity": entity,
                    "conflicts": sorted(conflicts, key=lambda x: x.get("source")),
                },
                sort_keys=True,
            )
            # small fingerprint (not cryptographic requirement here)
            import hashlib

            return hashlib.sha256(stable.encode()).hexdigest()
        except Exception:
            return f"{entity}:{len(conflicts)}"

    def _build_prompt(self, entity: str, conflicts: list[dict[str, Any]]) -> str:
        now = datetime.now(UTC).isoformat()
        lines = [
            "You are Home Assistant's automation conflict resolver.",
            f"Entity: {entity}",
            f"Time: {now}",
            "",
            "Conflicting intents (one per line):",
        ]
        for c in conflicts:
            lines.append(
                f"- source={c.get('source')}, action={c.get('service_name') or c.get('action')}, params={json.dumps(c.get('params', {}))}, reason={c.get('reason')}, ts={c.get('timestamp')}"
            )
        lines.append("")
        lines.append("Return JSON only with shape:")
        lines.append(
            '{"actions":[{"entity":"...","service_domain":"...","service_name":"...","params":{...},"explanation":"..."}], "fallback":"priority|first|last"}'
        )
        return "\n".join(lines)

    def _parse_response(self, text: str) -> dict[str, Any] | None:
        """Attempt to parse JSON from the model output."""
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            # try to extract first JSON object substring
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except Exception:
                    _LOGGER.debug(
                        "Failed to parse JSON substring from Anthropic response"
                    )
        return None

    async def _apply_decision(self, parsed: dict[str, Any]) -> None:
        """Execute each action in the parsed decision."""
        actions = parsed.get("actions", [])
        for a in actions:
            entity = a.get("entity")
            domain = a.get("service_domain")
            service = a.get("service_name")
            params = a.get("params", {}) or {}
            explanation = a.get("explanation", "")
            if not all([domain, service]):
                _LOGGER.warning("Invalid action entry (missing domain/service): %s", a)
                continue
            _LOGGER.info(
                "Applying resolved action %s.%s for %s (explanation: %s)",
                domain,
                service,
                entity,
                explanation,
            )
            # do not block; let HA execute asynchronously
            try:
                await self.hass.services.async_call(
                    domain, service, params, blocking=False
                )
            except Exception:
                _LOGGER.exception(
                    "Failed to execute resolved action %s.%s", domain, service
                )
            # emit event so frontend / logbook can show explanation
            self.hass.bus.async_fire(
                "anthropic_conflict_resolved",
                {
                    "entity_id": entity,
                    "action": f"{domain}.{service}",
                    "explanation": explanation,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )

    async def _fallback_resolution(self, conflicts: list[dict[str, Any]]) -> None:
        """Simple deterministic fallback: first-triggered wins."""
        if not conflicts:
            return
        # choose the conflict with earliest timestamp (first triggered)
        try:
            first = min(
                conflicts,
                key=lambda c: datetime.fromisoformat(c.get("timestamp")),  # type: ignore[arg-type]
            )
        except Exception:
            first = conflicts[0]
        domain = first.get("service_domain")
        service = first.get("service_name")
        params = first.get("params", {}) or {}
        _LOGGER.info("Fallback: executing first-intent %s.%s", domain, service)
        try:
            await self.hass.services.async_call(domain, service, params, blocking=False)
        except Exception:
            _LOGGER.exception("Fallback execution failed for %s.%s", domain, service)
        self.hass.bus.async_fire(
            "anthropic_conflict_fallback",
            {
                "entity_id": first.get("entity_id"),
                "action": f"{domain}.{service}",
                "reason": "fallback_first_triggered",
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )


# Module-level wrapper + demo helpers
_resolver: AIConflictResolver | None = None


def async_setup(hass, anth_helper, window_seconds: int = DEFAULT_WINDOW_SECONDS):
    """Initialize a global resolver instance for this integration."""
    global _resolver
    if _resolver is None:
        _resolver = AIConflictResolver(hass, anth_helper, window_seconds=window_seconds)
    # store pointer for other parts of HA to use
    hass.data.setdefault("anthropic", {})["conflict_resolver"] = _resolver
    _LOGGER.info("Anthropic Conflict Resolver set up (demo mode).")
    return _resolver


async def detect_conflicts(hass, user_input_text: str):
    """Demo conflict detector: simple heuristics that look for common contradictory verbs.
    In production you'd parse intents/entities from the context.
    Returns a list of intent dicts if a conflict is detected; otherwise [].
    """
    global _resolver
    if _resolver is None:
        return []

    text = (user_input_text or "").lower()
    # Simple heuristics for demo:
    # - If both 'play' and 'pause' mentioned -> conflict on media_player.spotify
    # - If both 'turn on' and 'turn off' -> conflict on light.living_room
    intents = []
    now = datetime.now(UTC).isoformat()

    if "play" in text and "pause" in text:
        intents = [
            {
                "entity_id": "media_player.spotify",
                "source": "automation.demo_play",
                "service_domain": "media_player",
                "service_name": "media_play",
                "params": {},
                "reason": "user requested play",
                "timestamp": now,
            },
            {
                "entity_id": "media_player.spotify",
                "source": "automation.demo_pause",
                "service_domain": "media_player",
                "service_name": "media_pause",
                "params": {},
                "reason": "user requested pause",
                "timestamp": now,
            },
        ]
    elif "turn on" in text and "turn off" in text:
        intents = [
            {
                "entity_id": "light.living_room",
                "source": "automation.demo_on",
                "service_domain": "light",
                "service_name": "turn_on",
                "params": {"brightness": 255},
                "reason": "user said turn on",
                "timestamp": now,
            },
            {
                "entity_id": "light.living_room",
                "source": "automation.demo_off",
                "service_domain": "light",
                "service_name": "turn_off",
                "params": {},
                "reason": "user said turn off",
                "timestamp": now,
            },
        ]

    # If we detected intents, register them with resolver and return them
    if intents:
        # register each intent (resolver.register_intent will spawn background resolution)
        for intent in intents:
            try:
                await _resolver.register_intent(intent)
            except Exception:
                _LOGGER.exception("Failed to register demo intent: %s", intent)
        return intents

    return []


def resolve_conflicts(conflicts, user_input_text: str) -> str:
    """Demo resolver for rewriting the input passed to Anthropic.
    Here we simply annotate the input. In production, you might ask Anthropic
    what to do (synchronously) or call _resolver to handle action execution.
    """
    if not conflicts:
        return user_input_text
    # return a short resolved instruction (human readable)
    return f"[Resolved {len(conflicts)} conflict(s)] {user_input_text}"


async def log_conflicts(hass, conflicts):
    """Emit a light event so UI/debuggers can show the conflict list."""
    hass.bus.async_fire(
        "anthropic_conflicts_logged",
        {"conflicts": conflicts, "timestamp": datetime.now(UTC).isoformat()},
    )
    _LOGGER.info("Anthropic conflicts logged: %s", conflicts)
