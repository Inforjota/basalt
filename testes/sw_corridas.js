// Ensaios de CORRIDAS do static/sw.js em node (ronda 11): a rede fica pendurada até o ensaio soltar cada resposta, o relógio é
// do ensaio (relogio.t) e há Web Locks simulados com o disjuntor do trinco encurtado (TRINCO_MS 3000 → 100 ms; o caminho é o
// mesmo). Três ganchos param o service worker num ponto exacto:
//   ganchoPut      — o próximo cache.put na cache da API (o 200 a guardar, com o trinco) fica suspenso até o ensaio o soltar;
//   ganchoLapides  — a k-ésima operação (match, keys, put, delete) na loja das lápides fica suspensa;
//   ganchoLeitura  — antes da N-ésima leitura de estado (caches.has, caches.match, Cache.match, Cache.keys) corre-se um
//                    escritor completo (um 404, um 401 ou «apagar a cópia»), que não conta nem fica sem rede.
// ronda 13, mais dois:
//   ganchoMatch    — a k-ésima caches.match(…, {cacheName: <cache da API>}) já leu a entrada mas a promessa fica por resolver
//                    até o ensaio a soltar (a Response já está em mão);
//   ganchoApaga    — o próximo Cache.delete na cache da API fica suspenso (a lápide já está posta, a entrada ainda lá está).
// As páginas: self.clients.matchAll devolve as de `paginas` (cada uma regista as mensagens e responde pelo canal, se `responde`).
//   node sw_corridas.js <cenario>      cenários: g2, g2b, g2put2, g3, g3b, poda, ultimo, lapide_depois, protocolo, aviso
//                                      — escreve em JSON o que aconteceu.
// BASALT_SW: ensaiar outra cópia do sw.js.
"use strict";
const fs = require("fs");
const path = require("path");

const ORIGEM = "https://basalt.teste";
const DONO = "d-ana";
const url = ORIGEM + "/api/nota?nome=n1";
const outra = ORIGEM + "/api/nota?nome=n10";
const GERACAO = "basaltcfg-copia-geracao";

const FONTE_REAL = fs.readFileSync(process.env.BASALT_SW || path.join(__dirname, "..", "basalt", "static", "sw.js"), "utf8");
if (FONTE_REAL.split("TRINCO_MS = 3000").length !== 2) throw new Error("o disjuntor do trinco mudou — rever este ensaio");
const FONTE = FONTE_REAL.replace("TRINCO_MS = 3000", "TRINCO_MS = 100").replace("AVISO_MS = 1000", "AVISO_MS = 300");

const dormir = (ms) => new Promise((r) => setTimeout(r, ms));
async function ate(cond, ms) { for (let i = 0; i < (ms || 10000) / 5; i++) { if (cond()) return true; await dormir(5); } return !!cond(); }
const urlDe = (req) => (typeof req === "string" ? new URL(req, ORIGEM).href : req.url);

// ───── Web Locks simulados (como em sw_ambiente.js): «exclusive» espera por todos, «shared» só por um exclusivo; o `signal`
// cancela quem espera (AbortError) — é assim que o disjuntor do sw.js larga um trinco preso ─────
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

// ───── estado do ensaio ─────
let lojas, pendentes, semRede, injectando, relogio, trabalhos, sw, ganchoPut, ganchoLapides, ganchoLeitura, API, APAGADAS;
let ganchoMatch, ganchoApaga, paginas, ouvintes;
const ehLapides = (n) => String(n).indexOf("basaltcfg-apagadas") === 0;

async function leitura(tipo, nome) {
  const g = ganchoLeitura;
  if (injectando || !g.armado || (g.filtro && !g.filtro(tipo, nome))) return;
  g.n++;
  if (g.n === g.em) { g.armado = false; g.disparou = true; g.onde = tipo + " " + nome; await g.fazer(); }
}
async function opLapides(op) {
  const g = ganchoLapides;
  if (injectando || !g.armado || (g.so && g.so !== op)) return;
  g.n++;
  if (g.n === g.em) { g.armado = false; g.parado = true; g.onde = op + " (" + g.n + ".ª)"; await new Promise((r) => { g.solta = r; }); }
}
class Cache {
  constructor(nome) { this.nome = nome; this.m = new Map(); }
  async put(req, resp) {
    if (ehLapides(this.nome)) await opLapides("put");
    if (this.nome === API && ganchoPut.em) { ganchoPut.em = false; ganchoPut.parado = true; await new Promise((r) => { ganchoPut.solta = r; }); }
    this.m.set(urlDe(req), resp);
  }
  async add() {}
  async match(req) {
    if (ehLapides(this.nome)) await opLapides("match");
    await leitura("Cache.match", this.nome);
    const r = this.m.get(urlDe(req)); return r ? r.clone() : undefined;
  }
  async keys() {
    if (ehLapides(this.nome)) await opLapides("keys");
    await leitura("Cache.keys", this.nome);
    return [...this.m.keys()].map((u) => new Request(u));
  }
  async delete(req) {
    if (ehLapides(this.nome)) await opLapides("delete");
    if (this.nome === API && ganchoApaga.armado) { ganchoApaga.armado = false; ganchoApaga.parado = true; await new Promise((r) => { ganchoApaga.solta = r; }); }
    return this.m.delete(urlDe(req));
  }
}
const caches = {
  async open(n) { if (!lojas.has(n)) lojas.set(n, new Cache(n)); return lojas.get(n); },
  async has(n) { await leitura("caches.has", n); return lojas.has(n); },
  async delete(n) { return lojas.delete(n); },
  async keys() { return [...lojas.keys()]; },
  async match(req, o) {
    await leitura("caches.match", o && o.cacheName);
    const c = o && o.cacheName ? lojas.get(o.cacheName) : null; const r = c ? c.m.get(urlDe(req)) : undefined; const out = r ? r.clone() : undefined;
    const g = ganchoMatch;
    if (o && o.cacheName === API && g.armado && !injectando && ++g.n === g.em) {
      g.armado = false; g.parado = true; g.achou = !!out; await new Promise((s) => { g.solta = s; });
    }
    return out;
  },
};
function fetchFalso() {
  if (semRede && !injectando) return Promise.reject(new TypeError("Failed to fetch"));
  return new Promise((res) => { pendentes.push(res); });
}
// ronda 13: os ouvintes do SW ficam registados (o de «message») e as páginas são as de `paginas`
const self = {
  addEventListener(t, f) { ouvintes[t] = f; }, skipWaiting() {}, location: { origin: ORIGEM },
  clients: { claim() {}, async matchAll() { return paginas.map(clientePagina); } },
};
function clientePagina(pg) {
  return { postMessage(msg, portas) {
    pg.recebidas.push({ msg, t: Date.now() });
    if (pg.responde && portas && portas[0]) setTimeout(() => { pg.respondeu.push(Date.now()); portas[0].postMessage({ ok: true }); }, pg.demora || 20);
  } };
}

function montar() {
  lojas = new Map(); pendentes = []; semRede = false; injectando = false; relogio = { t: 5000 }; trabalhos = [];
  ganchoMatch = { armado: false, em: 0, n: 0, parado: false, achou: false, solta: null };
  ganchoApaga = { armado: false, parado: false, solta: null };
  paginas = []; ouvintes = {};
  ganchoPut = { em: false, parado: false, solta: null };
  ganchoLapides = { armado: false, em: 0, n: 0, so: null, parado: false, solta: null, onde: "" };
  ganchoLeitura = { armado: false, em: 0, n: 0, filtro: null, fazer: null, disparou: false, onde: "" };
  class DateFalso extends Date { static now() { return relogio.t; } }
  sw = new Function("self", "caches", "fetch", "Date", "navigator", "AbortController", "setTimeout", "clearTimeout",
    FONTE + "\nreturn {tratarApi: tratarApi, cacheApiDe: cacheApiDe, lojaLapides: lojaLapides};")(
    self, caches, fetchFalso, DateFalso, { locks: criarLocks() }, AbortController, setTimeout, clearTimeout);
  API = sw.cacheApiDe(DONO);
  APAGADAS = sw.lojaLapides(DONO);
}

const cab = () => ({ "Content-Type": "application/json", "X-Basalt-Dono": DONO });
const resp200 = (t) => new Response(JSON.stringify({ texto: t }), { status: 200, headers: cab() });
const resp404 = () => new Response(JSON.stringify({ erro: "a nota já não existe" }), { status: 404, headers: cab() });
const resp401 = () => new Response(JSON.stringify({ erro: "sessão expirada" }), { status: 401, headers: cab() });
// ronda 13: o pedido de uma página deste protocolo leva «X-Basalt-Protocolo: 5» (`semProtocolo` = uma página de antes)
const pedido = (u, semProtocolo) => new Request(u || url, { headers: Object.assign({ "X-Basalt-Dono": DONO }, semProtocolo ? {} : { "X-Basalt-Protocolo": "5" }) });

// devolve {p, i} (a promessa e o índice da resposta pendente) — um `return p` numa função async esperava pela rede presa
async function pedir(u) {
  const n = pendentes.length;
  const p = sw.tratarApi(pedido(u), (t) => { trabalhos.push(t); });
  p.catch(() => {});
  for (let i = 0; i < 400 && pendentes.length === n; i++) await dormir(5);
  return { p, i: n };
}
async function acabar(p) { await p; await Promise.all(trabalhos.splice(0)); await dormir(20); }
// lidas sem ganchos (directamente do Map)
async function guardada(u) { const c = lojas.get(API); const g = c ? c.m.get(u || url) : undefined; return g ? (await g.clone().json()).texto : null; }
// a lápide que vale: a maior entre a entrada única (chave = URL) e as entradas por instante («/__lapide/<URL>/<hora>»)
function prefixo(u) { return "/__lapide/" + encodeURIComponent(u) + "/"; }
async function lapideEfectiva(u) {
  u = u || url;
  const c = lojas.get(APAGADAS); if (!c) return null;
  let l = null;
  for (const [k, v] of c.m) {
    if (k === u || k.indexOf(prefixo(u)) !== -1) { const n = Number(await v.clone().text()) || 0; if (l === null || n > l) l = n; }
  }
  return l;
}
async function lapideUnica(u) { const c = lojas.get(APAGADAS); const r = c ? c.m.get(u || url) : undefined; return r ? Number(await r.clone().text()) : null; }
function porInstante(u) { const c = lojas.get(APAGADAS); return c ? [...c.m.keys()].filter((k) => k.indexOf(prefixo(u || url)) !== -1).map((k) => Number(k.split("/").pop())).sort((a, b) => a - b) : []; }
function comoFoi(r) { return r ? (r.status + (r.headers.get("X-Basalt-From-Cache") ? " cache" : "")) : "nada"; }
async function offline(u, semProtocolo) {
  semRede = true;
  try { return comoFoi(await sw.tratarApi(pedido(u, semProtocolo))); }
  catch (e) { return "recusou"; } finally { semRede = false; }
}
// escritores que entram A MEIO de uma leitura: um 404 e um 401 completos (o trinco está com a leitura → disjuntor) e «apagar a
// cópia» como a página o faz (geração nova, marca de desligada, as caches da API fora)
async function por404() { relogio.t += 100; const q = await pedir(); pendentes[q.i](resp404()); const r = await q.p; return r.status; }
async function por401() { relogio.t += 100; const q = await pedir(); pendentes[q.i](resp401()); const r = await q.p; return r.status; }
async function apagarCopia() {
  const g = await caches.open(GERACAO); await g.put("/__geracao", new Response("1-x"));
  await caches.open("basaltcfg-copia-desligada");
  for (const n of [...lojas.keys()]) if (/^basalt-/.test(n)) lojas.delete(n);
  return "apagada";
}
const ESCRITORES = { "404": por404, "401": por401, apagar: apagarCopia };
async function injectar(nome) { injectando = true; try { return await ESCRITORES[nome](); } finally { injectando = false; } }
// a nota guardada às 1000, na geração «0-a» (a geração existe: a leitura dela é um Cache.match). Antes, OUTRA nota levou um 404
// às 500: a loja das lápides do dono existe, e as leituras dela (Cache.match, Cache.keys) também entram na contagem.
async function prepararNota() {
  montar();
  const g = await caches.open(GERACAO); await g.put("/__geracao", new Response("0-a"));
  await notaApagadaA(500, outra);
  relogio.t = 1000; const q = await pedir(); pendentes[q.i](resp200("ANTIGA")); await acabar(q.p);
  relogio.t = 2000;
  return guardada();
}
async function notaApagadaA(t, u) { relogio.t = t; const q = await pedir(u); pendentes[q.i](resp404()); await acabar(q.p); }

// ── G2: 200 de 20 suspenso no put (com o trinco); 404 de 10 suspenso na k-ésima operação (de tipo `so`) na loja das lápides;
//    404 de 30 grava; retoma o de 10; retoma o 200 de 20 ──
async function correrG2(so, k) {
  montar();
  relogio.t = 10; const a = await pedir();
  relogio.t = 20; const b = await pedir();
  relogio.t = 30; const c = await pedir();
  ganchoPut.em = true;
  pendentes[b.i](resp200("ANTIGA")); await b.p;
  const preso = await ate(() => ganchoPut.parado, 5000);             // o 200 de 20 a guardar, com o trinco
  ganchoLapides = { armado: true, em: k, n: 0, so, parado: false, solta: null, onde: "" };
  let fimA = false; a.p.then(() => { fimA = true; }, () => { fimA = true; });
  pendentes[a.i](resp404());                                          // 404 de 10: trinco preso → disjuntor → sem trinco
  await ate(() => ganchoLapides.parado || fimA, 10000);
  const parou = ganchoLapides.parado; ganchoLapides.armado = false;
  pendentes[c.i](resp404()); const r30 = await c.p;                  // 404 de 30: também pelo disjuntor; grava a lápide 30
  const depois30 = await lapideEfectiva();
  if (parou) ganchoLapides.solta();                                   // retoma o 404 de 10
  const r10 = await a.p;
  if (ganchoPut.solta) ganchoPut.solta();                             // retoma o 200 de 20
  await Promise.all(trabalhos.splice(0)); await dormir(20);
  return { preso, parou, onde: ganchoLapides.onde, r10: r10.status, r30: r30.status, depois30,
    lapide: await lapideEfectiva(), unica: await lapideUnica(), guardada: await guardada(), offline: await offline() };
}

const CENARIOS = {
  // ── ronda 13 (achado 5 da revisão externa do porte): o 404 de 10 suspenso na 2.ª escrita na loja das lápides ──
  async g2put2() { return correrG2("put", 2); },
  // ── ronda 13 (achado 1 da r10): a Response da ÚLTIMA releitura já em mão (promessa por resolver); um 404 pelo disjuntor
  //    acaba inteiro (lápide + apagar); só então se entrega ──
  async ultimo() {
    const antes = await prepararNota();
    ganchoMatch = { armado: true, em: 2, n: 0, parado: false, achou: false, solta: null };
    semRede = true;
    let fim = null;
    const p = sw.tratarApi(pedido()).then(comoFoi, () => "recusou").then((x) => { fim = x; return x; });
    const parou = await ate(() => ganchoMatch.parado || fim !== null, 5000) && ganchoMatch.parado;
    semRede = false;
    const estado = parou ? await injectar("404") : null;                 // completo: tirarNota acabou antes de a resposta seguir
    const meio = { lapide: await lapideEfectiva(), guardada: await guardada(), achou: ganchoMatch.achou };
    if (parou) ganchoMatch.solta();
    const r = await p;
    return { antes, parou, estado, meio, offline: r, depois: await offline() };
  },
  // ── ronda 13 (achado 1 da r10, variante): antes da última releitura, um 404 pelo disjuntor põe a lápide e fica suspenso no
  //    apagar — a entrada ainda lá está; entrega-se (ou não) e só depois o apagar acaba ──
  async lapide_depois() {
    const antes = await prepararNota();
    let q = null;
    ganchoLeitura = { armado: true, em: 2, n: 0, filtro: (t, n) => t === "caches.match" && n === API, disparou: false, onde: "",
      fazer: async () => {
        ganchoApaga = { armado: true, parado: false, solta: null };
        injectando = true;
        try { relogio.t += 100; q = await pedir(); pendentes[q.i](resp404()); } finally { injectando = false; }
        await ate(() => ganchoApaga.parado, 5000);
      } };
    const r = await offline();
    const parado = ganchoApaga.parado, meio = { lapide: await lapideEfectiva(), guardada: await guardada() };
    if (ganchoApaga.solta) ganchoApaga.solta();
    const r404 = q ? (await q.p).status : null;
    return { antes, disparou: ganchoLeitura.disparou, parado, meio, offline: r, r404, depois: await offline() };
  },
  // ── ronda 13 (achado 4 do porte / 3 da r10): só se serve a cópia a um pedido que fale o protocolo deste SW ──
  async protocolo() {
    const antes = await prepararNota();
    const sem = await offline(null, true);
    semRede = true;
    let com = null, cab = {};
    try { const r = await sw.tratarApi(pedido()); com = comoFoi(r); cab = { protocolo: r.headers.get("X-Basalt-Protocolo"), epoca: r.headers.get("X-Basalt-Epoca") }; }
    catch (e) { com = "recusou"; } finally { semRede = false; }
    return { antes, sem, com, cab };
  },
  // ── ronda 13: um 404, um 401 e {tipo:"revogar"} avisam as páginas e só acabam depois de elas responderem (uma que não
  //    responde não prende para sempre) ──
  async aviso() {
    const out = {};
    for (const caso of ["404", "401", "revogar"]) {
      await prepararNota();
      paginas = [{ recebidas: [], respondeu: [], responde: true, demora: 60 }, { recebidas: [], respondeu: [], responde: false }];
      const t0 = Date.now(); let fim = 0, estado = null;
      if (caso === "revogar") {
        const canal = new MessageChannel(), resposta = new Promise((r) => { canal.port1.onmessage = (e) => { fim = Date.now(); r(e.data); canal.port1.close(); }; });
        let espera = null;
        if (ouvintes.message) ouvintes.message({ data: { tipo: "revogar" }, ports: [canal.port2], waitUntil(p) { espera = p; } });
        estado = await Promise.race([resposta, dormir(3000).then(() => "sem resposta")]);
        if (espera) await espera;
      } else {
        relogio.t += 100; const q = await pedir(); pendentes[q.i](caso === "404" ? resp404() : resp401());
        estado = (await q.p).status; fim = Date.now();
      }
      const [a, b] = paginas;
      const ep = lojas.get("basaltcfg-epoca"), marca = ep ? ep.m.get(ORIGEM + "/__epoca") : null;
      out[caso] = { estado, recebeu: a.recebidas.map((x) => x.msg && x.msg.tipo), calada: b.recebidas.map((x) => x.msg && x.msg.tipo),
        acabou_depois_da_resposta: !!(a.respondeu.length && fim >= a.respondeu[0]), demorou: fim - t0,
        epoca: marca ? await marca.clone().text() : null };
    }
    paginas = [];
    return out;
  },
  async g2() {
    const caso = await correrG2("put", 1);
    relogio.t = 40;                                    // controlo: a nota recriada (GET de 40) guarda-se e serve-se sem rede
    const q = await pedir(); pendentes[q.i](resp200("RECRIADA")); await acabar(q.p);
    return { caso, controlo: { guardada: await guardada(), offline: await offline() } };
  },
  async g2b() {
    const linhas = [];
    for (let k = 1; k <= 40; k++) {
      const r = await correrG2(null, k);
      if (!r.parou) break;
      linhas.push({ k, onde: r.onde, lapide: r.lapide, unica: r.unica, guardada: r.guardada, offline: r.offline });
    }
    return { linhas };
  },
  // ── G3: o pedido sem rede; antes da N-ésima leitura da GERAÇÃO entra um 404 completo (pelo disjuntor) ──
  async g3() {
    const linhas = [];
    for (let N = 1; N <= 20; N++) {
      const antes = await prepararNota(); let estado = null;
      ganchoLeitura = { armado: true, em: N, n: 0, filtro: (t, n) => t === "Cache.match" && n === GERACAO,
        fazer: async () => { estado = await injectar("404"); }, disparou: false, onde: "" };
      const r = await offline();
      if (!ganchoLeitura.disparou) break;
      linhas.push({ N, antes, estado, lapide: await lapideEfectiva(), guardada: await guardada(), offline: r });
    }
    const g = await prepararNota(), semNada = await offline();     // controlo: sem nada a meio, serve-se sem rede
    const q = await pedir(); pendentes[q.i](resp404()); await acabar(q.p);
    const depois404 = await offline();                              // controlo: depois de um 404 (sem corrida), recusa
    return { linhas, controlo: { guardada: g, semNada, depois404 } };
  },
  // ── G3 generalizado: antes de CADA leitura de estado do pedido sem rede, um 404, um 401 ou «apagar a cópia» ──
  async g3b() {
    const linhas = [];
    for (const e of ["404", "401", "apagar"]) {
      for (let N = 1; N <= 60; N++) {
        const antes = await prepararNota(); let estado = null;
        ganchoLeitura = { armado: true, em: N, n: 0, filtro: null, fazer: async () => { estado = await injectar(e); }, disparou: false, onde: "" };
        const r = await offline();
        if (!ganchoLeitura.disparou) break;
        linhas.push({ escritor: e, N, onde: ganchoLeitura.onde, antes, estado, offline: r });
      }
    }
    return { linhas };
  },
  // ── poda das lápides por instante: só saem as dominadas por outra mais nova da mesma nota; nunca a maior ──
  async poda() {
    montar();
    for (const t of [10, 20, 30, 40, 50]) await notaApagadaA(t);
    await notaApagadaA(15, outra);
    const seguidas = { por_instante: porInstante(), lapide: await lapideEfectiva(), unica: await lapideUnica(), outra: porInstante(outra) };
    // um 404 de 60 suspenso na 1.ª remoção (a poda dele); entretanto um 404 de 70 corre inteiro; retoma o de 60
    ganchoLapides = { armado: true, em: 1, n: 0, so: "delete", parado: false, solta: null, onde: "" };
    relogio.t = 60; const a = await pedir(); let fimA = false; a.p.then(() => { fimA = true; }, () => { fimA = true; });
    pendentes[a.i](resp404());
    await ate(() => ganchoLapides.parado || fimA, 5000);
    const parou = ganchoLapides.parado; ganchoLapides.armado = false;
    await notaApagadaA(70);
    if (parou) ganchoLapides.solta();
    await acabar(a.p);
    const suspensa = { parou, por_instante: porInstante(), lapide: await lapideEfectiva(), unica: await lapideUnica() };
    // controlo: um GET que parte depois de todas as lápides guarda a nota e serve-a sem rede
    relogio.t = 80; const nv = await pedir(); pendentes[nv.i](resp200("NOVA")); await acabar(nv.p);
    // controlo: as lápides com mais de LAPIDE_DIAS saem num 404 seguinte (relógio real muito à frente)
    relogio.t = 80 + 31 * 86400000; await notaApagadaA(relogio.t, ORIGEM + "/api/nota?nome=n2");
    return { seguidas, suspensa, controlo: { nova: await guardada(), offline: await offline(),
      depois_de_31_dias: { n1: porInstante(), n1_unica: await lapideUnica(), n10: porInstante(outra) } } };
  },
};

const nome = process.argv[2];
if (!CENARIOS[nome]) { console.error("cenário desconhecido: " + nome); process.exit(2); }
CENARIOS[nome]().then((r) => { process.stdout.write(JSON.stringify(r)); process.exit(0); }, (e) => { console.error(e && e.stack || e); process.exit(1); });
