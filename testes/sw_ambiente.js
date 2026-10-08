// Ambiente de testes do static/sw.js em node (sem browser): carrega o service worker num contexto vm com `self`,
// `caches` e `fetch` simulados, corre uma lista de passos e escreve em JSON o que aconteceu.
//   node sw_ambiente.js '<json>'   com {"passos": [...], "caches": {"nome": ["url", ...]}, "evento": "activate"}
// Cada passo: {"url": "/api/nota?nome=a", "rede": "ok|rede|401|403|404|410|login|html|500|502|503|504",
//              "metodo": "GET", "modo": "cors|navigate", "dono": "d-a", "eco": "d-b"|""}.
// ronda 9: o pedido leva X-Basalt-Dono = `dono` do passo (ou o de cfg.dono, ou "d-teste"; "" = sem cabeçalho) e a resposta
// do servidor traz o eco desse dono — ou `eco` se o passo o disser ("" = sem eco).
// A resposta «ok» traz {"versao": <nº do passo>} — assim vê-se se o que chegou é a cópia guardada ou a da rede.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ORIGEM = "https://basalt.teste";
const cfg = JSON.parse(process.argv[2] || "{}");

// ───── CacheStorage simulada (chave = URL completo, com a query) ─────
const armazem = new Map();
const chave = (r) => (typeof r === "string" ? new URL(r, ORIGEM).href : r.url);
class Cache {
  constructor() { this.m = new Map(); }
  async match(r) { const x = this.m.get(chave(r)); return x ? x.clone() : undefined; }
  async put(r, resp) { this.m.set(chave(r), resp.clone()); }
  async delete(r) { return this.m.delete(chave(r)); }
  async add(r) { const resp = await globalFetch(new Request(new URL(chave(r), ORIGEM))); if (!resp.ok) throw new TypeError("add falhou"); await this.put(r, resp); }
  async keys() { return [...this.m.keys()]; }
}
const caches = {
  async open(n) { if (!armazem.has(n)) armazem.set(n, new Cache()); return armazem.get(n); },
  async has(n) { return armazem.has(n); },
  async delete(n) { return armazem.delete(n); },
  async keys() { return [...armazem.keys()]; },
  async match(r, op) {
    const nomes = op && op.cacheName ? [op.cacheName] : [...armazem.keys()];
    for (const n of nomes) { const c = armazem.get(n); const x = c && (await c.match(r)); if (x) return x; }
    return undefined;
  },
};
for (const [n, urls] of Object.entries(cfg.caches || {})) {
  const c = new Cache();
  for (const u of urls) c.m.set(new URL(u, ORIGEM).href, new Response(JSON.stringify({ versao: "antiga" }), { headers: { "content-type": "application/json" } }));
  armazem.set(n, c);
}

// ───── rede simulada ─────
let rede = "ok", passo = -1, eco = null;
const chamadas = [];
function globalFetch(pedido) {
  chamadas.push({ url: pedido.url, redirect: pedido.redirect });
  const doPedido = (pedido.headers && pedido.headers.get && pedido.headers.get("X-Basalt-Dono")) || "";
  const devolve = eco === null ? doPedido : eco;
  const cab = Object.assign({ "content-type": "application/json; charset=utf-8" }, devolve ? { "X-Basalt-Dono": devolve } : {});
  const json = (status, corpo) => new Response(JSON.stringify(corpo), { status, headers: cab });
  switch (rede) {
    case "rede": return Promise.reject(new TypeError("Failed to fetch"));
    case "ok": return Promise.resolve(json(200, { versao: passo }));
    case "html": return Promise.resolve(new Response("<html>Iniciar sessão</html>", { status: 200, headers: { "content-type": "text/html" } }));
    case "login":   // 302 para a página de login com redirect "manual" → resposta opaca
      if (pedido.redirect === "manual") return Promise.resolve({ type: "opaqueredirect", status: 0, ok: false, headers: new Headers() });
      return Promise.reject(new TypeError("CORS: redireccionamento para outro domínio"));
    default: return Promise.resolve(json(Number(rede), { erro: "código " + rede }));
  }
}

// ───── o service worker ─────
const ouvintes = {};
const self = {
  location: { origin: ORIGEM },
  addEventListener: (t, f) => { ouvintes[t] = f; },
  skipWaiting: () => {},
  clients: { claim: async () => {} },
};
const ctx = vm.createContext({ self, caches, fetch: globalFetch, Response, Request, Headers, URL, Promise, console });
// BASALT_SW: testar outra cópia do sw.js (ex.: a de .bak) em vez da servida
vm.runInContext(fs.readFileSync(process.env.BASALT_SW || path.join(__dirname, "..", "basalt", "static", "sw.js"), "utf8"), ctx, { filename: "sw.js" });

async function evento(tipo) {
  let espera = Promise.resolve();
  ouvintes[tipo]({ waitUntil: (p) => { espera = p; } });
  await espera;
}

async function correr() {
  if (cfg.evento) await evento(cfg.evento);
  const out = [];
  for (let i = 0; i < (cfg.passos || []).length; i++) {
    const p = cfg.passos[i];
    passo = i; rede = p.rede || "ok"; eco = p.eco !== undefined ? p.eco : null;
    const url = new URL(p.url, ORIGEM).href;
    const metodo = p.metodo || "GET";
    const dono = p.dono !== undefined ? p.dono : (cfg.dono !== undefined ? cfg.dono : "d-teste");
    const pedido = p.modo === "navigate" ? { url, method: metodo, mode: "navigate" }
      : new Request(url, { method: metodo, headers: dono ? { "X-Basalt-Dono": dono } : {} });
    let resp = null;
    const ev = { request: pedido, respondWith: (x) => { resp = x; } };
    ouvintes.fetch(ev);
    const r = { intercetado: resp !== null };
    if (resp !== null) {
      try {
        const x = await resp;
        r.tipo = x.type; r.status = x.status;
        r.deCache = x.headers.get("X-Basalt-From-Cache") === "1";
        r.recusa = x.headers.get("X-Basalt-Recusa") === "1";
        if (x.type !== "error") { const t = await x.text(); try { r.corpo = JSON.parse(t); } catch (e) { r.corpo = t; } }
      } catch (e) { r.erro = String(e && e.message || e); }
    }
    await new Promise((s) => setTimeout(s, 5));       // deixa acabar os caches.put que o sw.js não espera
    out.push(r);
  }
  const estado = {};
  for (const [n, c] of armazem) estado[n] = [...c.m.keys()].map((k) => k.slice(ORIGEM.length));
  process.stdout.write(JSON.stringify({ passos: out, caches: estado, chamadas: chamadas.map((c) => ({ url: c.url.slice(ORIGEM.length), redirect: c.redirect })) }));
}
correr().catch((e) => { console.error(e); process.exit(1); });
