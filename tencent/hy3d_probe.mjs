// Probe Tencent TokenHub before wiring it into GOLEM: is the key accepted, and what does a
// hy-3d-component call return? The key comes from the secrets file and is never printed.
//
//   node tencent/hy3d_probe.mjs check
//       Free. GET /v1/models: is the key accepted in this region, and which hy-3d models are listed.
//   node tencent/hy3d_probe.mjs submit <url> <body.json> --yes
//       COSTS CREDITS (hy-3d-component: 30 credits per call; the free trial has 100).
//       POSTs body.json to <url>, both copied from step 2 of the service page's Access Examples.
//   node tencent/hy3d_probe.mjs query <url> <body.json>
//       POSTs a task lookup (e.g. the task ID from submit) to <url>.
//
// <url> is the full https:// address (a /v1/... path also works, except in Git Bash, which
// rewrites it). The key is only ever sent to TokenHub hosts.
//
// The full response is saved to ~/.golem/probe_out/ (outside the repo, since result links may be
// signed). The console shows only the status, the time taken and the response's shape.
//
// Key: TENCENT_MAAS_API_KEY in ~/.golem/secrets.env (override the file with GOLEM_SECRETS; a real
// environment variable wins). Region: GOLEM_TOKENHUB_BASE, default Singapore.

import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const BASE = (process.env.GOLEM_TOKENHUB_BASE || "https://tokenhub-intl.tencentcloudmaas.com").replace(/\/+$/, "");
const SECRETS = process.env.GOLEM_SECRETS || join(homedir(), ".golem", "secrets.env");
const OUT = join(homedir(), ".golem", "probe_out");

function fail(message) {
  console.error(`[hy3d] ${message}`);
  process.exit(1);
}

function apiKey() {
  let value = process.env.TENCENT_MAAS_API_KEY || "";
  if (!value && existsSync(SECRETS)) {
    for (const raw of readFileSync(SECRETS, "utf8").split(/\r?\n/)) {
      const line = raw.trim();
      if (line.startsWith("TENCENT_MAAS_API_KEY=")) value = line.slice(line.indexOf("=") + 1).trim().replace(/^["']|["']$/g, "");
    }
  }
  if (!value) fail(`TENCENT_MAAS_API_KEY is empty. Paste it after 'TENCENT_MAAS_API_KEY=' in ${SECRETS}.`);
  return value;
}

// Strings become their type and length, and links lose their query string, so nothing signed
// or long reaches the console.
function shape(value, depth = 0) {
  if (depth > 6) return "...";
  if (Array.isArray(value)) return value.length ? [`array(${value.length}) of:`, shape(value[0], depth + 1)] : "array(0)";
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, shape(v, depth + 1)]));
  }
  if (typeof value === "string") {
    if (/^https?:\/\//.test(value)) {
      const url = new URL(value);
      return `url ${url.host}${url.pathname}${url.search ? " (+query string)" : ""}`;
    }
    return value.length <= 60 ? value : `string(${value.length})`;
  }
  return value;
}

async function call(mode, method, path, body) {
  const key = apiKey();
  if (body !== undefined && body.includes(key)) fail("the body file contains the API key; remove it (the key goes in the header).");
  if (/^[A-Za-z]:[\\/]/.test(path)) fail(`the shell turned the path into a file path (${path}); pass the full https:// URL instead.`);
  const url = /^https?:\/\//.test(path) ? path : BASE + (path.startsWith("/") ? path : `/${path}`);
  const host = new URL(url);
  if (host.origin !== new URL(BASE).origin && !/\.tencentcloudmaas\.(com|tech)$/.test(host.hostname)) {
    fail(`refusing to send the key to ${host.origin}: only TokenHub hosts (*.tencentcloudmaas.com/.tech) get it.`);
  }
  const started = performance.now();
  let response;
  try {
    response = await fetch(url, {
      method,
      headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
      body,
      signal: AbortSignal.timeout(120_000),
    });
  } catch (error) {
    fail(`${method} ${url} failed after ${Math.round(performance.now() - started)} ms: ${error.cause?.code || error.name}`);
  }
  const ms = Math.round(performance.now() - started);
  const text = await response.text();
  mkdirSync(OUT, { recursive: true });
  const saved = join(OUT, `${new Date().toISOString().replace(/[:.]/g, "-")}_${mode}.json`);
  writeFileSync(saved, JSON.stringify({ mode, method, url, status: response.status, ms, body: text }, null, 2));

  console.log(`[hy3d] ${method} ${url}`);
  console.log(`[hy3d] HTTP ${response.status} in ${ms} ms; full response saved to ${saved}`);
  let json;
  try {
    json = JSON.parse(text);
  } catch {
    console.log(`[hy3d] not JSON (${text.length} chars)`);
    return null;
  }
  return json;
}

const [mode, path, bodyFile] = process.argv.slice(2);
if (mode === "check") {
  const json = await call("check", "GET", "/v1/models");
  const ids = (json?.data || []).map((m) => m.id).filter(Boolean);
  if (!ids.length) {
    console.log(JSON.stringify(shape(json), null, 2));
  } else {
    const hy3d = ids.filter((id) => id.startsWith("hy-3d"));
    console.log(`[hy3d] ${ids.length} models listed; hy-3d: ${hy3d.length ? hy3d.join(", ") : "none"}`);
  }
} else if (mode === "submit" || mode === "query") {
  if (!path || !bodyFile) fail(`usage: node tencent/hy3d_probe.mjs ${mode} <url> <body.json>${mode === "submit" ? " --yes" : ""}`);
  if (mode === "submit" && !process.argv.includes("--yes")) {
    fail("submit spends credits (hy-3d-component: 30 per call, 100 free). Add --yes to send it.");
  }
  const body = readFileSync(bodyFile, "utf8");
  JSON.parse(body); // stop on a malformed body before spending anything
  const json = await call(mode, "POST", path, body);
  if (json !== null) console.log(JSON.stringify(shape(json), null, 2));
} else {
  fail("usage: node tencent/hy3d_probe.mjs check | submit <url> <body.json> --yes | query <url> <body.json>");
}
