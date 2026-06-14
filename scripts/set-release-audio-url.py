#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
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


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolved_under(path: Path, parent: Path) -> bool:
    return path.resolve(strict=False).is_relative_to(parent.resolve(strict=False))


def lexical_path(path: Path) -> Path:
    return Path(os.path.abspath(path))


def lexical_under(path: Path, parent: Path) -> bool:
    return lexical_path(path).is_relative_to(lexical_path(parent))


def maybe_under(path: Path, parent: Path) -> bool:
    return lexical_under(path, parent) or resolved_under(path, parent)


def require_publishable_manifest(path: Path, manifest: dict[str, Any]) -> None:
    root = repo_root()
    private_roots = (root / "local", root / "owned-text")
    for private in private_roots:
        if maybe_under(path, private):
            name = private.relative_to(root).as_posix()
            raise SystemExit(f"refusing to update a manifest under ignored `{name}/`")
    if manifest.get("localOnly") is True:
        raise SystemExit("refusing to update a `localOnly` manifest")
    audio = manifest.get("audio")
    if isinstance(audio, str):
        audio_path = Path(audio)
        resolved_audio = audio_path if audio_path.is_absolute() else root / audio_path
        for private in private_roots:
            if not maybe_under(resolved_audio, private):
                continue
            name = private.relative_to(root).as_posix()
            raise SystemExit(
                f"refusing to update a manifest whose `audio` is under ignored `{name}/`"
            )
    audio_chunks = manifest.get("audioChunks")
    if isinstance(audio_chunks, list):
        manifest_dir = lexical_path(path).parent
        for chunk in audio_chunks:
            if not isinstance(chunk, dict):
                continue
            chunk_path = chunk.get("path")
            if not isinstance(chunk_path, str):
                continue
            raw_path = Path(chunk_path)
            candidates = (
                (raw_path,)
                if raw_path.is_absolute()
                else (manifest_dir / raw_path, root / raw_path)
            )
            for candidate in candidates:
                for private in private_roots:
                    if not maybe_under(candidate, private):
                        continue
                    name = private.relative_to(root).as_posix()
                    raise SystemExit(
                        "refusing to update a manifest whose `audioChunks` "
                        f"reference ignored `{name}/`"
                    )


def main() -> None:
    args = parse_args()
    manifest_path = args.manifest
    disk_path = lexical_path(manifest_path)
    manifest: dict[str, Any] = json.loads(disk_path.read_text(encoding="utf-8"))
    require_publishable_manifest(manifest_path, manifest)
    manifest["releaseAudio"] = {
        "url": args.url,
        "tag": args.tag,
        "asset": args.asset,
    }
    disk_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"updated {disk_path} releaseAudio.url")


if __name__ == "__main__":
    main()
