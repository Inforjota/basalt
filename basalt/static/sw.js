// Service Worker do Basalt — offline "razoável": mostra a última versão boa
// quando não há rede, nunca esconde uma sessão expirada atrás de dados velhos.
"use strict";

var CACHE_VERSAO = "basalt-v15";      // sobe de versão quando a lógica de cache muda
// ronda 9 (achado 2 da ronda 7): a página só volta a ligar a cópia com um service worker que fale este protocolo (trinco partilhado
// e geração gravada em cada entrada). Um SW antigo (v8, sem gerações) ainda a controlar a página não responde → não religa.
// Protocolo 4 (ronda 9, achado G1 da ronda 2): uma cache da API por dono — um SW de protocolo 3 ainda tinha a cache comum.
// Protocolo 5 (ronda 13): a época da cópia (ver EPOCA). Uma página aberta de protocolo 4 recarrega quando este SW a passa a
// controlar (o código dela aceitava respostas revogadas a meio); se não recarregar (alterações por gravar no editor), já não
// recebe a cópia da API deste SW: só se serve a um pedido com «X-Basalt-Protocolo: 5» — sem rede, ela cai na sua IndexedDB.
var PROTOCOLO = 5;
// «Apagar cópia deste aparelho» cria esta cache-marca (não começa por "basalt-", por isso sobrevive à limpeza
// do activate) — enquanto existir, nenhuma resposta da API volta a ser guardada neste aparelho.
var MARCA_DESLIGADA = "basaltcfg-copia-desligada";
// sessão expirada / acesso recusado (criada aqui ou pela página) — enquanto tiver ENTRADAS, a cópia da API não se
// serve, nem sem rede. cada trancar é uma entrada própria ("/__tranca/<id>", corpo = hora); destrancar apaga só
// as entradas anteriores ao pedido que trouxe a resposta boa, nunca a cache inteira — um trancar mais novo (de outro
// pedido ou separador, no meio) fica. A entrada antiga "/__quando" (página/sw anteriores) conta como mais uma.
var MARCA_TRANCADA = "basaltcfg-copia-trancada";
// lápides — quando uma nota dá 404/410 fica aqui a hora. Uma resposta 200 que tenha PARTIDO antes dessa hora (pedido atrasado)
// já não entra na cache. Não começa por "basalt-": sobrevive ao activate. Ronda 11: cada 404/410 escreve a SUA entrada
// («/__lapide/<URL codificado>/<hora>») e a lápide que vale é a maior. Ronda 13: a entrada antiga (chave = o URL) só se lê
// (as de antes contam como chão); já não se escreve (ver marcarApagada).
var MARCA_APAGADAS = "basaltcfg-apagadas";
// ronda 7 (geração): «apagar a cópia» (na página) muda esta marca ANTES de pôr a de desligada. Um pedido que partiu antes
// disso compara-a ao guardar: se mudou, a cópia foi apagada (e talvez religada) enquanto ele corria e a resposta dele é de
// antes — não entra. Antes só se via a marca de desligada no instante de guardar: apagar+religar a meio deixava passar.
var MARCA_GERACAO = "basaltcfg-copia-geracao";
// ronda 13 (época da cópia): uma resposta da cópia já LIDA (a entrada, ou o corpo na página) não se revoga apagando a cache — um 404,
// um 401 ou «apagar a cópia» que acabassem no meio deixavam-na ser entregue na mesma. Quem revoga muda a época ANTES de mexer
// (EPOCA em memória, já, sem esperar; e a marca persistente «/__epoca», para a página e os outros SW) e, no fim, avisa cada
// página e espera que ela confirme (avisarPaginas). Quem entrega compara a época de quando começou com a de agora, sem nenhum
// await entre a comparação e a entrega — se mudou, volta a ver (a 2.ª vez desiste).
var MARCA_EPOCA = "basaltcfg-epoca";
var EPOCA = 0;
var AVISO_MS = 1000;            // quanto se espera pela confirmação de cada página (uma página antiga não responde)
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
var CACHE_API = CACHE_VERSAO + "-api";        // + "-" + dono: uma por conta (ver cacheApiDe)
var CACHE_VENDOR = CACHE_VERSAO + "-vendor";   // /static/vendor/ (mermaid, katex) — cache-first, guardado no 1º uso

var CASCA_APP = ["/", "/index.html"];

// ronda 13: e a vista só de leitura de uma fonte externa (as mesmas regras: lápide no 404/410, tranca no 401, época)
var API_EM_CACHE = ["/api/notas", "/api/nota", "/api/indice", "/api/categorias", "/api/fonte"];

// ronda 9 (achado G1 da ronda 2): a cache da API é uma POR DONO (a conta + espaço da cópia, que a página manda em X-Basalt-Dono).
// Dois separadores de contas diferentes no mesmo browser nunca se servem um ao outro: o pedido de A só lê a cache de A.
// Guarda-se só se o pedido traz o dono E o servidor devolve o mesmo (ele confere-o com a sessão); sem dono, nem guarda nem serve.
function donoDoPedido(pedido) {
  var d = (pedido && pedido.headers && pedido.headers.get && pedido.headers.get("X-Basalt-Dono")) || "";
  return /^[A-Za-z0-9_-]{1,64}$/.test(d) ? d : "";
}
function cacheApiDe(dono) {
  return CACHE_API + "-" + dono;
}
function ehCacheApi(n) {
  return /^basalt-.*-api(-[A-Za-z0-9_-]+)?$/.test(n);
}
// trancar, mudar de conta: fora a cópia da API de TODAS as contas (a de cada versão antiga sai no activate)
async function apagarCachesApi() {
  try {
    var nomes = await caches.keys();
    for (var i = 0; i < nomes.length; i++) {
      if (ehCacheApi(nomes[i])) await caches.delete(nomes[i]);
    }
  } catch (e) {}
}

// ronda 13 (época da cópia): a persistente — "" = nunca mudou; na dúvida (a cache falhou) um valor que nunca é igual (não se entrega)
async function lerEpoca() {
  try {
    if (!(await caches.has(MARCA_EPOCA))) return "";
    var r = await (await caches.open(MARCA_EPOCA)).match("/__epoca");
    return r ? await r.text() : "";
  } catch (e) {
    return "?" + Math.random();
  }
}
// a de memória muda JÁ (na chamada, antes de qualquer await de quem chama); a persistente a seguir (hora.ficha: nunca se repete)
function mudarEpoca() {
  EPOCA++;
  return caches.open(MARCA_EPOCA)
    .then(function (c) { return c.put("/__epoca", new Response(agora() + "." + ficha())); })
    .catch(function () {});
}
// cada página (também as que este SW ainda não controla) recebe {tipo:"revogada"} e responde pelo canal; espera-se pela resposta
// de todas, até AVISO_MS cada (uma página de antes desta ronda, ou parada, não responde). Quando isto acaba, nenhuma página que
// tenha respondido entrega uma resposta da cópia lida antes: a época dela já mudou.
async function avisarPaginas() {
  try {
    var lista = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    await Promise.all(lista.map(function (cliente) {
      return new Promise(function (feito) {
        var relogio = setTimeout(feito, AVISO_MS);
        try {
          var canal = new MessageChannel();
          canal.port1.onmessage = function () { clearTimeout(relogio); feito(); };
          cliente.postMessage({ tipo: "revogada" }, [canal.port2]);
        } catch (e) {
          clearTimeout(relogio);
          feito();
        }
      });
    }));
  } catch (e) {}
}
async function revogada() {
  await mudarEpoca();
  await avisarPaginas();
}

// ronda 9 (achado 2): a página pergunta o protocolo antes de religar a cópia (MessageChannel)
// ronda 13: e avisa quando revogou a cópia ela própria (trancou, apagou a cópia, mudou de conta) — muda a época, avisa as outras
// páginas e só então responde
self.addEventListener("message", function (event) {
  var d = event && event.data;
  if (d && d.tipo === "protocolo" && event.ports && event.ports[0]) event.ports[0].postMessage({ protocolo: PROTOCOLO, versao: CACHE_VERSAO });
  if (d && d.tipo === "revogar") {
    var fim = revogada().catch(function () {}).then(function () {
      if (event.ports && event.ports[0]) event.ports[0].postMessage({ ok: true });
    });
    if (event.waitUntil) event.waitUntil(fim);
  }
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
              return n.indexOf("basalt-") === 0 && n !== CACHE_APP && n !== CACHE_VENDOR &&
                !(n.indexOf(CACHE_API + "-") === 0 && ehCacheApi(n));   // ronda 9: as desta versão (uma por dono) ficam
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
// ronda 13: uma resposta da cópia da API leva também o protocolo e a época com que foi entregue (a página volta a ler a época
// depois de ler o corpo e compara-a com esta)
function marcarComoCache(resposta, epoca) {
  var cabecalhos = new Headers(resposta.headers);
  cabecalhos.set("X-Basalt-From-Cache", "1");
  if (epoca !== undefined) {
    cabecalhos.set("X-Basalt-Protocolo", String(PROTOCOLO));
    cabecalhos.set("X-Basalt-Epoca", epoca);
  }
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
// Trinco preso → não serve (na dúvida, nada de dados privados). ronda 9 (achado G1 da ronda 2): só da cache do dono do pedido.
// ronda 9 (achados 2 e 3 da ronda 8): a entrada só se serve se a nota não levou lápide DEPOIS de ela partir (um 200 atrasado que
// entrou depois de um 404, com o trinco preso, não volta a mostrar a nota apagada) e se, DEPOIS de a ler, a cópia continua
// ligada, destrancada e na mesma geração («apagar a cópia» com o trinco preso pode correr enquanto o cache.match está a meio).
// ronda 11: um 404/410 ou um 401 pelo disjuntor (trinco preso por esta leitura) podiam entrar DEPOIS de se ver a lápide e antes
// de se entregar — nas últimas leituras da geração/marcas — e a entrada, já apagada da cache, era entregue na mesma. Agora a
// ÚLTIMA leitura é a da própria entrada: só se entrega se ainda lá estiver, igual (mesma hora de partida, geração e dono). Quem
// apaga a nota, tranca ou apaga a cópia tira a entrada da cache depois de pôr a sua marca; se o fez antes desta releitura, ela
// já não está, e se o fez depois, a entrega aconteceu antes dele.
function mesmaEntrada(a, b) {
  return !!(a && b) && ["X-Basalt-Inicio", "X-Basalt-Geracao", "X-Basalt-Dono"].every(function (h) {
    return a.headers.get(h) === b.headers.get(h);
  });
}
// ronda 13 (achado 1 da revisão externa r10): a entrada lida na última releitura é uma Response já em mão — um 404 pelo
// disjuntor que acabasse (lápide + apagar) com essa leitura a meio, ou que pusesse a lápide depois das verificações e apagasse a
// seguir, deixava-a ser entregue na mesma. Agora cada volta guarda a época ao começar (a de memória, que um 404/401/troca de conta
// deste SW muda logo ao chegar, e a persistente, que a página muda antes de trancar ou apagar), relê a persistente antes da
// releitura da entrada (que continua a ser a última leitura) e, DEPOIS dela, só entrega se nenhuma mudou — entre essa comparação e
// o return não há await. Mudou → outra volta (a 2.ª desiste). A resposta leva a época lida (X-Basalt-Epoca): a página compara-a
// com a que lê depois do corpo, e assim o que o SW (ou outro separador) revogar até ela entregar também conta.
// E só se serve a um pedido de uma página deste protocolo.
var MUDOU = {};
async function daCache(pedido) {
  var dono = donoDoPedido(pedido);
  if (!dono) return null;
  var prot = (pedido.headers && pedido.headers.get && pedido.headers.get("X-Basalt-Protocolo")) || "";
  if (prot !== String(PROTOCOLO)) return null;
  for (var volta = 0; volta < 2; volta++) {
    var e0 = EPOCA, p0 = await lerEpoca();
    var r = await comTrinco("shared", async function () {
      if (await semCopia()) return null;
      var g = await geracaoDaCopia();
      var e = await caches.match(pedido, { cacheName: cacheApiDe(dono) });
      if (!e || e.headers.get("X-Basalt-Geracao") !== g || e.headers.get("X-Basalt-Dono") !== dono) return null;
      if (await apagadaDepois(pedido, Number(e.headers.get("X-Basalt-Inicio")) || 0)) return null;
      if ((await semCopia()) || (await geracaoDaCopia()) !== g) return null;
      var p1 = await lerEpoca();                                               // ronda 13: a época persistente, antes da releitura
      var e2 = await caches.match(pedido, { cacheName: cacheApiDe(dono) });   // ronda 11: a entrada ainda lá está, igual — o ÚLTIMO await
      if (!mesmaEntrada(e, e2)) return null;
      if (EPOCA !== e0 || p1 !== p0) return MUDOU;                           // ronda 13: e nada mudou entretanto (sem await até ao return)
      return marcarComoCache(e2, p1);
    });
    if (!r.ok) return null;
    if (r.valor !== MUDOU) return r.valor;
  }
  return null;
}
// ronda 13: trancar, tirar uma nota e mudar de conta mudam a época ao começar (antes de qualquer await: EPOCA++ já na chamada) e
// avisam as páginas no fim (revogada), depois de largar o trinco
async function trancar() {
  await mudarEpoca();
  var fazer = async function () {
    try {                                                        // 1.º a marca (entrada própria, com a hora), depois apagar
      await trancas();                                           // ronda 6 G5: vê as trancas já postas — a nova fica acima de todas
      var c = await caches.open(MARCA_TRANCADA);
      var q = agora();
      var id = q.toString(36) + "-" + Math.random().toString(36).slice(2, 8);
      await c.put("/__tranca/" + id, new Response(corpoTranca(q)));   // ronda 9: hora + ficha
    } catch (e) {}
    await apagarCachesApi();                                     // ronda 9: as de todas as contas
  };
  var r = await comTrinco("exclusive", fazer);
  if (!r.ok) await fazer();                                      // ronda 9: trinco preso → tranca-se na mesma (só acrescenta)
  await revogada();
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
// ronda 10 (achado M3 da r3): as lápides eram comuns às contas (chave = só o URL) — um 404 de B ao URL «comum» deixava de servir,
// sem rede, a cópia de A desse URL. Agora cada dono tem as suas («basaltcfg-apagadas-<dono>»); só um pedido SEM dono põe uma
// lápide na loja comum, que continua a valer para todas (o lado seguro: na dúvida não se serve).
function lojaLapides(dono) {
  return dono ? MARCA_APAGADAS + "-" + dono : MARCA_APAGADAS;
}
// ronda 11: as entradas por instante de uma nota — «/__lapide/<URL codificado>/<hora>». O encodeURIComponent codifica as «/» do
// URL, por isso a hora é o último pedaço da chave; o «/» final do prefixo separa «nome=n1» de «nome=n10».
function prefixoLapide(url) {
  return "/__lapide/" + encodeURIComponent(url) + "/";
}
function horaDaChave(k, prefixo) {
  var u = chaveDe({ chave: k }), i = u.indexOf(prefixo);
  return i === -1 ? null : Number(u.slice(i + prefixo.length)) || 0;
}
// ronda 11: a lápide de uma nota numa loja = a MAIOR entre a entrada antiga (chave = o URL) e as entradas por instante (0 = nenhuma).
// As por instante só se acrescentam, e a poda só tira as que têm outra MAIOR da mesma nota — a maior nunca sai. A varredura
// repete-se (até 3 vezes) até duas seguidas darem o mesmo, e fica a maior de tudo o que se viu.
async function lapideDe(c, url) {
  var prefixo = prefixoLapide(url), maior = 0, antes = null;
  var r = await c.match(url);
  if (r) maior = Number(await r.text()) || 0;
  for (var volta = 0; volta < 3; volta++) {
    var chaves = await c.keys(), estaVolta = 0;
    for (var i = 0; i < chaves.length; i++) {
      var h = horaDaChave(chaves[i], prefixo);
      if (h !== null && h > estaVolta) estaVolta = h;
    }
    if (estaVolta > maior) maior = estaVolta;
    if (antes !== null && estaVolta === antes) break;
    antes = estaVolta;
  }
  viuInstante(maior);
  return maior;
}
// ronda 11 (lápide que recuava): ler-o-máximo-e-escrever na MESMA chave deixava um 404 mais antigo, suspenso entre ler e escrever
// (pelo disjuntor do trinco, dois 404 correm sem trinco), escrever a sua hora por cima da de um 404 mais novo — e um 200 entre os
// dois ficava guardado e servia-se sem rede. Agora 1.º a entrada própria deste 404 (só acrescenta: nada a faz recuar) e por fim
// a poda. Ronda 13 (achado 5 da revisão externa do porte): a entrada antiga (chave = o URL) também se reescrevia aqui com o máximo
// visto — e recuava da mesma maneira (um 404 de 10 suspenso escrevia 10 por cima dos 30). Já não se escreve: as por instante
// bastam a este SW, e uma entrada antiga de antes continua a ler-se como chão (lapideDe) até a poda dos 30 dias a tirar.
async function marcarApagada(pedido, inicio) {
  try {
    var c = await caches.open(lojaLapides(donoDoPedido(pedido))), q = agora(), real = Date.now();   // ronda 7: a idade conta-se no relógio real
    // ronda 6 (achado M9 da ronda 4): a lápide leva a hora a que o pedido do 404/410 PARTIU (a cópia leva a do seu pedido — é a
    // mesma régua) e nunca desce: uma lápide mais nova já posta fica
    var marca = inicio || q, prefixo = prefixoLapide(pedido.url);
    viuInstante(marca);
    await c.put(prefixo + marca, new Response(String(marca)));
    // poda (só aqui, num 404 — raro): saem as lápides com mais de LAPIDE_DIAS e, desta nota, as por instante abaixo da maior
    // por instante vista (a maior fica sempre; quem a vir mais alta não tira a dela, por isso a maior de todas nunca sai)
    var chaves = await c.keys(), daNota = 0;
    for (var i = 0; i < chaves.length; i++) {
      var h = horaDaChave(chaves[i], prefixo);
      if (h !== null && h > daNota) daNota = h;
    }
    for (var j = 0; j < chaves.length; j++) {
      var hj = horaDaChave(chaves[j], prefixo);
      if (hj !== null && hj < daNota) { await c.delete(chaves[j]); continue; }
      var r = await c.match(chaves[j]);
      var l = r ? Number(await r.text()) || 0 : 0;
      if (real - l > LAPIDE_DIAS * 86400000) await c.delete(chaves[j]);
    }
  } catch (e) {}
}
// ronda 10 (M3): vê a loja do dono do pedido e a comum (lápides sem dono e as de antes desta ronda, que eram de todos)
async function apagadaDepois(pedido, inicio) {
  try {
    var dono = donoDoPedido(pedido), lojas = dono ? [lojaLapides(dono), MARCA_APAGADAS] : [MARCA_APAGADAS], depois = false;
    for (var i = 0; i < lojas.length; i++) {
      if (!(await caches.has(lojas[i]))) continue;
      var l = await lapideDe(await caches.open(lojas[i]), pedido.url);   // ronda 11: a maior (entrada antiga e por instante)
      if (l && inicio <= l) depois = true;                         // continua: viuInstante vê as duas horas
    }
    return depois;
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
// ronda 9 (achado G1 da ronda 2): guarda na cache do dono do pedido (foto.dono) e só se a resposta traz o mesmo dono; sem ele não
// guarda — mas destranca na mesma (um 200 JSON prova que a sessão voltou, seja de que conta for).
async function aceitar(pedido, corpo, resp, inicio, foto) {
  var dono = foto.dono && resp.headers.get("X-Basalt-Dono") === foto.dono ? foto.dono : "";
  var r = await comTrinco("exclusive", async function () {
    if (await converterLegadas()) return false;
    var lista = await trancas();
    if (!soTrancasDeAntes(lista, foto.trancas)) return false;     // tranca posta depois de o pedido partir (ou ilegível)
    var guardou = false;
    if (dono && !(await copiaDesligada()) && (await geracaoDaCopia()) === foto.geracao && !(await apagadaDepois(pedido, inicio))) {
      var c = await caches.open(cacheApiDe(dono)), ja = await c.match(pedido);
      var q = ja ? Number(ja.headers.get("X-Basalt-Inicio")) || 0 : 0;
      viuInstante(q);
      if (!(ja && q > inicio)) { await c.put(pedido, entradaApi(resp, corpo, inicio, foto.geracao)); guardou = true; }
      // ronda 9 (achado 2 da ronda 8): o que se verificou antes do put pode ter mudado no meio — com o trinco preso, um 404
      // posterior (lápide) ou «apagar a cópia» fazem-se sem trinco. Volta-se a ver DEPOIS de guardar e, se mudou, sai a entrada
      // que ESTE pedido pôs (e só essa).
      if (guardou && ((await copiaDesligada()) || (await geracaoDaCopia()) !== foto.geracao || (await apagadaDepois(pedido, inicio)))) {
        var minha = await c.match(pedido);
        if (minha && minha.headers.get("X-Basalt-Inicio") === String(inicio)) await c.delete(pedido);
        guardou = false;
      }
    }
    if (lista.length) await destrancar(lista);
    return guardou;
  });
  return r.ok ? r.valor : false;
}
// ronda 6 (achado M9 da ronda 4): um 404/410 só apaga da cache o que lá está se isso partiu ANTES dele — a mesma ordenação dos 200.
// Um 404/410 atrasado (a nota foi apagada, recriada, e um GET mais novo já guardou a recriada) não apaga nada nem põe lápide.
// ronda 9: debaixo do trinco (antes: na fila por URL); trinco preso → faz-se na mesma (só tira dados e põe a lápide).
// ronda 9 (achado G1 da ronda 2): tira da cache do dono do pedido; sem dono, só a lápide (que vale para todas).
async function tirarNota(pedido, inicio) {
  var dono = donoDoPedido(pedido);
  await mudarEpoca();                                              // ronda 13: antes de tudo (ver trancar)
  var fazer = async function () {
    try {
      if (!dono) { await marcarApagada(pedido, inicio); return true; }
      var c = await caches.open(cacheApiDe(dono)), ja = await c.match(pedido);
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
  await revogada();
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
  await mudarEpoca();                                              // ronda 13: antes de tudo (ver trancar)
  var fazer = async function () {
    try {
      await (await caches.open(MARCA_GERACAO)).put("/__geracao", new Response("conta-" + agora() + "-" + Math.random().toString(36).slice(2, 10)));
    } catch (e) {}
    await apagarCachesApi();                                       // ronda 9: as de todas as contas
  };
  var r = await comTrinco("exclusive", fazer);
  if (!r.ok) await fazer();
  await revogada();
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
  var foto = { geracao: await geracaoDaCopia(), trancas: chavesDe(await trancas()), dono: donoDoPedido(pedido) };   // ronda 9: e o dono
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
