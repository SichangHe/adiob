#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write a release-hosted audio URL into an adiob manifest."
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--url", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--asset", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest: dict[str, Any] = json.loads(args.manifest.read_text(encoding="utf-8"))
    manifest["releaseAudio"] = {
        "url": args.url,
        "tag": args.tag,
        "asset": args.asset,
    }
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"updated {args.manifest} releaseAudio.url")


if __name__ == "__main__":
    main()
