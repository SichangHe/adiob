#!/bin/bash
# 🤖 Publish every book that has local generated audio under local/owned-books/.
# Books missing from the private catalogs are added to internal-books.json first.
# usage: scripts/publish-all-audiobooks.sh            # dry run: audit only, changes nothing
#        scripts/publish-all-audiobooks.sh --publish  # catalog, upload release audio, push indexes, deploy Pages
set -euo pipefail
cd "$(dirname "$0")/.."
priv=../adiob-private-artifacts
mode=${1:-}
python3 -I - "$priv" "$mode" <<'PY'
import json, os, sys
priv, mode = sys.argv[1], sys.argv[2]
cats = {c: json.load(open(f"{priv}/{c}")) for c in ("books.json", "internal-books.json")}
ids = {b["id"] for c in cats.values() for b in c["books"]}
root = "local/owned-books"
for b in sorted(os.listdir(root)):
    d = f"{root}/{b}"
    if b in ids or not os.path.isdir(f"{d}/chunks") or not os.listdir(f"{d}/chunks"):
        continue
    m = json.load(open(f"{d}/manifest.json"))
    src = f"owned-text/{b}.txt"
    text = open(src).read() if os.path.isfile(src) else "\n\n".join(s["text"] for s in m["segments"]) + "\n"
    print(f"{'add' if mode == '--publish' else 'would add'} catalog entry: {b}")
    if mode != "--publish":
        continue
    os.makedirs(f"{priv}/cleaned-texts", exist_ok=True)
    open(f"{priv}/cleaned-texts/{b}.txt", "w").write(text)
    cats["internal-books.json"]["books"].append({
        "id": b, "title": m.get("title") or b, "author": m.get("author") or "",
        "text": f"cleaned-texts/{b}.txt",
        "release": {"repository": "SichangHe/adiob", "tag": "internal-audiobooks-v1"},
        "rightsConfirmed": True,
    })
    json.dump(cats["internal-books.json"], open(f"{priv}/internal-books.json", "w"), indent=2, ensure_ascii=False)
    open(f"{priv}/internal-books.json", "a").write("\n")
PY
if [ "$mode" = --publish ]; then
  if [ -n "$(git -C "$priv" status --porcelain -- internal-books.json cleaned-texts)" ]; then
    git -C "$priv" add internal-books.json cleaned-texts
    git -C "$priv" commit -q -m "chore: catalog books with local audio"
  fi
  python3 scripts/publish-private-audiobooks.py --confirm-rights --publish
else
  python3 scripts/publish-private-audiobooks.py --dry-run
fi
