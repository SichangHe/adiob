#!/usr/bin/env node
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../src/app.js", import.meta.url), "utf8");

function requirePattern(pattern, message) {
  if (!pattern.test(source)) {
    throw new Error(message);
  }
}

requirePattern(
  /function hasSpeech\(\) \{[\s\S]*canUseSpeechFallback\(\) &&\s*!hasAudio\(\),[\s\S]*\}/,
  "speech fallback must be unavailable while generated audio is available",
);
requirePattern(
  /function prefersSpeechPlayback\(\) \{\s*return hasSpeech\(\);\s*\}/,
  "speech preference must follow the no-audio fallback gate",
);
requirePattern(
  /function togglePlayback\(\) \{[\s\S]*if \(hasAudio\(\)\) \{[\s\S]*void audio\.play\(\);[\s\S]*return;[\s\S]*\}[\s\S]*if \(hasSpeech\(\)\) \{/,
  "play button must try generated audio before browser speech fallback",
);
