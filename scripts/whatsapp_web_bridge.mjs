#!/usr/bin/env node
// WhatsApp Web (Baileys) bridge for the local Lobster backend.
// Commands:
//   login    --auth <dir> --state <file>   start pairing, keep the QR in <file>, exit 0 once linked
//   status   --auth <dir>                  {"linked":bool,"me":{...}}
//   contacts --auth <dir> [--cache <f>] [--resume] [--wait ms]  dump the address book as JSON on stdout
//   profile  --auth <dir> --jid <jid>      about + profile picture (read only)
//   chats    --auth <dir> [--wait ms]      conversation list
//   messages --auth <dir> --chat <jid>     read messages (history + live)
//   groups   --auth <dir> [--wait ms]      group list
//   check    --auth <dir> --phones a,b     onWhatsApp lookups
//   send     --auth <dir> --to <jid> --text <s> [--image p | --file p] [--caption s]
//   watch    --auth <dir> [--interval s]   stay connected: push live contact/chat/message events as JSONL
//                                          on stdout and re-run the app-state delta every --interval seconds.
//                                          This is the "official client" behaviour - no re-pairing, ever.
//   logout   --auth <dir>                  remove the local link
//   selftest                               offline check of the pure helpers
// Global: --ledger <file> appends one JSON line per key node (state transitions + sync chunks) so a
// later "why did it fail" only needs that file; the backend appends its own "cmd" lines (with the
// DB counts) to the same file. The auth dir is a normal multi-file Baileys session, so it survives
// restarts: pair once, reuse forever.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createRequire } from "node:module";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");

function args() {
  const out = { _: [] };
  const list = process.argv.slice(2);
  for (let i = 0; i < list.length; i += 1) {
    const item = list[i];
    if (item.startsWith("--")) out[item.slice(2)] = list[i + 1] && !list[i + 1].startsWith("--") ? list[++i] : "1";
    else out._.push(item);
  }
  return out;
}

// ── 关键节点记账（只追加，不覆盖）────────────────────────────────────────────
let LEDGER = "";
function appendLedger(payload) {
  if (!LEDGER) return;
  try {
    fs.mkdirSync(path.dirname(LEDGER), { recursive: true });
    fs.appendFileSync(LEDGER, JSON.stringify({ at: new Date().toISOString(), source: "bridge", ...payload }) + "\n", "utf8");
  } catch (err) { /* 记账失败不影响主流程 */ }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function emit(payload) {
  process.stdout.write(JSON.stringify(payload) + "\n");
}

function resolveBaileys() {
  const candidates = [
    path.join(ROOT, "nodejs", "node_modules", "openclaw", "node_modules", "@whiskeysockets", "baileys"),
    path.join(ROOT, "nodejs", "node_modules", "@whiskeysockets", "baileys"),
  ];
  for (const dir of candidates) {
    const entry = path.join(dir, "lib", "index.js");
    if (fs.existsSync(entry)) return entry;
  }
  const req = createRequire(import.meta.url);
  for (const base of [path.join(ROOT, "nodejs"), ROOT]) {
    try {
      return req.resolve("@whiskeysockets/baileys", { paths: [base] });
    } catch (err) { /* keep looking */ }
  }
  return "";
}

async function loadBaileys() {
  const entry = resolveBaileys();
  if (!entry) throw new Error("找不到内置的 @whiskeysockets/baileys（nodejs/node_modules 里应该有）");
  const mod = await import(pathToFileURL(entry).href);
  return mod.default && mod.default.makeWASocket ? mod.default : mod;
}

function digitsFromJid(value) {
  const jid = String(value || "");
  if (!jid || jid.includes("@g.us") || jid.includes("@lid") || jid.includes("broadcast") || jid.includes("newsletter")) return "";
  const head = jid.split("@")[0].split(":")[0];
  return /^\d{6,20}$/.test(head) ? head : "";
}

// 号码 / jid 两种写法都收：8613800138000、+86 138..., 8613800138000@s.whatsapp.net
function normalizeJid(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (raw.includes("@")) return raw;
  const digits = raw.replace(/[^0-9]/g, "");
  return digits ? digits + "@s.whatsapp.net" : "";
}

function contactSample(row) {
  const out = { fields: Object.keys(row || {}).slice(0, 24) };
  for (const key of ["id", "name", "notify", "verifiedName", "phoneNumber", "lid", "imgUrl"]) {
    const value = row ? row[key] : "";
    out[key] = value === undefined || value === null ? "" : String(value).slice(0, 80);
  }
  return out;
}

function connectionCode(lastDisconnect) {
  const error = lastDisconnect && lastDisconnect.error;
  if (!error) return "";
  return String((error.output && error.output.statusCode) || error.statusCode || error.message || "").slice(0, 120);
}

function reasonText(lastDisconnect) {
  const error = lastDisconnect && lastDisconnect.error;
  return error ? String(error.message || error).slice(0, 200) : "";
}

function writeState(file, payload) {
  if (!file) return;
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(payload, null, 1), "utf8");
  } catch (err) { /* ignore */ }
}

function readCreds(dir) {
  try {
    const raw = fs.readFileSync(path.join(dir, "creds.json"), "utf8");
    const creds = JSON.parse(raw);
    return {
      linked: true,
      me: {
        id: String((creds.me && creds.me.id) || ""),
        name: String((creds.me && creds.me.name) || ""),
        phone: digitsFromJid(creds.me && creds.me.id),
      },
    };
  } catch (err) {
    return { linked: false, me: {} };
  }
}

// Baileys 自己会用 pino 往 stdout 打日志，会污染「每行一个 JSON」的输出协议；换成静默 logger。
const silentLogger = {
  level: "silent",
  trace() {}, debug() {}, info() {}, warn() {}, error() {}, fatal() {},
  child() { return silentLogger; },
};

async function openSocket(authDir, opts = {}) {
  const baileys = await loadBaileys();
  const { useMultiFileAuthState, makeWASocket, fetchLatestBaileysVersion } = baileys;
  fs.mkdirSync(authDir, { recursive: true });
  const { state, saveCreds } = await useMultiFileAuthState(authDir);
  let version;
  try {
    ({ version } = await fetchLatestBaileysVersion());
  } catch (err) { version = undefined; }
  const sock = makeWASocket({
    auth: state,
    version,
    logger: silentLogger,
    printQRInTerminal: false,
    // 必须开：通讯录是随首次 history/app-state 同步下来的（关了就永远 0 个）
    syncFullHistory: true,
    markOnlineOnConnect: false,
    shouldSyncHistoryMessage: () => true,
    browser: ["Lobster", "Chrome", "1.0"],
    ...opts.socket,
  });
  sock.ev.on("creds.update", saveCreds);
  return { sock, DisconnectReason: baileys.DisconnectReason || {}, baileys };
}

function sleepMs(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// 一条读命令 = 一个连接 + 收数据。WhatsApp 会主动踢掉连接（440 replaced / 428 / 515 重连），
// 老实现被踢就直接返回空结果（通讯录就永远 0）；这里在 deadline 内自动重连续收，
// 每个连接的开关/断开原因都进协议账本，出问题不用猜。
async function withConnection(authDir, handler, options = {}) {
  const deadline = Number(options.deadline || Date.now() + 60000);
  const retryDelayMs = Math.max(500, Number(options.retryDelayMs || 1500));
  const maxAttempts = Math.max(1, Number(options.maxAttempts || 6));
  let attempt = 0;
  let lastError = "";
  while (Date.now() < deadline && attempt < maxAttempts) {
    attempt += 1;
    let sock = null;
    let closed = "";
    try {
      ({ sock } = await openSocket(authDir));
    } catch (err) {
      lastError = String((err && err.message) || err).slice(0, 200);
      appendLedger({ event: "conn_open_failed", attempt, error: lastError });
      await sleepMs(2000);
      continue;
    }
    appendLedger({ event: "conn_open", attempt });
    sock.ev.on("connection.update", (update) => {
      if (update && update.connection === "close") {
        closed = connectionCode(update.lastDisconnect) || "closed";
        appendLedger({ event: "conn_close", attempt, code: closed, reason: reasonText(update.lastDisconnect) });
      }
    });
    let result = { done: false };
    try {
      result = (await handler(sock, { attempt, closed: () => closed, deadline })) || { done: false };
    } catch (err) {
      result = { done: false, error: String((err && err.message) || err).slice(0, 200) };
      appendLedger({ event: "conn_handler_failed", attempt, error: result.error });
    }
    try { sock.end(undefined); } catch (err) { /* ignore */ }
    if (result.done) return { ...result, attempts: attempt };
    if (String(closed) === "401") return { ...result, attempts: attempt, logged_out: true };
    if (Date.now() + retryDelayMs >= deadline) break;
    await sleepMs(retryDelayMs);
  }
  return { done: false, attempts: attempt, error: lastError };
}

async function waitForOpen(sock, timeoutMs = 20000) {
  if (sock.ws && sock.ws.readyState === 1) return true;
  return await new Promise((resolve) => {
    let settled = false;
    const finish = (value) => { if (!settled) { settled = true; clearTimeout(timer); resolve(value); } };
    const timer = setTimeout(() => finish(false), timeoutMs);
    sock.ev.on("connection.update", (update) => {
      if (update && update.connection === "open") finish(true);
      else if (update && update.connection === "close") finish(false);
    });
  });
}

// 通讯录/联系方式的变更挂在 app-state patch 上（首次 history 同步只来一次，之后不会再给）：
// 每个连接 open 之后主动 resync 一次（只读），联系人会以 contacts.upsert 下来。
async function primeSocket(sock, options = {}) {
  const ready = await waitForOpen(sock, Number(options.timeoutMs || 20000));
  appendLedger({ event: "conn_ready", ready });
  if (!ready || !sock.resyncAppState) return ready;
  try {
    const collections = ["critical_block", "critical_unblock_low", "regular", "regular_high", "regular_low"];
    appendLedger({ event: "contacts_resync_start", collections });
    await sock.resyncAppState(collections, false);
    appendLedger({ event: "contacts_resync_done" });
  } catch (err) {
    appendLedger({ event: "contacts_resync_failed", error: String((err && err.message) || err).slice(0, 200) });
  }
  return ready;
}

// ── login：状态机 + 515（restart required）自动重连 ──────────────────────────
// 扫码配对后 WhatsApp 会用 515 要求「用新凭据重连一次」；老版本收到 515 只写了个
// reconnecting 就退出，creds 里的 me 永远落不下来 —— 页面看到的「已取号 1/1」也就不是协议拿的。
async function cmdLogin(a) {
  const authDir = path.resolve(a.auth || ".");
  const stateFile = a.state ? path.resolve(a.state) : "";
  const deadline = Date.now() + Math.max(20000, Number(a.timeout || 300000));
  const baileys = await loadBaileys();
  const DR = baileys.DisconnectReason || {};
  const LOGGED_OUT = String(DR.loggedOut || 401);
  const RESTART_REQUIRED = String(DR.restartRequired || 515);
  let attempt = 0;
  let qrPrinted = false;
  let lastError = "";

  while (Date.now() < deadline) {
    attempt += 1;
    const phase = attempt === 1 ? "connecting" : "reconnecting";
    writeState(stateFile, { state: phase, qr: "", me: {}, attempt, error: lastError, at: Date.now() });
    appendLedger({ event: "web_login_state", state: phase, attempt, error: lastError });

    let sock;
    try {
      ({ sock } = await openSocket(authDir));
    } catch (err) {
      lastError = String((err && err.message) || err).slice(0, 200);
      writeState(stateFile, { state: "error", qr: "", me: {}, attempt, error: lastError, at: Date.now() });
      appendLedger({ event: "web_login_state", state: "error", attempt, error: lastError });
      await sleep(2000);
      continue;
    }

    const outcome = await new Promise((resolve) => {
      let settled = false;
      const done = (value) => { if (!settled) { settled = true; clearTimeout(timer); resolve(value); } };
      const timer = setTimeout(() => done({ kind: "timeout" }), Math.max(5000, deadline - Date.now()));
      sock.ev.on("connection.update", (update) => {
        const { connection, lastDisconnect, qr } = update || {};
        if (qr) {
          writeState(stateFile, { state: "qr", qr: String(qr), me: {}, attempt, at: Date.now() });
          if (!qrPrinted) {
            qrPrinted = true;
            appendLedger({ event: "web_login_qr", qr_len: String(qr).length });
            emit({ ok: true, state: "qr", qr_len: String(qr).length });
          }
        }
        if (connection === "open") {
          const me = readCreds(authDir).me;
          writeState(stateFile, { state: "linked", qr: "", me, attempt, error: "", at: Date.now() });
          appendLedger({ event: "web_login_state", state: "linked", attempt, me });
          emit({ ok: true, state: "linked", me, attempt });
          done({ kind: "linked" });
        } else if (connection === "close") {
          const code = connectionCode(lastDisconnect);
          appendLedger({ event: "web_login_close", attempt, code, reason: reasonText(lastDisconnect), restart_required: code === RESTART_REQUIRED });
          done({ kind: "close", code, reason: reasonText(lastDisconnect) });
        }
      });
    });

    try { sock.end(undefined); } catch (err) { /* ignore */ }
    await sleep(400);

    if (outcome.kind === "linked") process.exit(0);

    if (outcome.kind === "close") {
      if (String(outcome.code) === LOGGED_OUT) {
        writeState(stateFile, { state: "logged_out", qr: "", me: {}, error: "设备已在手机上退出登录", at: Date.now() });
        appendLedger({ event: "web_login_state", state: "logged_out", attempt, code: outcome.code });
        emit({ ok: false, state: "logged_out", error: "设备已在手机上退出登录，请在手机上重新连接设备" });
        process.exit(0);
      }
      lastError = "close/" + (outcome.code || "unknown");
      // 515 = 配对刚完成要求的「用新凭据重连」，立刻重连（creds 已经由 creds.update 落盘）
      writeState(stateFile, { state: "reconnecting", qr: "", me: {}, attempt, error: lastError, code: outcome.code, at: Date.now() });
      appendLedger({ event: "web_login_reconnect", attempt, code: outcome.code, restart_required: String(outcome.code) === RESTART_REQUIRED });
      if (String(outcome.code) !== RESTART_REQUIRED && attempt >= 8) {
        writeState(stateFile, { state: "error", qr: "", me: {}, attempt, error: lastError, at: Date.now() });
        emit({ ok: false, state: "error", error: "连接反复失败（code=" + String(outcome.code) + "），请重新扫码绑定" });
        process.exit(1);
      }
      await sleep(String(outcome.code) === RESTART_REQUIRED ? 800 : 2500);
      continue;
    }

    lastError = "timeout";
    break;
  }

  writeState(stateFile, { state: "idle", qr: "", me: {}, error: lastError, at: Date.now() });
  appendLedger({ event: "web_login_state", state: "idle", attempt, error: lastError });
  emit({ ok: false, state: "idle", error: "等待扫码超时，请重新点「扫码绑定」" });
  process.exit(0);
}

async function cmdStatus(a) {
  const authDir = path.resolve(a.auth || ".");
  const info = readCreds(authDir);
  emit({ ok: true, ...info });
  process.exit(0);
}

// ── contacts：分批 + 续传 + partial（历史同步只来一次，所以还带 app-state resync）────
async function cmdContacts(a) {
  const authDir = path.resolve(a.auth || ".");
  const cacheFile = a.cache ? path.resolve(a.cache) : "";
  const resume = ["1", "true", "yes"].indexOf(String(a.resume || "").toLowerCase()) >= 0;
  const chunkSize = Math.max(20, Math.min(Number(a.chunk || 200), 2000));
  const waitMs = Math.max(8000, Number(a.wait || 45000));
  const settleMs = Math.max(3000, Number(a.settle || 12000));
  const overallMs = Math.max(waitMs, Math.min(Number(a.overall || waitMs * 3), 240000));
  const creds = readCreds(authDir);
  if (!creds.linked) {
    emit({ ok: false, error: "还没有扫码绑定，请先在页面上扫码" });
    process.exit(0);
  }

  const collected = new Map();
  let resumedFrom = 0;
  if (resume && cacheFile) {
    try {
      const cached = JSON.parse(fs.readFileSync(cacheFile, "utf8"));
      ((cached && cached.contacts) || []).forEach((row) => {
        if (row && row.id && !collected.has(String(row.id))) { collected.set(String(row.id), row); resumedFrom += 1; }
      });
    } catch (err) { /* 第一次跑还没有缓存文件 */ }
  }

  let historyPackets = 0;
  let historyProgress = 0;
  let historyDone = false;
  let pending = 0;
  let lastEventAt = 0;
  const samples = [];

  const saveCache = () => {
    if (!cacheFile) return;
    try {
      fs.mkdirSync(path.dirname(cacheFile), { recursive: true });
      fs.writeFileSync(cacheFile, JSON.stringify({ at: Date.now(), contacts: Array.from(collected.values()) }), "utf8");
    } catch (err) { /* ignore */ }
  };
  const flushChunk = (force) => {
    if (!pending && !force) return;
    pending = 0;
    appendLedger({ event: "contacts_chunk", progress: historyProgress, received: collected.size, resumed: resumedFrom, partial: !historyDone, sample: samples.slice(0, 3) });
    saveCache();
  };
  const remember = (row) => {
    if (!row || !row.id) return;
    const jid = String(row.id);
    if (jid.includes("@g.us") || jid.includes("broadcast") || jid.includes("newsletter")) return;
    const prev = collected.get(jid);
    const phone = digitsFromJid(jid) || digitsFromJid(row.phoneNumber);
    const item = {
      id: jid,
      phone,
      name: String(row.name || "").trim(),
      notify: String(row.notify || "").trim(),
      verifiedName: String(row.verifiedName || "").trim(),
    };
    // 只覆盖有值的字段，别把 history 里带下来的号/名冲掉
    collected.set(jid, { ...(prev || {}), ...Object.fromEntries(Object.entries(item).filter(([, v]) => v)) });
    lastEventAt = Date.now();
    if (!prev) {
      pending += 1;
      if (samples.length < 3) samples.push(contactSample(row));
    }
    if (pending >= chunkSize) flushChunk(false);
  };

  const outcome = await withConnection(authDir, async (sock, ctx) => {
    sock.ev.on("contacts.upsert", (rows) => (rows || []).forEach(remember));
    sock.ev.on("contacts.update", (rows) => (rows || []).forEach(remember));
    sock.ev.on("messaging-history.set", (payload) => {
      historyPackets += 1;
      const progress = Number((payload && payload.progress) || 0);
      if (progress) historyProgress = progress;
      if (payload && (payload.isLatest === true || progress >= 100)) historyDone = true;
      ((payload && payload.contacts) || []).forEach(remember);
      lastEventAt = Date.now();
      appendLedger({ event: "contacts_history", packets: historyPackets, progress: historyProgress, contacts: ((payload && payload.contacts) || []).length, chats: ((payload && payload.chats) || []).length, is_latest: !!(payload && payload.isLatest) });
    });

    const connectedAt = Date.now();
    if (String(a.resync || "1") !== "0") {
      await primeSocket(sock);
      if (collected.size) lastEventAt = Date.now();
    }

    const windowEnd = Math.min(ctx.deadline, connectedAt + waitMs);
    while (Date.now() < windowEnd) {
      const quiet = lastEventAt ? Date.now() - lastEventAt : 0;
      if ((historyDone || collected.size) && lastEventAt && quiet >= settleMs && Date.now() - connectedAt > 6000) {
        return { done: true };
      }
      if (ctx.closed()) return { done: false };
      await sleepMs(500);
    }
    return { done: false };
  }, { deadline: Date.now() + overallMs, maxAttempts: 6 });

  flushChunk(true);
  const contacts = Array.from(collected.values());
  const partial = !historyDone;
  appendLedger({
    event: "contacts_done",
    received: contacts.length,
    with_phone: contacts.filter((row) => row.phone).length,
    partial,
    history_ok: historyPackets > 0,
    resumed_from: resumedFrom,
    attempts: outcome.attempts || 0,
    sample: samples.slice(0, 3),
  });
  emit({
    ok: true,
    count: contacts.length,
    with_phone: contacts.filter((row) => row.phone).length,
    partial,
    history_ok: historyPackets > 0,
    resumed_from: resumedFrom,
    attempts: outcome.attempts || 0,
    contacts,
    me: creds.me,
  });
  process.exit(0);
}

async function cmdProfile(a) {
  const authDir = path.resolve(a.auth || ".");
  const jid = normalizeJid(a.jid || a.phone || a.to || "");
  if (!jid) { emit({ ok: false, error: "需要 --jid <jid 或号码>" }); process.exit(0); }
  const { sock } = await openSocket(authDir);
  await waitForOpen(sock);
  const info = { about: "", set_at: "", picture: "", exists: null };
  try {
    const status = await sock.fetchStatus(jid);
    info.about = String((status && status.status) || "").slice(0, 500);
    info.set_at = String((status && status.setAt) || "").slice(0, 40);
  } catch (err) { /* 对方没设状态 */ }
  try {
    info.picture = String((await sock.profilePictureUrl(jid, "image")) || "").slice(0, 600);
  } catch (err) { /* 没头像 / 隐私设置 */ }
  try {
    const rows = await sock.onWhatsApp(jid);
    info.exists = !!(rows && rows[0] && rows[0].exists);
  } catch (err) { /* ignore */ }
  emit({ ok: true, jid, ...info });
  try { sock.end(undefined); } catch (err) { /* ignore */ }
  setTimeout(() => process.exit(0), 200);
}

async function cmdLogout(a) {
  const authDir = path.resolve(a.auth || ".");
  try { fs.rmSync(authDir, { recursive: true, force: true }); } catch (err) { /* ignore */ }
  appendLedger({ event: "web_logout", auth_dir: authDir });
  emit({ ok: true, removed: authDir });
  process.exit(0);
}

async function cmdChats(a) {
  const authDir = path.resolve(a.auth || ".");
  const waitMs = Math.max(6000, Number(a.wait || 20000));
  const { sock } = await openSocket(authDir);
  await primeSocket(sock);
  const chats = new Map();
  const remember = (row) => {
    if (!row || !row.id) return;
    const id = String(row.id);
    if (id.includes("broadcast") || id.includes("newsletter")) return;
    const prev = chats.get(id) || {};
    chats.set(id, {
      ...prev,
      id,
      name: String(row.name || row.subject || prev.name || "").trim(),
      group: id.includes("@g.us"),
      unread: Number(row.unreadCount || prev.unread || 0),
      ts: Number(row.conversationTimestamp || prev.ts || 0),
      phone: digitsFromJid(id),
    });
  };
  sock.ev.on("chats.upsert", (rows) => (rows || []).forEach(remember));
  sock.ev.on("chats.update", (rows) => (rows || []).forEach(remember));
  sock.ev.on("messaging-history.set", (payload) => ((payload && payload.chats) || []).forEach(remember));
  await new Promise((resolve) => setTimeout(resolve, waitMs));
  try { Object.values((sock.store || {}).chats || {}).forEach(remember); } catch (err) { /* ignore */ }
  const rows = Array.from(chats.values()).sort((x, y) => (y.ts || 0) - (x.ts || 0));
  appendLedger({ event: "chats_done", received: rows.length, groups: rows.filter((row) => row.group).length });
  emit({ ok: true, count: rows.length, chats: rows });
  try { sock.end(undefined); } catch (err) { /* ignore */ }
  setTimeout(() => process.exit(0), 200);
}

async function cmdMessages(a) {
  const authDir = path.resolve(a.auth || ".");
  const jid = String(a.chat || a.jid || "").trim();
  const limit = Math.max(1, Math.min(Number(a.limit || 50), 500));
  const waitMs = Math.max(4000, Number(a.wait || 12000));
  const { sock } = await openSocket(authDir);
  await waitForOpen(sock);
  const rows = new Map();
  const take = (msg) => {
    if (!msg) return;
    const key = msg.key || {};
    const remote = String(key.remoteJid || "");
    if (!remote) return;
    const id = String(key.id || remote + ":" + String(msg.messageTimestamp || "") + ":" + rows.size);
    rows.set(id, {
      id,
      chat: remote,
      from_me: !!key.fromMe,
      ts: Number(msg.messageTimestamp || 0),
      text: extractText(msg),
    });
  };
  sock.ev.on("messaging-history.set", (payload) => ((payload && payload.messages) || []).forEach(take));
  sock.ev.on("messages.upsert", (payload) => ((payload && payload.messages) || []).forEach(take));
  await new Promise((resolve) => setTimeout(resolve, waitMs));
  const all = Array.from(rows.values()).sort((x, y) => (x.ts || 0) - (y.ts || 0));
  const filtered = (jid ? all.filter((row) => row.chat === jid) : all).slice(-limit);
  appendLedger({ event: "messages_done", chat: jid || "*", scanned: all.length, returned: filtered.length });
  emit({ ok: true, chat: jid, scanned: all.length, count: filtered.length, messages: filtered });
  try { sock.end(undefined); } catch (err) { /* ignore */ }
  setTimeout(() => process.exit(0), 200);
}

function extractText(msg) {
  try {
    const m = msg.message || {};
    const inner = m.ephemeralMessage ? m.ephemeralMessage.message : m;
    const withCaption = inner.documentWithCaptionMessage ? inner.documentWithCaptionMessage.message : inner;
    return String(
      withCaption.conversation ||
      (withCaption.extendedTextMessage && withCaption.extendedTextMessage.text) ||
      (withCaption.imageMessage && (withCaption.imageMessage.caption || "[图片]")) ||
      (withCaption.videoMessage && (withCaption.videoMessage.caption || "[视频]")) ||
      (withCaption.audioMessage && "[语音]") ||
      (withCaption.documentMessage && (withCaption.documentMessage.fileName || "[文件]")) ||
      (withCaption.stickerMessage && "[贴纸]") ||
      ""
    ).trim();
  } catch (err) { return ""; }
}

async function cmdGroups(a) {
  const authDir = path.resolve(a.auth || ".");
  const { sock } = await openSocket(authDir);
  await primeSocket(sock);
  const rows = new Map();
  const remember = (row) => {
    if (!row || !row.id || !String(row.id).includes("@g.us")) return;
    const prev = rows.get(String(row.id)) || {};
    rows.set(String(row.id), { ...prev, id: String(row.id), name: String(row.subject || row.name || prev.name || "").trim() });
  };
  sock.ev.on("groups.upsert", (rowsIn) => (rowsIn || []).forEach(remember));
  sock.ev.on("groups.update", (rowsIn) => (rowsIn || []).forEach(remember));
  await new Promise((resolve) => setTimeout(resolve, Math.max(4000, Number(a.wait || 12000))));
  try { Object.values((sock.store || {}).chats || {}).forEach((row) => { if (String(row.id || "").includes("@g.us")) remember(row); }); } catch (err) { /* ignore */ }
  const list = Array.from(rows.values());
  appendLedger({ event: "groups_done", received: list.length });
  emit({ ok: true, count: list.length, groups: list });
  try { sock.end(undefined); } catch (err) { /* ignore */ }
  setTimeout(() => process.exit(0), 200);
}

async function cmdCheck(a) {
  const authDir = path.resolve(a.auth || ".");
  const raw = String(a.phones || "").split(/[,\s]+/).filter(Boolean);
  if (!raw.length) { emit({ ok: false, error: "需要 --phones a,b" }); process.exit(0); }
  const { sock, baileys } = await openSocket(authDir);
  await waitForOpen(sock);
  const onWa = (sock.onWhatsApp || (baileys && baileys.onWhatsApp) || (() => Promise.resolve([]))).bind(sock);
  const out = [];
  for (const phone of raw.slice(0, 100)) {
    const digits = String(phone).replace(/[^0-9]/g, "");
    if (!digits) { out.push({ input: phone, exists: false }); continue; }
    try {
      const res = await onWa.call(sock, digits + "@s.whatsapp.net");
      out.push({ input: phone, digits, exists: !!(res && res[0] && res[0].exists), jid: (res && res[0] && res[0].jid) || "" });
    } catch (err) { out.push({ input: phone, digits, exists: false, error: String(err.message || err).slice(0, 80) }); }
  }
  appendLedger({ event: "check_done", asked: out.length, exists: out.filter((row) => row.exists).length });
  emit({ ok: true, count: out.length, results: out });
  try { sock.end(undefined); } catch (err) { /* ignore */ }
  setTimeout(() => process.exit(0), 200);
}

// 中风险：发送。白名单/去重/限速都在后端做，这里只负责「一次真发一条」。
async function cmdSend(a) {
  const authDir = path.resolve(a.auth || ".");
  const to = normalizeJid(a.to || a.jid || "");
  const text = String(a.text == null ? "" : a.text);
  const caption = String(a.caption == null ? "" : a.caption);
  const image = String(a.image || "").trim();
  const file = String(a.file || "").trim();
  if (!to) { emit({ ok: false, error: "需要 --to <jid 或号码>" }); process.exit(0); }
  if (!text && !image && !file) { emit({ ok: false, error: "需要 --text 或 --image/--file" }); process.exit(0); }
  const creds = readCreds(authDir);
  if (!creds.linked) { emit({ ok: false, error: "还没有扫码绑定，请先在页面上扫码" }); process.exit(0); }
  const started = Date.now();
  const { sock } = await openSocket(authDir);
  await waitForOpen(sock);
  let kind = "text";
  let content = { text };
  if (image) {
    if (!fs.existsSync(image)) { emit({ ok: false, error: "图片不存在：" + image }); process.exit(0); }
    content = { image: fs.readFileSync(image), caption: caption || text };
    kind = "image";
  } else if (file) {
    if (!fs.existsSync(file)) { emit({ ok: false, error: "文件不存在：" + file }); process.exit(0); }
    content = { document: fs.readFileSync(file), fileName: path.basename(file), mimetype: "application/octet-stream", caption: caption || text };
    kind = "file";
  }
  try {
    const result = await sock.sendMessage(to, content);
    const id = String((result && result.key && result.key.id) || "");
    appendLedger({ event: "send_done", to, kind, id, elapsed_ms: Date.now() - started });
    emit({ ok: true, to, kind, id, ts: Number((result && result.messageTimestamp) || Math.floor(Date.now() / 1000)), elapsed_ms: Date.now() - started, me: creds.me.phone });
  } catch (err) {
    appendLedger({ event: "send_failed", to, kind, error: String((err && err.message) || err).slice(0, 200), elapsed_ms: Date.now() - started });
    emit({ ok: false, error: String((err && err.message) || err).slice(0, 300), to });
  }
  try { sock.end(undefined); } catch (err) { /* ignore */ }
  setTimeout(() => process.exit(0), 300);
}

function slimContact(row) {
  if (!row || !row.id) return null;
  return {
    id: String(row.id),
    name: String(row.name || "").trim(),
    notify: String(row.notify || "").trim(),
    verifiedName: String(row.verifiedName || "").trim(),
    phoneNumber: String(row.phoneNumber || "").trim(),
  };
}

function slimChat(row) {
  if (!row || !row.id) return null;
  const id = String(row.id);
  return {
    id,
    name: String(row.name || row.subject || "").trim(),
    group: id.includes("@g.us"),
    unread: Number(row.unreadCount || 0),
    ts: Number(row.conversationTimestamp || 0),
  };
}

function slimMessage(msg) {
  if (!msg || !msg.key) return null;
  const key = msg.key;
  return {
    id: String(key.id || ""),
    chat: String(key.remoteJid || ""),
    from_me: !!key.fromMe,
    ts: Number(msg.messageTimestamp || 0),
    text: extractText(msg),
  };
}

// 常驻监听：官方客户端就是这么干的 —— 保持一条长连接，服务端把联系人/会话/消息的变化推下来；
// 重连时用 app-state 的版本游标（app-state-sync-version-*.json）只拉「上次之后」的增量。
// 所以永远不需要重新扫码，只有 401（手机上解绑）才需要重扫一次。
async function cmdWatch(a) {
  const authDir = path.resolve(a.auth || ".");
  const stateFile = a.state ? path.resolve(a.state) : "";
  const intervalMs = Math.max(60000, Number(a.interval || 300) * 1000);
  const creds = readCreds(authDir);
  if (!creds.linked) {
    emit({ ok: false, error: "还没有扫码绑定，请先在页面上扫码" });
    process.exit(0);
  }
  const collections = ["critical_block", "critical_unblock_low", "regular", "regular_high", "regular_low"];
  const COLLECTIONS = collections;
  const line = (payload) => {
    try { process.stdout.write(JSON.stringify({ at: Date.now(), ...payload }) + "\n"); } catch (err) { /* ignore */ }
  };
  let stopRequested = false;
  const onStop = () => { stopRequested = true; appendLedger({ event: "watch_stop_signal" }); };
  process.on("SIGINT", onStop);
  process.on("SIGTERM", onStop);

  emit({ ok: true, state: "watching", me: creds.me, interval_seconds: intervalMs / 1000 });
  appendLedger({ event: "watch_start", interval_seconds: intervalMs / 1000, me: creds.me });

  let attempt = 0;
  while (!stopRequested) {
    attempt += 1;
    let sock = null;
    try {
      ({ sock } = await openSocket(authDir));
    } catch (err) {
      const message = String((err && err.message) || err).slice(0, 200);
      appendLedger({ event: "watch_open_failed", attempt, error: message });
      line({ event: "error", where: "open", error: message });
      await sleepMs(3000);
      continue;
    }
    let closed = "";
    sock.ev.on("connection.update", (update) => {
      if (!update) return;
      if (update.connection === "open") {
        writeState(stateFile, { state: "watching", qr: "", me: readCreds(authDir).me, attempt, at: Date.now() });
        line({ event: "state", value: "open", attempt });
      } else if (update.connection === "close") {
        closed = connectionCode(update.lastDisconnect) || "closed";
        appendLedger({ event: "watch_close", attempt, code: closed, reason: reasonText(update.lastDisconnect) });
        line({ event: "state", value: "closed", code: closed, attempt });
      }
    });
    sock.ev.on("contacts.upsert", (rows) => line({ event: "contacts_upsert", rows: (rows || []).map(slimContact).filter(Boolean) }));
    sock.ev.on("contacts.update", (rows) => line({ event: "contacts_update", rows: (rows || []).map(slimContact).filter(Boolean) }));
    sock.ev.on("chats.upsert", (rows) => line({ event: "chats_upsert", rows: (rows || []).map(slimChat).filter(Boolean) }));
    sock.ev.on("chats.update", (rows) => line({ event: "chats_update", rows: (rows || []).map(slimChat).filter(Boolean) }));
    sock.ev.on("groups.upsert", (rows) => line({ event: "groups_upsert", rows: (rows || []).map(slimChat).filter(Boolean) }));
    sock.ev.on("groups.update", (rows) => line({ event: "groups_update", rows: (rows || []).map(slimChat).filter(Boolean) }));
    sock.ev.on("messages.upsert", (payload) => {
      const rows = (((payload || {}).messages) || []).map(slimMessage).filter(Boolean);
      if (rows.length) line({ event: "messages_upsert", type: String((payload || {}).type || ""), rows });
    });

    const ready = await waitForOpen(sock, 20000);
    appendLedger({ event: "watch_ready", attempt, ready });
    line({ event: "state", value: ready ? "ready" : "not_ready", attempt });

    if (ready) {
      // 每次（重）连都先补一次增量：只拉上次版本之后的 patch
      try {
        await sock.resyncAppState(COLLECTIONS, false);
        line({ event: "resync", reason: "connect", attempt });
      } catch (err) {
        line({ event: "resync_failed", reason: "connect", error: String((err && err.message) || err).slice(0, 160) });
      }
      // 之后按 interval 周期补增量（相当于官方客户端的后台同步节拍）
      while (!stopRequested && !closed) {
        const slice = 5000;
        let waited = 0;
        while (waited < intervalMs && !stopRequested && !closed) {
          await sleepMs(Math.min(slice, intervalMs - waited));
          waited += slice;
          line({ event: "heartbeat", at: Date.now(), attempt });
        }
        if (stopRequested || closed) break;
        try {
          await sock.resyncAppState(COLLECTIONS, false);
          line({ event: "resync", reason: "interval", attempt });
        } catch (err) {
          line({ event: "resync_failed", reason: "interval", error: String((err && err.message) || err).slice(0, 160) });
          break;
        }
      }
    }
    try { sock.end(undefined); } catch (err) { /* ignore */ }
    if (stopRequested) break;
    if (String(closed) === "401") {
      appendLedger({ event: "watch_logged_out" });
      writeState(stateFile, { state: "logged_out", qr: "", me: {}, error: "设备已在手机上退出登录", at: Date.now() });
      line({ event: "logged_out" });
      break;
    }
    await sleepMs(Math.min(60000, 3000 * attempt));
  }

  appendLedger({ event: "watch_end", attempt });
  emit({ ok: true, state: "stopped" });
  process.exit(0);
}

// 离线自检：不连 WhatsApp，只跑纯函数（本地验证用）。
function cmdSelftest() {
  const checks = [];
  const expect = (name, actual, wanted) => {
    const ok = JSON.stringify(actual) === JSON.stringify(wanted);
    checks.push({ name, ok, actual, wanted });
  };
  expect("digits_phone_jid", digitsFromJid("8613800138000@s.whatsapp.net"), "8613800138000");
  expect("digits_group", digitsFromJid("123456789-987654@g.us"), "");
  expect("digits_devicesuffix", digitsFromJid("8613800138000:12@s.whatsapp.net"), "8613800138000");
  expect("normalize_plain", normalizeJid("+86 138 0013 8000"), "8613800138000@s.whatsapp.net");
  expect("normalize_jid", normalizeJid("8613800138000@s.whatsapp.net"), "8613800138000@s.whatsapp.net");
  expect("normalize_empty", normalizeJid("abc"), "");
  expect("code_515", connectionCode({ error: { output: { statusCode: 515 } } }), "515");
  expect("code_missing", connectionCode({}), "");
  const sample = contactSample({ id: "8613800138000@s.whatsapp.net", notify: "Ann", extra: "x" });
  expect("sample_fields", sample.fields.includes("notify"), true);
  expect("sample_notify", sample.notify, "Ann");
  expect("sample_blank", sample.verifiedName, "");
  expect("slim_contact", slimContact({ id: "8613800138000@s.whatsapp.net", notify: "Ann" }), { id: "8613800138000@s.whatsapp.net", name: "", notify: "Ann", verifiedName: "", phoneNumber: "" });
  expect("slim_contact_null", slimContact({}), null);
  expect("slim_chat_group", slimChat({ id: "120363000000000000@g.us", subject: "Team", unreadCount: 2 }), { id: "120363000000000000@g.us", name: "Team", group: true, unread: 2, ts: 0 });
  expect("slim_message", slimMessage({ key: { id: "m1", remoteJid: "8613800138000@s.whatsapp.net", fromMe: true }, messageTimestamp: 5, message: { conversation: "hi" } }), { id: "m1", chat: "8613800138000@s.whatsapp.net", from_me: true, ts: 5, text: "hi" });
  const before = LEDGER;
  LEDGER = path.join(process.env.TEMP || "/tmp", "wa_bridge_selftest.jsonl");
  try { fs.rmSync(LEDGER, { force: true }); } catch (err) { /* ignore */ }
  appendLedger({ event: "selftest", ok: true });
  const written = fs.existsSync(LEDGER) ? fs.readFileSync(LEDGER, "utf8").trim() : "";
  expect("ledger_appended", written.includes('"selftest"') && written.includes('"source":"bridge"'), true);
  try { fs.rmSync(LEDGER, { force: true }); } catch (err) { /* ignore */ }
  LEDGER = before;
  emit({ ok: checks.every((row) => row.ok), count: checks.length, failed: checks.filter((row) => !row.ok), checks });
  process.exit(checks.every((row) => row.ok) ? 0 : 1);
}

const a = args();
LEDGER = a.ledger ? path.resolve(a.ledger) : "";
const cmd = (a._[0] || "").toLowerCase();
const run = { login: cmdLogin, status: cmdStatus, contacts: cmdContacts, watch: cmdWatch, profile: cmdProfile, logout: cmdLogout, chats: cmdChats, messages: cmdMessages, groups: cmdGroups, check: cmdCheck, send: cmdSend, selftest: cmdSelftest }[cmd];
if (!run) {
  emit({ ok: false, error: "未知命令：" + cmd });
  process.exit(2);
}
Promise.resolve()
  .then(() => run(a))
  .catch((err) => {
    emit({ ok: false, error: String((err && err.message) || err), cmd });
    appendLedger({ event: "cmd_failed", cmd, error: String((err && err.message) || err).slice(0, 200) });
    process.exit(1);
  });
