"""
Diagnostic: send one prompt through Pi and print the raw RPC event stream.

The chat path deliberately reduces Pi's events to a small SSE vocabulary, which
is right for the product and useless for debugging a turn that produces no text.
This prints everything, including the message payloads that carry provider
errors, so a silent turn has a visible cause.

    python -m scripts.agent_smoke "Say hello"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.agent.pi_client import PiClient
from app.core.logging import configure_logging

_MAX = 600


async def run(prompt: str) -> int:
    client = PiClient()
    try:
        await client.start()
        print(f"--- prompt: {prompt}")
        async for event in client.prompt(prompt):
            kind = event.get("type")
            if kind == "message_update":
                delta = event.get("assistantMessageEvent") or {}
                if delta.get("type") == "text_delta":
                    print(delta.get("delta"), end="", flush=True)
                    continue
                print(f"\n[event] {kind}:{delta.get('type')}", flush=True)
                continue
            print(f"\n[event] {kind} {json.dumps(event)[:_MAX]}", flush=True)
        print("\n--- stream ended")
    finally:
        await client.stop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Print Pi's raw RPC event stream for one prompt.")
    parser.add_argument("prompt", nargs="?", default="Say FOUNDATION OK and nothing else.")
    args = parser.parse_args()

    configure_logging()
    return asyncio.run(run(args.prompt))


if __name__ == "__main__":
    sys.exit(main())
