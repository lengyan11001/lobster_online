#!/usr/bin/env node
// WhatsApp Web (Baileys) bridge for the local Lobster backend.
// Commands:
//   login   --auth <dir> --state <file>   start pairing, keep the QR in <file>, exit 0 once linked
//   status  --auth <dir>                  {"linked":bool,"me":{...}}
//   contacts --auth <dir> [--timeout ms]  dump the address book as JSON on stdout
//   logout  --auth <dir>                  remove the local link
// The auth dir is a normal multi-file Baileys session, so it survives restarts: pair once, reuse forever.
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

async function openSocket(authDir, opts = {}) {
  const baileys = await loadBaileys();
  const { useMultiFileAuthState, makeWASocket, fetchLatestBaileysVersion, DisconnectReason } = baileys;
  fs.mkdirSync(authDir, { recursive: true });
  const { state, saveCreds } = await useMultiFileAuthState(authDir);
  let version;
  try {
    ({ version } = await fetchLatestBaileysVersion());
  } catch (err) { version = undefined; }
  const sock = makeWASocket({
    auth: state,
    version,
    printQRInTerminal: false,
    // 必须开：通讯录是随首次 history/app-state 同步下来的（关了就永远 0 个）
    syncFullHistory: true,
    markOnlineOnConnect: false,
    shouldSyncHistoryMessage: () => true,
    browser: ["Lobster", "Chrome", "1.0"],
    ...opts.socket,
  });
  sock.ev.on("creds.update", saveCreds);
  return { sock, DisconnectReason, baileys };
}

async function cmdLogin(a) {
  const authDir = path.resolve(a.auth || ".");
  const stateFile = a.state ? path.resolve(a.state) : "";
  const { sock, DisconnectReason } = await openSocket(authDir);
  writeState(stateFile, { state: "connecting", qr: "", me: {}, at: Date.now() });
  let printed = false;
  await new Promise((resolve) => {
    sock.ev.on("connection.update", (update) => {
      const { connection, lastDisconnect, qr } = update || {};
      if (qr) {
        writeState(stateFile, { state: "qr", qr: String(qr), me: {}, at: Date.now() });
        if (!printed) { printed = true; process.stdout.write(JSON.stringify({ ok: true, state: "qr", qr_len: String(qr).length }) + "\n"); }
      }
      if (connection === "open") {
        const me = readCreds(authDir).me;
        writeState(stateFile, { state: "linked", qr: "", me, at: Date.now() });
        process.stdout.write(JSON.stringify({ ok: true, state: "linked", me }) + "\n");
        resolve();
      } else if (connection === "close") {
        const code = lastDisconnect && lastDisconnect.error && lastDisconnect.error.output ? lastDisconnect.error.output.statusCode : "";
        if (code === (DisconnectReason && DisconnectReason.loggedOut)) {
          writeState(stateFile, { state: "logged_out", qr: "", me: {}, at: Date.now() });
          process.stdout.write(JSON.stringify({ ok: false, state: "logged_out", error: "设备已在手机上退出登录" }) + "\n");
          resolve();
        } else {
          writeState(stateFile, { state: "reconnecting", qr: "", me: {}, error: String(code || ""), at: Date.now() });
        }
      }
    });
  });
  try { sock.end(undefined); } catch (err) { /* ignore */ }
  setTimeout(() => process.exit(0), 200);
}

async function cmdStatus(a) {
  const authDir = path.resolve(a.auth || ".");
  const info = readCreds(authDir);
  process.stdout.write(JSON.stringify({ ok: true, ...info }) + "\n");
  process.exit(0);
}

async function cmdContacts(a) {
  const authDir = path.resolve(a.auth || ".");
  const timeout = Math.max(3000, Number(a.timeout || 60000));
  const creds = readCreds(authDir);
  if (!creds.linked) {
    process.stdout.write(JSON.stringify({ ok: false, error: "还没有扫码绑定，请先在页面上扫码" }) + "\n");
    process.exit(0);
  }
  const { sock } = await openSocket(authDir);
  const collected = new Map();
  const remember = (row) => {
    if (!row || !row.id) return;
    const jid = String(row.id);
    if (jid.includes("@g.us")) return;
    const phone = digitsFromJid(jid) || digitsFromJid(row.phoneNumber);
    const item = {
      id: jid,
      phone,
      name: String(row.name || "").trim(),
      notify: String(row.notify || "").trim(),
      verifiedName: String(row.verifiedName || "").trim(),
    };
    const key = jid;
    const prev = collected.get(key) || {};
    collected.set(key, { ...prev, ...Object.fromEntries(Object.entries(item).filter(([, v]) => v)) });
  };
  sock.ev.on("contacts.upsert", (rows) => (rows || []).forEach(remember));
  sock.ev.on("contacts.update", (rows) => (rows || []).forEach(remember));
  sock.ev.on("messaging-history.set", (payload) => {
    ((payload && payload.contacts) || []).forEach(remember);
  });
  const waitMs = Math.max(8000, Number(a.wait || 45000));
  const settleMs = Math.max(4000, Number(a.settle || 12000));
  let lastEvent = 0;
  const bump = () => { lastEvent = Date.now(); };
  sock.ev.on("contacts.upsert", bump);
  sock.ev.on("contacts.update", bump);
  sock.ev.on("messaging-history.set", (payload) => {
    bump();
    const list = ((payload && payload.contacts) || []).length;
    if (list) process.stderr.write("history contacts=" + list + "\n");
  });
  await new Promise((resolve, reject) => {
    const started = Date.now();
    const timer = setTimeout(() => resolve(), waitMs);
    const idle = setInterval(() => {
      if (lastEvent && Date.now() - lastEvent >= settleMs && Date.now() - started > 6000) {
        clearTimeout(timer); clearInterval(idle); resolve();
      }
    }, 1000);
    sock.ev.on("connection.update", (update) => {
      if (update && update.connection === "close") {
        clearTimeout(timer); clearInterval(idle);
        reject(new Error("连接被关闭（会话可能已在手机上退出登录）"));
      }
    });
  });
  try {
    const store = sock.store || {};
    Object.values(store.contacts || {}).forEach(remember);
  } catch (err) { /* ignore */ }
  const contacts = Array.from(collected.values());
  process.stdout.write(JSON.stringify({ ok: true, count: contacts.length, with_phone: contacts.filter((row) => row.phone).length, contacts }) + "\n");
  try { sock.end(undefined); } catch (err) { /* ignore */ }
  setTimeout(() => process.exit(0), 200);
}

async function cmdLogout(a) {
  const authDir = path.resolve(a.auth || ".");
  try { fs.rmSync(authDir, { recursive: true, force: true }); } catch (err) { /* ignore */ }
  process.stdout.write(JSON.stringify({ ok: true, removed: authDir }) + "\n");
  process.exit(0);
}

async function cmdChats(a) {
  const authDir = path.resolve(a.auth || ".");
  const waitMs = Math.max(6000, Number(a.wait || 20000));
  const { sock } = await openSocket(authDir);
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
  process.stdout.write(JSON.stringify({ ok: true, count: rows.length, chats: rows }) + "\n");
  try { sock.end(undefined); } catch (err) { /* ignore */ }
  setTimeout(() => process.exit(0), 200);
}

async function cmdMessages(a) {
  const authDir = path.resolve(a.auth || ".");
  const jid = String(a.chat || a.jid || "").trim();
  const limit = Math.max(1, Math.min(Number(a.limit || 50), 500));
  if (!jid) { process.stdout.write(JSON.stringify({ ok: false, error: "需要 --chat <jid>" }) + "\n"); process.exit(0); }
  const { sock } = await openSocket(authDir);
  const rows = [];
  sock.ev.on("messages.upsert", (payload) => {
    ((payload && payload.messages) || []).forEach((msg) => {
      const key = msg.key || {};
      rows.push({
        id: String(key.id || ""),
        chat: String(key.remoteJid || ""),
        from_me: !!key.fromMe,
        ts: Number(msg.messageTimestamp || 0),
        text: extractText(msg) ,
      });
    });
  });
  await new Promise((resolve) => setTimeout(resolve, Math.max(4000, Number(a.wait || 12000))));
  const filtered = rows.filter((row) => row.chat === jid).slice(-limit);
  process.stdout.write(JSON.stringify({ ok: true, chat: jid, count: filtered.length, messages: filtered }) + "\n");
  try { sock.end(undefined); } catch (err) { /* ignore */ }
  setTimeout(() => process.exit(0), 200);
}

function extractText(msg) {
  try {
    const m = msg.message || {};
    const inner = m.ephemeralMessage ? m.ephemeralMessage.message : m;
    return String(
      inner.conversation ||
      (inner.extendedTextMessage && inner.extendedTextMessage.text) ||
      (inner.imageMessage && (inner.imageMessage.caption || "[图片]")) ||
      (inner.videoMessage && (inner.videoMessage.caption || "[视频]")) ||
      (inner.audioMessage && "[语音]") ||
      (inner.documentMessage && (inner.documentMessage.fileName || "[文件]")) ||
      ""
    ).trim();
  } catch (err) { return ""; }
}

async function cmdGroups(a) {
  const authDir = path.resolve(a.auth || ".");
  const { sock } = await openSocket(authDir);
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
  process.stdout.write(JSON.stringify({ ok: true, count: list.length, groups: list }) + "\n");
  try { sock.end(undefined); } catch (err) { /* ignore */ }
  setTimeout(() => process.exit(0), 200);
}

async function cmdCheck(a) {
  const authDir = path.resolve(a.auth || ".");
  const raw = String(a.phones || "").split(/[,\s]+/).filter(Boolean);
  if (!raw.length) { process.stdout.write(JSON.stringify({ ok: false, error: "需要 --phones a,b" }) + "\n"); process.exit(0); }
  const { sock } = await openSocket(authDir);
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
  process.stdout.write(JSON.stringify({ ok: true, count: out.length, results: out }) + "\n");
  try { sock.end(undefined); } catch (err) { /* ignore */ }
  setTimeout(() => process.exit(0), 200);
}

const a = args();
const cmd = (a._[0] || "").toLowerCase();
const run = { login: cmdLogin, status: cmdStatus, contacts: cmdContacts, logout: cmdLogout, chats: cmdChats, messages: cmdMessages, groups: cmdGroups, check: cmdCheck }[cmd];
if (!run) {
  process.stdout.write(JSON.stringify({ ok: false, error: "未知命令：" + cmd }) + "\n");
  process.exit(2);
}
run(a).catch((err) => {
  process.stdout.write(JSON.stringify({ ok: false, error: String((err && err.message) || err) }) + "\n");
  process.exit(1);
});