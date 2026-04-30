"""
Main agent loop for Hermes Playwright co-pilot.
Processes messages from overlay via WebSocket
and executes Playwright actions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from websocket_bridge import bridge

logger = logging.getLogger(__name__)


class SessionState:
    """Tracks current automation session."""

    def __init__(self, test_name: str = ""):
        self.session_id = str(uuid.uuid4())[:8]
        self.test_name = test_name
        self.steps: List[Dict] = []
        self.selected_element: Optional[Dict] = None
        self.pending_confirmation: Optional[Dict] = None
        self.current_url: str = ""
        self.mode: str = "interactive"
        self.is_paused: bool = False
        self.step_counter: int = 0

    def record_step(
        self,
        action: str,
        element: str,
        locator: str,
        generated_line: str
    ) -> int:
        self.step_counter += 1
        step = {
            "number": self.step_counter,
            "action": action,
            "element": element,
            "locator": locator,
            "generated_line": generated_line,
            "status": "success"
        }
        self.steps.append(step)
        return self.step_counter

    def get_recorded_steps_summary(self) -> str:
        if not self.steps:
            return "No steps recorded yet."
        lines = ["Recorded steps:"]
        for step in self.steps:
            lines.append(
                f"  {step['number']}. "
                f"{step['action']} — "
                f"{step['element']}"
            )
        return "\n".join(lines)


class AgentWSLoop:
    """
    Main loop that:
    1. Receives messages from overlay via bridge
    2. Processes each message type
    3. Calls Playwright tools
    4. Sends responses back to overlay
    """

    def __init__(self):
        self.session = SessionState()
        self.running = False
        self._playwright_ready = False

    async def start(
        self,
        test_name: str = ""
    ) -> None:
        """Start the agent loop."""
        self.session = SessionState(
            test_name=test_name
        )
        self.running = True
        print(
            f"🤖 Agent loop started — "
            f"session: {self.session.session_id}"
        )
        await self._main_loop()

    async def _main_loop(self) -> None:
        """
        Main processing loop.
        Waits for messages and processes them.
        """
        while self.running:
            try:
                message = await bridge.get_next_message(
                    timeout=1.0
                )
                if message is None:
                    continue

                msg_type = message.get("type", "")
                payload = message.get(
                    "payload", {}
                )

                logger.debug(
                    f"Processing: {msg_type}"
                )

                await self._route_message(
                    msg_type, payload
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    f"Error in main loop: {e}"
                )
                await bridge.send_error(
                    message=f"Unexpected error: {str(e)}",
                    suggestion="Try your action again"
                )

    async def _route_message(
        self,
        msg_type: str,
        payload: Dict[str, Any]
    ) -> None:
        """Route message to correct handler."""

        handlers = {
            "session_start":    self._handle_session_start,
            "element_selected": self._handle_element_selected,
            "user_intent":      self._handle_user_intent,
            "user_confirm":     self._handle_user_confirm,
            "user_correct":     self._handle_user_correct,
            "command":          self._handle_command,
            "reconnect":        self._handle_reconnect,
            "ping":             self._handle_ping,
        }

        handler = handlers.get(msg_type)
        if handler:
            await handler(payload)
        else:
            logger.warning(
                f"Unknown message type: {msg_type}"
            )

    async def _handle_session_start(
        self,
        payload: Dict
    ) -> None:
        """Handle session start from overlay."""
        test_name = payload.get(
            "test_name", "untitled"
        )
        url = payload.get("url", "")

        self.session = SessionState(
            test_name=test_name
        )
        self.session.current_url = url

        print(
            f"📋 Session started: {test_name}"
        )

        await bridge.send_session_ready(
            self.session.session_id
        )

        if url:
            await bridge.send_status(
                message=f"Navigating to {url}...",
                stage="navigation"
            )
            await self._navigate_to(url)

    async def _handle_element_selected(
        self,
        payload: Dict
    ) -> None:
        """Store selected element for next intent."""
        print(f"[WS] element_selected received: "
              f"{payload.get('tag')} "
              f"'{payload.get('text','')[:30]}'")
        self.session.selected_element = payload
        element_text = payload.get("text", "")
        element_tag = payload.get("tag", "")
        print(
            f"🎯 Element selected: "
            f"{element_tag} '{element_text}'"
        )

    async def _handle_user_intent(
        self,
        payload: Dict
    ) -> None:
        """
        Process user's plain English intent.
        This is the core handler.
        """
        print(f"[WS] user_intent received: "
              f"'{payload.get('message','')[:50]}'")
        message = payload.get("message", "")
        element = payload.get(
            "element",
            self.session.selected_element or {}
        )

        if not message:
            await bridge.send_error(
                message="No intent received",
                suggestion="Please type what you want to do"
            )
            return

        print(f"💬 Intent received: {message}")

        await bridge.send_thinking(
            "Understanding your intent..."
        )

        # Classify intent
        action_type = self._classify_intent(message)
        confidence = self._score_confidence(
            message, element, action_type
        )

        print(
            f"   Action: {action_type}, "
            f"Confidence: {confidence}%"
        )

        if confidence < 70:
            # Ask for clarification
            await bridge.send_to_overlay({
                "type": "clarification_needed",
                "payload": {
                    "message": (
                        f"I'm not sure what you want to do. "
                        f"Could you be more specific? "
                        f"For example: 'click this button' "
                        f"or 'fill this field with test@email.com'"
                    )
                }
            })
            return

        # Find locator if element provided
        locator = ""
        if element:
            await bridge.send_status(
                message="Finding locator...",
                stage="locator_search"
            )
            locator = await self._find_locator(element)

            if not locator:
                await bridge.send_error(
                    message=(
                        "Could not find a stable locator "
                        "for this element."
                    ),
                    suggestion=(
                        "Try selecting the element again "
                        "or describe it differently"
                    )
                )
                return

        # Build confirmation
        element_name = (
            element.get("text", "") or
            element.get("aria_label", "") or
            element.get("tag", "element")
        )

        understood = self._build_understood_text(
            action_type, element_name, message
        )

        # Store pending confirmation
        self.session.pending_confirmation = {
            "action_type": action_type,
            "element": element,
            "element_name": element_name,
            "locator": locator,
            "message": message,
            "understood": understood
        }

        # Send confirmation request to overlay
        await bridge.send_confirmation_request(
            understood=understood,
            locator=locator,
            action=action_type,
            element_name=element_name,
            highlight=element.get(
                "bounding_box", {}
            ),
            confidence=confidence
        )

    async def _handle_user_confirm(
        self,
        payload: Dict
    ) -> None:
        """Execute confirmed action."""
        confirmed = payload.get("confirmed", False)

        if not confirmed:
            self.session.pending_confirmation = None
            await bridge.send_to_overlay({
                "type": "cancelled",
                "payload": {
                    "message": "Action cancelled. "
                               "Select element and try again."
                }
            })
            return

        pending = self.session.pending_confirmation
        if not pending:
            await bridge.send_error(
                message="No pending action to confirm",
                suggestion="Select an element first"
            )
            return

        action_type = pending["action_type"]
        locator = pending["locator"]
        element_name = pending["element_name"]
        message = pending["message"]

        await bridge.send_status(
            message=f"Executing: {pending['understood']}",
            stage="executing"
        )

        # Execute the action
        success, generated_line, error = (
            await self._execute_action(
                action_type=action_type,
                locator=locator,
                message=message,
                element=pending["element"]
            )
        )

        if success:
            step_num = self.session.record_step(
                action=action_type,
                element=element_name,
                locator=locator,
                generated_line=generated_line
            )

            await bridge.send_step_recorded(
                step_number=step_num,
                action=action_type,
                element=element_name,
                locator=locator,
                generated_line=generated_line
            )

            # Send code update
            await bridge.send_to_overlay({
                "type": "code_update",
                "payload": {
                    "new_line": generated_line,
                    "step_number": step_num,
                    "all_steps": self.session.steps
                }
            })

            self.session.pending_confirmation = None
            print(
                f"✅ Step {step_num} recorded: "
                f"{action_type} {element_name}"
            )
        else:
            await bridge.send_error(
                message=f"Action failed: {error}",
                step=self.session.step_counter + 1,
                suggestion=(
                    "The element may have changed. "
                    "Try selecting it again."
                )
            )

    async def _handle_user_correct(
        self,
        payload: Dict
    ) -> None:
        """User corrected the understood intent."""
        correction = payload.get("correction", "")
        print(f"✏️  Correction: {correction}")

        if self.session.pending_confirmation:
            corrected_payload = {
                "message": correction,
                "element": self.session.pending_confirmation.get(
                    "element", {}
                )
            }
            await self._handle_user_intent(
                corrected_payload
            )

    async def _handle_command(
        self,
        payload: Dict
    ) -> None:
        """Handle slash commands from overlay."""
        command = payload.get("command", "")
        print(f"⌘ Command: {command}")

        commands = {
            "/generate": self._cmd_generate,
            "/status":   self._cmd_status,
            "/clear":    self._cmd_clear,
            "/run":      self._cmd_run,
        }

        handler = commands.get(command)
        if handler:
            await handler()
        else:
            await bridge.send_to_overlay({
                "type": "command_response",
                "payload": {
                    "message": (
                        f"Unknown command: {command}. "
                        f"Available: /generate /status "
                        f"/clear /run"
                    )
                }
            })

    async def _handle_reconnect(
        self,
        payload: Dict
    ) -> None:
        """Handle overlay reconnection after navigation."""
        url = payload.get("url", "")
        self.session.current_url = url
        print(f"🔄 Overlay reconnected at: {url}")

        await bridge.send_to_overlay({
            "type": "reconnect_confirmed",
            "payload": {
                "session_id": self.session.session_id,
                "step_count": len(self.session.steps),
                "message": (
                    f"Session restored — "
                    f"{len(self.session.steps)} "
                    f"steps recorded"
                )
            }
        })

    async def _handle_ping(
        self,
        payload: Dict
    ) -> None:
        """Respond to ping from overlay."""
        await bridge.send_to_overlay({
            "type": "pong",
            "payload": {"status": "alive"}
        })

    def _classify_intent(
        self,
        message: str
    ) -> str:
        """
        Rule-based intent classification.
        No LLM needed for clear intents.
        """
        msg = message.lower().strip()

        if any(w in msg for w in [
            "click", "press", "tap", "hit",
            "submit", "button"
        ]):
            return "click"

        if any(w in msg for w in [
            "fill", "type", "enter", "input",
            "write", "put"
        ]):
            return "fill"

        if any(w in msg for w in [
            "assert", "check", "verify",
            "confirm", "should", "must",
            "expect", "visible", "hidden"
        ]):
            return "assert"

        if any(w in msg for w in [
            "go to", "navigate", "open",
            "visit", "load"
        ]):
            return "navigate"

        if any(w in msg for w in [
            "select", "choose", "pick",
            "dropdown", "option"
        ]):
            return "select"

        if any(w in msg for w in [
            "upload", "attach", "file"
        ]):
            return "upload"

        if any(w in msg for w in [
            "scroll", "scroll to", "scroll down"
        ]):
            return "scroll"

        if any(w in msg for w in [
            "hover", "mouse over", "move to"
        ]):
            return "hover"

        if any(w in msg for w in [
            "wait", "pause"
        ]):
            return "wait"

        return "unknown"

    def _score_confidence(
        self,
        message: str,
        element: Dict,
        action_type: str
    ) -> int:
        """Score confidence 0-100."""
        score = 50  # base

        # Known action verb found
        if action_type != "unknown":
            score += 20

        # Element is selected
        if element:
            score += 15

        # Element has stable attributes
        if element.get("data_testid"):
            score += 10
        elif element.get("aria_label"):
            score += 8
        elif element.get("id"):
            score += 5

        # Message has specific value for fill
        if action_type == "fill":
            msg = message.lower()
            if "with" in msg or '"' in msg:
                score += 5

        # Cap at 100
        return min(score, 100)

    def _build_understood_text(
        self,
        action_type: str,
        element_name: str,
        message: str
    ) -> str:
        """Build human readable confirmation."""
        templates = {
            "click":    f"Click the [{element_name}]",
            "fill":     f"Fill [{element_name}]",
            "assert":   f"Assert [{element_name}] is visible",
            "navigate": f"Navigate to page",
            "select":   f"Select option from [{element_name}]",
            "upload":   f"Upload file to [{element_name}]",
            "scroll":   f"Scroll to [{element_name}]",
            "hover":    f"Hover over [{element_name}]",
            "wait":     f"Wait for [{element_name}]",
        }
        return templates.get(
            action_type,
            f"Perform action on [{element_name}]"
        )

    async def _find_locator(
        self,
        element: Dict
    ) -> str:
        """Find best locator for element."""
        try:
            from tools.playwright_automation import (
                locator_find,
                locator_validate
            )
            import json as _json

            result_raw = await locator_find(
                element_data=element
            )
            result = _json.loads(result_raw)

            if result.get("found"):
                locator = result["locator"]

                # Validate it
                val_raw = await locator_validate(
                    locator=locator
                )
                val = _json.loads(val_raw)

                if val.get("valid"):
                    return locator

            return ""
        except Exception as e:
            logger.error(f"Locator find error: {e}")
            return ""

    async def _execute_action(
        self,
        action_type: str,
        locator: str,
        message: str,
        element: Dict
    ):
        """
        Execute Playwright action.
        Returns (success, generated_line, error)
        """
        try:
            from tools.playwright_automation import (
                action_click,
                action_fill,
                action_assert,
                page_navigate
            )
            import json as _json
            import re

            if action_type == "click":
                result = _json.loads(
                    await action_click(
                        locator=locator
                    )
                )
                success = result.get("success", False)
                generated_line = (
                    f"await {self._locator_to_var(locator)}"
                    f".click()"
                )
                return (
                    success,
                    generated_line,
                    result.get("error", "")
                )

            elif action_type == "fill":
                # Extract value from message
                value = self._extract_fill_value(message)
                result = _json.loads(
                    await action_fill(
                        locator=locator,
                        value=value
                    )
                )
                success = result.get("success", False)
                generated_line = (
                    f"await {self._locator_to_var(locator)}"
                    f".fill('{value}')"
                )
                return (
                    success,
                    generated_line,
                    result.get("error", "")
                )

            elif action_type == "assert":
                result = _json.loads(
                    await action_assert(
                        locator=locator,
                        assertion="visible"
                    )
                )
                success = result.get("passed", False)
                generated_line = (
                    f"await expect("
                    f"{self._locator_to_var(locator)}"
                    f").toBeVisible()"
                )
                return (
                    success,
                    generated_line,
                    result.get("error", "")
                )

            elif action_type == "navigate":
                url = self._extract_url(message)
                if url:
                    result = _json.loads(
                        await page_navigate(url=url)
                    )
                    success = (
                        result.get("status") == "navigated"
                    )
                    generated_line = (
                        f"await page.goto('{url}')"
                    )
                    return (success, generated_line, "")
                return (
                    False,
                    "",
                    "Could not extract URL from intent"
                )

            else:
                return (
                    False,
                    "",
                    f"Action type '{action_type}' "
                    f"not yet implemented"
                )

        except Exception as e:
            logger.error(f"Execute action error: {e}")
            return (False, "", str(e))

    def _extract_fill_value(
        self,
        message: str
    ) -> str:
        """Extract value from fill intent."""
        import re
        # "fill with X" or 'fill with "X"'
        patterns = [
            r'with\s+"([^"]+)"',
            r"with\s+'([^']+)'",
            r'with\s+(\S+)',
            r'"([^"]+)"',
            r"'([^']+)'"
        ]
        for pattern in patterns:
            match = re.search(
                pattern, message, re.IGNORECASE
            )
            if match:
                return match.group(1)
        return ""

    def _extract_url(self, message: str) -> str:
        """Extract URL from navigation intent."""
        import re
        url_pattern = r'https?://[^\s]+'
        match = re.search(url_pattern, message)
        if match:
            return match.group(0)
        return ""

    def _locator_to_var(self, locator: str) -> str:
        """Convert locator to variable name hint."""
        return "element"

    async def _navigate_to(self, url: str) -> None:
        """Navigate browser to URL."""
        try:
            from tools.playwright_automation import (
                page_navigate
            )
            import json as _json
            result = _json.loads(
                await page_navigate(url=url)
            )
            if result.get("status") == "navigated":
                self.session.current_url = url
        except Exception as e:
            logger.error(f"Navigation error: {e}")

    async def _cmd_generate(self) -> None:
        """Generate TypeScript test script."""
        if not self.session.steps:
            await bridge.send_to_overlay({
                "type": "command_response",
                "payload": {
                    "message": "No steps recorded yet."
                }
            })
            return

        summary = self.session.get_recorded_steps_summary()
        await bridge.send_to_overlay({
            "type": "command_response",
            "payload": {
                "message": (
                    f"{summary}\n\n"
                    f"Generating TypeScript script..."
                )
            }
        })

        code = self._generate_ts_code()
        import os
        os.makedirs(".hermes/output", exist_ok=True)
        filename = (
            f".hermes/output/"
            f"{self.session.test_name.replace(' ', '-')}"
            f".spec.ts"
        )
        with open(filename, "w") as f:
            f.write(code)

        await bridge.send_to_overlay({
            "type": "script_generated",
            "payload": {
                "filename": filename,
                "code": code,
                "message": f"Script saved to: {filename}"
            }
        })

    def _generate_ts_code(self) -> str:
        """Generate clean TypeScript test."""
        from datetime import datetime
        ts = datetime.now().isoformat()

        lines = [
            "// ============================================",
            f"// Generated by Hermes Automation Co-pilot",
            f"// Session: {ts}",
            f"// Test: {self.session.test_name}",
            "// ============================================",
            "",
            "import { test, expect } from '@playwright/test'",
            "",
            "// === TEST ===",
            f"test('{self.session.test_name}',"
            " async ({ page }) => {",
        ]

        for step in self.session.steps:
            lines.append(
                f"  {step['generated_line']}"
            )

        lines.append("})")
        return "\n".join(lines)

    async def _cmd_status(self) -> None:
        """Show session status."""
        summary = self.session.get_recorded_steps_summary()
        await bridge.send_to_overlay({
            "type": "command_response",
            "payload": {"message": summary}
        })

    async def _cmd_clear(self) -> None:
        """Clear all recorded steps."""
        self.session.steps = []
        self.session.step_counter = 0
        await bridge.send_to_overlay({
            "type": "command_response",
            "payload": {
                "message": "All steps cleared."
            }
        })

    async def _cmd_run(self) -> None:
        """Run generated test."""
        await bridge.send_to_overlay({
            "type": "command_response",
            "payload": {
                "message": "Run feature coming soon."
            }
        })


# Global agent loop instance
agent_loop = AgentWSLoop()
