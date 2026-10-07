// Service Worker do Basalt — offline "razoável": mostra a última versão boa
// quando não há rede, nunca esconde uma sessão expirada atrás de dados velhos.
"use strict";

var CACHE_VERSAO = "basalt-v8";      // sobe de versão quando a lógica de cache muda
// «Apagar cópia deste aparelho» cria esta cache-marca (não começa por "basalt-", por isso sobrevive à limpeza
// do activate) — enquanto existir, nenhuma resposta da API volta a ser guardada neste aparelho.
var MARCA_DESLIGADA = "basaltcfg-copia-desligada";
// sessão expirada / acesso recusado (criada aqui ou pela página) — enquanto tiver ENTRADAS, a cópia da API não se
// serve, nem sem rede. cada trancar é uma entrada própria ("/__tranca/<id>", corpo = hora); destrancar apaga só
// as entradas anteriores ao pedido que trouxe a resposta boa, nunca a cache inteira — um trancar mais novo (de outro
// pedido ou separador, no meio) fica. A entrada antiga "/__quando" (página/sw anteriores) conta como mais uma.
var MARCA_TRANCADA = "basaltcfg-copia-trancada";
// lápides — quando uma nota dá 404/410 fica aqui a hora (chave = o URL do pedido). Uma resposta 200 que tenha
// PARTIDO antes dessa hora (pedido atrasado) já não entra na cache. Não começa por "basalt-": sobrevive ao activate.
var MARCA_APAGADAS = "basaltcfg-apagadas";
var LAPIDE_DIAS = 30;
// ronda 6 (achado G5 da ronda 4): o relógio pode andar PARA TRÁS (acerto NTP, mudança de hora) — as horas com que se ordenam
// pedidos, trancas e lápides vêm daqui e nunca descem: cada uma é pelo menos 1 ms acima da última vista (também das já
// guardadas nas marcas e na cache, que viuInstante regista ao lê-las). Antes vinham directas de Date.now(): um 401 recebido
// com o relógio atrasado punha uma tranca «mais antiga» que um GET já em curso, e a resposta antiga desse GET destrancava-a.
// E para as TRANCAS nem isso chega: OUTRA parte (a página, outro separador, um SW acabado de arrancar) pode pôr uma tranca
// com hora mais baixa que um pedido já em curso. «Antes/depois de o pedido partir» decide-se pela fotografia das CHAVES
// das trancas que já existiam ao partir (chavesDe), que não depende de relógio nenhum.
var ULTIMO = 0;
function agora() {
  var t = Date.now();
  if (t <= ULTIMO) t = ULTIMO + 1;
  ULTIMO = t;
  return t;
}
function viuInstante(t) {
  t = Number(t) || 0;
  if (t > ULTIMO) ULTIMO = t;
}
// ronda 6 G5: a chave de uma tranca (um Request na cache a sério; uma string nos ensaios) e a fotografia de um conjunto delas
function chaveDe(t) { return t && t.chave ? (t.chave.url || String(t.chave)) : ""; }
function chavesDe(lista) { var s = {}; for (var i = 0; i < lista.length; i++) { var u = chaveDe(lista[i]); if (u) s[u] = true; } return s; }
var CACHE_APP = CACHE_VERSAO + "-app";
var CACHE_API = CACHE_VERSAO + "-api";
var CACHE_VENDOR = CACHE_VERSAO + "-vendor";   // /static/vendor/ (mermaid, katex) — cache-first, guardado no 1º uso

var CASCA_APP = ["/", "/index.html"];

var API_EM_CACHE = ["/api/notas", "/api/nota", "/api/indice", "/api/categorias"];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches
      .open(CACHE_APP)
      .then(function (cache) {
        // cache.addAll é tudo-ou-nada — bastava "/index.html" dar 404 (este servidor só serve "/" ou
        // "/static/…", nunca "/index.html" literal) para NENHUM dos dois ficar em cache, e sem isso a "casca"
        // nunca carregava offline. Cada URL entra independente: uma falhar não estraga as outras.
        return Promise.all(CASCA_APP.map(function (u) { return cache.add(u).catch(function () {}); }));
      })
      .catch(function () {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches
      .keys()
      .then(function (nomes) {
        return Promise.all(
          nomes
            .filter(function (n) {
              return n.indexOf("basalt-") === 0 && n !== CACHE_APP && n !== CACHE_API && n !== CACHE_VENDOR;
            })
            .map(function (n) {
              return caches.delete(n);
            })
        );
      })
      .then(function () {
        return self.clients.claim();
      })
  );
});

function ehPedidoApiEmCache(url) {
  for (var i = 0; i < API_EM_CACHE.length; i++) {
    if (url.pathname === API_EM_CACHE[i]) return true;
  }
  return false;
}
function ehPedidoCasca(url) {
  return url.pathname === "/" || url.pathname === "/index.html" || url.pathname.indexOf("/static/") === 0;
}
function ehPedidoVendor(url) {
  return url.pathname.indexOf("/static/vendor/") === 0;
}

// mermaid/katex são grandes e nunca mudam sozinhos — cache-first (guarda no 1º uso, nunca mais pede à rede
// a não ser que falte na cache). Isto corre ANTES do "casca" no listener de fetch, senão o /static/ genérico ganhava.
async function tratarVendor(pedido) {
  var doCache = await caches.match(pedido);
  if (doCache) return doCache;
  var resp = await fetch(pedido);
  if (resp && resp.ok) {
    var copia = resp.clone();
    caches.open(CACHE_VENDOR).then(function (c) { c.put(pedido, copia); }).catch(function () {});
  }
  return resp;
}

// Devolve a resposta da cache com um cabeçalho extra, para a página saber
// que está a mostrar dados antigos e avisar discretamente o utilizador.
function marcarComoCache(resposta) {
  var cabecalhos = new Headers(resposta.headers);
  cabecalhos.set("X-Basalt-From-Cache", "1");
  return new Response(resposta.body, {
    status: resposta.status,
    statusText: resposta.statusText,
    headers: cabecalhos,
  });
}

async function tratarCasca(pedido) {
  try {
    var resp = await fetch(pedido);
    if (resp && resp.ok) {
      var copia = resp.clone();
      caches.open(CACHE_APP).then(function (c) { c.put(pedido, copia); }).catch(function () {});
    }
    return resp;
  } catch (e) {
    var doCache = await caches.match(pedido);
    if (doCache) return marcarComoCache(doCache);
    throw e;
  }
}

function copiaDesligada() {
  return caches.has(MARCA_DESLIGADA).catch(function () { return false; });
}
// as trancas em vigor — [{chave, quando}] ([] = destrancada). Na dúvida (a cache falhou) conta como trancada:
// nunca servir dados privados por não se conseguir ler a marca.
async function trancas() {
  try {
    if (!(await caches.has(MARCA_TRANCADA))) return [];
    var c = await caches.open(MARCA_TRANCADA);
    var chaves = await c.keys();
    var out = [];
    for (var i = 0; i < chaves.length; i++) {
      var q = 0;
      try {
        var r = await c.match(chaves[i]);
        q = r ? Number(await r.text()) || 0 : 0;
      } catch (e) {}
      viuInstante(q);
      out.push({ chave: chaves[i], quando: q });
    }
    return out;
  } catch (e) {
    return [{ chave: null, quando: 0 }];
  }
}
async function copiaTrancada() {
  return (await trancas()).length > 0;
}
// as duas marcas pesam em TODOS os caminhos que servem cópia (sem rede, 502–504) e depois de cada cache.put
async function semCopia() {
  return (await copiaDesligada()) || (await copiaTrancada());
}
async function daCache(pedido) {
  if (await semCopia()) return null;
  var r = await caches.match(pedido, { cacheName: CACHE_API });
  return r ? marcarComoCache(r) : null;
}
async function trancar() {
  try {                                                          // 1.º a marca (entrada própria, com a hora), depois apagar
    await trancas();                                             // ronda 6 G5: vê as trancas já postas — a nova fica acima de todas
    var c = await caches.open(MARCA_TRANCADA);
    var q = agora();
    var id = q.toString(36) + "-" + Math.random().toString(36).slice(2, 8);
    await c.put("/__tranca/" + id, new Response(String(q)));
  } catch (e) {}
  await caches.delete(CACHE_API).catch(function () {});
}
// destranca SÓ as entradas que já existiam quando o pedido partiu. Apaga-se entrada a entrada, pela chave —
// nunca a cache inteira: uma tranca posta no meio fica. ronda 6 G5: «já existia» é estar na fotografia das chaves tirada ao
// partir (quem chama só chega aqui quando TODAS estão), não «hora < inicio» — o relógio pode ter andado para trás.
async function destrancar(lista) {
  try {
    var c = await caches.open(MARCA_TRANCADA);
    for (var i = 0; i < lista.length; i++) {
      if (lista[i].chave) await c.delete(lista[i].chave);
    }
  } catch (e) {}
}
async function marcarApagada(pedido, inicio) {
  try {
    var c = await caches.open(MARCA_APAGADAS), q = agora();
    // ronda 6 (achado M9 da ronda 4): a lápide leva a hora a que o pedido do 404/410 PARTIU (a cópia leva a do seu pedido — é a
    // mesma régua) e nunca desce: uma lápide mais nova já posta fica
    var marca = inicio || q, ja = await c.match(pedido.url);
    if (ja) marca = Math.max(marca, Number(await ja.text()) || 0);
    viuInstante(marca);
    await c.put(pedido.url, new Response(String(marca)));
    var chaves = await c.keys();                                   // as lápides velhas saem (só aqui, num 404 — raro)
    for (var i = 0; i < chaves.length; i++) {
      var r = await c.match(chaves[i]);
      var l = r ? Number(await r.text()) || 0 : 0;
      if (q - l > LAPIDE_DIAS * 86400000) await c.delete(chaves[i]);
    }
  } catch (e) {}
}
async function apagadaDepois(pedido, inicio) {
  try {
    if (!(await caches.has(MARCA_APAGADAS))) return false;
    var r = await (await caches.open(MARCA_APAGADAS)).match(pedido.url);
    if (!r) return false;
    var l = Number(await r.text()) || 0;
    viuInstante(l);
    return inicio <= l;
  } catch (e) {
    return true;                                                   // na dúvida, não guardar
  }
}
// a cópia guardada leva a hora a que o pedido partiu — uma resposta mais ANTIGA que chegue depois (dois pedidos à
// mesma nota, o 1.º atrasado) não passa por cima da mais nova.
function comInicio(resposta, inicio) {
  var cabecalhos = new Headers(resposta.headers);
  cabecalhos.set("X-Basalt-Inicio", String(inicio));
  return new Response(resposta.body, { status: resposta.status, statusText: resposta.statusText, headers: cabecalhos });
}
// ronda 5 (auditoria externa, ronda 4): o «ver se há mais nova» e o «guardar» eram duas operações separadas — duas respostas à
// mesma nota a chegar quase juntas viam as duas a cache vazia e a mais ANTIGA podia ficar por cima. Agora, por URL, cada
// guardar espera pelo anterior (match+put em fila) e a comparação vale mesmo.
var GUARDAR_FILA = {};
function emFila(chave, fn) {
  var anterior = GUARDAR_FILA[chave] || Promise.resolve();
  var p = anterior.catch(function () {}).then(fn);
  GUARDAR_FILA[chave] = p;
  var largar = function () { if (GUARDAR_FILA[chave] === p) delete GUARDAR_FILA[chave]; };
  p.then(largar, largar);
  return p;
}
function guardarSeMaisNova(c, pedido, copia, inicio) {
  return emFila((pedido && pedido.url) || String(pedido), async function () {
    var ja = await c.match(pedido);
    var q = ja ? Number(ja.headers.get("X-Basalt-Inicio")) || 0 : 0;
    viuInstante(q);
    if (q > inicio) return false;
    await c.put(pedido, comInicio(copia, inicio));
    return true;
  });
}
// ronda 6 (achado M9 da ronda 4): um 404/410 só apaga da cache o que lá está se isso partiu ANTES dele — a mesma ordenação dos 200.
// Um 404/410 atrasado (a nota foi apagada, recriada, e um GET mais novo já guardou a recriada) não apaga nada nem põe lápide.
// Na mesma fila por URL que o guardar, para a comparação valer mesmo.
function apagarSeMaisAntiga(c, pedido, inicio) {
  return emFila((pedido && pedido.url) || String(pedido), async function () {
    var ja = await c.match(pedido);
    var q = ja ? Number(ja.headers.get("X-Basalt-Inicio")) || 0 : 0;
    viuInstante(q);
    if (ja && q > inicio) return false;
    await marcarApagada(pedido, inicio);                           // ronda 4 G9: 1.º a lápide (com a hora), depois apagar
    await c.delete(pedido);
    return true;
  });
}

// o redireccionamento para o login vira um 401 de verdade (antes era Response.error(), que a página lia como
// «sem rede» e podia servir a cópia do aparelho)
function respostaLogin() {
  return new Response(JSON.stringify({ erro: "A sessão expirou — entra outra vez.", login: true }), {
    status: 401,
    headers: { "Content-Type": "application/json; charset=utf-8", "X-Basalt-Recusa": "1" },
  });
}

// a cópia só se mostra quando a REDE falha. Se o servidor responde — mesmo que seja a recusar
// (401/403), a dizer que a nota já não existe (404/410) ou a mandar para o login (redireccionamento) — essa
// resposta ganha sempre: nunca se transforma uma recusa num 200 com dados privados guardados.
async function tratarApi(pedido) {
  var resp, antes = chavesDe(await trancas()), inicio = agora();   // ronda 6 G5: as trancas que JÁ existiam quando este pedido partiu
  try {
    // redirect "manual": o 302 para o login chega como "opaqueredirect" em vez de ser seguido para outro domínio e
    // falhar como se fosse falta de rede (era assim que a sessão expirada passava por "sem rede").
    resp = await fetch(new Request(pedido, { redirect: "manual" }));
  } catch (e) {
    var doCache = await daCache(pedido);
    if (doCache) return doCache;
    throw e;
  }
  if (resp.type === "opaqueredirect" || resp.status === 401 || resp.status === 403) {
    await trancar();                                               // sessão expirada / acesso recusado → fora tudo
    return resp.type === "opaqueredirect" ? respostaLogin() : resp;
  }
  if (resp.status === 404 || resp.status === 410) {
    try {                                                          // ronda 6 M9: só apaga o que partiu antes deste pedido (e põe a lápide)
      await apagarSeMaisAntiga(await caches.open(CACHE_API), pedido, inicio);
    } catch (e) {
      await marcarApagada(pedido, inicio);
    }
    return comInicio(resp, inicio);                                // ronda 6 G5: a página ordena com a MESMA hora deste pedido
  }
  if (resp.status === 502 || resp.status === 503 || resp.status === 504) {
    // o servidor do Basalt não está a responder (reinício, queda) — é como estar sem rede
    var doCacheIndisp = await daCache(pedido);
    return doCacheIndisp || resp;
  }
  if (resp.ok) {
    var tipo = resp.headers.get("content-type") || "";
    // um 200 que não seja JSON (a página de login do proxy em HTML, com 200) não prova que a sessão está boa —
    // não destranca nada nem se guarda. Só uma resposta JSON da API conta. (Antes destrancava-se antes de olhar ao tipo.)
    if (tipo.indexOf("application/json") === -1) return resp;
    var lista = await trancas();
    // pedido feito ANTES de trancar (resposta atrasada): não destranca nem se guarda. Feito depois: a sessão voltou.
    // ronda 6 G5: decide-se pelas CHAVES que já existiam ao partir (fotografia), não pela hora; uma tranca ilegível conta como nova.
    for (var i = 0; i < lista.length; i++) if (!antes[chaveDe(lista[i])]) return resp;
    if (lista.length) await destrancar(lista);
    // pedido que partiu ANTES de a nota dar 404/410 (resposta atrasada) — não volta a guardá-la
    if (await apagadaDepois(pedido, inicio)) return resp;
    if (!(await copiaDesligada())) {
      var copia = resp.clone();
      caches.open(CACHE_API)
        .then(function (c) { return guardarSeMaisNova(c, pedido, copia, inicio).then(function () { return c; }); })
        .then(function (c) { return apagadaDepois(pedido, inicio).then(function (ap) { if (ap) return c.delete(pedido); }); })
        .then(function () { return semCopia(); })
        .then(function (marca) { if (marca) return caches.delete(CACHE_API); })   // apagada/trancada a meio → fora
        .catch(function () {});
    }
    return comInicio(resp, inicio);                                // ronda 6 G5: a página ordena com a MESMA hora deste pedido
  }
  return resp;
}

self.addEventListener("fetch", function (event) {
  var pedido = event.request;
  if (pedido.method !== "GET") return; // nunca intercepta POST/PUT — vão sempre direitos à rede

  var url = new URL(pedido.url);
  if (url.origin !== self.location.origin) return;

  if (ehPedidoVendor(url)) {
    event.respondWith(tratarVendor(pedido));
    return;
  }
  if (ehPedidoApiEmCache(url)) {
    if (pedido.mode === "navigate") return;   // abrir /api/… na barra de endereço: o browser trata (e segue o login)
    event.respondWith(tratarApi(pedido));
    return;
  }
  if (ehPedidoCasca(url)) {
    event.respondWith(tratarCasca(pedido));
    return;
  }
  // outros pedidos (estado, grafo, entrada, arrumar, unir…): direto à rede, sem cache
});
