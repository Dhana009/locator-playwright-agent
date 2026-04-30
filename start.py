#!/usr/bin/env python3
"""
Minimal Hermes runner — reads from ~/.hermes/config.yaml automatically.
Usage:
  .venv/bin/python start.py "Your task"
"""

from __future__ import annotations
import argparse
import os
import sys
import yaml
from pathlib import Path


def load_hermes_config() -> dict:
    config_path = Path.home() / ".hermes" / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}


def main() -> int:
    config = load_hermes_config()
    model_cfg = config.get("model", {})

    base_url = (
        os.getenv("HERMES_BASE_URL", "").strip()
        or model_cfg.get("base_url", "")
        or "https://opencode.ai/zen/go/v1"
    )

    provider = model_cfg.get(
        "provider", "opencode-go"
    )

    if provider == "opencode-go":
        api_key = os.getenv(
            "OPENCODE_API_KEY", ""
        ).strip()
        if not api_key:
            opencode_auth = Path.home() / (
                ".local/share/opencode/auth.json"
            )
            if opencode_auth.exists():
                import json
                auth = json.loads(
                    opencode_auth.read_text()
                )
                for k, v in auth.items():
                    if isinstance(v, dict) and "key" in v:
                        api_key = v["key"]
                        break
                    elif (isinstance(v, str) and
                          v.startswith("sk-")):
                        api_key = v
                        break
    else:
        api_key = os.getenv(
            "OPENAI_API_KEY", ""
        ).strip()

    model = (
        os.getenv("HERMES_MODEL", "").strip()
        or model_cfg.get("default", "deepseek-v4-flash")
    )

    p = argparse.ArgumentParser()
    p.add_argument("query", nargs="?")
    p.add_argument("--base-url", default=base_url)
    p.add_argument("--model", default=model)
    p.add_argument("--api-key", default=api_key)
    p.add_argument("--max-turns", type=int,
                   default=32)
    p.add_argument("--toolsets",
                   default="safe")
    p.add_argument(
        "--ws-mode",
        action="store_true",
        help="Run in WebSocket mode for overlay UI"
    )
    args = p.parse_args()

    print(f"🔗 Provider: {provider}")
    print(f"🤖 Model: {args.model}")
    print(f"🌐 Base URL: {args.base_url}")

    # WebSocket mode — for overlay UI
    if args.ws_mode:
        import asyncio
        from websocket_bridge import bridge
        from agent_ws_loop import agent_loop
        from tools.playwright_automation import (
            browser_launch,
            inject_overlay,
        )
        import json

        async def run_ws_mode():
            # Kill any leftover process on port 7777
            from websocket_bridge import kill_existing_bridge
            kill_existing_bridge()
            await asyncio.sleep(0.5)

            print("🚀 Starting Playwright co-pilot...")

            # Start WebSocket bridge
            await bridge.start()

            # Launch browser
            print("🌐 Launching browser...")
            result = json.loads(
                await browser_launch(
                    headless=False
                )
            )
            print(f"✅ Browser ready: {result}")

            print("💉 Injecting overlay UI...")
            inject_result = json.loads(
                await inject_overlay()
            )
            print(f"✅ Overlay: {inject_result}")

            # Start agent loop
            print(
                "⏳ Waiting for overlay to connect..."
            )
            print(
                "   Open browser at any URL to begin"
            )

            # Run bridge server and agent loop
            # concurrently
            await asyncio.gather(
                bridge.server.serve_forever(),
                agent_loop.start()
            )

        asyncio.run(run_ws_mode())
        return 0

    # Normal chat mode (existing behavior)
    text = args.query.strip() if args.query else (
        sys.stdin.read().strip()
    )
    if not text:
        p.error(
            "Provide a message as argument "
            "or on stdin."
        )

    enabled = [
        s.strip()
        for s in args.toolsets.split(",")
        if s.strip()
    ]

    from run_agent import AIAgent
    agent = AIAgent(
        base_url=args.base_url,
        model=args.model,
        api_key=args.api_key,
        max_iterations=args.max_turns,
        enabled_toolsets=enabled or None,
        quiet_mode=not sys.stderr.isatty(),
        platform="minimal",
    )
    reply = agent.chat(text)
    if reply:
        print(reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
