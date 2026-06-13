#!/usr/bin/env node
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const defaults = {
  cdp: "http://127.0.0.1:9279",
  pageUrlSubstring: "/ebook-viewer/",
  frameSelector: "#epub-frame",
  contentSelector: "#epub-viewer-content",
  nextSelector: "button[aria-label='Next']",
  start: "current",
  maxSections: 500,
  waitMs: 1500,
  pollMs: 250,
  changeTimeoutMs: 15000,
};

class UsageError extends Error {}

class CdpClient {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.nextId = 1;
    this.pending = new Map();
    this.ws = null;
  }

  async connect() {
    this.ws = new WebSocket(this.wsUrl);
    this.ws.addEventListener("message", (event) => this.onMessage(event));
    await new Promise((resolvePromise, rejectPromise) => {
      this.ws.addEventListener("open", resolvePromise, { once: true });
      this.ws.addEventListener("error", rejectPromise, { once: true });
    });
    await this.call("Runtime.enable");
  }

  onMessage(event) {
    const message = JSON.parse(event.data);
    const pending = this.pending.get(message.id);
    if (!pending) {
      return;
    }
    this.pending.delete(message.id);
    clearTimeout(pending.timer);
    if (message.error) {
      pending.reject(new Error(JSON.stringify(message.error)));
      return;
    }
    pending.resolve(message.result);
  }

  call(method, params = {}, timeoutMs = 20000) {
    const id = this.nextId;
    this.nextId += 1;
    const payload = JSON.stringify({ id, method, params });
    return new Promise((resolvePromise, rejectPromise) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        rejectPromise(new Error(`${method} timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      this.pending.set(id, { resolve: resolvePromise, reject: rejectPromise, timer });
      this.ws.send(payload);
    });
  }

  async eval(fn, args = {}) {
    const expression = `(${fn})(${JSON.stringify(args)})`;
    const result = await this.call("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    }
    return result.result.value;
  }

  close() {
    this.ws?.close();
  }
}

function usage() {
  return `usage: node scripts/extract-ebsco-ebook-text.mjs --out owned-text/book.txt [options]

options:
  --cdp URL                    chrome devtools endpoint, default ${defaults.cdp}
  --page-url-substring TEXT    page URL filter, default ${defaults.pageUrlSubstring}
  --frame-selector CSS         ebook iframe selector, default ${defaults.frameSelector}
  --content-selector CSS       readable content selector, default ${defaults.contentSelector}
  --next-selector CSS          next button selector, default ${defaults.nextSelector}
  --start current|first-toc|toc:N
                               start point, default current
  --max-sections N             stop after N sections, default ${defaults.maxSections}
  --wait-ms N                  wait after TOC or next click, default ${defaults.waitMs}
  --poll-ms N                  navigation poll interval, default ${defaults.pollMs}
  --change-timeout-ms N        next-page wait limit, default ${defaults.changeTimeoutMs}
  --help                       print this help`;
}

function repoRoot() {
  return dirname(dirname(fileURLToPath(import.meta.url)));
}

function parseArgs(argv) {
  const args = { ...defaults, out: "" };
  const names = new Map([
    ["--cdp", "cdp"],
    ["--out", "out"],
    ["--page-url-substring", "pageUrlSubstring"],
    ["--frame-selector", "frameSelector"],
    ["--content-selector", "contentSelector"],
    ["--next-selector", "nextSelector"],
    ["--start", "start"],
    ["--max-sections", "maxSections"],
    ["--wait-ms", "waitMs"],
    ["--poll-ms", "pollMs"],
    ["--change-timeout-ms", "changeTimeoutMs"],
  ]);
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (token === "--help") {
      console.log(usage());
      process.exit(0);
    }
    const eqIndex = token.indexOf("=");
    const rawName = eqIndex === -1 ? token : token.slice(0, eqIndex);
    const inlineValue = eqIndex === -1 ? undefined : token.slice(eqIndex + 1);
    const key = names.get(rawName);
    if (!key) {
      throw new UsageError(`unknown option ${token}`);
    }
    const value = inlineValue ?? argv[index + 1];
    if (!value) {
      throw new UsageError(`missing value for ${rawName}`);
    }
    if (inlineValue === undefined) {
      index += 1;
    }
    args[key] = value;
  }
  for (const key of ["maxSections", "waitMs", "pollMs", "changeTimeoutMs"]) {
    args[key] = readPositiveInt(args[key], key);
  }
  if (!args.out) {
    throw new UsageError("missing --out");
  }
  if (!/^(current|first-toc|toc:\d+)$/.test(args.start)) {
    throw new UsageError("--start must be current, first-toc, or toc:N");
  }
  return args;
}

function readPositiveInt(value, name) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new UsageError(`${name} must be a positive integer`);
  }
  return parsed;
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`GET ${url} failed with ${response.status}`);
  }
  return response.json();
}

async function findEbookPage(args) {
  const cdp = args.cdp.replace(/\/$/, "");
  const pages = await fetchJson(`${cdp}/json/list`);
  const matches = pages.filter((entry) => entry.type === "page" && entry.url.includes(args.pageUrlSubstring));
  if (matches.length === 0) {
    throw new Error(`no Chrome page URL contains ${args.pageUrlSubstring}`);
  }
  if (matches.length > 1) {
    const candidates = matches
      .map((entry, index) => `${index + 1}. ${entry.title || "(untitled)"} ${safePageUrl(entry.url)}`)
      .join("\n");
    throw new Error(
      `multiple Chrome pages match ${args.pageUrlSubstring}; rerun with a more specific --page-url-substring:\n${candidates}`,
    );
  }
  return matches[0];
}

function safePageUrl(rawUrl) {
  try {
    const url = new URL(rawUrl);
    return `${url.origin}${url.pathname}`;
  } catch {
    return rawUrl.split(/[?#]/, 1)[0];
  }
}

function readSection(args) {
  const clean = (text) =>
    (text || "")
      .replace(/\u00a0/g, " ")
      .replace(/[\u200b-\u200d\ufeff]/g, "")
      .replace(/\r\n?/g, "\n")
      .split("\n")
      .map((line) => line.replace(/\s+/g, " ").trim())
      .filter(Boolean)
      .join("\n");
  const frame = document.querySelector(args.frameSelector);
  if (!frame) {
    return { ok: false, error: `missing frame selector ${args.frameSelector}`, url: location.href };
  }
  const doc = frame.contentDocument;
  if (!doc) {
    return { ok: false, error: `frame ${args.frameSelector} is not readable`, url: location.href };
  }
  const content = doc.querySelector(args.contentSelector) || doc.querySelector("[role='main']") || doc.body;
  if (!content) {
    return { ok: false, error: `missing content selector ${args.contentSelector}`, url: location.href };
  }
  const text = clean(content.innerText || content.textContent || "");
  return {
    ok: true,
    title: doc.title || document.title,
    outerTitle: document.title,
    url: location.href,
    bodyId: doc.body?.id || "",
    text,
    textLen: text.length,
  };
}

function clickTocStart(args) {
  const parseNavPoint = (id) => {
    const match = /^ebook-toc-navPoint-(\d+)-label$/.exec(id);
    return match ? Number(match[1]) : Number.MAX_SAFE_INTEGER;
  };
  let link = null;
  if (args.navPoint) {
    link = document.querySelector(`#ebook-toc-navPoint-${args.navPoint}-label`);
  } else {
    link = [...document.querySelectorAll("[id^='ebook-toc-navPoint-'][id$='-label']")]
      .sort((left, right) => parseNavPoint(left.id) - parseNavPoint(right.id))[0];
  }
  if (!link) {
    const toc = document.querySelector("#toc, button[aria-label='Table of contents']");
    if (toc) {
      toc.click();
      return { ok: false, openedToc: true, reason: "opened-toc" };
    }
    return { ok: false, reason: args.navPoint ? `missing navPoint ${args.navPoint}` : "missing toc link" };
  }
  link.click();
  return { ok: true, target: link.id, navPoint: parseNavPoint(link.id) };
}

function clickNext(args) {
  const isDisabled = (element) =>
    element.disabled ||
    element.getAttribute("aria-disabled") === "true" ||
    element.closest("[aria-disabled='true'], [disabled]");
  const button =
    [...document.querySelectorAll(args.nextSelector)].find((entry) => !isDisabled(entry)) ||
    [...document.querySelectorAll("button,a")].find(
      (entry) => (entry.getAttribute("aria-label") || "").trim().toLowerCase() === "next" && !isDisabled(entry),
    );
  if (!button) {
    const disabledNext = [...document.querySelectorAll(args.nextSelector)].find((entry) => isDisabled(entry));
    return { ok: false, reason: disabledNext ? "disabled-next" : "missing-next" };
  }
  button.click();
  return { ok: true };
}

function digest(text) {
  return createHash("sha256").update(text).digest("hex");
}

function signature(section) {
  return `${section.url}\0${section.bodyId}\0${digest(section.text)}`;
}

function sectionToken(section) {
  const match = /\/section\/([^/?#]+)/.exec(section.url);
  return match ? match[1] : section.bodyId || "unknown";
}

async function readSectionOrThrow(client, args) {
  const section = await client.eval(readSection, args);
  if (!section.ok) {
    throw new Error(section.error);
  }
  return section;
}

async function goToStart(client, args) {
  if (args.start === "current") {
    return;
  }
  const navPoint = args.start.startsWith("toc:") ? Number(args.start.slice(4)) : null;
  let clicked = await client.eval(clickTocStart, { navPoint });
  if (!clicked.ok && clicked.openedToc) {
    await sleep(args.waitMs);
    clicked = await client.eval(clickTocStart, { navPoint });
  }
  if (!clicked.ok) {
    throw new Error(`could not select start point: ${clicked.reason}`);
  }
  const expectedToken = `navPoint-${clicked.navPoint}`;
  const landed = await waitForSectionToken(client, args, expectedToken);
  if (!landed.ok) {
    throw new Error(`TOC start ${clicked.target} did not load ${expectedToken}; current section is ${landed.token}`);
  }
}

async function waitForChangedSection(client, args, previousSignature) {
  const startedMs = Date.now();
  let lastSection = null;
  while (Date.now() - startedMs < args.changeTimeoutMs) {
    await sleep(args.pollMs);
    lastSection = await readSectionOrThrow(client, args);
    if (signature(lastSection) !== previousSignature) {
      return { changed: true, section: lastSection };
    }
  }
  return { changed: false, section: lastSection };
}

async function waitForSectionToken(client, args, expectedToken) {
  const startedMs = Date.now();
  let token = "unknown";
  while (Date.now() - startedMs < args.changeTimeoutMs) {
    await sleep(args.pollMs);
    const section = await readSectionOrThrow(client, args);
    token = sectionToken(section);
    if (token === expectedToken) {
      await sleep(args.waitMs);
      return { ok: true };
    }
  }
  return { ok: false, token };
}

function resolveOutputPath(outArg) {
  const root = repoRoot();
  const ownedRoot = join(root, "owned-text");
  const out = isAbsolute(outArg) ? resolve(outArg) : resolve(root, outArg);
  const fromOwned = relative(ownedRoot, out);
  if (fromOwned === "" || fromOwned.startsWith("..") || isAbsolute(fromOwned)) {
    throw new UsageError("--out must be inside repo-local owned-text/");
  }
  return out;
}

async function extract(client, args) {
  await goToStart(client, args);
  const sections = [];
  const seen = new Set();
  let stopReason = "max-sections";
  for (let index = 1; index <= args.maxSections; index += 1) {
    const section = await readSectionOrThrow(client, args);
    const key = signature(section);
    if (seen.has(key)) {
      stopReason = "repeated-section";
      break;
    }
    seen.add(key);
    if (section.text) {
      sections.push(section);
    }
    process.stderr.write(`section=${index} token=${sectionToken(section)} chars=${section.textLen}\n`);
    if (index === args.maxSections) {
      stopReason = "max-sections";
      break;
    }
    const next = await client.eval(clickNext, { nextSelector: args.nextSelector });
    if (!next.ok) {
      stopReason = next.reason;
      break;
    }
    const changed = await waitForChangedSection(client, args, key);
    if (!changed.changed) {
      stopReason = "next-click-no-change";
      break;
    }
    await sleep(args.waitMs);
  }
  return { sections, stopReason };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const out = resolveOutputPath(args.out);
  const page = await findEbookPage(args);
  const client = new CdpClient(page.webSocketDebuggerUrl);
  await client.connect();
  try {
    const result = await extract(client, args);
    const text = result.sections.map((section) => section.text).join("\n\n");
    await mkdir(dirname(out), { recursive: true });
    await writeFile(out, `${text.replace(/\s+$/u, "")}\n`, "utf8");
    const first = result.sections[0] || null;
    const last = result.sections.at(-1) || null;
    console.log(
      JSON.stringify(
        {
          out,
          sourceTitle: first?.outerTitle || page.title,
          startTitle: first?.title || "",
          finalTitle: last?.title || "",
          sectionCount: result.sections.length,
          textChars: text.length,
          stopReason: result.stopReason,
        },
        null,
        2,
      ),
    );
  } finally {
    client.close();
  }
}

main().catch((error) => {
  if (error instanceof UsageError) {
    console.error(error.message);
    console.error(usage());
    process.exit(2);
  }
  console.error(error.message);
  process.exit(1);
});
