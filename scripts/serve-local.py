#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CHUNK_SIZE = 64 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve adiob static files with byte-range support.")
    parser.add_argument("port", nargs="?", type=int, default=8000)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--directory", type=Path, default=Path("."))
    return parser.parse_args()


def parse_byte_range(header: str, size: int) -> tuple[int, int] | None:
    if not header.startswith("bytes=") or "," in header:
        return None
    start_text, sep, end_text = header.removeprefix("bytes=").partition("-")
    if not sep:
        return None
    try:
        if not start_text:
            suffix_len = int(end_text)
            if suffix_len <= 0:
                return None
            return max(0, size - suffix_len), size - 1
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        return None
    return start, min(end, size - 1)


class RangeRequestHandler(SimpleHTTPRequestHandler):
    range: tuple[int, int] | None = None

    def send_head(self):
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()
        try:
            source = open(path, "rb")
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None
        stat = os.fstat(source.fileno())
        size = stat.st_size
        self.range = None
        byte_range = self.headers.get("Range")
        if byte_range:
            parsed_range = parse_byte_range(byte_range, size)
            if parsed_range is None:
                source.close()
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return None
            start, end = parsed_range
            self.range = start, end
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(end - start + 1))
            source.seek(start)
        else:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Length", str(size))
        self.send_header("Content-type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Last-Modified", self.date_time_string(stat.st_mtime))
        self.end_headers()
        return source

    def copyfile(self, source, outputfile) -> None:
        if self.range is None:
            return super().copyfile(source, outputfile)
        start, end = self.range
        remaining = end - start + 1
        while remaining > 0:
            chunk = source.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)


def main() -> None:
    args = parse_args()
    handler = partial(RangeRequestHandler, directory=str(args.directory))
    server = ThreadingHTTPServer((args.bind, args.port), handler)
    print(f"serving {args.directory.resolve()} at http://{args.bind}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
