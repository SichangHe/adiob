# adiob

`adiob` is a small static audiobook reader prototype for GitHub Pages.

It assumes audio has already been generated and aligned to text. The page loads a book manifest, plays a local audio file, highlights the current sentence, and lets a reader seek by sentence.

## local run

```sh
python3 -m http.server 8000
```

Open `http://localhost:8000` from this directory.

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
|-- src
|   |-- app.js
|   `-- style.css
`-- index.html
```

## data format

`data/books.json` points to one or more book manifests. Each manifest contains:

- `audio`: relative path to pre-generated audio
- `cover`: relative path to cover art
- `segments`: ordered text spans with `startSec` and `endSec`

Segment timing can be paragraph-level or sentence-level. This demo uses sentence-level timing.

## rights

The included sample text, timing data, generated audio, and cover image are original demo assets released as `CC0-1.0`. Code is under `MIT`.

See `ASSET-LICENSE.md` for the sample asset notice.

For a public GitHub Pages deployment, only publish books, text, covers, and audio that you have the right to distribute. This prototype intentionally does not include copyrighted book ingestion or any mechanism for hiding infringement.
