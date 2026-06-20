#!/usr/bin/env node
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../src/app.js", import.meta.url), "utf8");

function requirePattern(pattern, message) {
  if (!pattern.test(source)) {
    throw new Error(message);
  }
}

function forbidPattern(pattern, message) {
  if (pattern.test(source)) {
    throw new Error(message);
  }
}

requirePattern(
  /function hasSpeech\(\) \{[\s\S]*canUseSpeechFallback\(\) &&\s*!hasDeclaredAudio\(\),[\s\S]*\}/,
  "speech fallback must be unavailable while generated audio is declared",
);
requirePattern(
  /declaredAudio: Boolean\([\s\S]*audioChunks\.length[\s\S]*nextBook\.releaseAudio\?\.url[\s\S]*nextBook\.audio[\s\S]*\)/,
  "declared generated-audio state must survive playback fallback mutation",
);
requirePattern(
  /function hasDeclaredAudio\(\) \{\s*return Boolean\(book\?\.declaredAudio\);\s*\}/,
  "declared generated-audio state must not depend on mutable audio chunks",
);
requirePattern(
  /function hasAudio\(\) \{\s*return Boolean\(!audioUnavailable && \(hasChunkedAudio\(\) \|\| hasFallbackAudio\(\)\)\);\s*\}/,
  "playable generated-audio state must follow mutable loaded audio sources",
);
requirePattern(
  /\["\.m4a", "audio\/mp4"\]/,
  "m4a generated audio must advertise the Safari-compatible audio/mp4 media type",
);
requirePattern(
  /function prefersSpeechPlayback\(\) \{\s*return hasSpeech\(\);\s*\}/,
  "speech preference must follow the no-audio fallback gate",
);
requirePattern(
  /function loadAudioSource\(path\) \{[\s\S]*document\.createElement\("source"\)[\s\S]*source\.type = type;[\s\S]*source\.addEventListener\("error",[\s\S]*handleAudioError\(\);[\s\S]*audio\.replaceChildren\(source\);[\s\S]*audio\.load\(\);[\s\S]*\}/,
  "generated audio must load through a typed source element",
);
requirePattern(
  /function handleAudioError\(\) \{[\s\S]*fallbackFromChunkError\(\)[\s\S]*disableBrokenAudioFallback\(\)[\s\S]*loadAudioSource\(assetPath\(book\.audio\)\);[\s\S]*\}/,
  "source errors must reach generated-audio fallback handling",
);
requirePattern(
  /function loadAudioChunk\(index, playAfterLoad = false\) \{[\s\S]*loadAudioSource\(nextSrc\);[\s\S]*\}/,
  "audio chunks must use the typed generated-audio path",
);
requirePattern(
  /function togglePlayback\(\) \{[\s\S]*if \(hasAudio\(\)\) \{[\s\S]*void audio\.play\(\);[\s\S]*return;[\s\S]*\}[\s\S]*if \(hasSpeech\(\)\) \{/,
  "play button must try generated audio before browser speech fallback",
);
forbidPattern(
  /systemSpeechSelected|SYSTEM_SPEECH_PARAMS/,
  "URL-selected system speech must not bypass generated audio",
);
forbidPattern(
  /audio\.src\s*=/,
  "generated audio source assignment must keep explicit media type metadata",
);
