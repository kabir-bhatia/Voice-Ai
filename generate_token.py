import platform as _platform
import socket as _socket
import os
import argparse
import secrets
from datetime import timedelta

if getattr(_platform, "_uname_cache", None) is None:
    try:
        _platform._uname_cache = _platform.uname_result(
            "Windows", _socket.gethostname(), "", "", ""
        )
    except Exception:
        pass

from dotenv import load_dotenv
load_dotenv(".env.local")

from livekit.api import AccessToken, VideoGrants


def create_token(room_name: str, identity: str = "user1", minutes: int = 60) -> str:
    # The agent uses automatic dispatch (no agent_name in agent.py), so the token
    # does NOT need roomConfig.agents — the worker joins any new room on its own.
    api_key = os.environ["LIVEKIT_API_KEY"]
    api_secret = os.environ["LIVEKIT_API_SECRET"]

    token = AccessToken(api_key, api_secret)
    token.with_identity(identity)
    token.with_name(identity)
    token.with_grants(VideoGrants(
        room_join=True,
        room=room_name,
    ))

    token.with_ttl(timedelta(minutes=minutes))
    return token.to_jwt()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a LiveKit join token with auto-dispatch")
    parser.add_argument("--room", default=f"demo-{secrets.token_hex(3)}",
                        help="Room name (default: a fresh unique demo-XXXXXX room)")
    parser.add_argument("--identity", default="user1", help="User identity (default: user1)")
    parser.add_argument("--minutes", type=int, default=60, help="Token TTL in minutes (default: 60)")
    args = parser.parse_args()

    token = create_token(args.room, args.identity, args.minutes)
    url = os.environ.get("LIVEKIT_URL", "")
    join_url = f"https://meet.livekit.io/?liveKitUrl={url}&token={token}"

    print(f"\nToken (valid for {args.minutes} min):")
    print(f"\n{token}\n")
    print(f"Join URL (open in browser):")
    print(f"\n{join_url}\n")