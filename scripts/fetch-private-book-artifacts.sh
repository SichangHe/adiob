#!/usr/bin/env bash
set -euo pipefail

target="${1:-_private-books}"
repo="${PRIVATE_BOOK_ARTIFACT_REPO:-SichangHe/adiob-private-artifacts}"
ref="${PRIVATE_BOOK_ARTIFACT_REF:-main}"
token="${PRIVATE_BOOK_ARTIFACTS_TOKEN:-}"

if [[ -z "$token" ]]; then
  printf 'PRIVATE_BOOK_ARTIFACTS_TOKEN is not set; deploying public sample catalog only\n' >&2
  rm -rf "$target"
  mkdir -p "$target"
  exit 0
fi
if [[ ! "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  printf 'PRIVATE_BOOK_ARTIFACT_REPO must look like owner/repo\n' >&2
  exit 2
fi

rm -rf "$target"
auth="$(printf 'x-access-token:%s' "$token" | base64 | tr -d '\n')"
mkdir -p "$target"
git -C "$target" init -q
git -C "$target" remote add origin "https://github.com/${repo}.git"
git \
  -C "$target" \
  -c credential.helper= \
  -c "http.https://github.com/.extraheader=AUTHORIZATION: basic ${auth}" \
  fetch --depth 1 origin "$ref"
git -C "$target" checkout -q --detach FETCH_HEAD
if [[ "$ref" =~ ^[0-9a-f]{40}$ ]]; then
  actual="$(git -C "$target" rev-parse HEAD)"
  if [[ "$actual" != "$ref" ]]; then
    printf 'private artifact checkout mismatch: expected %s got %s\n' "$ref" "$actual" >&2
    exit 2
  fi
fi
git -C "$target" remote remove origin || true
