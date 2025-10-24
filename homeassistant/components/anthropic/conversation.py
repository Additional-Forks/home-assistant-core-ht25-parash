"""Conversation support for Anthropic."""

import logging
from typing import Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import CONF_LLM_HASS_API, MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AnthropicConfigEntry, conflict_resolver
from .const import CONF_PROMPT, DOMAIN
from .entity import AnthropicBaseLLMEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: AnthropicConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up conversation entities."""
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != "conversation":
            continue

        async_add_entities(
            [AnthropicConversationEntity(config_entry, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class AnthropicConversationEntity(
    conversation.ConversationEntity,
    conversation.AbstractConversationAgent,
    AnthropicBaseLLMEntity,
):
    """Anthropic conversation agent."""

    _attr_supports_streaming = True

    def __init__(self, entry: AnthropicConfigEntry, subentry: ConfigSubentry) -> None:
        """Initialize the agent."""
        super().__init__(entry, subentry)
        if self.subentry.data.get(CONF_LLM_HASS_API):
            self._attr_supported_features = (
                conversation.ConversationEntityFeature.CONTROL
            )

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""
        return MATCH_ALL

    _LOGGER.warning("Custom conversation.py loaded and active!")

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Call the API."""
        options = self.subentry.data

        # Step 1: Check if this is user feedback about conflict resolution
        if self._is_conflict_feedback(user_input.text):
            return await self._process_conflict_feedback(user_input.text)

        # Step 2: Detect conflicts before sending prompt
        conflicts = await conflict_resolver.detect_conflicts(self.hass, user_input.text)
        if conflicts:
            _LOGGER.info("Conflict detected: %s", conflicts)

            # Step 3: Try to resolve conflicts
            resolved_text = conflict_resolver.resolve_conflicts(
                conflicts, user_input.text
            )
            _LOGGER.debug("Resolved input: %s", resolved_text)

            # Replace user input with resolved version
            user_input.text = resolved_text
        else:
            _LOGGER.debug("No conflicts detected for input: %s", user_input.text)

        # Step 4: Continue with normal Anthropic call

        try:
            await chat_log.async_provide_llm_data(
                user_input.as_llm_context(DOMAIN),
                options.get(CONF_LLM_HASS_API),
                options.get(CONF_PROMPT),
                user_input.extra_system_prompt,
            )
        except conversation.ConverseError as err:
            return err.as_conversation_result()

        await self._async_handle_chat_log(chat_log)

        if conflicts:
            await conflict_resolver.log_conflicts(self.hass, conflicts)

        return conversation.async_get_result_from_chat_log(user_input, chat_log)

    def _is_conflict_feedback(self, text: str) -> bool:
        """Detect if user is providing feedback about conflict resolution."""
        feedback_indicators = [
            "you should prioritize",
            "you should choose",
            "prefer",
            "instead of",
            "conflict resolution",
            "automation priority",
            "keep the lights off",
            "even if motion detected",
            "evening automation",
            "motion detection",
        ]
        text_lower = text.lower()
        return any(indicator in text_lower for indicator in feedback_indicators)

    async def _process_conflict_feedback(
        self, feedback_text: str
    ) -> conversation.ConversationResult:
        """Process user feedback and update conflict resolution preferences."""
        # Extract entity from feedback (simple heuristic)
        entity = self._extract_entity_from_feedback(feedback_text)

        if entity:
            # Record feedback in conflict resolver
            resolver = self.hass.data.get("anthropic", {}).get("conflict_resolver")
            if resolver:
                await resolver.record_user_feedback(entity, feedback_text)
                response = f"Thank you for the feedback! I've recorded your preference for {entity}: '{feedback_text}'. This will be used to improve future conflict resolutions."
            else:
                response = "I understand your feedback, but the conflict resolver is not available right now."
        else:
            response = "I understand your feedback about conflict resolution. Could you specify which entity (like 'light.kitchen' or 'media_player.spotify') this applies to?"

        return conversation.ConversationResult(
            response=response,
            conversation_id=conversation.ConversationResult._generate_conversation_id(),
        )

    def _extract_entity_from_feedback(self, feedback_text: str) -> str:
        """Extract entity from feedback text using simple heuristics."""
        text_lower = feedback_text.lower()

        # Common entity patterns
        if "kitchen" in text_lower or "light.kitchen" in text_lower:
            return "light.kitchen"
        if "living room" in text_lower or "light.living_room" in text_lower:
            return "light.living_room"
        if "spotify" in text_lower or "media_player.spotify" in text_lower:
            return "media_player.spotify"
        if "demo" in text_lower or "media_player.demo" in text_lower:
            return "media_player.demo"
        if "light" in text_lower:
            return (
                "light.living_room"  # default to living room for generic light feedback
            )
        if "media" in text_lower or "music" in text_lower:
            return (
                "media_player.spotify"  # default to spotify for generic media feedback
            )

        return ""
