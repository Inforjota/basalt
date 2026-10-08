// Service Worker do Basalt — offline "razoável": mostra a última versão boa
// quando não há rede, nunca esconde uma sessão expirada atrás de dados velhos.
"use strict";

var CACHE_VERSAO = "basalt-v11";      // sobe de versão quando a lógica de cache muda
// ronda 9 (achado 2 da ronda 7): a página só volta a ligar a cópia com um service worker que fale este protocolo (trinco partilhado
// e geração gravada em cada entrada). Um SW antigo (v8, sem gerações) ainda a controlar a página não responde → não religa.
var PROTOCOLO = 3;
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
// ronda 7 (geração): «apagar a cópia» (na página) muda esta marca ANTES de pôr a de desligada. Um pedido que partiu antes
// disso compara-a ao guardar: se mudou, a cópia foi apagada (e talvez religada) enquanto ele corria e a resposta dele é de
// antes — não entra. Antes só se via a marca de desligada no instante de guardar: apagar+religar a meio deixava passar.
var MARCA_GERACAO = "basaltcfg-copia-geracao";
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
// ronda 9 (achado 3 da ronda 7): a fotografia guarda chave → CORPO inteiro. O corpo de uma tranca é «hora.ficha» (9 algarismos ao
// acaso): a mesma chave reposta com a mesma hora já não passa pela de antes. As páginas antigas lêem Number(corpo) ≈ hora.
function chavesDe(lista) { var s = {}; for (var i = 0; i < lista.length; i++) { var u = chaveDe(lista[i]); if (u) s[u] = lista[i].corpo; } return s; }
function horaDe(corpo) { return Math.floor(Number(String(corpo).split(".")[0])) || 0; }
function ficha() { return ("000000000" + Math.floor(Math.random() * 1e9)).slice(-9); }
function corpoTranca(q) { return String(q) + "." + ficha(); }
// ronda 9 (achados 3/4): a chave fixa «/__quando» (páginas de antes da ronda 3) é a única que se repete — nunca se destranca por uma
// resposta; passa (debaixo do trinco) a uma entrada própria «/__tranca/legado-…», que o 200 seguinte já pode destrancar
function ehLegada(u) { return u.indexOf("/__tranca/") === -1; }

// ronda 9 (achados 3/4/8 da ronda 7): UM trinco para tudo o que lê-e-decide ou escreve nas marcas da cópia e na cache da API — o
// Web Lock «basalt-copia», partilhado pela página, pelos outros separadores e por este service worker. Antes cada parte
// comparava uma fotografia e só depois apagava/guardava: outra parte podia trancar, destrancar ou apagar a cópia no meio.
//  - nunca se segura o trinco durante um fetch nem com uma transacção da IndexedDB aberta;
//  - «basalt-fila» (página) pode pedir este trinco; este nunca pede o «basalt-fila» (sem ciclos);
//  - disjuntor: se o trinco não chega em TRINCO_MS (quem o tem ficou pendurado), devolve {ok:false} e quem chamou decide —
//    pôr marcas (trancar, lápide) faz-se mesmo assim; guardar/servir/destrancar desiste.
//  - sem Web Locks (browsers antigos, os ensaios em node): uma cadeia de promessas (só exclui dentro deste contexto).
var TRINCO = "basalt-copia", TRINCO_MS = 3000, TRINCO_CADEIA = Promise.resolve();
function comTrincoCadeia(fn) {
  var p = TRINCO_CADEIA.then(function () { return fn(); }).then(function (v) { return { ok: true, valor: v }; });
  TRINCO_CADEIA = p.catch(function () {});
  return p;
}
function comTrinco(modo, fn) {
  var locks = typeof navigator !== "undefined" && navigator && navigator.locks;
  if (!locks || !locks.request || typeof AbortController !== "function" || typeof setTimeout !== "function") return comTrincoCadeia(fn);
  var ac = new AbortController(), entrou = false;
  var relogio = setTimeout(function () { ac.abort(); }, TRINCO_MS);
  try {
    return locks.request(TRINCO, { mode: modo, signal: ac.signal }, function () {
      entrou = true; clearTimeout(relogio);
      return Promise.resolve().then(fn);
    }).then(function (v) { return { ok: true, valor: v }; }, function (e) {
      clearTimeout(relogio);
      if (entrou) throw e;
      return ac.signal.aborted ? { ok: false } : comTrincoCadeia(fn);   // o pedido do trinco falhou por outra razão → cadeia
    });
  } catch (e) {
    clearTimeout(relogio);
    return comTrincoCadeia(fn);
  }
}
var CACHE_APP = CACHE_VERSAO + "-app";
var CACHE_API = CACHE_VERSAO + "-api";
var CACHE_VENDOR = CACHE_VERSAO + "-vendor";   // /static/vendor/ (mermaid, katex) — cache-first, guardado no 1º uso

var CASCA_APP = ["/", "/index.html"];

var API_EM_CACHE = ["/api/notas", "/api/nota", "/api/indice", "/api/categorias"];

// ronda 9 (achado 2): a página pergunta o protocolo antes de religar a cópia (MessageChannel)
self.addEventListener("message", function (event) {
  var d = event && event.data;
  if (d && d.tipo === "protocolo" && event.ports && event.ports[0]) event.ports[0].postMessage({ protocolo: PROTOCOLO, versao: CACHE_VERSAO });
});

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
        return comTrinco("exclusive", converterLegadas).catch(function () {});   // ronda 9: «/__quando» → entrada própria
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
// ronda 7 (geração): "" = nunca apagada; na dúvida (a cache falhou) um valor que nunca é igual — não se guarda
async function geracaoDaCopia() {
  try {
    if (!(await caches.has(MARCA_GERACAO))) return "";
    var r = await (await caches.open(MARCA_GERACAO)).match("/__geracao");
    return r ? await r.text() : "";
  } catch (e) {
    return "?" + Math.random();
  }
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
      var corpo = "";
      try {
        var r = await c.match(chaves[i]);
        corpo = r ? await r.text() : "";
      } catch (e) {}
      var q = horaDe(corpo);
      viuInstante(q);
      out.push({ chave: chaves[i], quando: q, corpo: corpo });
    }
    return out;
  } catch (e) {
    return [{ chave: null, quando: 0, corpo: "" }];
  }
}
// ronda 9: as trancas em vigor são TODAS da fotografia tirada quando o pedido partiu (chave e corpo iguais)? Uma ilegível
// (sem chave) ou legada conta como nova.
function soTrancasDeAntes(lista, antes) {
  for (var i = 0; i < lista.length; i++) {
    var u = chaveDe(lista[i]);
    if (!u || ehLegada(u) || antes[u] !== lista[i].corpo) return false;
  }
  return true;
}
// ronda 9: só debaixo do trinco. Devolve true se havia alguma legada (quem chama não destranca nada nessa volta).
async function converterLegadas() {
  var lista = await trancas(), mudou = false;
  for (var i = 0; i < lista.length; i++) {
    var u = chaveDe(lista[i]);
    if (!u || !ehLegada(u)) continue;
    var c = await caches.open(MARCA_TRANCADA);
    await c.put("/__tranca/legado-" + ficha(), new Response(corpoTranca(lista[i].quando || agora())));   // 1.º a nova, depois sai a velha
    await c.delete(lista[i].chave);
    mudou = true;
  }
  return mudou;
}
async function copiaTrancada() {
  return (await trancas()).length > 0;
}
// as duas marcas pesam em TODOS os caminhos que servem cópia (sem rede, 502–504) e depois de cada cache.put
async function semCopia() {
  return (await copiaDesligada()) || (await copiaTrancada());
}
// ronda 9: serve-se com o trinco partilhado (ninguém tranca/apaga a meio) e só uma entrada gravada na geração ACTUAL da cópia
// (X-Basalt-Geracao) — uma resposta de antes de «apagar a cópia» que tenha entrado por outro caminho não se mostra.
// Trinco preso → não serve (na dúvida, nada de dados privados).
async function daCache(pedido) {
  var r = await comTrinco("shared", async function () {
    if (await semCopia()) return null;
    var g = await geracaoDaCopia();
    var e = await caches.match(pedido, { cacheName: CACHE_API });
    if (!e || e.headers.get("X-Basalt-Geracao") !== g) return null;
    return marcarComoCache(e);
  });
  return r.ok ? r.valor : null;
}
async function trancar() {
  var fazer = async function () {
    try {                                                        // 1.º a marca (entrada própria, com a hora), depois apagar
      await trancas();                                           // ronda 6 G5: vê as trancas já postas — a nova fica acima de todas
      var c = await caches.open(MARCA_TRANCADA);
      var q = agora();
      var id = q.toString(36) + "-" + Math.random().toString(36).slice(2, 8);
      await c.put("/__tranca/" + id, new Response(corpoTranca(q)));   // ronda 9: hora + ficha
    } catch (e) {}
    await caches.delete(CACHE_API).catch(function () {});
  };
  var r = await comTrinco("exclusive", fazer);
  if (!r.ok) await fazer();                                      // ronda 9: trinco preso → tranca-se na mesma (só acrescenta)
}
// destranca SÓ as entradas que já existiam quando o pedido partiu. Apaga-se entrada a entrada, pela chave —
// nunca a cache inteira: uma tranca posta no meio fica. ronda 6 G5: «já existia» é estar na fotografia das chaves tirada ao
// partir (quem chama só chega aqui quando TODAS estão), não «hora < inicio» — o relógio pode ter andado para trás.
// ronda 9 (achado 4): só se chama debaixo do trinco, logo a seguir a comparar — ninguém troca a tranca entre comparar e apagar.
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
    var c = await caches.open(MARCA_APAGADAS), q = agora(), real = Date.now();   // ronda 7: a idade conta-se no relógio real
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
      if (real - l > LAPIDE_DIAS * 86400000) await c.delete(chaves[i]);
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
// ronda 9: a entrada guardada leva a hora a que o pedido partiu (ronda 4: uma resposta mais ANTIGA que chegue depois não passa
// por cima da mais nova) e a geração da cópia com que partiu (daCache só serve a da geração actual)
function entradaApi(resposta, corpo, inicio, geracao) {
  var cabecalhos = new Headers(resposta.headers);
  cabecalhos.set("X-Basalt-Inicio", String(inicio));
  cabecalhos.set("X-Basalt-Geracao", geracao);
  return new Response(corpo, { status: resposta.status, statusText: resposta.statusText, headers: cabecalhos });
}
// ronda 9 (achados 3/4/8 da ronda 7): a resposta 200 de um pedido — comparar as trancas, guardar e destrancar, tudo debaixo do
// trinco exclusivo. Substitui a fila por URL (emFila) e a limpeza a seguir (limparSeFicouVelha + caches.delete): essas
// decidiam com o que tinham lido antes e podiam apagar uma cache mais nova inteira (#8). O corpo lê-se ANTES de pedir o
// trinco (nunca se segura o trinco à espera da rede). Ordem: 1.º guardar, depois destrancar (se o SW morrer no meio fica
// trancada — o lado seguro). Guarda só se: a cópia está ligada, a geração é a do pedido, a nota não levou lápide depois de o
// pedido partir e o que lá está não é de um pedido mais novo. Trinco preso → não guarda nem destranca.
async function aceitar(pedido, corpo, resp, inicio, foto) {
  var r = await comTrinco("exclusive", async function () {
    if (await converterLegadas()) return false;
    var lista = await trancas();
    if (!soTrancasDeAntes(lista, foto.trancas)) return false;     // tranca posta depois de o pedido partir (ou ilegível)
    var guardou = false;
    if (!(await copiaDesligada()) && (await geracaoDaCopia()) === foto.geracao && !(await apagadaDepois(pedido, inicio))) {
      var c = await caches.open(CACHE_API), ja = await c.match(pedido);
      var q = ja ? Number(ja.headers.get("X-Basalt-Inicio")) || 0 : 0;
      viuInstante(q);
      if (!(ja && q > inicio)) { await c.put(pedido, entradaApi(resp, corpo, inicio, foto.geracao)); guardou = true; }
    }
    if (lista.length) await destrancar(lista);
    return guardou;
  });
  return r.ok ? r.valor : false;
}
// ronda 6 (achado M9 da ronda 4): um 404/410 só apaga da cache o que lá está se isso partiu ANTES dele — a mesma ordenação dos 200.
// Um 404/410 atrasado (a nota foi apagada, recriada, e um GET mais novo já guardou a recriada) não apaga nada nem põe lápide.
// ronda 9: debaixo do trinco (antes: na fila por URL); trinco preso → faz-se na mesma (só tira dados e põe a lápide).
async function tirarNota(pedido, inicio) {
  var fazer = async function () {
    try {
      var c = await caches.open(CACHE_API), ja = await c.match(pedido);
      var q = ja ? Number(ja.headers.get("X-Basalt-Inicio")) || 0 : 0;
      viuInstante(q);
      if (ja && q > inicio) return false;
      await marcarApagada(pedido, inicio);                         // ronda 4 G9: 1.º a lápide (com a hora), depois apagar
      await c.delete(pedido);
      return true;
    } catch (e) {
      await marcarApagada(pedido, inicio);
      return false;
    }
  };
  var r = await comTrinco("exclusive", fazer);
  if (!r.ok) await fazer();
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
// ronda 8 (contas no mesmo browser): entrar e sair mudam a conta do browser. 1.º muda a geração (um pedido que partiu antes
// não guarda nada), depois a cópia da API sai — antes de o pedido seguir e outra vez quando ele volta (um GET que tenha
// partido no meio, ainda com a conta anterior, também não fica). O pedido segue para a rede tal e qual.
// ronda 9: debaixo do trinco (nada a meio de um «aceitar»); trinco preso → faz-se na mesma (só tira dados).
async function mudouDeConta() {
  var fazer = async function () {
    try {
      await (await caches.open(MARCA_GERACAO)).put("/__geracao", new Response("conta-" + agora() + "-" + Math.random().toString(36).slice(2, 10)));
    } catch (e) {}
    await caches.delete(CACHE_API).catch(function () {});
  };
  var r = await comTrinco("exclusive", fazer);
  if (!r.ok) await fazer();
}
async function tratarSaidaEntrada(pedido) {
  await mudouDeConta();
  try {
    return await fetch(pedido);
  } finally {
    await mudouDeConta();
  }
}

// ronda 9: `manter` recebe o trabalho que continua depois de a resposta seguir (guardar/destrancar) — no fetch é o waitUntil,
// para o browser não parar o service worker a meio.
async function tratarApi(pedido, manter) {
  // ronda 6 G5 / ronda 9: a fotografia (sem trinco — só se lê) das trancas que JÁ existiam e da geração da cópia ao partir
  var foto = { geracao: await geracaoDaCopia(), trancas: chavesDe(await trancas()) };
  var resp, inicio = agora();
  try {
    // redirect "manual": o 302 para o login chega como "opaqueredirect" em vez de ser seguido para outro domínio e
    // falhar como se fosse falta de rede (era assim que a sessão expirada passava por "sem rede").
    resp = await fetch(new Request(pedido, { redirect: "manual" }));
  } catch (e) {
    var doCache = await daCache(pedido);
    if (doCache) return doCache;
    throw e;
  }
  // ronda 8: «entrou outra conta neste browser» não é sessão expirada — a página pede para recarregar; não se tranca nada
  if (resp.status === 403 && resp.headers.get("X-Basalt-Outra-Conta") === "1") return resp;
  if (resp.type === "opaqueredirect" || resp.status === 401 || resp.status === 403) {
    await trancar();                                               // sessão expirada / acesso recusado → fora tudo
    return resp.type === "opaqueredirect" ? respostaLogin() : resp;
  }
  if (resp.status === 404 || resp.status === 410) {
    await tirarNota(pedido, inicio);                               // ronda 6 M9: só apaga o que partiu antes deste pedido (e põe a lápide)
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
    // pedido feito ANTES de trancar (resposta atrasada): não destranca nem se guarda. Feito depois: a sessão voltou.
    // ronda 6 G5: decide-se pelas trancas que já existiam ao partir (fotografia), não pela hora. ronda 4 G9: um pedido que partiu
    // antes de a nota dar 404/410 não a volta a guardar. ronda 9: tudo isso (e guardar) numa só secção com trinco — aceitar.
    var copia = resp.clone();
    (manter || function () {})(copia.arrayBuffer()
      .then(function (corpo) { return aceitar(pedido, corpo, copia, inicio, foto); })
      .catch(function () { return false; }));
    return comInicio(resp, inicio);                                // ronda 6 G5: a página ordena com a MESMA hora deste pedido
  }
  return resp;
}

self.addEventListener("fetch", function (event) {
  var pedido = event.request;
  if (pedido.method === "POST") {
    // ronda 8: entrar/sair — a resposta vem da rede, só se limpa a cópia da API à volta (ver tratarSaidaEntrada)
    var urlConta = new URL(pedido.url);
    if (urlConta.origin === self.location.origin && (urlConta.pathname === "/api/sair" || urlConta.pathname === "/api/entrar")) {
      event.respondWith(tratarSaidaEntrada(pedido));
    }
    return;
  }
  if (pedido.method !== "GET") return; // nunca intercepta POST/PUT — vão sempre direitos à rede

  var url = new URL(pedido.url);
  if (url.origin !== self.location.origin) return;

  if (ehPedidoVendor(url)) {
    event.respondWith(tratarVendor(pedido));
    return;
  }
  if (ehPedidoApiEmCache(url)) {
    if (pedido.mode === "navigate") return;   // abrir /api/… na barra de endereço: o browser trata (e segue o login)
    // ronda 9: guardar/destrancar continua depois de a resposta seguir — o waitUntil (pedido JÁ, durante o evento) segura o
    // service worker vivo até isso acabar; antes era uma promessa solta que o browser podia cortar a meio
    var trabalhos = [];
    var resposta = tratarApi(pedido, function (p) { trabalhos.push(p); });
    event.respondWith(resposta);
    var fim = function () { return Promise.all(trabalhos).catch(function () {}); };
    if (event.waitUntil) event.waitUntil(resposta.then(fim, fim));
    return;
  }
  if (ehPedidoCasca(url)) {
    event.respondWith(tratarCasca(pedido));
    return;
  }
  // outros pedidos (estado, grafo, entrada, arrumar, unir…): direto à rede, sem cache
});
