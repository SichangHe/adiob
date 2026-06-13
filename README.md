# adiob

`adiob` is a small static audiobook reader prototype for GitHub Pages.

It assumes audio has already been generated and aligned to text. The page loads a book manifest, plays a local audio file, highlights the current sentence, and lets a reader seek by sentence.

## local run

```sh
python3 scripts/serve-local.py 8000
```

Open `http://localhost:8000` from this directory.

Use `scripts/serve-local.py` instead of `python3 -m http.server` for local audiobook testing. It supports byte-range requests, which browsers need for seeking inside `media/sample.m4a`.

The local server serves static files from disk. The page tries `releaseAudio.url` first when the manifest has one, then falls back to `audio`. If the page still plays old audio, update or remove `releaseAudio.url`; for local fallback audio, regenerate or replace the file named by `audio` in `data/small-walk.json`:

```sh
uv run --with 'kokoro>=0.9.4' --with soundfile scripts/generate-kokoro-audio.py --manifest data/small-walk.json --out media/sample.m4a --confirm-rights --rough-timings
```

## voice and publication workflow

The preferred local voice path is Kokoro-82M via the `kokoro` Python package. The model card describes Kokoro as an "open-weight TTS model with 82 million parameters" and lists `apache-2.0` licensing. ElevenLabs can be a high-quality commercial API alternative, but it is not the open-source/local workflow in this prototype.

Generate audio only from public-domain, permissively licensed, or user-provided text:

```sh
uv run --with 'kokoro>=0.9.4' --with soundfile scripts/generate-kokoro-audio.py --manifest data/small-walk.json --out media/sample.m4a --confirm-rights --rough-timings
```

Publish generated audio as a GitHub release asset and write the release URL into the manifest:

```sh
scripts/publish-release-audio.sh --confirm-rights -R OWNER/REPO audio-small-walk-v1 data/small-walk.json media/sample.m4a
```

The release script requires `origin` to match `OWNER/REPO` before a real upload. Add `--clobber` only when replacing an existing release asset is intended.

The Pages UI tries `releaseAudio.url` first when present and falls back to `audio` if the release asset cannot be loaded. The GitHub Pages workflow is in `.github/workflows/pages.yml`.

The `-10` and `+10` buttons seek by seconds. Use the Speed menu to change playback tempo in the browser without regenerating audio.

## layout

```text
.
|-- data
|   |-- books.json
|   `-- small-walk.json
|-- docs
|   `-- design.md
|-- media
|   |-- cover.png
|   `-- sample.m4a
|-- scripts
|   |-- generate-kokoro-audio.py
|   |-- publish-release-audio.sh
|   |-- serve-local.py
|   `-- set-release-audio-url.py
|-- src
|   |-- app.js
|   `-- style.css
`-- index.html
```

## data format

`data/books.json` points to one or more book manifests. Each manifest contains:

- `audio`: relative path to pre-generated audio
- `releaseAudio.url`: optional GitHub release asset URL preferred by the UI
- `cover`: relative path to cover art
- `segments`: ordered text spans with `startSec` and `endSec`

Segment timing can be paragraph-level or sentence-level. This demo uses sentence-level timing.

## rights

The included sample text, timing data, generated audio, and cover image are original demo assets released as `CC0-1.0`. Code is under `MIT`.

See `ASSET-LICENSE.md` for the sample asset notice.

For a public GitHub Pages deployment, only publish books, text, covers, and audio that you have the right to distribute. This prototype intentionally does not include copyrighted book ingestion or any mechanism for hiding infringement.
