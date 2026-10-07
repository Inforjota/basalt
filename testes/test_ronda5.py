"""ronda 5 (07/10/2026) — o que ficou da auditoria externa (ronda 4), portado da instalação de origem:
  recuo da fusão (basalt.unir._recuar) diz, nota a nota, o que NÃO voltou ao texto de antes e onde ficou guardado
    (antes anunciava sempre «recuei tudo», mesmo quando uma troca de volta falhava);
  página: os botões do ecrã de conflito têm a mesma guarda de geração da cópia que o editor já tinha (apagar a cópia e
    religá-la enquanto o PUT corria já não faz a confirmação velha escrever na cópia nova);
  página: só um 200 que seja JSON destranca a cópia (um 200 text/html — a página de login do proxy — não);
  service worker: «ver se a cache tem mais nova» e «guardar» passam a ser uma só operação por URL (duas respostas à mesma
    nota quase juntas já não deixam a mais antiga por cima).
Cada teste falha na versão anterior (`git archive 01f3b83`; página com BASALT_STATIC=…/basalt/static) e passa nesta.
"""
import threading

import pytest

from basalt import cas, unir
from pagina_falsa import (ESTADO, NOTA, abrir_nota_e_esperar, com_nota, esperar, esperar_py, fonte_sw, por_conflito, pronta,
                          reset, versao_cache)
from test_unir import A, B, BOA, real, git  # noqa: F401  — a memória de rascunho com git e as notas A/B/outra/MEMORY


# ───────────────────────── recuo da fusão: a verdade sobre o que não voltou ─────────────────────────
@pytest.fixture
def rapido(monkeypatch):
    monkeypatch.setattr(cas, "GRACA", 0.05)


def _salvos_de(nome):
    try:
        return sorted(p for p in cas.SALVOS.iterdir() if p.name.startswith(nome + "."))
    except OSError:
        return []


def _fusao_que_recua(real, intruso, monkeypatch):
    """`intruso(alvo)` corre mesmo antes de cada troca do CAS (gancho `cas.antes_de_trocar`). Devolve o texto do erro."""
    monkeypatch.setattr(cas, "antes_de_trocar", intruso)
    with pytest.raises(RuntimeError) as e:
        unir.aplicar(real, "nota_a", "nota_b", BOA, A, B)
    return str(e.value)


def test_recuo_que_nao_conseguiu_repor_uma_nota_diz_qual_e_onde_ficou_o_texto_de_antes(real, rapido, monkeypatch):
    vezes = {}

    def intruso(alvo):
        vezes[alvo.name] = vezes.get(alvo.name, 0) + 1
        if alvo.name == "outra.md":                                   # a fusão falha a meio: a nota desaparece antes da troca
            alvo.unlink()
        elif alvo.name == "nota_a.md" and vezes[alvo.name] == 2:      # …e durante o RECUO um agente grava na A
            alvo.write_text("AGENTE escreveu durante o recuo\n", encoding="utf-8")
    erro = _fusao_que_recua(real, intruso, monkeypatch)
    assert "nota_a" in erro and "não voltou" in erro and "recuei tudo" not in erro, erro
    assert (real / "nota_a.md").read_text() == "AGENTE escreveu durante o recuo\n"      # a escrita do agente ganha
    assert (real / "nota_b.md").read_text() == B                                        # a «fundida» voltou
    salvos = _salvos_de("nota_a")
    assert len(salvos) == 1 and salvos[0].read_text() == A, salvos                      # o texto de antes da A ficou guardado…
    assert str(salvos[0]) in erro                                                       # …e o erro diz onde


def test_recuo_em_que_a_nota_desapareceu_entretanto_diz_o_motivo_e_guarda_o_texto_de_antes(real, rapido, monkeypatch):
    vezes = {}

    def intruso(alvo):
        vezes[alvo.name] = vezes.get(alvo.name, 0) + 1
        if alvo.name == "outra.md" or (alvo.name == "nota_a.md" and vezes[alvo.name] == 2):
            alvo.unlink()                                             # durante o recuo, alguém apagou a A
    erro = _fusao_que_recua(real, intruso, monkeypatch)
    assert "nota_a" in erro and "desapareceu" in erro and "recuei tudo" not in erro, erro
    assert not (real / "nota_a.md").exists()
    assert (real / "nota_b.md").read_text() == B
    salvos = _salvos_de("nota_a")
    assert len(salvos) == 1 and salvos[0].read_text() == A and str(salvos[0]) in erro


def test_controlo_recuo_que_repoe_tudo_continua_a_dizer_recuei_tudo(real, rapido, monkeypatch):
    def intruso(alvo):
        if alvo.name == "outra.md" and alvo.exists():
            alvo.unlink()
    erro = _fusao_que_recua(real, intruso, monkeypatch)
    assert "recuei tudo" in erro and "não voltou" not in erro, erro
    assert (real / "nota_a.md").read_text() == A and (real / "nota_b.md").read_text() == B
    assert not _salvos_de("nota_a")


# ───────────────────────── página: botões do conflito e 200 em HTML ─────────────────────────
@pytest.mark.lento
def test_botao_do_conflito_com_o_put_suspenso_apagar_a_copia_e_religar_nao_escreve_na_copia_nova(base, contexto):
    reset(pacote=[dict(NOTA, texto="S novo", hash="H2")], put_solta=threading.Event())
    page = contexto().new_page()
    page.on("dialog", lambda d: d.accept())
    page.goto(base + "/")
    pronta(page)
    com_nota(page)
    por_conflito(page)
    assert abrir_nota_e_esperar(page, "conf-minha")
    page.click("#conf-minha")                                         # o PUT («MEU», base H2) parte e fica suspenso no servidor
    assert esperar_py(page, lambda: len(ESTADO["put"]) >= 1)
    ger_antes = page.evaluate("window.__t.geracao()")
    page.evaluate("window.__t.desligarCopiaLocal()")                  # noutro separador: «apagar a cópia deste aparelho»
    page.evaluate("window.__t.religarCopiaLocal()")                   # …e logo «voltar a ligar»: cópia limpa, geração nova
    assert esperar(page, "window.__t.idbGetAll('notas').then(l => l.length === 1 && l[0].texto === 'S novo')")
    ESTADO["modo"] = "sem-rede"                                       # daqui em diante a rede cai — só o PUT em curso responde…
    ESTADO["put_solta"].set()                                         # …com 200 (gravou «MEU» no servidor, hash H3)
    avisou = esperar(page, "document.body.textContent.indexOf('apagada entretanto') >= 0", 4000)
    page.wait_for_timeout(300)
    n = page.evaluate("window.__t.idbGet('notas', 'n1').then(function(x){ return x ? {texto: x.texto, hash: x.hash} : null; })")
    assert page.evaluate("window.__t.geracao()") > ger_antes
    assert [p.get("texto") for p in ESTADO["put"]] == ["MEU"]
    assert avisou, "a página não disse que a cópia foi apagada entretanto"
    assert n and n["texto"] == "S novo", f"a confirmação velha escreveu na cópia nova: {n}"


@pytest.mark.lento
def test_um_200_em_html_nao_destranca_a_copia_trancada_por_um_403_e_um_200_json_destranca(base, contexto):
    reset()
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    ESTADO["modo"] = "403"
    page.evaluate("window.__t.apiGet('/api/notas').then(function(){}, function(){})")
    assert esperar(page, "window.__t.bloqueada()", 3000), "o 403 não trancou a cópia"
    ESTADO["modo"] = "html"                                           # a sessão caiu: o proxy responde 200 com a página de login
    page.evaluate("window.__t.apiGet('/api/notas').then(function(){}, function(){})")
    page.wait_for_timeout(500)
    assert page.evaluate("window.__t.bloqueada()") is True, "um 200 text/html destrancou a cópia"
    ESTADO["modo"] = "ok"                                             # controlo: uma resposta JSON a sério destranca
    page.evaluate("window.__t.apiGet('/api/notas').then(function(){}, function(){})")
    assert esperar(page, "!window.__t.bloqueada()", 3000), "um 200 JSON não destrancou"


# ───────────────────────── service worker: guardarSeMaisNova em fila por URL ─────────────────────────
# A cache falsa tem o `match` ATRASÁVEL: a leitura vê o estado no instante em que é pedida mas só responde uns ms depois —
# é assim que se reproduz «a resposta antiga ainda a ler a cache quando a nova já gravou».
SW_FILA_GUARDAR = r"""
async ([fonte, api]) => {
  function urlDe(req){ return typeof req === "string" ? new URL(req, location.origin).href : req.url; }
  var atrasos = [];
  function Cache(nome){ this.nome = nome; this.m = new Map(); }
  Cache.prototype.put = async function(req, resp){ this.m.set(urlDe(req), resp); };
  Cache.prototype.add = async function(){};
  Cache.prototype.match = function(req){ var r = this.m.get(urlDe(req)); var d = atrasos.length ? atrasos.shift() : 0;
    return new Promise(function(res){ setTimeout(function(){ res(r ? r.clone() : undefined); }, d); }); };
  Cache.prototype.keys = async function(){ return Array.from(this.m.keys()).map(function(u){ return new Request(u); }); };
  Cache.prototype.delete = async function(req){ return this.m.delete(urlDe(req)); };
  var lojas = new Map();
  var caches = {
    open: async function(n){ if(!lojas.has(n)) lojas.set(n, new Cache(n)); return lojas.get(n); },
    has: async function(n){ return lojas.has(n); },
    delete: async function(n){ return lojas.delete(n); },
    keys: async function(){ return Array.from(lojas.keys()); },
    match: async function(req, o){ var c = o && o.cacheName ? lojas.get(o.cacheName) : null; if(c) return c.match(req);
      for (const cc of lojas.values()){ var r = await cc.match(req); if(r) return r; } return undefined; }
  };
  var fetchFalso = function(){ return new Promise(function(){}); };
  var self = {addEventListener: function(){}, skipWaiting: function(){}, clients: {claim: function(){}}, location: {origin: location.origin}};
  var sw = new Function("self", "caches", "fetch", fonte + "\nreturn {guardarSeMaisNova: guardarSeMaisNova};")(self, caches, fetchFalso);
  var dormir = function(ms){ return new Promise(function(r){ setTimeout(r, ms); }); };
  var resp = function(t){ return new Response(JSON.stringify({texto: t}), {status: 200, headers: {"Content-Type": "application/json"}}); };
  var url = location.origin + "/api/nota?nome=n1";
  async function lida(c){ var g = await c.match(url); if(!g) return null; return {inicio: g.headers.get("X-Basalt-Inicio"), texto: (await g.json()).texto}; }
  // o caso: a ANTIGA (partiu a 100) chega e começa a guardar; a leitura dela à cache demora 120 ms; 10 ms depois chega a
  // NOVA (partiu a 200), lê a cache (vazia, sem atraso) e grava. A antiga acaba de ler «vazia» e gravava por cima.
  var c = await caches.open(api);
  atrasos = [120, 0];
  var p1 = sw.guardarSeMaisNova(c, new Request(url), resp("ANTIGA"), 100);
  await dormir(10);
  var p2 = sw.guardarSeMaisNova(c, new Request(url), resp("NOVA"), 200);
  var r1 = await p1, r2 = await p2;
  await dormir(20);
  var caso = await lida(c);
  // controlo: nova e depois antiga, em sequência — a antiga tem de ser recusada
  var c2 = await caches.open(api + "-controlo");
  var s1 = await sw.guardarSeMaisNova(c2, new Request(url), resp("NOVA"), 200);
  var s2 = await sw.guardarSeMaisNova(c2, new Request(url), resp("ANTIGA"), 100);
  return {caso: caso, controlo: await lida(c2), s1: s1, s2: s2};
}
"""


@pytest.mark.lento
def test_service_worker_duas_respostas_a_mesma_nota_quase_juntas_fica_a_mais_nova(base, contexto):
    reset()
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    r = page.evaluate(SW_FILA_GUARDAR, [fonte_sw(), versao_cache() + "-api"])
    assert r["controlo"] == {"inicio": "200", "texto": "NOVA"} and r["s1"] is True and r["s2"] is False, r
    assert r["caso"] == {"inicio": "200", "texto": "NOVA"}, f"ficou a ANTIGA por cima: {r['caso']}"
