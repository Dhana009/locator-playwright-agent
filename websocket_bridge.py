"""
WebSocket bridge for Hermes Playwright
co-pilot. Connects Overlay UI to Hermes agent.
Runs on ws://localhost:7777
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)

class HermesWebSocketBridge:
    """
    WebSocket server that bridges:
      Overlay UI (JavaScript in browser)
      ↕
      Hermes agent (Python backend)
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 7777
    ):
        self.host = host
        self.port = port
        self.clients: Set = set()
        
        # Messages FROM overlay TO agent
        self.incoming_queue: asyncio.Queue = (
            asyncio.Queue()
        )
        
        # Messages FROM agent TO overlay
        self.outgoing_queue: asyncio.Queue = (
            asyncio.Queue()
        )
        
        self.server = None
        self.session_id: Optional[str] = None
        self.running = False

    async def start(self) -> None:
        """Start the WebSocket server."""
        try:
            websockets = __import__("websockets")
            self.server = await websockets.serve(
                self._handle_client,
                self.host,
                self.port
            )
            self.running = True
            logger.info(
                f"WebSocket bridge started on "
                f"ws://{self.host}:{self.port}"
            )
            print(
                f"🔌 WebSocket bridge ready: "
                f"ws://{self.host}:{self.port}"
            )
        except OSError as e:
            if "address already in use" in str(e).lower():
                print(
                    f"⚠️  Port {self.port} already in use."
                    f" Stop previous session first with:"
                    f" lsof -ti:{self.port} | xargs kill -9"
                )
            logger.error(
                f"Failed to start WebSocket bridge: {e}"
            )
            raise
        except Exception as e:
            logger.error(
                f"Failed to start WebSocket bridge: {e}"
            )
            raise

    async def stop(self) -> None:
        """Stop the WebSocket server."""
        self.running = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            print("🔌 WebSocket bridge stopped")

    async def _handle_client(
        self,
        websocket,
        path: str = "/"
    ) -> None:
        """Handle a new client connection."""
        self.clients.add(websocket)
        client_addr = websocket.remote_address
        print(f"🔗 Overlay connected: {client_addr}")

        try:
            async for raw_message in websocket:
                try:
                    message = json.loads(raw_message)
                    await self.incoming_queue.put(
                        message
                    )
                    logger.debug(
                        f"Received: {message.get('type')}"
                    )
                except json.JSONDecodeError as e:
                    logger.error(
                        f"Invalid JSON from overlay: {e}"
                    )
                    await self._send_to_client(
                        websocket,
                        {
                            "type": "error",
                            "payload": {
                                "message": "Invalid JSON",
                                "detail": str(e)
                            }
                        }
                    )
        except Exception as e:
            logger.info(
                f"Client disconnected: {client_addr}"
                f" — {e}"
            )
        finally:
            self.clients.discard(websocket)
            print(f"🔌 Overlay disconnected: "
                  f"{client_addr}")

    async def _send_to_client(
        self,
        websocket,
        message: Dict[str, Any]
    ) -> None:
        """Send message to specific client."""
        try:
            await websocket.send(
                json.dumps(message)
            )
        except Exception as e:
            logger.error(
                f"Failed to send to client: {e}"
            )
            self.clients.discard(websocket)

    async def broadcast(
        self,
        message: Dict[str, Any]
    ) -> None:
        """Send message to ALL connected clients."""
        if not self.clients:
            return
        raw = json.dumps(message)
        disconnected = set()
        for client in self.clients.copy():
            try:
                await client.send(raw)
            except Exception:
                disconnected.add(client)
        for client in disconnected:
            self.clients.discard(client)

    async def send_to_overlay(
        self,
        message: Dict[str, Any]
    ) -> None:
        """
        Send message from agent to overlay.
        Use this from agent code.
        """
        await self.broadcast(message)

    async def get_next_message(
        self,
        timeout: float = 300.0
    ) -> Optional[Dict[str, Any]]:
        """
        Get next message from overlay.
        Blocks until message arrives or timeout.
        Returns None on timeout.
        """
        try:
            return await asyncio.wait_for(
                self.incoming_queue.get(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            return None

    async def send_status(
        self,
        message: str,
        stage: str = ""
    ) -> None:
        """Send status update to overlay."""
        await self.send_to_overlay({
            "type": "status",
            "payload": {
                "message": message,
                "stage": stage
            }
        })

    async def send_thinking(
        self,
        message: str
    ) -> None:
        """Send thinking indicator to overlay."""
        await self.send_to_overlay({
            "type": "thinking",
            "payload": {"message": message}
        })

    async def send_confirmation_request(
        self,
        understood: str,
        locator: str,
        action: str,
        element_name: str,
        highlight: Optional[Dict] = None,
        confidence: int = 95
    ) -> None:
        """Ask user to confirm understood intent."""
        await self.send_to_overlay({
            "type": "confirmation_request",
            "payload": {
                "understood": understood,
                "locator": locator,
                "action": action,
                "element_name": element_name,
                "highlight": highlight or {},
                "confidence": confidence
            }
        })

    async def send_step_recorded(
        self,
        step_number: int,
        action: str,
        element: str,
        locator: str,
        generated_line: str,
        status: str = "success"
    ) -> None:
        """Notify overlay that step was recorded."""
        await self.send_to_overlay({
            "type": "step_recorded",
            "payload": {
                "step_number": step_number,
                "action": action,
                "element": element,
                "locator": locator,
                "generated_line": generated_line,
                "status": status
            }
        })

    async def send_error(
        self,
        message: str,
        step: Optional[int] = None,
        suggestion: str = ""
    ) -> None:
        """Send error to overlay."""
        await self.send_to_overlay({
            "type": "error",
            "payload": {
                "message": message,
                "step": step,
                "suggestion": suggestion
            }
        })

    async def send_session_ready(
        self,
        session_id: str
    ) -> None:
        """Tell overlay session is ready."""
        await self.send_to_overlay({
            "type": "session_ready",
            "payload": {
                "session_id": session_id,
                "message": "Ready — browser connected"
            }
        })

    @property
    def is_overlay_connected(self) -> bool:
        """Check if any overlay client connected."""
        return len(self.clients) > 0

    @property
    def client_count(self) -> int:
        """Number of connected overlay clients."""
        return len(self.clients)


def kill_existing_bridge(port: int = 7777) -> None:
    """
    Kill any process using our WebSocket port.
    Call this before starting if port may be in use.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True
        )
        pids = result.stdout.strip().split("\n")
        pids = [p for p in pids if p.strip()]
        if pids:
            for pid in pids:
                subprocess.run(
                    ["kill", "-9", pid],
                    capture_output=True
                )
            print(
                f"🔪 Killed {len(pids)} process(es)"
                f" on port {port}"
            )
    except Exception:
        pass


# Global bridge instance
# Import this from other modules:
# from websocket_bridge import bridge
bridge = HermesWebSocketBridge()
