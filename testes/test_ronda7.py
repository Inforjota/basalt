"""ronda 7 (07/10/2026) — a ronda seguinte da auditoria externa (ronda 6), portada da instalação de origem:
  servidor (basalt.unir)
    A5 «ronda 7 (escrita guardada durante o recuo)» uma escrita feita em B, durante o recuo da fusão, por quem a tinha aberta ia
       parar a SALVOS mas não aparecia no erro, que dizia na mesma «recuei tudo, nada ficou a meio»;
    A6 «ronda 7 (erro ao confirmar o recuo)» um OSError (EIO) ao confirmar o recuo saía cru («OSError: …»), sem relato nota a nota,
       e deixava temporários escondidos na memória;
  página (index.html) e service worker (sw.js)
    SW2, P1a, P1b «ronda 7 (geração)»: a gravação sem rede ignorava a geração persistente da cópia — outro separador sem
        localStorage apagava e religava a cópia enquanto o PUT corria e a edição descartada voltava à fila da cópia nova (P1a na
        função, P1b pelo editor); e apagar + religar não invalidava os pedidos que o service worker já tinha em curso: o 200 antigo
        voltava a ficar em cache e servia-se sem rede (SW2, com a CacheStorage verdadeira e a página verdadeira a apagar);
    SW3, P3 «ronda 7 (chave reutilizada)»: a fotografia das trancas guardava só a PRESENÇA da chave — uma tranca nova com a mesma
        chave (uma página antiga usa sempre «/__quando») era apagada por uma resposta antiga (SW3 no service worker; P3 na página);
    SW9b «ronda 7 (limpeza na fila)»: no service worker, a limpeza depois de guardar corria fora da fila por URL e apagava uma
        recriação mais nova;
    SW10 «ronda 7 (idade das lápides)»: uma hora futura vista numa entrada da cache empurrava o relógio lógico, podava lápides
        recentes e ressuscitava notas apagadas;
    P7 «ronda 7 (404/410 atrasado)»: um 404/410 atrasado apagava da página a nota entretanto recriada por um pacote posterior;
    P8 «ronda 7 (ordem dos pacotes)»: limpar a lápide deixava um pacote ANTIGO de outro separador repor a nota velha.
Cada teste de caso falha na versão anterior à ronda 7 (a árvore sem as correcções; a página com BASALT_STATIC=…/basalt/static) e
passa nesta; os controlos (C1, C2 e a parte «controlo» de cada teste de página/SW) passam nas duas — nos testes que juntam caso e
controlo, os asserts do controlo vêm primeiro, para a falha na versão antiga ser no caso e não no controlo.
"""
import errno
import os
import threading
from pathlib import Path

import pytest

from basalt import cas, unir
from pagina_falsa import ESTADO, NOTA, com_nota, esperar, esperar_py, fonte_sw, pronta, reset, versao_cache
from test_unir import A, B, BOA, real, git  # noqa: F401  — a memória de rascunho com git e as notas A/B/outra/MEMORY


# ───────────────────────── servidor: a fusão e o recuo ─────────────────────────
@pytest.fixture
def rapido(monkeypatch):
    monkeypatch.setattr(cas, "GRACA", 0.05)


def _salvos():
    """o que ficou guardado à parte (os temporários escondidos do CAS não contam)."""
    try:
        return sorted(p for p in cas.SALVOS.iterdir() if p.is_file() and not p.name.startswith("."))
    except OSError:
        return []


def _ler(real, nome):
    p = real / nome
    return p.read_text(encoding="utf-8") if p.exists() else None


def _correr(real):
    """"" = a fusão aplicou-se; senão o texto do erro (um erro que não seja RuntimeError vem com o tipo à frente — é o que o
    código antigo deixava sair)."""
    try:
        unir.aplicar(real, "nota_a", "nota_b", BOA, A, B)
    except RuntimeError as e:
        return str(e)
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    return ""


def _estado(real, outra):
    ta, tb, to = _ler(real, "nota_a.md"), _ler(real, "nota_b.md"), _ler(real, "outra.md")
    return (f"A: {'de antes' if ta == A else 'fundida' if ta == BOA else 'APAGADA' if ta is None else 'outra coisa'}"
            f" · B: {'de antes' if tb == B else 'APAGADA' if tb is None else 'outra coisa'}"
            f" · outra: {'de antes' if to == outra else 'APAGADA' if to is None else 'ligações mudadas' if 'nota_b' not in to else 'outra coisa'}")


def _temporarios(real):
    """os temporários escondidos que o CAS deixa na memória se algo correr mal (`.<nota>.<rótulo>-….tmp`)."""
    return sorted(p.name for p in real.glob(".*.tmp"))


def _sem_disco(monkeypatch):
    """a troca de «outra» dá «disco cheio» uma vez → a fusão recua. Devolve a lista que regista que o erro aconteceu."""
    original, vezes = cas.trocar, [0]

    def trocar_sem_disco(alvo, novo, esperado, rotulo="cas"):
        if Path(alvo).name == "outra.md" and rotulo == "unir" and not vezes[0]:
            vezes[0] = 1
            raise OSError(errno.ENOSPC, "No space left on device", str(alvo))
        return original(alvo, novo, esperado, rotulo)

    monkeypatch.setattr(cas, "trocar", trocar_sem_disco)
    return vezes


# A5 «ronda 7 (escrita guardada durante o recuo)». Critério da reprodução: a escrita tardia de quem tinha B aberta cai no inode
# que SAIU, durante a graça do recuo (o gancho `durante_graca`). Antes: a escrita ia para SALVOS mas o erro dizia «recuei tudo».
def test_recuo_da_fusao_a_escrita_tardia_de_quem_tinha_b_aberta_fica_em_salvos_e_o_erro_diz_onde_em_vez_de_recuei_tudo(real, rapido, monkeypatch):
    outra = _ler(real, "outra.md")
    vezes = _sem_disco(monkeypatch)
    chamadas, fd, escreveu = {}, [None], []

    def intruso(alvo):
        alvo = Path(alvo)
        chamadas[alvo.name] = chamadas.get(alvo.name, 0) + 1
        if alvo.name == "nota_b.md" and chamadas[alvo.name] == 1:        # um escritor abre B ANTES de ela ser retirada
            fd[0] = os.open(alvo, os.O_WRONLY)

    def tardio(alvo):                                                    # e acaba de escrever no inode que SAIU
        if Path(alvo).name == "nota_b.md" and fd[0] is not None and not escreveu:
            dados = (B + "EDIÇÃO tardia de quem tinha B aberta\n").encode("utf-8")
            os.ftruncate(fd[0], 0)
            os.pwrite(fd[0], dados, 0)
            escreveu.append(1)

    monkeypatch.setattr(cas, "antes_de_trocar", intruso)
    monkeypatch.setattr(cas, "durante_graca", tardio)
    erro = _correr(real)
    if fd[0] is not None:
        os.close(fd[0])
    guardados = [p for p in _salvos() if "EDIÇÃO tardia" in p.read_text(encoding="utf-8")]
    estado = _estado(real, outra)
    # controlos (passam no código antigo): o disco cheio fez a fusão recuar, o escritor escreveu, as notas voltaram e a escrita foi guardada
    assert vezes[0] == 1 and erro and fd[0] is not None and escreveu, f"o disco cheio devia ter feito a fusão falhar e o escritor ter escrito: {erro!r}"
    assert (_ler(real, "nota_a.md"), _ler(real, "nota_b.md"), _ler(real, "outra.md")) == (A, B, outra), estado
    assert len(guardados) == 1, f"a edição tardia de quem tinha B aberta perdeu-se (nada em SALVOS) · {estado}"
    # o caso: o erro tem de dizer a verdade
    assert "recuei tudo" not in erro, f"o erro diz «recuei tudo» com uma escrita guardada à parte (antes: ignorava-a) · erro: {erro!r}"
    assert "«nota_b»" in erro, f"o erro não diz de que nota é a escrita guardada: {erro!r}"
    assert str(guardados[0]) in erro, f"o erro não diz onde ficou a escrita: {erro!r}"


# A6 «ronda 7 (erro ao confirmar o recuo)». Critério: o EIO só acontece a ler temporários do recuo («unir-recuo» no nome), depois
# de as reposições estarem feitas. Antes: o OSError saía cru de `aplicar`, sem relato, e deixava o que saiu num temporário escondido.
def test_recuo_da_fusao_eio_a_ler_os_temporarios_do_recuo_o_erro_e_o_relato_nota_a_nota_e_nao_sobram_temporarios(real, rapido, monkeypatch):
    outra = _ler(real, "outra.md")
    vezes = _sem_disco(monkeypatch)
    ler_original, eio = cas._ler, []

    def ler_com_eio(p):
        if "unir-recuo" in Path(p).name:
            eio.append(Path(p).name)
            raise OSError(errno.EIO, "Input/output error", str(p))
        return ler_original(p)

    monkeypatch.setattr(cas, "_ler", ler_com_eio)
    erro = _correr(real)
    monkeypatch.setattr(cas, "_ler", ler_original)
    estado = _estado(real, outra)
    textos = [p.read_text(encoding="utf-8") for p in _salvos()]
    assert vezes[0] == 1 and eio and erro, f"o disco cheio e o EIO deviam ter acontecido: disco {vezes[0]}, EIO em {len(eio)} leitura(s), erro {erro!r}"   # controlo
    assert not erro.startswith("OSError"), f"o erro saiu cru, sem relato (antes: OSError) · erro: {erro!r}"
    assert "recuei o que pude" in erro and "Input/output error" in erro and "«nota_a»" in erro, f"o erro não é o relato nota a nota: {erro!r}"
    assert (_ler(real, "nota_a.md"), _ler(real, "nota_b.md"), _ler(real, "outra.md")) == (A, B, outra), estado
    assert A in textos, f"o texto de antes de A devia ficar guardado em SALVOS: {len(textos)} ficheiro(s)"
    assert not _temporarios(real), f"sobraram temporários escondidos na memória: {_temporarios(real)}"


# C1 — controlo
def test_controlo_fusao_sem_interferencia_aplica_apaga_b_muda_as_ligacoes_e_faz_commit(real, rapido):
    erro = _correr(real)
    assert not erro, erro
    assert _ler(real, "nota_a.md") == BOA and _ler(real, "nota_b.md") is None
    outra = _ler(real, "outra.md")
    assert "nota_b" not in outra and outra.count("nota_a") == 4, outra
    assert "unir nota_b em nota_a" in git(real, "log", "--oneline")


# C2 — controlo
def test_controlo_disco_cheio_sem_mais_interferencia_recua_limpo_diz_recuei_tudo_sem_salvos_nem_temporarios(real, rapido, monkeypatch):
    outra = _ler(real, "outra.md")
    vezes = _sem_disco(monkeypatch)
    erro = _correr(real)
    assert vezes[0] == 1 and erro, f"o disco cheio devia ter feito a fusão falhar: {erro!r}"
    assert "recuei tudo" in erro, erro
    assert (_ler(real, "nota_a.md"), _ler(real, "nota_b.md"), _ler(real, "outra.md")) == (A, B, outra), _estado(real, outra)
    assert not _salvos(), _salvos()
    assert not _temporarios(real), _temporarios(real)


# ───────────────────────── service worker ─────────────────────────
# O service worker carregado como função (self/caches/fetch/Date falsos): a rede fica pendurada até o teste soltar cada
# resposta, e o RELÓGIO é do teste (relogio.t) — é assim que se reproduz «o relógio andou para trás a meio de um pedido».
# A `caches.match` com cacheName NÃO cai para as outras lojas quando essa não existe (uma lápide guardada pelo URL da nota
# passaria por «cópia em cache»).
# `reais`: o service worker usa a CacheStorage VERDADEIRA da página (para a página a sério apagar/religar a cópia — SW2).
# `ganchoHas`: a N-ésima consulta à marca das lápides fica suspensa até o teste a soltar (SW9b).
SW_PRELUDIO = r"""
async ([fonte, api, reais]) => {
  function urlDe(req){ return typeof req === "string" ? new URL(req, location.origin).href : req.url; }
  function Cache(nome){ this.nome = nome; this.m = new Map(); }
  Cache.prototype.put = async function(req, resp){ this.m.set(urlDe(req), resp); };
  Cache.prototype.add = async function(){};
  Cache.prototype.match = async function(req){ var r = this.m.get(urlDe(req)); return r ? r.clone() : undefined; };
  Cache.prototype.keys = async function(){ return Array.from(this.m.keys()).map(function(u){ return new Request(u); }); };
  Cache.prototype.delete = async function(req){ return this.m.delete(urlDe(req)); };
  var lojas = new Map();
  var ganchoHas = {n: 0, em: 0, solta: null, parado: false};
  var caches = {
    open: async function(n){ if(!lojas.has(n)) lojas.set(n, new Cache(n)); return lojas.get(n); },
    has: async function(n){
      if(n.indexOf("basaltcfg-apagadas") === 0){   // ronda 10 (M3): a loja do dono e a comum
        ganchoHas.n++;
        if(ganchoHas.em && ganchoHas.n === ganchoHas.em){ ganchoHas.parado = true; await new Promise(function(r){ ganchoHas.solta = r; }); }
      }
      return lojas.has(n);
    },
    delete: async function(n){ return lojas.delete(n); },
    keys: async function(){ return Array.from(lojas.keys()); },
    match: async function(req, o){ var c = o && o.cacheName ? lojas.get(o.cacheName) : null; return c ? c.match(req) : undefined; }
  };
  var pendentes = [], semRede = false;
  var fetchFalso = function(){ if(semRede) return Promise.reject(new TypeError("Failed to fetch"));
    return new Promise(function(res){ pendentes.push(res); }); };
  var relogio = {t: 5000};
  class DateFalso extends Date { static now(){ return relogio.t; } }
  var self = {addEventListener: function(){}, skipWaiting: function(){}, clients: {claim: function(){}}, location: {origin: location.origin}};
  var sw = new Function("self", "caches", "fetch", "Date", fonte + "\nreturn {tratarApi: tratarApi, trancas: trancas};")(
    self, reais ? window.caches : caches, fetchFalso, DateFalso);
  var dormir = function(ms){ return new Promise(function(r){ setTimeout(r, ms); }); };
  function U(nome){ return location.origin + "/api/nota?nome=" + nome; }
  var url = U("n1");
  function resp200(t){ return new Response(JSON.stringify({texto: t}), {status: 200, headers: {"Content-Type": "application/json", "X-Basalt-Dono": "d-ana"}}); }
  function resp404(){ return new Response(JSON.stringify({erro: "não existe"}), {status: 404, headers: {"Content-Type": "application/json", "X-Basalt-Dono": "d-ana"}}); }
  function resp410(){ return new Response(JSON.stringify({erro: "apagada"}), {status: 410, headers: {"Content-Type": "application/json", "X-Basalt-Dono": "d-ana"}}); }
  // devolve {p: promessa} e não a promessa: um `return p` numa função async fazia o `await pedir()` esperar pela resposta
  // da rede que o teste ainda não soltou (bloqueio mútuo).
  async function pedirU(u){ var n = pendentes.length; var p = sw.tratarApi(new Request(u, {headers: {"X-Basalt-Dono": "d-ana", "X-Basalt-Protocolo": "5"}}));
    for(var i = 0; i < 200 && pendentes.length === n; i++) await dormir(5); return {p: p}; }
  function pedir(){ return pedirU(url); }
  async function lerEntrada(g){ return g ? {inicio: g.headers.get("X-Basalt-Inicio"), texto: (await g.json()).texto} : null; }
  async function guardadaU(u){ var c = lojas.get(api); return lerEntrada(c ? await c.match(u) : undefined); }
  function guardada(){ return guardadaU(url); }
  async function guardadaReal(){ return lerEntrada(await window.caches.match(url, {cacheName: api})); }
  function trancasN(){ var c = lojas.get("basaltcfg-copia-trancada"); return c ? c.m.size : 0; }
  async function lapideU(u){ var c = lojas.get("basaltcfg-apagadas-d-ana"); if(!c) return null; var r = await c.match(u); if(r) return await r.text();
    // ronda 13: só se escrevem as entradas por instante («/__lapide/<URL codificado>/<hora>»); vale a maior
    var pre = new URL("/__lapide/" + encodeURIComponent(u) + "/", location.origin).href, h = null;
    c.m.forEach(function(v, k){ if(k.indexOf(pre) === 0){ var x = Number(k.slice(pre.length)); if(h === null || x > h) h = x; } });
    return h === null ? null : String(h); }
  function lapide(){ return lapideU(url); }
  async function porTranca(id, hora){ var c = await caches.open("basaltcfg-copia-trancada"); await c.put("/__tranca/" + id, new Response(String(hora))); }
  async function offline(){ semRede = true;
    try { var r = await sw.tratarApi(new Request(url, {headers: {"X-Basalt-Dono": "d-ana", "X-Basalt-Protocolo": "5"}})); return r ? (r.status + (r.headers.get("X-Basalt-From-Cache") ? " cache" : "")) : "nada"; }
    catch(e){ return "recusou"; } finally { semRede = false; } }
"""


def _sw_cenario(corpo):
    return SW_PRELUDIO + corpo + "\n}\n"


# SW2 «ronda 7 (geração)»: a página verdadeira apaga e religa a cópia (CacheStorage verdadeira) com um GET do SW pendurado
SW2 = _sw_cenario(r"""
  relogio.t = 2000;
  var p1 = (await pedir()).p;                       // GET parte antes de apagar a cópia e fica pendurado
  await window.__t.desligarCopiaLocal();            // «apagar a cópia deste aparelho» (a página verdadeira)
  await window.__t.religarCopiaLocal();             // …e «voltar a ligar», antes de a resposta chegar
  pendentes[0](resp200("ANTES_DE_APAGAR")); await p1; await dormir(250);
  var caso = {guardada: await guardadaReal(), offline: await offline()};
  relogio.t = 3000;                                 // controlo: um GET que parte DEPOIS de religar guarda normalmente
  var p2 = (await pedir()).p; pendentes[1](resp200("DEPOIS")); await p2; await dormir(250);
  var controlo = {guardada: await guardadaReal(), offline: await offline()};
  return {caso: caso, controlo: controlo};
""")

# SW3 «ronda 7 (chave reutilizada)»: a mesma chave de tranca reposta com outra hora é uma tranca NOVA
SW3 = _sw_cenario(r"""
  await porTranca("K", 1000);                       // trancada (chave K)
  relogio.t = 2000;
  var p1 = (await pedir()).p;                       // GET parte com K na fotografia
  await porTranca("K", 3000);                       // outra parte tranca outra vez COM A MESMA CHAVE (página antiga: «/__quando»)
  pendentes[0](resp200("PRIVADO")); await p1; await dormir(150);
  var caso = {trancas: trancasN(), guardada: await guardada(), offline: await offline()};
  relogio.t = 4000;                                 // controlo: um GET que parte depois da tranca nova destranca e guarda
  var p2 = (await pedir()).p; pendentes[1](resp200("NOVO")); await p2; await dormir(150);
  var controlo = {trancas: trancasN(), guardada: await guardada(), offline: await offline()};
  return {caso: caso, controlo: controlo};
""")

# SW9b «ronda 7 (limpeza na fila)»: a limpeza depois de guardar corre na fila por URL e só apaga o que é deste pedido (ou mais antigo)
SW9B = _sw_cenario(r"""
  // a 2.ª consulta às lápides (a verificação DEPOIS de guardar o 200 de p1) fica suspensa. ronda 9: o SW já não tem essa
  // verificação depois de guardar (era ela que apagava a recriação) — a única consulta de p1 é a de aceitar(), ANTES de
  // guardar e debaixo do trinco; suspende-se essa (a 1.ª) e o resultado exigido é o mesmo.
  ganchoHas.em = fonte.indexOf("async function aceitar(") !== -1 ? 1 : 2;
  relogio.t = 2000;
  var p1 = (await pedir()).p; pendentes[0](resp200("ANTIGO")); await p1;
  for(var i = 0; i < 200 && !ganchoHas.parado; i++) await dormir(5);
  var parou = ganchoHas.parado;
  relogio.t = 3000;
  var p2 = (await pedir()).p; pendentes[1](resp410()); await dormir(150);         // a nota foi apagada (410) — não se espera por p2
  relogio.t = 4000;
  var p3 = (await pedir()).p; pendentes[2](resp200("RECRIADA")); await p3; await dormir(150);   // …e recriada, e um GET novo guarda-a
  ganchoHas.em = 0; ganchoHas.solta();              // só agora a verificação antiga de p1 continua
  await p2; await dormir(300);
  var caso = {guardada: await guardada(), lapide: await lapide()};
  relogio.t = 5000;                                 // controlo: um 410 novo tira-a (com lápide) e um 200 depois dele volta a guardar
  var p4 = (await pedir()).p; pendentes[3](resp410()); await p4; await dormir(150);
  var apagou = {guardada: await guardada(), lapide: await lapide()};
  relogio.t = 6000;
  var p5 = (await pedir()).p; pendentes[4](resp200("DEPOIS")); await p5; await dormir(150);
  return {parou: parou, caso: caso, apagou: apagou, depois: await guardada()};
""")

# SW10 «ronda 7 (idade das lápides)»: a idade das lápides conta-se no relógio REAL, não no relógio lógico (que uma hora futura empurra)
SW10 = _sw_cenario(r"""
  var A = U("a"), B = U("b"), C = U("c"), D = U("d"), DIA = 86400000;
  relogio.t = 2000;
  var pA1 = (await pedirU(A)).p;                    // GET antigo a A, pendurado
  relogio.t = 2100;
  var pA2 = (await pedirU(A)).p; pendentes[1](resp410()); await pA2; await dormir(100);   // A foi apagada: lápide 2100
  var lapAntes = await lapideU(A);
  await (await caches.open(api)).put(B, new Response(JSON.stringify({texto: "FUTURO"}),
    {headers: {"Content-Type": "application/json", "X-Basalt-Inicio": String(2000 + 40 * DIA)}}));   // uma entrada com hora 40 dias no futuro
  relogio.t = 2200;
  var pB = (await pedirU(B)).p; pendentes[2](resp200("B")); await pB; await dormir(150);  // lê-la empurra o relógio lógico
  relogio.t = 2300;
  var pC = (await pedirU(C)).p; pendentes[3](resp404()); await pC; await dormir(150);     // um 404 a C faz a poda das lápides
  var lapDepoisPoda = await lapideU(A);
  pendentes[0](resp200("ANTIGO")); await pA1; await dormir(150);                           // só agora chega o 200 antigo de A
  var caso = {lapide: lapDepoisPoda, guardada: await guardadaU(A)};
  relogio.t = 2300 + 31 * DIA;                      // controlo: passados 31 dias A SÉRIO, a lápide de A sai na poda seguinte
  var pD = (await pedirU(D)).p; pendentes[4](resp404()); await pD; await dormir(150);
  return {lapAntes: lapAntes, caso: caso, controlo: {lapide: await lapideU(A)}};
""")


def _avaliar_sw(base, contexto, cenario, reais=False):
    reset()
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    return page.evaluate(cenario, [fonte_sw(), versao_cache() + "-api-d-ana", reais])


# SW2 «ronda 7 (geração)». Antes: o 200 de um GET que partiu antes de a cópia ser apagada+religada voltava a ficar em cache e
# servia-se sem rede (o service worker não olhava para a geração da cópia).
@pytest.mark.lento
def test_service_worker_apagar_e_religar_a_copia_com_um_get_em_curso_o_200_antigo_nao_volta_a_ficar_em_cache(base, contexto):
    r = _avaliar_sw(base, contexto, SW2, reais=True)
    c, k = r["caso"], r["controlo"]
    assert (k["guardada"] or {}).get("texto") == "DEPOIS" and k["offline"] == "200 cache", \
        f"controlo (GET que parte depois de religar) devia guardar normalmente: cache {k['guardada']}, sem rede {k['offline']}"
    assert c["guardada"] is None and c["offline"] == "recusou", \
        f"depois de apagar+religar, o 200 antigo ficou em cache: cache {c['guardada']}, sem rede {c['offline']} (antes: ANTES_DE_APAGAR / 200 cache)"


# SW3 «ronda 7 (chave reutilizada)». Antes: a fotografia das trancas guardava só a presença da chave; a tranca nova com a mesma
# chave («/__quando» de uma página antiga) era apagada pelo 200 de um GET que partiu antes.
@pytest.mark.lento
def test_service_worker_tranca_nova_com_a_mesma_chave_e_outra_hora_nao_e_apagada_pelo_200_de_um_get_que_partiu_antes(base, contexto):
    r = _avaliar_sw(base, contexto, SW3)
    c, k = r["caso"], r["controlo"]
    assert k["trancas"] == 0 and (k["guardada"] or {}).get("texto") == "NOVO" and k["offline"] == "200 cache", \
        f"controlo (GET depois da tranca nova) devia destrancar e guardar: trancas {k['trancas']}, cache {k['guardada']}, sem rede {k['offline']}"
    assert c["trancas"] == 1 and c["guardada"] is None and c["offline"] == "recusou", \
        f"o 200 antigo apagou a tranca nova (mesma chave): trancas {c['trancas']}, cache {c['guardada']}, sem rede {c['offline']} (antes: 0 / PRIVADO / 200 cache)"


# SW9b «ronda 7 (limpeza na fila)». Antes: a verificação depois de guardar corria fora da fila por URL e apagava a nota recriada
# que um GET mais novo tinha acabado de guardar.
@pytest.mark.lento
def test_service_worker_a_limpeza_atrasada_depois_de_guardar_um_200_antigo_nao_apaga_a_nota_recriada_por_um_get_mais_novo(base, contexto):
    r = _avaliar_sw(base, contexto, SW9B)
    c, a = r["caso"], r["apagou"]
    assert r["parou"], "a verificação das lápides não ficou suspensa: o cenário não se montou"                                    # controlo
    assert a["guardada"] is None and a["lapide"] is not None and (r["depois"] or {}).get("texto") == "DEPOIS", \
        f"controlo (410 novo tira a nota e um 200 depois volta a guardá-la): cache {a['guardada']}, lápide {a['lapide']}, 200 depois {r['depois']}"
    assert (c["guardada"] or {}).get("texto") == "RECRIADA" and c["lapide"] is not None, \
        f"a limpeza atrasada apagou a nota recriada: cache {c['guardada']}, lápide {c['lapide']} (antes: cache None)"


# SW10 «ronda 7 (idade das lápides)». Antes: uma entrada com a hora 40 dias no futuro empurrava o relógio lógico; a poda seguinte
# apagava a lápide recente e o 200 antigo de A ressuscitava a nota.
@pytest.mark.lento
def test_service_worker_uma_hora_futura_numa_entrada_da_cache_nao_poda_lapides_recentes_a_idade_conta_no_relogio_real(base, contexto):
    r = _avaliar_sw(base, contexto, SW10)
    c, k = r["caso"], r["controlo"]
    assert r["lapAntes"] is not None and k["lapide"] is None, \
        f"controlo: a lápide devia existir ({r['lapAntes']}) e sair passados 31 dias reais ({k['lapide']})"
    assert c["lapide"] is not None and c["guardada"] is None, \
        f"a hora futura podou a lápide recente e o 200 antigo ressuscitou a nota: lápide {c['lapide']} (antes: None), cache {c['guardada']} (antes: ANTIGO)"


# ───────────────────────── página: gravação sem rede com a cópia refeita, trancas, 404/410, pacotes ─────────────────────────
TRANCA_QUANDO_1000 = """async () => { await window.__t.idbPut('meta', {chave:'bloqueada', valor:true});
  var c = await caches.open('basaltcfg-copia-trancada'); await c.put('/__quando', new Response('1000')); }"""
TRANCA_QUANDO_AGORA = """async () => { var c = await caches.open('basaltcfg-copia-trancada'); await c.put('/__quando', new Response(String(Date.now()))); }"""
LER_OFFLINE = "window.__t.tentarOffline('/api/nota?nome=n1').then(n => n ? 'DEU' : 'nada', e => 'recusou')"
N_NOTAS = "window.__t.idbGetAll('notas').then(l => l.length)"
TEXTO_N1 = "window.__t.idbGet('notas', 'n1').then(x => x ? x.texto : null)"
FILA = "window.__t.idbGetAll('fila').then(l => l.map(x => x.texto))"
GET_N1 = "window.__t.apiGet('/api/nota?nome=n1').then(() => 'ok', e => 'erro ' + e.status)"
SEM_LS = "Storage.prototype.setItem = function(){}; Storage.prototype.removeItem = function(){}; Storage.prototype.getItem = function(){ return null; };"


def _dois_separadores(base, contexto, pacote_texto="S novo"):
    """A com a cópia já descarregada; B sem localStorage (A não sabe do que B faz) — é B que apaga e religa a cópia."""
    reset(pacote=[dict(NOTA, texto=pacote_texto, hash="H2")])
    ctx = contexto()
    a = ctx.new_page()
    a.on("dialog", lambda d: d.accept())
    a.goto(base + "/")
    pronta(a)
    com_nota(a)
    b = ctx.new_page()
    b.add_init_script(SEM_LS)
    b.goto(base + "/")
    pronta(b)
    return a, b


def _apagar_religar(b):
    b.evaluate("window.__t.desligarCopiaLocal()")
    b.evaluate("window.__t.religarCopiaLocal()")
    return esperar(b, "window.__t.idbGetAll('notas').then(l => l.length === 1 && l[0].texto === 'S novo')")


# P1a «ronda 7 (geração)». Antes: guardarEdicaoOffline ignorava a geração de antes e a edição descartada entrava na fila da cópia nova.
@pytest.mark.lento
def test_pagina_guardar_edicao_offline_com_a_geracao_de_antes_de_outro_separador_apagar_e_religar_recusa_e_a_fila_fica_vazia(base, contexto):
    a, b = _dois_separadores(base, contexto)
    g = a.evaluate("window.__t.geracao()")
    refeita = _apagar_religar(b)
    r = a.evaluate("g => window.__t.guardarEdicaoOffline('n1', 'S novo', 'EDICAO DESCARTADA', g).then(() => 'guardou', e => 'recusou: ' + e.message)", g)
    fila, texto = a.evaluate(FILA), a.evaluate(TEXTO_N1)
    g2 = a.evaluate("window.__t.geracao()")                           # controlo: com a geração em vigor guarda (e entra na fila)
    r2 = a.evaluate("g => window.__t.guardarEdicaoOffline('n1', 'S novo', 'EDICAO NOVA', g).then(() => 'guardou', e => 'recusou: ' + e.message)", g2)
    fila2 = a.evaluate(FILA)
    assert refeita and g == 0, f"a cópia devia ter sido refeita em B (refeita {refeita}) e a geração de A ser 0 ({g})"            # controlo
    assert r2 == "guardou" and "EDICAO NOVA" in fila2, \
        f"controlo (geração em vigor {g2}) devia guardar e pôr a edição na fila: {r2}, fila {fila2}"
    assert r.startswith("recusou") and "apagada entretanto" in r, \
        f"a gravação com a geração de antes devia ser recusada com o aviso: {r[:90]!r} (antes: guardou)"
    assert fila == [] and texto == "S novo", f"a edição descartada entrou na fila da cópia nova: fila {fila}, nota {texto!r} (antes: ['EDICAO DESCARTADA'])"
    assert g2 > g, f"A devia ter lido a geração nova (persistente) que B criou: geração de A {g2}, antes de B {g} (antes: 0)"
    assert fila2 == ["EDICAO NOVA"], f"a fila devia ter só a edição feita com a geração em vigor: {fila2}"


# P1b «ronda 7 (geração)». Antes: o PUT falhava por rede e o editor guardava a edição na cópia nova, a dizer «guardado no aparelho».
@pytest.mark.lento
def test_pagina_editor_o_put_corre_outro_separador_apaga_e_religa_a_copia_o_put_falha_por_rede_a_edicao_nao_entra_na_copia_nova_e_avisa(base, contexto):
    a, b = _dois_separadores(base, contexto)
    ESTADO["put_solta"] = threading.Event()
    a.evaluate("location.hash = '#/nota/n1'")
    a.wait_for_selector("#editar-btn", timeout=8000)
    a.click("#editar-btn")
    a.wait_for_selector("#ed-gravar", timeout=8000)
    a.wait_for_selector("#ed-editor-host .cm-content, #ed-textarea", timeout=8000)
    if a.query_selector("#ed-textarea"):
        a.fill("#ed-textarea", "S novo EDICAO DESCARTADA")
    else:
        a.click("#ed-editor-host .cm-content")
        a.keyboard.press("Control+End")
        a.keyboard.type(" EDICAO DESCARTADA")
    a.click("#ed-gravar")                                             # o PUT parte com a geração 0 e fica suspenso
    partiu = esperar_py(a, lambda: len(ESTADO["put"]) >= 1)
    refeita = _apagar_religar(b)
    ESTADO["put_falha_rede"] = True
    ESTADO["modo"] = "sem-rede"
    ESTADO["put_solta"].set()                                         # o PUT cai por falta de rede
    avisou = esperar(a, "((document.getElementById('ed-banner') || {}).textContent || '').indexOf('apagada entretanto') >= 0", 5000)
    a.wait_for_timeout(300)
    banner = a.evaluate("(document.getElementById('ed-banner') || {textContent: '(sem banner)'}).textContent.trim()")
    fila, texto = a.evaluate(FILA), a.evaluate(TEXTO_N1)
    enviado = ESTADO["put"][0].get("texto", "") if ESTADO["put"] else ""
    visto = f"aviso: {banner[:110]!r} · fila: {fila} · nota: {texto!r}"
    assert partiu and "EDICAO DESCARTADA" in enviado, f"o PUT devia ter partido com a edição: partiu {partiu}, enviado {enviado!r}"          # controlo
    assert refeita, "a cópia não foi refeita em B"                                                                                        # controlo
    assert avisou, f"o editor não avisou que a cópia foi apagada entretanto · {visto} (antes: «guardado no aparelho»)"
    assert fila == [], f"a edição entrou na fila da cópia nova · {visto} (antes: fila com a edição)"
    assert texto == "S novo", f"a nota da cópia nova mudou · {visto}"


# P3 «ronda 7 (chave reutilizada)». Antes: o 200 do GET apagava a tranca nova de uma página antiga (chave fixa «/__quando») e a cópia
# ficava legível sem rede.
@pytest.mark.lento
def test_pagina_tranca_antiga_quando_reposta_com_hora_nova_durante_um_get_o_200_nao_a_apaga_e_a_copia_continua_trancada(base, contexto):
    reset(pacote=[dict(NOTA, texto="PRIVADO", hash="H2")])
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    com_nota(page)
    page.evaluate(TRANCA_QUANDO_1000)                                 # trancada por uma página antiga (chave fixa, sem id)
    antes = page.evaluate(LER_OFFLINE)
    ESTADO["nota_solta"] = threading.Event()
    page.evaluate("void (window.__get = " + GET_N1 + ")")
    page.wait_for_timeout(300)
    page.evaluate(TRANCA_QUANDO_AGORA)                                # a página antiga tranca OUTRA VEZ (mesma chave, hora nova)
    page.wait_for_timeout(50)
    ESTADO["nota_solta"].set()
    r = page.evaluate("window.__get")
    page.wait_for_timeout(300)
    n = page.evaluate("window.__t.trancasNaMarca().then(l => l.length)")
    depois = page.evaluate(LER_OFFLINE)
    ESTADO["nota_solta"] = None                                       # controlo: um GET que parte DEPOIS da tranca nova destranca
    r2 = page.evaluate(GET_N1)
    page.wait_for_timeout(300)
    n2 = page.evaluate("window.__t.trancasNaMarca().then(l => l.length)")
    controlo = page.evaluate(LER_OFFLINE)
    assert antes == "recusou" and r == "ok", f"antes: {antes} · GET: {r}"                                                                 # controlo
    assert r2 == "ok" and n2 == 0 and controlo == "DEU", \
        f"controlo (GET depois da tranca nova) devia destrancar: GET {r2}, trancas {n2}, sem rede {controlo}"
    assert n == 1, f"o 200 do GET antigo apagou a tranca nova (mesma chave): trancas {n} (antes: 0)"
    assert depois == "recusou", f"a cópia ficou legível sem rede com a tranca nova em vigor: {depois} (antes: DEU)"


# P7 «ronda 7 (404/410 atrasado)». Antes: o 410 atrasado apagava da cópia a nota que um pacote posterior tinha recriado.
@pytest.mark.lento
def test_pagina_um_410_atrasado_nao_apaga_a_nota_que_um_pacote_posterior_recriou_e_um_410_novo_apaga(base, contexto):
    reset(pacote=[dict(NOTA, texto="S novo", hash="H2")])
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    com_nota(page)
    page.wait_for_timeout(300)
    ESTADO["nota_solta"] = threading.Event()
    ESTADO["nota_status"] = 410
    page.evaluate("void (window.__get = " + GET_N1 + ")")             # o GET parte (a nota está apagada) e fica suspenso
    page.wait_for_timeout(300)
    ESTADO["pacote"] = [dict(NOTA, texto="RECRIADA", hash="H9")]
    page.evaluate("window.__t.sincronizarPacote()")                   # a nota foi recriada e um pacote posterior trá-la
    recriada = esperar(page, TEXTO_N1 + ".then(t => t === 'RECRIADA')", 4000)
    ESTADO["nota_solta"].set()
    r = page.evaluate("window.__get")
    page.wait_for_timeout(300)                                        # …e só agora chega o 410 antigo
    texto = page.evaluate(TEXTO_N1)
    ESTADO["nota_solta"] = None                                       # controlo: um 410 de um pedido NOVO tira-a
    r2 = page.evaluate(GET_N1)
    page.wait_for_timeout(300)
    n2 = page.evaluate(N_NOTAS)
    assert recriada and r == "erro 410", f"o pacote posterior devia ter recriado a nota (recriada {recriada}) e o GET atrasado dar 410 ({r})"   # controlo
    assert r2 == "erro 410" and n2 == 0, f"controlo (410 de um pedido novo) devia tirar a nota: GET {r2}, notas {n2}"
    assert texto == "RECRIADA", f"o 410 atrasado apagou a nota recriada pelo pacote posterior: {texto!r} (antes: None)"


# P8 «ronda 7 (ordem dos pacotes)». Antes: o pacote antigo de A (suspenso no servidor) chegava depois de a lápide sair com o pacote
# posterior de B e repunha a nota velha.
@pytest.mark.lento
def test_pagina_dois_separadores_o_pacote_antigo_de_a_suspenso_nao_repoe_a_nota_velha_depois_do_410_e_do_pacote_posterior_de_b(base, contexto):
    reset(pacote=[dict(NOTA, texto="ANTIGA", hash="H2")])
    ctx = contexto()
    a = ctx.new_page()
    a.goto(base + "/")
    pronta(a)
    com_nota(a)
    b = ctx.new_page()
    b.goto(base + "/")
    pronta(b)
    esperar_py(a, lambda: ESTADO["pacote_pedidos"] >= 2)
    a.wait_for_timeout(500)
    ESTADO["pacote_uma"] = threading.Event()
    solta = ESTADO["pacote_uma"]
    n0 = ESTADO["pacote_pedidos"]
    a.evaluate("void (window.__pac = window.__t.sincronizarPacote())")    # o pacote de A (com ANTIGA) fica suspenso no servidor
    chegou = esperar_py(a, lambda: ESTADO["pacote_pedidos"] > n0)
    ESTADO["nota_status"] = 410
    r = a.evaluate(GET_N1)
    a.wait_for_timeout(200)                                           # a nota foi apagada: sai da cópia, com lápide
    n_apagada = a.evaluate(N_NOTAS)
    ESTADO["nota_status"] = 200
    ESTADO["pacote"] = [dict(NOTA, texto="RECRIADA", hash="H9")]
    b.evaluate("window.__t.sincronizarPacote()")                      # B descarrega um pacote posterior: recriada (e a lápide sai)
    recriada = esperar(b, TEXTO_N1 + ".then(t => t === 'RECRIADA')", 4000)
    solta.set()
    a.evaluate("window.__pac")
    a.wait_for_timeout(300)                                           # só agora chega o pacote antigo de A
    texto = a.evaluate(TEXTO_N1)
    ESTADO["pacote"] = [dict(NOTA, texto="NOVA", hash="H10")]         # controlo: um pacote NOVO de A entra
    a.evaluate("window.__t.sincronizarPacote()")
    nova = esperar(a, TEXTO_N1 + ".then(t => t === 'NOVA')", 4000)
    assert chegou and r == "erro 410" and n_apagada == 0 and recriada, \
        f"o cenário não se montou: pacote de A suspenso {chegou} · GET {r} · notas {n_apagada} · B recriou {recriada}"                    # controlo
    assert nova, "controlo: um pacote novo de A devia entrar"
    assert texto == "RECRIADA", f"o pacote antigo de A repôs a nota velha: {texto!r} (antes: 'ANTIGA')"
