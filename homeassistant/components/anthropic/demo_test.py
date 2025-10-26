#!/usr/bin/env python3
"""Anthropic integration demo (mock-friendly)."""

from __future__ import annotations
import asyncio
from typing import Any
import re

# ---- Config: helper entity ids (UI shows/edits these) -----------------------
USER_FEEDBACK_ENTITY = "input_text.demo_user_text"
OUTPUT_ENTITY = "input_text.demo_output_text"

# ---- Optional deps: use real helper/resolver if available; else mock --------
try:
    from . import anthropic_helper  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    anthropic_helper = None  # noqa: N816

try:
    from . import conflict_resolver  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    conflict_resolver = None  # noqa: N816


# ======= Small helpers =======================================================


def _choose_preference(text: str, a_terms: list[str], b_terms: list[str]) -> str | None:
    """
    Return 'a' | 'b' | None based on free-text like 'X over Y'.
    a_terms/b_terms are lists of synonyms (lowercase).
    """
    s = text.lower()

    def any_term(terms: list[str]) -> str:
        # Build alternation that preserves word boundaries for multiword phrases
        alts = [re.escape(t) for t in sorted(terms, key=len, reverse=True)]
        return r"(?:%s)" % "|".join(alts)

    A = any_term(a_terms)
    B = any_term(b_terms)

    # Strong signal: "<A> ... over ... <B>" or "<B> ... over ... <A>"
    m = re.search(rf"\b({A}|{B})\b.*?\bover\b.*?\b({A}|{B})\b", s)
    if m and m.group(1) != m.group(2):
        first = m.group(1)
        # Map first match back to 'a' or 'b'
        if re.fullmatch(A, first):
            return "a"
        if re.fullmatch(B, first):
            return "b"

    # Weak signal: term frequency
    score_a = sum(s.count(t) for t in a_terms)
    score_b = sum(s.count(t) for t in b_terms)
    if score_a > score_b:
        return "a"
    if score_b > score_a:
        return "b"
    return None


async def _set_text(hass, entity_id: str, value: str) -> None:
    await hass.services.async_call(
        "input_text",
        "set_value",
        {"entity_id": entity_id, "value": value},
        blocking=True,
    )


async def _logbook(
    hass, message: str, entity_id: str = "script.anthropic_demo"
) -> None:
    await hass.services.async_call(
        "logbook",
        "log",
        {"name": "Conflict Resolver", "message": message, "entity_id": entity_id},
        blocking=True,
    )


async def _syslog(hass, message: str, level: str = "info") -> None:
    await hass.services.async_call(
        "system_log",
        "write",
        {"message": message, "level": level},
        blocking=True,
    )


def _state(hass, entity_id: str, default: str = "") -> str:
    st = hass.states.get(entity_id)
    return (st.state if st and st.state is not None else default).strip()


# ======= Demo behaviors ======================================================


async def run_kitchen_demo(hass, log: str | None = None) -> None:
    feedback = _state(hass, USER_FEEDBACK_ENTITY)
    pref = _choose_preference(feedback, a_terms=["schedule"], b_terms=["motion"])

    entity = "light.kitchen"
    conflict = "automation.schedule vs automation.motion"

    # Default: motion wins (turn_on)
    service, params = "light.turn_on", {"brightness": 200}
    confidence = 92

    if pref == "a":  # schedule
        service, params = "light.turn_off", {}
        confidence = 95
    elif pref == "b":  # motion
        service, params = "light.turn_on", {"brightness": 200}
        confidence = 95

    msg = (
        f"Entity: {entity}<br>"
        f"Conflict: {conflict}<br>"
        f"Resolution: {service} with {params}<br>"
        f"Confidence: {confidence}%"
    )

    await _set_text(hass, OUTPUT_ENTITY, msg)
    await _logbook(hass, msg, entity_id="script.kitchen_light_mock")
    if log:
        await _syslog(hass, f"[kitchen_demo] {log}")


async def run_user_intervention(hass, message: str | None = None) -> None:
    """Store user guidance (cache) and log it."""
    if message is None:
        message = _state(hass, USER_FEEDBACK_ENTITY, "")
    await _set_text(hass, USER_FEEDBACK_ENTITY, message)

    await _logbook(hass, message, entity_id="script.user_intervention")
    await _syslog(hass, f"User intervention: {message}")


async def run_media_demo(hass, message: str | None = None) -> None:
    """Resolve movie night vs phone call using user feedback if provided."""
    feedback = _state(hass, USER_FEEDBACK_ENTITY)
    pref = _choose_preference(
        feedback,
        a_terms=["movie night", "movie_night", "movie"],
        b_terms=["phone call", "phone_call", "call", "phone"],
    )

    entity = "media_player.living_room"
    conflict = "automation.movie_night vs automation.phone_call"

    # Default: phone call wins (mute)
    service, params = "media_player.volume_mute", {"is_volume_muted": True}
    confidence = 88

    if pref == "a":  # movie night wins
        service, params = (
            "media_player.play_media",
            {
                "media_content_id": "movie",
                "media_content_type": "movie",
            },
        )
        confidence = 95
    elif pref == "b":  # phone call wins
        service, params = "media_player.volume_mute", {"is_volume_muted": True}
        confidence = 95

    msg = (
        f"Entity: {entity}<br>"
        f"Conflict: {conflict}<br>"
        f"Resolution: {service} with {params}<br>"
        f"Confidence: {confidence}%"
    )

    # Brief intro + final message
    await _set_text(hass, OUTPUT_ENTITY, "Media Player Mock Scenario Started")
    await _logbook(
        hass, "Media Player Mock Scenario Started", entity_id="script.media_player_mock"
    )
    await _syslog(hass, "CONFLICT RESOLVER: Media Player Mock Scenario")
    await asyncio.sleep(0.3)

    await _set_text(hass, OUTPUT_ENTITY, msg)
    await _logbook(hass, msg, entity_id="script.media_player_mock")
    if message:
        await _syslog(hass, message)


async def reset_user_intervention(hass, clear_output: bool = False) -> None:
    """Clear user feedback and any in-memory caches in helper/resolver."""
    # Clear helper entities
    await _set_text(hass, USER_FEEDBACK_ENTITY, "")
    if clear_output:
        await _set_text(hass, OUTPUT_ENTITY, "")

    # Try to reset real modules if present; otherwise noop.
    if conflict_resolver is not None:
        if hasattr(conflict_resolver, "reset"):
            conflict_resolver.reset()  # type: ignore[misc]
        elif hasattr(conflict_resolver, "_demo_resolver"):
            conflict_resolver._demo_resolver = None  # type: ignore[attr-defined]

    if anthropic_helper is not None:
        if hasattr(anthropic_helper, "reset"):
            anthropic_helper.reset()  # type: ignore[misc]
        elif hasattr(anthropic_helper, "_demo_helper"):
            anthropic_helper._demo_helper = None  # type: ignore[attr-defined]

    await _logbook(
        hass,
        "User intervention + resolver cache cleared",
        entity_id="script.user_intervention",
    )
