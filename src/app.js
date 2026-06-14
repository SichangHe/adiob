const audio = document.querySelector("#audio");
const back = document.querySelector("#back");
const cover = document.querySelector("#cover");
const currentTime = document.querySelector("#current-time");
const duration = document.querySelector("#duration");
const forward = document.querySelector("#forward");
const license = document.querySelector("#book-license");
const play = document.querySelector("#play");
const playbackRate = document.querySelector("#playback-rate");
const releaseAudio = document.querySelector("#release-audio");
const scrub = document.querySelector("#scrub");
const segments = document.querySelector("#segments");
const title = document.querySelector("#book-title");
const author = document.querySelector("#book-author");
const bookSelect = document.querySelector("#book-select");
const bookPicker = document.querySelector("#book-picker");
const buildTag = document.querySelector("#build-tag");

let book = null;
let catalog = null;
let activeId = "";
let pendingSeekSec = null;
let currentManifest = "";

function renderBuildTag() {
  const build = buildTag?.dataset.build;
  if (!build) {
    return;
  }
  const isLocal = ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
  buildTag.textContent = `${isLocal ? "localhost" : "source"} ${build}`;
}

async function loadJson(path) {
  const response = await fetch(path);
  if (!response.ok) {
    return { error: `could not load ${path}` };
  }
  return { value: await response.json() };
}

function catalogPath() {
  return document.body.dataset.catalog || "data/books.json";
}

function requestedManifest() {
  const manifest = new URLSearchParams(window.location.search).get("manifest");
  if (!manifest) {
    return { value: null };
  }
  const parts = manifest.split("/");
  if (
    !manifest.startsWith("local/") ||
    manifest.includes(":") ||
    manifest.includes("?") ||
    manifest.includes("#") ||
    manifest.includes("\\") ||
    parts.some((part) => part === "" || part === "." || part === "..") ||
    !manifest.endsWith(".json")
  ) {
    return { error: "manifest must be a relative local JSON path" };
  }
  return { value: manifest };
}

function requestedBook() {
  return new URLSearchParams(window.location.search).get("book");
}

function rootPath(path) {
  if (/^[a-z][a-z0-9+.-]*:/i.test(path) || path.startsWith("/")) {
    return path;
  }
  if (path.startsWith("../") || path.startsWith("./")) {
    return path;
  }
  return `../${path}`;
}

function assetPath(path) {
  if (/^[a-z][a-z0-9+.-]*:/i.test(path) || path.startsWith("/")) {
    return path;
  }
  if (path.startsWith("local/") || path.startsWith("media/")) {
    return rootPath(path);
  }
  if (!currentManifest) {
    return rootPath(path);
  }
  return new URL(path, new URL(currentManifest, window.location.href)).href;
}

async function loadBook() {
  const requested = requestedManifest();
  if (requested.error) {
    return requested;
  }
  if (requested.value) {
    bookPicker.hidden = true;
    currentManifest = rootPath(requested.value);
    return loadJson(currentManifest);
  }
  const indexResult = await loadJson(catalogPath());
  if (indexResult.error) {
    return indexResult;
  }
  catalog = indexResult.value;
  renderBookSelect(catalog.books);
  const selectedId = requestedBook() || catalog.defaultBook;
  const item = catalog.books.find((entry) => entry.id === selectedId);
  if (!item) {
    return { error: "selected book is missing" };
  }
  bookSelect.value = item.id;
  currentManifest = item.manifest;
  return loadJson(item.manifest);
}

function renderBookSelect(books) {
  bookSelect.replaceChildren(
    ...books.map((entry) => {
      const option = document.createElement("option");
      option.value = entry.id;
      option.textContent = entry.title || entry.id;
      return option;
    }),
  );
  bookPicker.hidden = false;
}

function renderError(message) {
  title.textContent = "adiob";
  segments.replaceChildren();
  const error = document.createElement("p");
  error.className = "load-error";
  error.textContent = message;
  segments.append(error);
}

function renderBook(nextBook) {
  book = nextBook;
  document.title = `${book.title} | adiob`;
  title.textContent = book.title;
  author.textContent = book.author;
  license.textContent = book.license;
  cover.src = assetPath(book.cover);
  cover.alt = `${book.title} cover`;
  audio.dataset.releaseFallbackUsed = "0";
  audio.src = assetPath(book.releaseAudio?.url || book.audio);
  setPlaybackRate();
  renderReleaseAudio(book.releaseAudio);
  scrub.max = String(book.durationSec);
  duration.textContent = fmtTime(book.durationSec);
  segments.replaceChildren(...book.segments.map(renderSegment));
  activeId = "";
  updateProgress(0);
}

function renderReleaseAudio(track) {
  if (!track?.url) {
    releaseAudio.hidden = true;
    releaseAudio.removeAttribute("href");
    return;
  }
  releaseAudio.hidden = false;
  releaseAudio.href = assetPath(track.url);
  releaseAudio.textContent = track.asset ? `Release audio: ${track.asset}` : "Release audio";
}

function renderSegment(segment, index) {
  const button = document.createElement("button");
  button.className = "segment";
  button.type = "button";
  button.dataset.id = segment.id;
  button.dataset.index = String(index);
  button.dataset.startSec = String(segment.startSec);
  const text = document.createElement("span");
  text.className = "segment-text";
  text.textContent = segment.text;
  button.append(text);
  button.addEventListener("click", () => {
    seekTo(segment.startSec);
    void audio.play();
  });
  return button;
}

function fmtTime(valueSec) {
  const sec = Math.max(0, Math.floor(valueSec));
  const min = Math.floor(sec / 60);
  return `${min}:${String(sec % 60).padStart(2, "0")}`;
}

function segmentAt(valueSec) {
  if (!book) {
    return null;
  }
  return book.segments.find((segment) => valueSec >= segment.startSec && valueSec < segment.endSec) ?? book.segments.at(-1);
}

function updateProgress(valueSec) {
  scrub.value = String(valueSec);
  currentTime.textContent = fmtTime(valueSec);
  const nextSegment = segmentAt(valueSec);
  if (!nextSegment || nextSegment.id === activeId) {
    return;
  }
  activeId = nextSegment.id;
  for (const button of segments.querySelectorAll(".segment")) {
    const isActive = button.dataset.id === activeId;
    button.classList.toggle("active", isActive);
    button.toggleAttribute("aria-current", isActive);
  }
  const active = segments.querySelector(".segment.active");
  active?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function maxPlaybackSec() {
  if (Number.isFinite(audio.duration)) {
    return audio.duration;
  }
  if (Number.isFinite(book?.durationSec)) {
    return book.durationSec;
  }
  return 0;
}

function seekTo(valueSec) {
  const targetSec = Math.min(Math.max(0, valueSec), maxPlaybackSec());
  if (audio.readyState === 0) {
    pendingSeekSec = targetSec;
  } else {
    audio.currentTime = targetSec;
  }
  updateProgress(targetSec);
}

function seekBy(deltaSec) {
  const scrubSec = Number(scrub.value);
  const currentSec =
    audio.readyState === 0 && Number.isFinite(scrubSec) ? scrubSec : audio.currentTime;
  seekTo(currentSec + deltaSec);
}

function setPlaying(isPlaying) {
  play.textContent = isPlaying ? "Pause" : "Play";
}

function setPlaybackRate() {
  audio.playbackRate = Number(playbackRate.value);
}

back.addEventListener("click", () => seekBy(-10));
forward.addEventListener("click", () => seekBy(10));
play.addEventListener("click", () => {
  if (audio.paused) {
    audio.play();
    return;
  }
  audio.pause();
});
scrub.addEventListener("input", () => {
  seekTo(Number(scrub.value));
});
playbackRate.addEventListener("change", setPlaybackRate);
bookSelect.addEventListener("change", async () => {
  if (!catalog) {
    return;
  }
  const item = catalog.books.find((entry) => entry.id === bookSelect.value);
  if (!item) {
    renderError("selected book is missing");
    return;
  }
  const url = new URL(window.location.href);
  url.searchParams.delete("manifest");
  url.searchParams.set("book", item.id);
  window.history.replaceState(null, "", url);
  currentManifest = item.manifest;
  const result = await loadJson(item.manifest);
  if (result.error) {
    renderError(result.error);
    return;
  }
  renderBook(result.value);
});
audio.addEventListener("loadedmetadata", () => {
  const maxSec = maxPlaybackSec();
  scrub.max = String(maxSec);
  duration.textContent = fmtTime(maxSec);
  if (pendingSeekSec !== null) {
    audio.currentTime = pendingSeekSec;
    pendingSeekSec = null;
  }
});
audio.addEventListener("error", () => {
  if (!book?.releaseAudio?.url || !book.audio || audio.dataset.releaseFallbackUsed === "1") {
    return;
  }
  audio.dataset.releaseFallbackUsed = "1";
  audio.src = assetPath(book.audio);
  audio.load();
});
audio.addEventListener("timeupdate", () => updateProgress(audio.currentTime));
audio.addEventListener("play", () => setPlaying(true));
audio.addEventListener("pause", () => setPlaying(false));
audio.addEventListener("ended", () => setPlaying(false));

renderBuildTag();
const result = await loadBook();
if (result.error) {
  renderError(result.error);
} else {
  renderBook(result.value);
}
