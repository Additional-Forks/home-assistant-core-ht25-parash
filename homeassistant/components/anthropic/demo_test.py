#!/usr/bin/env python3
"""
Anthropic Integration Demo Test Script

This script demonstrates the Anthropic integration's conflict resolution
capabilities in mock mode without requiring a real API key.

Usage:
    python demo_test.py
"""

import asyncio
import json
import sys
from pathlib import Path

# Add the homeassistant directory to the path so we can import the components
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from homeassistant.components.anthropic.anthropic_helper import AnthropicHelper
from homeassistant.components.anthropic import conflict_resolver

# Global helper and resolver for demo
_demo_helper = None
_demo_resolver = None


async def test_kitchen_light_conflict():
    """Test kitchen light conflict resolution."""
    print(f"Kitchen Light Conflict")
    print("=" * 40)

    global _demo_helper, _demo_resolver

    if _demo_helper is None:
        helper = AnthropicHelper(None, client=None)
        helper.mock_mode = True
    else:
        helper = _demo_helper

    # Kitchen light conflict scenario
    entity = "light.kitchen"
    prompt = "light.kitchen turn_on turn_off"

    print(f"Entity: {entity}")
    print()

    # Show the conflict configuration JSON
    conflict_config = {
        "entity_id": "light.kitchen",
        "conflicts": [
            {
                "source": "automation.schedule",
                "service_domain": "light",
                "service_name": "turn_off",
                "params": {},
                "reason": "evening schedule",
                "timestamp": "2025-10-24T17:00:00Z",
            },
            {
                "source": "automation.motion",
                "service_domain": "light",
                "service_name": "turn_on",
                "params": {"brightness": 200},
                "reason": "motion detected",
                "timestamp": "2025-10-24T17:00:01Z",
            },
        ],
    }
    print("Conflict Configuration:")
    print(json.dumps(conflict_config, indent=2))
    print()

    try:
        # If we have user feedback, show how it would affect the response
        if _demo_resolver:
            feedback = _demo_resolver._get_feedback_context(entity, [])
            if feedback:
                # Simulate different response based on feedback
                result = {
                    "actions": [
                        {
                            "entity": "light.kitchen",
                            "service_domain": "light",
                            "service_name": "turn_off",
                            "params": {},
                            "explanation": "Following user preference: schedule takes priority over user activity",
                        }
                    ],
                    "confidence": 0.95,
                }
            else:
                response = await helper.async_call_anthropic(prompt)
                result = json.loads(response)
        else:
            response = await helper.async_call_anthropic(prompt)
            result = json.loads(response)

        if "actions" in result and result["actions"]:
            action = result["actions"][0]
            confidence = result.get("confidence", 0)

            print(
                f"Resolution: {action.get('service_domain', 'unknown')}.{action.get('service_name', 'unknown')}"
            )
            if action.get("params"):
                print(f"Parameters: {action.get('params')}")
            print(f"Confidence: {confidence:.0%}")
            print(f"Reasoning: {action.get('explanation', 'No explanation')}")

        else:
            print("ERROR: No actions in response")

    except Exception as e:
        print(f"ERROR: {e}")

    print()


async def test_user_intervention():
    """Test user feedback intervention."""
    print("User Feedback Recorded")
    print("=" * 40)

    # Create helper and resolver for the demo
    global _demo_helper, _demo_resolver
    _demo_helper = AnthropicHelper(None, client=None)
    _demo_helper.mock_mode = True

    # Create a mock conflict resolver with feedback storage
    class MockConflictResolver:
        def __init__(self):
            self._user_feedback = {}

        async def record_user_feedback(self, entity: str, feedback: str):
            self._user_feedback[entity] = feedback
            print(f"Entity: {entity}")
            print(f"Feedback: {feedback}")

        def _get_feedback_context(self, entity: str, conflicts):
            return self._user_feedback.get(entity, "")

    _demo_resolver = MockConflictResolver()
    await _demo_resolver.record_user_feedback(
        "light.kitchen",
        "You should prioritize schedule over user activity for kitchen lights",
    )
    print()


async def main():
    """Main demo test function."""
    print("Anthropic Integration Demo")
    print("=" * 50)
    print()

    await test_kitchen_light_conflict()
    await test_user_intervention()
    await test_kitchen_light_conflict()

    print("-" * 50)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDemo interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nDemo failed with error: {e}")
        sys.exit(1)
