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

let book = null;
let activeId = "";
let pendingSeekSec = null;

async function loadJson(path) {
  const response = await fetch(path);
  if (!response.ok) {
    return { error: `could not load ${path}` };
  }
  return { value: await response.json() };
}

async function loadBook() {
  const indexResult = await loadJson("data/books.json");
  if (indexResult.error) {
    return indexResult;
  }
  const item = indexResult.value.books.find((entry) => entry.id === indexResult.value.defaultBook);
  if (!item) {
    return { error: "default book is missing" };
  }
  return loadJson(item.manifest);
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
  cover.src = book.cover;
  cover.alt = `${book.title} cover`;
  audio.dataset.releaseFallbackUsed = "0";
  audio.src = book.releaseAudio?.url || book.audio;
  setPlaybackRate();
  renderReleaseAudio(book.releaseAudio);
  scrub.max = String(book.durationSec);
  duration.textContent = fmtTime(book.durationSec);
  segments.replaceChildren(...book.segments.map(renderSegment));
  updateProgress(0);
}

function renderReleaseAudio(track) {
  if (!track?.url) {
    releaseAudio.hidden = true;
    releaseAudio.removeAttribute("href");
    return;
  }
  releaseAudio.hidden = false;
  releaseAudio.href = track.url;
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
    audio.currentTime = segment.startSec;
    audio.play();
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
  const currentSec = audio.readyState === 0 ? Number(scrub.value) : audio.currentTime;
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
  audio.src = book.audio;
  audio.load();
});
audio.addEventListener("timeupdate", () => updateProgress(audio.currentTime));
audio.addEventListener("play", () => setPlaying(true));
audio.addEventListener("pause", () => setPlaying(false));
audio.addEventListener("ended", () => setPlaying(false));

const result = await loadBook();
if (result.error) {
  renderError(result.error);
} else {
  renderBook(result.value);
}
