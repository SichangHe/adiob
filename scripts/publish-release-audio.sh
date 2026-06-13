#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
usage: scripts/publish-release-audio.sh [--dry-run] [--confirm-rights] [--clobber] [-R owner/repo] TAG MANIFEST AUDIO_FILE [AUDIO_FILE...]

Uploads generated audiobook audio as GitHub release assets, then writes the
primary asset URL into MANIFEST as releaseAudio.url.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

dry_run=0
confirm_rights=0
clobber=0
repo=""
while (($#)); do
  case "$1" in
    --dry-run)
      dry_run=1
      shift
      ;;
    --confirm-rights)
      confirm_rights=1
      shift
      ;;
    --clobber)
      clobber=1
      shift
      ;;
    -R|--repo)
      (($# >= 2)) || die "$1 needs owner/repo"
      repo="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      die "unknown option $1"
      ;;
    *)
      break
      ;;
  esac
done

(($# >= 3)) || {
  usage >&2
  exit 2
}

tag="$1"
manifest="$2"
shift 2
files=("$@")
[[ "$tag" =~ ^[A-Za-z0-9._-]+$ ]] || die "use a simple release tag with letters, digits, dots, underscores, or hyphens"
[[ -f "$manifest" ]] || die "manifest not found: $manifest"

for file in "${files[@]}"; do
  [[ -f "$file" ]] || die "audio file not found: $file"
  asset_name="$(basename "$file")"
  [[ "$asset_name" =~ ^[A-Za-z0-9._-]+$ ]] || die "asset name must be URL-safe: $asset_name"
done

remote_repo_from_url() {
  local url="$1"
  local path=""
  case "$url" in
    git@github.com:*)
      path="${url#git@github.com:}"
      ;;
    https://github.com/*)
      path="${url#https://github.com/}"
      ;;
    http://github.com/*)
      path="${url#http://github.com/}"
      ;;
    ssh://git@github.com/*)
      path="${url#ssh://git@github.com/}"
      ;;
    *)
      return 1
      ;;
  esac
  path="${path%.git}"
  path="${path%/}"
  [[ "$path" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || return 1
  printf '%s\n' "$path"
}

lower() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

repo_args=()
if [[ -n "$repo" ]]; then
  repo_args=(-R "$repo")
fi
if [[ -n "$repo" ]]; then
  repo="$(gh repo view "$repo" --json nameWithOwner -q .nameWithOwner)"
else
  repo="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
fi
primary_asset="$(basename "${files[0]}")"
release_url="https://github.com/${repo}/releases/download/${tag}/${primary_asset}"

if ((dry_run)); then
  printf 'repo: %s\n' "$repo"
  printf 'release: %s\n' "$tag"
  printf 'primary url: %s\n' "$release_url"
  printf 'would upload:'
  printf ' %q' "${files[@]}"
  printf '\n'
  printf 'would update: %s\n' "$manifest"
  exit 0
fi

((confirm_rights)) || die "pass --confirm-rights only for public-domain, permissively licensed, or user-provided audio"
origin_url="$(git remote get-url origin 2>/dev/null)" || die "configure git remote origin for the intended GitHub repository before publishing"
origin_repo="$(remote_repo_from_url "$origin_url")" || die "origin must be a GitHub repository URL"
[[ "$(lower "$origin_repo")" == "$(lower "$repo")" ]] || die "target repo $repo does not match origin $origin_repo"

if ! gh release view "$tag" "${repo_args[@]}" >/dev/null 2>&1; then
  gh release create "$tag" "${repo_args[@]}" --title "$tag" --notes "Audiobook audio assets for rights-cleared adiob content."
fi
upload_args=()
if ((clobber)); then
  upload_args=(--clobber)
fi
gh release upload "$tag" "${files[@]}" "${upload_args[@]}" "${repo_args[@]}"
python3 scripts/set-release-audio-url.py "$manifest" --url "$release_url" --tag "$tag" --asset "$primary_asset"
printf '%s\n' "$release_url"
