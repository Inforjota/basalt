// Service Worker do Basalt — offline "razoável": mostra a última versão boa
// quando não há rede, nunca esconde uma sessão expirada atrás de dados velhos.
"use strict";

var CACHE_VERSAO = "basalt-v6";      // sobe de versão quando a lógica de cache muda
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
    var c = await caches.open(MARCA_TRANCADA);
    var agora = Date.now();
    var id = agora.toString(36) + "-" + Math.random().toString(36).slice(2, 8);
    await c.put("/__tranca/" + id, new Response(String(agora)));
  } catch (e) {}
  await caches.delete(CACHE_API).catch(function () {});
}
// destranca SÓ as entradas que já existiam quando o pedido partiu (hora < inicio; sem hora = antiga, conta
// como anterior). Apaga-se entrada a entrada, pela chave — nunca a cache inteira: uma tranca posta no meio fica.
async function destrancarAnteriores(lista, inicio) {
  try {
    var c = await caches.open(MARCA_TRANCADA);
    for (var i = 0; i < lista.length; i++) {
      if (lista[i].chave && lista[i].quando < inicio) await c.delete(lista[i].chave);
    }
  } catch (e) {}
}
async function marcarApagada(pedido) {
  try {
    var c = await caches.open(MARCA_APAGADAS), agora = Date.now();
    await c.put(pedido.url, new Response(String(agora)));
    var chaves = await c.keys();                                   // as lápides velhas saem (só aqui, num 404 — raro)
    for (var i = 0; i < chaves.length; i++) {
      var r = await c.match(chaves[i]);
      var q = r ? Number(await r.text()) || 0 : 0;
      if (agora - q > LAPIDE_DIAS * 86400000) await c.delete(chaves[i]);
    }
  } catch (e) {}
}
async function apagadaDepois(pedido, inicio) {
  try {
    if (!(await caches.has(MARCA_APAGADAS))) return false;
    var r = await (await caches.open(MARCA_APAGADAS)).match(pedido.url);
    if (!r) return false;
    return inicio <= (Number(await r.text()) || 0);
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
async function guardarSeMaisNova(c, pedido, copia, inicio) {
  var ja = await c.match(pedido);
  var q = ja ? Number(ja.headers.get("X-Basalt-Inicio")) || 0 : 0;
  if (q > inicio) return false;
  await c.put(pedido, comInicio(copia, inicio));
  return true;
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
  var resp, inicio = Date.now();
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
    await marcarApagada(pedido);                                   // 1.º a lápide (com a hora), depois apagar
    await caches.open(CACHE_API).then(function (c) { return c.delete(pedido); }).catch(function () {});
    return resp;
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
    var lista = await trancas(), maisNova = 0;
    for (var i = 0; i < lista.length; i++) if (lista[i].quando > maisNova) maisNova = lista[i].quando;
    // pedido feito ANTES de trancar (resposta atrasada): não destranca nem se guarda. Feito depois: a sessão voltou.
    if (lista.length && inicio <= maisNova) return resp;
    if (lista.length) await destrancarAnteriores(lista, inicio);
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
