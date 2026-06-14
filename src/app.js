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
let restoringProgress = false;
let renderSeq = 0;

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

function assetPath(path, manifestPath = currentManifest) {
  if (/^[a-z][a-z0-9+.-]*:/i.test(path) || path.startsWith("/")) {
    return path;
  }
  if (path.startsWith("local/") || path.startsWith("media/")) {
    return rootPath(path);
  }
  if (!manifestPath) {
    return rootPath(path);
  }
  return new URL(path, new URL(manifestPath, window.location.href)).href;
}

function normalizeSegment(segment, index) {
  if (!segment || typeof segment !== "object") {
    return { error: "manifest segment is not an object" };
  }
  const startSec = Number(segment.startSec);
  const endSec = Number(segment.endSec);
  const text = String(segment.text ?? "").trim();
  if (!Number.isFinite(startSec) || !Number.isFinite(endSec) || endSec < startSec) {
    return { error: "manifest segment has invalid timing" };
  }
  if (!text) {
    return { error: "manifest segment is missing text" };
  }
  return {
    value: {
      id: String(segment.id ?? `s${String(index + 1).padStart(3, "0")}`),
      startSec,
      endSec,
      text,
    },
  };
}

function normalizeSegments(rawSegments, startIndex = 0) {
  if (!Array.isArray(rawSegments) || !rawSegments.length) {
    return { error: "manifest has no segments" };
  }
  const nextSegments = [];
  for (const [index, segment] of rawSegments.entries()) {
    const result = normalizeSegment(segment, startIndex + index);
    if (result.error) {
      return result;
    }
    nextSegments.push(result.value);
  }
  return { value: nextSegments };
}

async function loadSegments(nextBook, manifestPath) {
  if (Array.isArray(nextBook.segments)) {
    return normalizeSegments(nextBook.segments);
  }
  const chunks = nextBook.segmentChunks;
  if (!Array.isArray(chunks) || !chunks.length) {
    return { error: "manifest has no segment data" };
  }
  const nextSegments = [];
  for (const chunk of chunks) {
    const path = chunk?.path;
    if (typeof path !== "string" || !path) {
      return { error: "segment chunk is missing a path" };
    }
    const result = await loadJson(assetPath(path, manifestPath));
    if (result.error) {
      return result;
    }
    const rawSegments = Array.isArray(result.value)
      ? result.value
      : result.value?.segments;
    const normalized = normalizeSegments(rawSegments, nextSegments.length);
    if (normalized.error) {
      return normalized;
    }
    nextSegments.push(...normalized.value);
  }
  return { value: nextSegments };
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

function progressStorageKey(nextBook = book) {
  const id = nextBook?.id || currentManifest || "unknown";
  return `adiob.progress.${id}`;
}

function savedProgressSec(nextBook) {
  try {
    const value = Number(window.localStorage.getItem(progressStorageKey(nextBook)));
    return Number.isFinite(value) && value > 0 ? value : 0;
  } catch {
    return 0;
  }
}

function saveProgressSec(valueSec, explicit = false) {
  if (!book || !Number.isFinite(valueSec)) {
    return;
  }
  if (restoringProgress && valueSec <= 0 && !explicit) {
    return;
  }
  try {
    window.localStorage.setItem(progressStorageKey(), String(Math.max(0, valueSec)));
  } catch {
    return;
  }
}

async function renderBook(nextBook, manifestPath = currentManifest) {
  if (manifestPath !== currentManifest) {
    return;
  }
  const renderId = ++renderSeq;
  const segmentResult = await loadSegments(nextBook, manifestPath);
  if (renderId !== renderSeq || manifestPath !== currentManifest) {
    return;
  }
  if (segmentResult.error) {
    renderError(segmentResult.error);
    return;
  }
  book = { ...nextBook, segments: segmentResult.value };
  const restoredSec = Math.min(savedProgressSec(book), Number(book.durationSec) || 0);
  restoringProgress = restoredSec > 0;
  pendingSeekSec = restoredSec > 0 ? restoredSec : null;
  document.title = `${book.title} | adiob`;
  title.textContent = book.title;
  author.textContent = book.author;
  license.textContent = book.license;
  cover.src = assetPath(book.cover, manifestPath);
  cover.alt = `${book.title} cover`;
  audio.dataset.releaseFallbackUsed = "0";
  audio.src = assetPath(book.releaseAudio?.url || book.audio, manifestPath);
  setPlaybackRate();
  renderReleaseAudio(book.releaseAudio, manifestPath);
  scrub.max = String(book.durationSec);
  duration.textContent = fmtTime(book.durationSec);
  segments.replaceChildren(...book.segments.map(renderSegment));
  activeId = "";
  updateProgress(restoredSec);
}

function renderReleaseAudio(track, manifestPath) {
  if (!track?.url) {
    releaseAudio.hidden = true;
    releaseAudio.removeAttribute("href");
    return;
  }
  releaseAudio.hidden = false;
  releaseAudio.href = assetPath(track.url, manifestPath);
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
  if (Number.isFinite(audio.duration) && audio.duration > 0) {
    return audio.duration;
  }
  if (Number.isFinite(book?.durationSec)) {
    return book.durationSec;
  }
  return 0;
}

function seekTo(valueSec) {
  const targetSec = Math.min(Math.max(0, valueSec), maxPlaybackSec());
  const isRestoring = restoringProgress;
  pendingSeekSec = targetSec;
  if (audio.readyState >= 1) {
    try {
      audio.currentTime = targetSec;
      if (!isRestoring || targetSec <= 0) {
        pendingSeekSec = null;
        restoringProgress = false;
      }
    } catch {
      pendingSeekSec = targetSec;
    }
  }
  updateProgress(targetSec);
  saveProgressSec(targetSec, true);
}

function seekBy(deltaSec) {
  const scrubSec = Number(scrub.value);
  const currentSec =
    pendingSeekSec ??
    (audio.readyState >= 1 && Number.isFinite(audio.currentTime)
      ? audio.currentTime
      : Number.isFinite(scrubSec)
        ? scrubSec
        : 0);
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
scrub.addEventListener("change", () => {
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
  const manifestPath = item.manifest;
  currentManifest = manifestPath;
  const result = await loadJson(manifestPath);
  if (manifestPath !== currentManifest) {
    return;
  }
  if (result.error) {
    renderError(result.error);
    return;
  }
  await renderBook(result.value, manifestPath);
});
audio.addEventListener("loadedmetadata", () => {
  const maxSec = maxPlaybackSec();
  scrub.max = String(maxSec);
  duration.textContent = fmtTime(maxSec);
  if (pendingSeekSec !== null) {
    const targetSec = Math.min(Math.max(0, pendingSeekSec), maxSec);
    seekTo(targetSec);
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
audio.addEventListener("timeupdate", () => {
  updateProgress(audio.currentTime);
  if (restoringProgress && audio.currentTime > 0) {
    restoringProgress = false;
    pendingSeekSec = null;
  }
  saveProgressSec(audio.currentTime);
});
audio.addEventListener("play", () => setPlaying(true));
audio.addEventListener("pause", () => setPlaying(false));
audio.addEventListener("ended", () => setPlaying(false));

renderBuildTag();
const result = await loadBook();
if (result.error) {
  renderError(result.error);
} else {
  await renderBook(result.value, currentManifest);
}
