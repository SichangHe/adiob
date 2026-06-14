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
const pageTools = document.querySelector("#page-tools");
const pageSelect = document.querySelector("#page-select");
const pageStatus = document.querySelector("#page-status");
const prevPage = document.querySelector("#prev-page");
const nextPage = document.querySelector("#next-page");

const RATE_STEP = 0.1;
const MIN_RATE = 0.1;
const MAX_RATE = 2;
const DEFAULT_RATE = 1;
const SEEK_STEP_SEC = 10;
const VOLUME_STEP = 0.1;
const AUDIO_CHUNK_TOLERANCE_SEC = 0.05;

let book = null;
let catalog = null;
let activeId = "";
let activePage = null;
let activePageIndex = 0;
let pendingSeekSec = null;
let currentManifest = "";
let restoringProgress = false;
let renderSeq = 0;
let pageSeq = 0;
let pageLoad = null;
let activeAudioChunkIndex = -1;
let resumeAudioAfterLoad = false;
let speechUtterance = null;
let speechProgressTimer = null;
let speechPlaying = false;
let speechSeq = 0;

function renderBuildTag() {
  const build = buildTag?.dataset.build;
  if (!build) {
    return;
  }
  const isLocal = ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
  buildTag.textContent = `${isLocal ? "localhost" : "source"} ${build}`;
}

function rateValue(rate) {
  return rate.toFixed(1);
}

function rateLabel(rate) {
  return `${rateValue(rate).replace(/\.0$/, "")}x`;
}

function renderPlaybackRates() {
  const options = [];
  for (let rate = MIN_RATE; rate <= MAX_RATE + RATE_STEP / 2; rate += RATE_STEP) {
    const value = Number(rate.toFixed(1));
    const option = document.createElement("option");
    option.value = rateValue(value);
    option.textContent = rateLabel(value);
    option.selected = value === DEFAULT_RATE;
    options.push(option);
  }
  playbackRate.replaceChildren(...options);
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

function normalizePage(page, index) {
  if (!page || typeof page !== "object") {
    return { error: "manifest page is not an object" };
  }
  const startSec = Number(page.startSec ?? 0);
  const endSec = Number(page.endSec ?? startSec);
  if (!Number.isFinite(startSec) || !Number.isFinite(endSec) || endSec < startSec) {
    return { error: "manifest page has invalid timing" };
  }
  return {
    value: {
      id: String(page.id ?? `p${String(index + 1).padStart(3, "0")}`),
      title: String(page.title ?? `Page ${index + 1}`),
      path: typeof page.path === "string" ? page.path : "",
      startSec,
      endSec,
      count: Number(page.count ?? 0),
      segments: Array.isArray(page.segments) ? page.segments : null,
    },
  };
}

function normalizePages(nextBook) {
  if (Array.isArray(nextBook.pages) && nextBook.pages.length) {
    const pages = [];
    for (const [index, page] of nextBook.pages.entries()) {
      const result = normalizePage(page, index);
      if (result.error) {
        return result;
      }
      pages.push(result.value);
    }
    return { value: pages };
  }
  if (Array.isArray(nextBook.segmentChunks) && nextBook.segmentChunks.length) {
    const pages = [];
    for (const [index, chunk] of nextBook.segmentChunks.entries()) {
      const result = normalizePage(
        {
          ...chunk,
          title: chunk.title ?? `Page ${index + 1}`,
        },
        index,
      );
      if (result.error) {
        return result;
      }
      pages.push(result.value);
    }
    return { value: pages };
  }
  if (Array.isArray(nextBook.segments)) {
    const normalized = normalizeSegments(nextBook.segments);
    if (normalized.error) {
      return normalized;
    }
    const first = normalized.value[0];
    const last = normalized.value.at(-1);
    return {
      value: [
        {
          id: "p001",
          title: "Page 1",
          path: "",
          startSec: first.startSec,
          endSec: last.endSec,
          count: normalized.value.length,
          segments: normalized.value,
        },
      ],
    };
  }
  return { error: "manifest has no page data" };
}

function normalizeAudioChunks(rawChunks) {
  if (rawChunks === undefined) {
    return { value: [] };
  }
  if (!Array.isArray(rawChunks) || !rawChunks.length) {
    return { error: "manifest audioChunks must be a non-empty list" };
  }
  const chunks = [];
  let previousEndSec = 0;
  for (const [index, chunk] of rawChunks.entries()) {
    if (!chunk || typeof chunk !== "object") {
      return { error: "manifest audio chunk is not an object" };
    }
    const path = typeof chunk.path === "string" ? chunk.path : "";
    const startSec = Number(chunk.startSec);
    const endSec = Number(chunk.endSec);
    if (!path) {
      return { error: "manifest audio chunk is missing path" };
    }
    if (
      !Number.isFinite(startSec) ||
      !Number.isFinite(endSec) ||
      Math.abs(startSec - previousEndSec) > AUDIO_CHUNK_TOLERANCE_SEC ||
      endSec <= startSec
    ) {
      return { error: "manifest audio chunk has invalid timing" };
    }
    chunks.push({
      id: String(chunk.id ?? `chunk-${String(index + 1).padStart(3, "0")}`),
      path,
      startSec,
      endSec,
    });
    previousEndSec = endSec;
  }
  return { value: chunks };
}

async function loadPageSegments(page, manifestPath) {
  if (Array.isArray(page.segments)) {
    return normalizeSegments(page.segments);
  }
  if (!page.path) {
    return { error: "page is missing a segment path" };
  }
  const result = await loadJson(assetPath(page.path, manifestPath));
  if (result.error) {
    return result;
  }
  const rawSegments = Array.isArray(result.value)
    ? result.value
    : result.value?.segments;
  return normalizeSegments(rawSegments);
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
  license.hidden = true;
  pageTools.hidden = true;
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
  stopSpeech(false);
  audio.pause();
  setPlaying(false);
  const renderId = ++renderSeq;
  pageSeq += 1;
  pageLoad = null;
  const pageResult = normalizePages(nextBook);
  const chunkResult = normalizeAudioChunks(nextBook.audioChunks);
  if (renderId !== renderSeq || manifestPath !== currentManifest) {
    return;
  }
  if (pageResult.error) {
    renderError(pageResult.error);
    return;
  }
  const audioChunks = chunkResult.error ? [] : chunkResult.value;
  const manifestDurationSec = Number(nextBook.durationSec);
  const inferredDurationSec =
    audioChunks.at(-1)?.endSec ?? pageResult.value.at(-1)?.endSec ?? 0;
  book = {
    ...nextBook,
    pages: pageResult.value,
    audioChunks,
    durationSec: Number.isFinite(manifestDurationSec)
      ? manifestDurationSec
      : inferredDurationSec,
  };
  activePage = null;
  activePageIndex = 0;
  activeAudioChunkIndex = -1;
  resumeAudioAfterLoad = false;
  const restoredSec = Math.min(savedProgressSec(book), Number(book.durationSec) || 0);
  restoringProgress = restoredSec > 0;
  pendingSeekSec = restoredSec > 0 ? restoredSec : null;
  document.title = `${book.title} | adiob`;
  title.textContent = book.title;
  author.textContent = book.author;
  const bookLicense = String(book.license ?? "").trim();
  license.textContent = bookLicense;
  license.hidden = !bookLicense;
  cover.src = assetPath(book.cover, manifestPath);
  cover.alt = `${book.title} cover`;
  audio.dataset.releaseFallbackUsed = "0";
  if (hasChunkedAudio()) {
    loadAudioChunk(audioChunkIndexAt(restoredSec));
  } else if (hasAudio()) {
    audio.src = assetPath(book.releaseAudio?.url || book.audio, manifestPath);
    audio.load();
  } else {
    audio.removeAttribute("src");
    audio.load();
  }
  setPlaybackRate();
  renderReleaseAudio(book.releaseAudio, manifestPath);
  scrub.max = String(book.durationSec);
  duration.textContent = fmtTime(book.durationSec);
  activeId = "";
  renderPageSelect();
  setAudioControls();
  await switchPage(pageIndexAt(restoredSec), { updateSec: restoredSec });
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

function hasChunkedAudio() {
  return Boolean(book?.audioChunks?.length);
}

function hasFallbackAudio() {
  return Boolean(book && (book.releaseAudio?.url || book.audio));
}

function hasAudio() {
  return Boolean(book && (hasChunkedAudio() || hasFallbackAudio()));
}

function hasSpeech() {
  return Boolean(book && !hasAudio() && "speechSynthesis" in window);
}

function canPlay() {
  return hasAudio() || hasSpeech();
}

function setAudioControls() {
  const disabled = !canPlay();
  play.disabled = disabled;
  if (disabled) {
    setPlaying(false);
  }
}

function audioChunkIndexAt(valueSec) {
  const chunks = book?.audioChunks ?? [];
  if (!chunks.length) {
    return -1;
  }
  const index = chunks.findIndex(
    (chunk) =>
      valueSec >= chunk.startSec - AUDIO_CHUNK_TOLERANCE_SEC &&
      valueSec < chunk.endSec + AUDIO_CHUNK_TOLERANCE_SEC,
  );
  if (index >= 0) {
    return index;
  }
  return valueSec >= chunks.at(-1).endSec ? chunks.length - 1 : 0;
}

function activeAudioChunk() {
  if (!hasChunkedAudio() || activeAudioChunkIndex < 0) {
    return null;
  }
  return book.audioChunks[activeAudioChunkIndex] ?? null;
}

function audioGlobalTime() {
  const chunk = activeAudioChunk();
  if (chunk) {
    return Math.min(chunk.endSec, chunk.startSec + audio.currentTime);
  }
  return audio.currentTime;
}

function loadAudioChunk(index, playAfterLoad = false) {
  const chunks = book?.audioChunks ?? [];
  if (!chunks.length) {
    return false;
  }
  const nextIndex = Math.min(Math.max(0, index), chunks.length - 1);
  const chunk = chunks[nextIndex];
  const nextSrc = assetPath(chunk.path);
  resumeAudioAfterLoad = playAfterLoad;
  if (activeAudioChunkIndex === nextIndex && audio.getAttribute("src") === nextSrc) {
    if (playAfterLoad && audio.readyState >= 1) {
      void audio.play();
    }
    return true;
  }
  activeAudioChunkIndex = nextIndex;
  audio.src = nextSrc;
  audio.load();
  return true;
}

function applyPendingAudioSeek(maxSec = maxPlaybackSec()) {
  if (pendingSeekSec === null || !hasAudio() || audio.readyState < 1) {
    return false;
  }
  const targetSec = Math.min(Math.max(0, pendingSeekSec), maxSec);
  const chunk = activeAudioChunk();
  if (chunk && (targetSec < chunk.startSec || targetSec > chunk.endSec)) {
    return false;
  }
  const targetAudioSec = chunk ? targetSec - chunk.startSec : targetSec;
  try {
    audio.currentTime = Math.max(0, targetAudioSec);
    pendingSeekSec = null;
    restoringProgress = false;
    return true;
  } catch {
    return false;
  }
}

function currentPlaybackSec() {
  const scrubSec = Number(scrub.value);
  if (pendingSeekSec !== null) {
    return pendingSeekSec;
  }
  if (hasAudio() && audio.readyState >= 1 && Number.isFinite(audio.currentTime)) {
    return audioGlobalTime();
  }
  return Number.isFinite(scrubSec) ? scrubSec : 0;
}

function fallbackFromChunkError() {
  if (!hasChunkedAudio()) {
    return false;
  }
  const targetSec = currentPlaybackSec();
  book.audioChunks = [];
  activeAudioChunkIndex = -1;
  resumeAudioAfterLoad = false;
  pendingSeekSec = targetSec;
  if (hasFallbackAudio()) {
    audio.src = assetPath(book.releaseAudio?.url || book.audio);
    audio.load();
  } else {
    audio.removeAttribute("src");
    audio.load();
    pendingSeekSec = null;
    updateProgress(targetSec);
  }
  setPlaying(false);
  setAudioControls();
  return true;
}

function stopSpeech(updateButton = true) {
  speechSeq += 1;
  speechPlaying = false;
  if (speechProgressTimer !== null) {
    window.clearInterval(speechProgressTimer);
    speechProgressTimer = null;
  }
  if ("speechSynthesis" in window) {
    window.speechSynthesis.cancel();
  }
  speechUtterance = null;
  if (updateButton) {
    setPlaying(false);
  }
}

function runSpeechProgress(segment) {
  const startMs = window.performance.now();
  const startSec = segment.startSec;
  const durationSec = Math.max(0.25, segment.endSec - segment.startSec);
  speechProgressTimer = window.setInterval(() => {
    const elapsedSec = (window.performance.now() - startMs) / 1000;
    const fraction = Math.min(1, elapsedSec / durationSec);
    const valueSec = startSec + durationSec * fraction;
    updateProgress(valueSec);
    saveProgressSec(valueSec);
  }, 250);
}

async function playSpeechFrom(valueSec) {
  if (!hasSpeech()) {
    return;
  }
  stopSpeech(false);
  const nextSpeechSeq = speechSeq;
  const switched = await ensurePageForTime(valueSec);
  if (!switched || nextSpeechSeq !== speechSeq || !hasSpeech()) {
    return;
  }
  const segment = segmentAt(valueSec) ?? activePage?.segments?.[0];
  if (!segment) {
    setPlaying(false);
    return;
  }
  const pageIndex = activePageIndex;
  const segmentIndex = activePage.segments.findIndex((item) => item.id === segment.id);
  speechPlaying = true;
  setPlaying(true);
  updateProgress(segment.startSec);
  saveProgressSec(segment.startSec, true);
  speechUtterance = new SpeechSynthesisUtterance(segment.text);
  speechUtterance.rate = Number(playbackRate.value);
  speechUtterance.onend = () => {
    if (!speechPlaying || nextSpeechSeq !== speechSeq) {
      return;
    }
    void playNextSpeechSegment(pageIndex, segmentIndex, nextSpeechSeq);
  };
  speechUtterance.onerror = () => {
    if (nextSpeechSeq === speechSeq) {
      stopSpeech(true);
    }
  };
  runSpeechProgress(segment);
  window.speechSynthesis.speak(speechUtterance);
}

async function playNextSpeechSegment(pageIndex, segmentIndex, currentSpeechSeq) {
  if (currentSpeechSeq !== speechSeq) {
    return;
  }
  if (!book?.pages?.[pageIndex] || segmentIndex < 0) {
    stopSpeech(true);
    return;
  }
  if (pageIndex !== activePageIndex) {
    const switched = await switchPage(pageIndex, { updateSec: book.pages[pageIndex].startSec });
    if (!switched || currentSpeechSeq !== speechSeq) {
      return;
    }
  }
  const pageSegments = activePage?.segments ?? [];
  if (segmentIndex < pageSegments.length - 1) {
    await playSpeechFrom(pageSegments[segmentIndex + 1].startSec);
    return;
  }
  if (pageIndex < book.pages.length - 1) {
    const nextIndex = pageIndex + 1;
    const switched = await switchPage(nextIndex, { updateSec: book.pages[nextIndex].startSec });
    if (!switched || currentSpeechSeq !== speechSeq) {
      return;
    }
    await playSpeechFrom(book.pages[nextIndex].startSec);
    return;
  }
  stopSpeech(true);
}

function renderPageSelect() {
  const pages = book?.pages ?? [];
  pageSelect.replaceChildren(
    ...pages.map((page, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = `${index + 1}. ${page.title}`;
      return option;
    }),
  );
  pageTools.hidden = pages.length <= 1;
}

function updatePageControls() {
  const pages = book?.pages ?? [];
  pageSelect.value = String(activePageIndex);
  pageStatus.textContent = `${activePageIndex + 1} / ${pages.length}`;
  prevPage.disabled = activePageIndex <= 0;
  nextPage.disabled = activePageIndex >= pages.length - 1;
}

function pageIndexAt(valueSec) {
  const pages = book?.pages ?? [];
  if (!pages.length) {
    return 0;
  }
  const index = pages.findIndex(
    (page) => valueSec >= page.startSec && valueSec < page.endSec,
  );
  if (index >= 0) {
    return index;
  }
  return valueSec >= pages.at(-1).endSec ? pages.length - 1 : 0;
}

async function switchPage(index, options = {}) {
  if (!book?.pages?.length) {
    return false;
  }
  const pages = book.pages;
  const manifestPath = currentManifest;
  const nextIndex = Math.min(Math.max(0, index), pages.length - 1);
  if (pageLoad?.index === nextIndex && pageLoad.manifestPath === manifestPath) {
    return pageLoad.promise;
  }
  const page = pages[nextIndex];
  const nextSeq = ++pageSeq;
  const promise = (async () => {
    const result = await loadPageSegments(page, manifestPath);
    if (nextSeq !== pageSeq || manifestPath !== currentManifest || pages !== book?.pages) {
      return false;
    }
    if (result.error) {
      renderError(result.error);
      return false;
    }
    activePageIndex = nextIndex;
    activePage = { ...page, segments: result.value };
    segments.replaceChildren(...activePage.segments.map(renderSegment));
    activeId = "";
    updatePageControls();
    updateProgress(options.updateSec ?? Math.max(page.startSec, Number(scrub.value) || 0));
    return true;
  })();
  pageLoad = { index: nextIndex, manifestPath, promise };
  try {
    return await promise;
  } finally {
    if (pageLoad?.promise === promise) {
      pageLoad = null;
    }
  }
}

async function ensurePageForTime(valueSec) {
  const nextIndex = pageIndexAt(valueSec);
  if (nextIndex !== activePageIndex) {
    return switchPage(nextIndex, { updateSec: valueSec });
  }
  return true;
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
  button.addEventListener("click", (event) => {
    const targetSec = segmentClickSec(event, segment, text);
    seekTo(targetSec);
    if (hasAudio()) {
      void audio.play();
      return;
    }
    if (hasSpeech()) {
      void playSpeechFrom(targetSec);
    }
  });
  return button;
}

function caretOffsetFromPoint(x, y, root) {
  let node = null;
  let offset = 0;
  if (document.caretPositionFromPoint) {
    const position = document.caretPositionFromPoint(x, y);
    node = position?.offsetNode ?? null;
    offset = position?.offset ?? 0;
  } else if (document.caretRangeFromPoint) {
    const range = document.caretRangeFromPoint(x, y);
    node = range?.startContainer ?? null;
    offset = range?.startOffset ?? 0;
  }
  if (!node || !root.contains(node)) {
    return null;
  }
  const range = document.createRange();
  range.selectNodeContents(root);
  range.setEnd(node, offset);
  return range.toString().length;
}

function segmentClickSec(event, segment, text) {
  const length = text.textContent.length;
  const durationSec = segment.endSec - segment.startSec;
  if (length <= 0 || durationSec <= 0) {
    return segment.startSec;
  }
  const offset = caretOffsetFromPoint(event.clientX, event.clientY, text);
  if (offset === null) {
    return segment.startSec;
  }
  const fraction = Math.min(1, Math.max(0, offset / length));
  return segment.startSec + durationSec * fraction;
}

function fmtTime(valueSec) {
  const sec = Math.max(0, Math.floor(valueSec));
  const min = Math.floor(sec / 60);
  return `${min}:${String(sec % 60).padStart(2, "0")}`;
}

function segmentAt(valueSec) {
  if (!activePage?.segments?.length) {
    return null;
  }
  return (
    activePage.segments.find(
      (segment) => valueSec >= segment.startSec && valueSec < segment.endSec,
    ) ?? activePage.segments.at(-1)
  );
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
  if (segments.scrollHeight > segments.clientHeight + 1) {
    active?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

function maxPlaybackSec() {
  if (hasChunkedAudio()) {
    return Number.isFinite(book?.durationSec)
      ? book.durationSec
      : book.audioChunks.at(-1).endSec;
  }
  if (hasAudio() && Number.isFinite(audio.duration) && audio.duration > 0) {
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
  const restartSpeech = !hasAudio() && speechPlaying;
  if (restartSpeech) {
    stopSpeech(false);
  }
  pendingSeekSec = targetSec;
  void ensurePageForTime(targetSec);
  if (hasChunkedAudio()) {
    const chunkIndex = audioChunkIndexAt(targetSec);
    const wasPlaying = !audio.paused;
    if (chunkIndex !== activeAudioChunkIndex) {
      loadAudioChunk(chunkIndex, wasPlaying);
    }
    if (audio.readyState >= 1) {
      applyPendingAudioSeek();
    }
  } else if (hasAudio() && audio.readyState >= 1) {
    try {
      audio.currentTime = targetSec;
      if (!isRestoring || targetSec <= 0) {
        pendingSeekSec = null;
        restoringProgress = false;
      }
    } catch {
      pendingSeekSec = targetSec;
    }
  } else if (!hasAudio()) {
    pendingSeekSec = null;
    restoringProgress = false;
  }
  updateProgress(targetSec);
  saveProgressSec(targetSec, true);
  if (restartSpeech) {
    void playSpeechFrom(targetSec);
  }
}

function seekBy(deltaSec) {
  seekTo(currentPlaybackSec() + deltaSec);
}

function setPlaying(isPlaying) {
  play.textContent = isPlaying ? "Pause" : "Play";
}

function setPlaybackRate() {
  const rate = Number(playbackRate.value) || DEFAULT_RATE;
  audio.playbackRate = rate;
  if (speechPlaying) {
    void playSpeechFrom(Number(scrub.value) || activePage?.startSec || 0);
  }
}

function adjustPlaybackRate(delta) {
  const currentRate = Number(playbackRate.value) || DEFAULT_RATE;
  const nextRate = Math.min(
    MAX_RATE,
    Math.max(MIN_RATE, Number((currentRate + delta).toFixed(1))),
  );
  playbackRate.value = rateValue(nextRate);
  setPlaybackRate();
}

function adjustVolume(delta) {
  const nextVolume = Math.min(1, Math.max(0, audio.volume + delta));
  audio.volume = Number(nextVolume.toFixed(2));
  if (audio.volume > 0) {
    audio.muted = false;
  }
}

function togglePlayback() {
  if (!canPlay()) {
    return;
  }
  if (hasSpeech()) {
    if (speechPlaying) {
      stopSpeech(true);
      return;
    }
    void playSpeechFrom(Number(scrub.value) || activePage?.startSec || 0);
    return;
  }
  if (audio.paused) {
    audio.play();
    return;
  }
  audio.pause();
}

function shouldHandleKeyboard(event) {
  if (event.altKey || event.ctrlKey || event.metaKey) {
    return false;
  }
  const target = event.target;
  if (!(target instanceof Element)) {
    return true;
  }
  return !target.closest(
    "button, a[href], input, select, textarea, [contenteditable='true'], [role='button'], [role='link']",
  );
}

back.addEventListener("click", () => seekBy(-SEEK_STEP_SEC));
forward.addEventListener("click", () => seekBy(SEEK_STEP_SEC));
play.addEventListener("click", togglePlayback);
document.addEventListener("keydown", (event) => {
  if (!shouldHandleKeyboard(event)) {
    return;
  }
  if (event.key === " ") {
    event.preventDefault();
    togglePlayback();
    return;
  }
  if (event.key === "ArrowLeft") {
    event.preventDefault();
    seekBy(-SEEK_STEP_SEC);
    return;
  }
  if (event.key === "ArrowRight") {
    event.preventDefault();
    seekBy(SEEK_STEP_SEC);
    return;
  }
  if (event.key === "ArrowUp") {
    event.preventDefault();
    adjustVolume(VOLUME_STEP);
    return;
  }
  if (event.key === "ArrowDown") {
    event.preventDefault();
    adjustVolume(-VOLUME_STEP);
    return;
  }
  if (event.key === "<") {
    event.preventDefault();
    adjustPlaybackRate(-RATE_STEP);
    return;
  }
  if (event.key === ">") {
    event.preventDefault();
    adjustPlaybackRate(RATE_STEP);
  }
});
scrub.addEventListener("input", () => {
  seekTo(Number(scrub.value));
});
scrub.addEventListener("change", () => {
  seekTo(Number(scrub.value));
});
playbackRate.addEventListener("change", setPlaybackRate);
pageSelect.addEventListener("change", async () => {
  const index = Number(pageSelect.value);
  if (!Number.isFinite(index) || !book?.pages?.[index]) {
    return;
  }
  const nextBook = book;
  const page = nextBook.pages[index];
  const switched = await switchPage(index, { updateSec: page.startSec });
  if (switched && book === nextBook) {
    seekTo(page.startSec);
  }
});
prevPage.addEventListener("click", async () => {
  if (!book?.pages?.length) {
    return;
  }
  const nextBook = book;
  const index = Math.max(0, activePageIndex - 1);
  const page = nextBook.pages[index];
  const switched = await switchPage(index, { updateSec: page.startSec });
  if (switched && book === nextBook) {
    seekTo(page.startSec);
  }
});
nextPage.addEventListener("click", async () => {
  if (!book?.pages?.length) {
    return;
  }
  const nextBook = book;
  const index = Math.min(nextBook.pages.length - 1, activePageIndex + 1);
  const page = nextBook.pages[index];
  const switched = await switchPage(index, { updateSec: page.startSec });
  if (switched && book === nextBook) {
    seekTo(page.startSec);
  }
});
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
    if (hasChunkedAudio()) {
      applyPendingAudioSeek(maxSec);
    } else {
      const targetSec = Math.min(Math.max(0, pendingSeekSec), maxSec);
      seekTo(targetSec);
    }
  }
  if (resumeAudioAfterLoad) {
    resumeAudioAfterLoad = false;
    void audio.play();
  }
});
audio.addEventListener("error", () => {
  if (fallbackFromChunkError()) {
    return;
  }
  if (!book?.releaseAudio?.url || !book.audio || audio.dataset.releaseFallbackUsed === "1") {
    return;
  }
  audio.dataset.releaseFallbackUsed = "1";
  audio.src = assetPath(book.audio);
  audio.load();
});
audio.addEventListener("timeupdate", () => {
  const valueSec = audioGlobalTime();
  void ensurePageForTime(valueSec);
  updateProgress(valueSec);
  if (restoringProgress && valueSec > 0) {
    restoringProgress = false;
    pendingSeekSec = null;
  }
  saveProgressSec(valueSec);
});
audio.addEventListener("play", () => setPlaying(true));
audio.addEventListener("pause", () => setPlaying(false));
audio.addEventListener("ended", async () => {
  if (hasChunkedAudio()) {
    const nextIndex = activeAudioChunkIndex + 1;
    if (nextIndex >= book.audioChunks.length) {
      setPlaying(false);
      return;
    }
    const nextBook = book;
    const targetSec = nextBook.audioChunks[nextIndex].startSec;
    pendingSeekSec = targetSec;
    await ensurePageForTime(targetSec);
    if (book !== nextBook) {
      return;
    }
    loadAudioChunk(nextIndex, true);
    updateProgress(targetSec);
    saveProgressSec(targetSec, true);
    return;
  }
  if (!book?.pages?.length || activePageIndex >= book.pages.length - 1) {
    setPlaying(false);
    return;
  }
  const nextBook = book;
  const nextIndex = activePageIndex + 1;
  const page = nextBook.pages[nextIndex];
  const switched = await switchPage(nextIndex, { updateSec: page.startSec });
  if (!switched || book !== nextBook) {
    return;
  }
  seekTo(page.startSec);
  if (hasAudio() && book === nextBook) {
    void audio.play();
  }
});

renderPlaybackRates();
renderBuildTag();
const result = await loadBook();
if (result.error) {
  renderError(result.error);
} else {
  await renderBook(result.value, currentManifest);
}
