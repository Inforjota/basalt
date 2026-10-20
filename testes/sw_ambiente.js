// Ambiente de testes do static/sw.js em node (sem browser): carrega o service worker num contexto vm com `self`,
// `caches` e `fetch` simulados, corre uma lista de passos e escreve em JSON o que aconteceu.
//   node sw_ambiente.js '<json>'   com {"passos": [...], "caches": {"nome": ["url", ...]}, "evento": "activate"}
// Cada passo: {"url": "/api/nota?nome=a", "rede": "ok|rede|401|403|404|410|login|html|500|502|503|504",
//              "metodo": "GET", "modo": "cors|navigate", "dono": "d-a", "eco": "d-b"|""}.
// ronda 13: e leva X-Basalt-Protocolo = `protocolo` do passo ("5" por omissão; "" = sem — uma página de antes).
// ronda 9: o pedido leva X-Basalt-Dono = `dono` do passo (ou o de cfg.dono, ou "d-teste"; "" = sem cabeçalho) e a resposta
// do servidor traz o eco desse dono — ou `eco` se o passo o disser ("" = sem eco).
// A resposta «ok» traz {"versao": <nº do passo>} — assim vê-se se o que chegou é a cópia guardada ou a da rede.
// ronda 9 (achados 2 e 3 da ronda 8), tudo opcional — sem estes campos o ambiente é o de antes:
//   cfg.locks = true      Web Locks simulados (exclusão entre pedidos, com cancelamento pelo `signal`), com setTimeout e
//                         AbortController — sem eles o service worker usa a cadeia de promessas, que não tem disjuntor;
//   cfg.trinco_ms = N     encurta o disjuntor do trinco (TRINCO_MS, 3000 no sw.js) para o ensaio não esperar 3 s;
//   passo.gancho = {"em": "put_api" | "match_api", "faz": [acção, …]}   corre as acções UMA vez, a meio do pedido: «put_api» =
//                         antes de o 1.º cache.put na cache da API; «match_api» = depois de o caches.match(…, {cacheName: API})
//                         ter lido a entrada e antes de a devolver. Acções: {"pedido": {url, rede, dono, eco}} (um pedido completo
//                         ao service worker; o resultado vai em passo.gancho.resultados), {"nova_geracao": true},
//                         {"desligar": true} (a marca de «cópia desligada») e {"apagar_caches_api": true} — as três últimas são o
//                         que «apagar a cópia deste aparelho» faz à cache. O passo devolve `gancho: {disparou, resultados}`;
//   passo {"acao": "lapide", "url": …, "valor": N | "depois_da_entrada"}   põe à mão a lápide de uma nota («depois_da_entrada» =
//                         1 acima da hora X-Basalt-Inicio da entrada guardada).
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ORIGEM = "https://basalt.teste";
const cfg = JSON.parse(process.argv[2] || "{}");

// ───── CacheStorage simulada (chave = URL completo, com a query) ─────
const armazem = new Map();
const chave = (r) => (typeof r === "string" ? new URL(r, ORIGEM).href : r.url);
const ehCacheApi = (n) => /^basalt-.*-api(-[A-Za-z0-9_-]+)?$/.test(n || "");
let gancho = null;                        // armado pelo passo; dispara uma vez (ver `dispara`)
async function dispara(em, nome) {
  if (!gancho || gancho.em !== em || !ehCacheApi(nome)) return;
  const g = gancho; gancho = null; g.disparou = true;
  for (const a of g.faz) g.resultados.push(await acaoDoGancho(a));
}
class Cache {
  constructor(nome) { this.nome = nome; this.m = new Map(); }
  async match(r) { const x = this.m.get(chave(r)); return x ? x.clone() : undefined; }
  async put(r, resp) { await dispara("put_api", this.nome); this.m.set(chave(r), resp.clone()); }
  async delete(r) { return this.m.delete(chave(r)); }
  async add(r) { const resp = await globalFetch(new Request(new URL(chave(r), ORIGEM))); if (!resp.ok) throw new TypeError("add falhou"); await this.put(r, resp); }
  async keys() { return [...this.m.keys()]; }
}
const caches = {
  async open(n) { if (!armazem.has(n)) armazem.set(n, new Cache(n)); return armazem.get(n); },
  async has(n) { return armazem.has(n); },
  async delete(n) { return armazem.delete(n); },
  async keys() { return [...armazem.keys()]; },
  async match(r, op) {
    const nomes = op && op.cacheName ? [op.cacheName] : [...armazem.keys()];
    let achado;
    for (const n of nomes) { const c = armazem.get(n); const x = c && (await c.match(r)); if (x) { achado = x; break; } }
    if (op && op.cacheName) await dispara("match_api", op.cacheName);
    return achado;
  },
};
for (const [n, urls] of Object.entries(cfg.caches || {})) {
  const c = new Cache(n);
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

// ───── Web Locks simulados (só com cfg.locks) ─────
// Fila por nome, como a API a sério: «exclusive» espera por todos, «shared» só por um exclusivo; quem espera pode ser cancelado
// pelo `signal` (AbortError) — é assim que o disjuntor do sw.js (TRINCO_MS) larga um trinco preso.
function criarLocks() {
  const estado = new Map();
  const de = (n) => { if (!estado.has(n)) estado.set(n, { ex: false, sh: 0, fila: [] }); return estado.get(n); };
  const abortou = () => Object.assign(new Error("The operation was aborted."), { name: "AbortError" });
  function andar(n) {
    const s = de(n);
    while (s.fila.length) {
      const q = s.fila[0];
      if (q.modo === "exclusive" ? (s.ex || s.sh > 0) : s.ex) break;
      s.fila.shift();
      if (q.modo === "exclusive") s.ex = true; else s.sh++;
      const larga = () => { if (q.modo === "exclusive") s.ex = false; else s.sh--; andar(n); };
      Promise.resolve().then(() => q.cb({ name: n, mode: q.modo })).then((v) => { larga(); q.resolve(v); }, (e) => { larga(); q.reject(e); });
    }
  }
  return {
    request(nome, op, cb) {
      if (typeof op === "function") { cb = op; op = {}; }
      op = op || {};
      return new Promise((resolve, reject) => {
        if (op.signal && op.signal.aborted) { reject(abortou()); return; }
        const s = de(nome), q = { modo: op.mode || "exclusive", cb, resolve, reject };
        s.fila.push(q);
        if (op.signal) op.signal.addEventListener("abort", () => {
          const i = s.fila.indexOf(q);
          if (i !== -1) { s.fila.splice(i, 1); reject(abortou()); andar(nome); }
        });
        andar(nome);
      });
    },
  };
}

// ───── o service worker ─────
const ouvintes = {};
const self = {
  location: { origin: ORIGEM },
  addEventListener: (t, f) => { ouvintes[t] = f; },
  skipWaiting: () => {},
  clients: { claim: async () => {} },
};
const base = { self, caches, fetch: globalFetch, Response, Request, Headers, URL, Promise, console };
if (cfg.locks) Object.assign(base, { navigator: { locks: criarLocks() }, AbortController, setTimeout, clearTimeout });
const ctx = vm.createContext(base);
// BASALT_SW: testar outra cópia do sw.js (ex.: a de .bak) em vez da servida
vm.runInContext(fs.readFileSync(process.env.BASALT_SW || path.join(__dirname, "..", "basalt", "static", "sw.js"), "utf8"), ctx, { filename: "sw.js" });
if (cfg.trinco_ms) vm.runInContext("TRINCO_MS = " + Number(cfg.trinco_ms), ctx);

async function evento(tipo) {
  let espera = Promise.resolve();
  ouvintes[tipo]({ waitUntil: (p) => { espera = p; } });
  await espera;
}

// Um pedido GET à API, por inteiro, através do service worker. `trabalhos` recolhe o que o sw.js continua a fazer depois de a
// resposta seguir (o waitUntil do fetch): guardar na cache, destrancar.
async function pedir(p, trabalhos) {
  rede = p.rede || "ok"; eco = p.eco !== undefined ? p.eco : null;
  const url = new URL(p.url, ORIGEM).href;
  const metodo = p.metodo || "GET";
  const dono = p.dono !== undefined ? p.dono : (cfg.dono !== undefined ? cfg.dono : "d-teste");
  const protocolo = p.protocolo !== undefined ? p.protocolo : "5";   // ronda 13: a página de agora fala o protocolo 5 ("" = sem)
  const pedido = p.modo === "navigate" ? { url, method: metodo, mode: "navigate" }
    : new Request(url, { method: metodo, headers: Object.assign(dono ? { "X-Basalt-Dono": dono } : {}, protocolo ? { "X-Basalt-Protocolo": protocolo } : {}) });
  let resp = null;
  const ev = { request: pedido, respondWith: (x) => { resp = x; }, waitUntil: (x) => { trabalhos.push(x); } };
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
  return r;
}

// o que o gancho faz a meio de um pedido (ver o cabeçalho). Os trabalhos dos pedidos de dentro do gancho juntam-se aos do passo.
let trabalhosDoPasso = [];
async function acaoDoGancho(a) {
  if (a.pedido) {
    const antes = { rede, eco };
    try { return await pedir(a.pedido, trabalhosDoPasso); } finally { rede = antes.rede; eco = antes.eco; }
  }
  const loja = (n) => { if (!armazem.has(n)) armazem.set(n, new Cache(n)); return armazem.get(n); };
  if (a.nova_geracao) { loja("basaltcfg-copia-geracao").m.set(chave("/__geracao"), new Response("nova-" + Date.now())); return { nova_geracao: true }; }
  if (a.desligar) { loja("basaltcfg-copia-desligada"); return { desligar: true }; }
  if (a.apagar_caches_api) { for (const n of [...armazem.keys()]) if (ehCacheApi(n)) armazem.delete(n); return { apagar_caches_api: true }; }
  throw new Error("acção de gancho desconhecida: " + JSON.stringify(a));
}

function poeLapide(p) {
  let v = p.valor;
  if (v === "depois_da_entrada") {
    for (const [n, c] of armazem) {
      const x = ehCacheApi(n) && c.m.get(chave(p.url));
      if (x) v = (Number(x.headers.get("X-Basalt-Inicio")) || 0) + 1;
    }
  }
  if (!armazem.has("basaltcfg-apagadas")) armazem.set("basaltcfg-apagadas", new Cache("basaltcfg-apagadas"));
  armazem.get("basaltcfg-apagadas").m.set(chave(p.url), new Response(String(v)));
  return { acao: "lapide", valor: v };
}

async function correr() {
  if (cfg.evento) await evento(cfg.evento);
  const out = [];
  for (let i = 0; i < (cfg.passos || []).length; i++) {
    const p = cfg.passos[i];
    passo = i;
    if (p.acao === "lapide") { out.push(poeLapide(p)); continue; }
    gancho = p.gancho ? { em: p.gancho.em, faz: p.gancho.faz || [], disparou: false, resultados: [] } : null;
    const g = gancho;
    trabalhosDoPasso = [];
    const r = await pedir(p, trabalhosDoPasso);
    while (trabalhosDoPasso.length) await Promise.all(trabalhosDoPasso.splice(0)).catch(() => {});   // o que o sw.js faz depois de responder
    await new Promise((s) => setTimeout(s, 5));       // deixa acabar os caches.put que o sw.js não espera
    if (g) { r.gancho = { disparou: g.disparou, resultados: g.resultados }; gancho = null; }
    out.push(r);
  }
  const estado = {};
  for (const [n, c] of armazem) estado[n] = [...c.m.keys()].map((k) => k.slice(ORIGEM.length));
  process.stdout.write(JSON.stringify({ passos: out, caches: estado, chamadas: chamadas.map((c) => ({ url: c.url.slice(ORIGEM.length), redirect: c.redirect })) }));
}
correr().catch((e) => { console.error(e); process.exit(1); });
