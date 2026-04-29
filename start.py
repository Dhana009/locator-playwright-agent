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

    provider = model_cfg.get("provider", "opencode-go")

    # resolve API key based on provider
    if provider == "opencode-go":
        api_key = os.getenv("OPENCODE_API_KEY", "").strip()
        # try reading from opencode auth if not in env
        if not api_key:
            opencode_auth = Path.home() / ".local/share/opencode/auth.json"
            if opencode_auth.exists():
                import json
                auth = json.loads(opencode_auth.read_text())
                # extract key from auth.json
                for k, v in auth.items():
                    if isinstance(v, dict) and "key" in v:
                        api_key = v["key"]
                        break
                    elif isinstance(v, str) and v.startswith("sk-"):
                        api_key = v
                        break
    else:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()

    model = (
        os.getenv("HERMES_MODEL", "").strip()
        or model_cfg.get("default", "deepseek-v4-flash")
    )

    p = argparse.ArgumentParser()
    p.add_argument("query", nargs="?")
    p.add_argument("--base-url", default=base_url)
    p.add_argument("--model", default=model)
    p.add_argument("--api-key", default=api_key)
    p.add_argument("--max-turns", type=int, default=32)
    p.add_argument("--toolsets", default="safe")
    args = p.parse_args()

    text = args.query.strip() if args.query else sys.stdin.read().strip()
    if not text:
        p.error("Provide a message as argument or on stdin.")

    print(f"🔗 Provider: {provider}")
    print(f"🤖 Model: {args.model}")
    print(f"🌐 Base URL: {args.base_url}")

    enabled = [s.strip() for s in args.toolsets.split(",") if s.strip()]

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
