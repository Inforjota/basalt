"""Ronda 12 (D) — um resultado de uma fonte externa (`[fontes_extra]`) abre-se SÓ PARA LER.

Os comandos de `[fontes_extra]` trazem para o índice textos de fora ({fonte, ref, titulo, texto}). Até aqui esses resultados
apareciam na procura mas não se abriam («não navegável»). Agora `GET /api/fonte?fonte=&ref=` devolve o texto que o índice tem
dessa ref — todos os pedaços («ref», «ref#0», «ref#1»…, com o título dela), pela ordem — e a página abre-o numa vista só de
leitura (#/fonte/<fonte>/<ref>), com a fonte e o título à vista e sem editar, apagar nem unir.
As exclusões são as da procura (`sig.carregar`): um pedaço com o que parece uma password ou chave não sai; uma ref só com
pedaços desses não se abre (404). As notas não se lêem por aqui (abrem-se como sempre, editáveis).
Cada teste falha no código anterior a esta ronda pela razão certa (404 «Rota desconhecida» / o resultado é um bloco parado);
para a página contra a cópia antiga: `TESTE_STATIC=<cópia antiga>/basalt/static`."""
import http.client
import json
import os
import shutil
import subprocess
import threading
import urllib.parse
from pathlib import Path

import pytest

from pagina_falsa import esperar

TOML = """[basalt]
dados = "{dados}"
escutar = "127.0.0.1"
[auth]
modo = "aberto"
[[utilizadores]]
email = "ana@exemplo.pt"
espaco = "ana"
nome = "Ana"
admin = true
[ia]
fornecedor = "nenhum"
"""
FM = "---\nname: {nome}\ndescription: barcos do Pico\ntype: project\n---\n"
SEGREDO = "senha do router: Tr0ub4dor&3 e mais nada a dizer."     # o mesmo exemplo dos testes da ronda 10
PEDACOS = [                       # (fonte, ref, titulo, trecho) — fora de ordem de propósito
    ("manual", "caldeira#10", "Manual da caldeira", "PARTE DEZ: a caldeira desliga-se sozinha no verão."),
    ("manual", "caldeira#0", "Manual da caldeira", "PARTE ZERO: a caldeira fica na cave, ao lado do depósito."),
    ("manual", "caldeira#2", "Manual da caldeira", "PARTE DOIS: purgar a caldeira uma vez por ano."),
    ("manual", "caldeira#1", "Manual da caldeira", "PARTE UM: a pressão da caldeira anda entre 1 e 2 bar."),
    ("manual", "caldeira#3", "Manual da caldeira", "Acesso remoto da caldeira: " + SEGREDO),
    ("tarefas", "T-7", "Trocar a bomba de água", "A bomba de água do depósito faz barulho: trocar antes do inverno."),
    ("pedidos", "repo#12", "Pedido 12: corrigir o menu", "O menu do telemóvel não abre no Pico."),
    ("pedidos", "repo#13", "Pedido 13: traduzir a ajuda", "A ajuda precisa de estar também em inglês."),
    ("cofre", "C-1", "Acessos da caldeira", "Caldeira — " + SEGREDO),
    ("nota", "barcos#0", "barcos", "[barcos] barcos do Pico\n# Barcos\nOs barcos do Pico saem às nove."),
]


def _por_no_indice(esp, pedacos):
    from basalt import config, sig
    with config.dentro(esp):
        c = sig.db()
        c.executemany("INSERT OR REPLACE INTO pedaco (fonte, ref, titulo, trecho, hash, vec) VALUES (?, ?, ?, ?, 'h', x'')", pedacos)
        c.commit()
        c.close()


@pytest.fixture(scope="module")
def real(tmp_path_factory, request):
    """o servidor REAL (modo aberto, sem IA, pesquisa por palavras) com um índice que tem notas e fontes externas."""
    from basalt import config, segredos, web
    assert segredos.tem_segredo_texto(SEGREDO) and not segredos.tem_segredo_texto(PEDACOS[1][3])   # o teste distingue os dois
    tmp = tmp_path_factory.mktemp("r12d")
    t = tmp / "basalt.toml"
    t.write_text(TOML.format(dados=tmp / "dados"), encoding="utf-8")
    cfg = config.carregar(t, ambiente={})
    origem = Path(os.environ.get("TESTE_STATIC") or web.STATIC).resolve()
    estatico = tmp / "static"
    shutil.copytree(origem, estatico)
    original = web.STATIC
    web.STATIC = estatico
    request.addfinalizer(lambda: setattr(web, "STATIC", original))
    esp = cfg.espaco("ana")
    for d in (esp.real, esp.copia):
        d.mkdir(parents=True, exist_ok=True)
        (d / "barcos.md").write_text(FM.format(nome="barcos") + "# Barcos\nOs barcos do Pico saem às nove.\n", encoding="utf-8")
    _por_no_indice(esp, PEDACOS)
    srv = web.criar_servidor(cfg, host="127.0.0.1", porta=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], esp
    srv.shutdown()
    srv.server_close()


def _get(porta, caminho, metodo="GET"):
    """um pedido como o da página: com o dono da cópia (o servidor recusa a API sem ele)."""
    c = http.client.HTTPConnection("127.0.0.1", porta, timeout=60)
    c.request("GET", "/api/eu")
    dono = json.loads(c.getresponse().read())["dono"]
    c.request(metodo, caminho, headers={"X-Basalt-Dono": dono, "X-Basalt": "1"})
    r = c.getresponse()
    corpo = r.read()
    c.close()
    try:
        return r.status, json.loads(corpo)
    except ValueError:
        return r.status, corpo.decode("utf-8", "replace")


def _fonte(porta, fonte, ref):
    return _get(porta, "/api/fonte?" + urllib.parse.urlencode({"fonte": fonte, "ref": ref}))


# ───────────────────────── servidor ─────────────────────────

def test_fonte_externa_abre_com_todos_os_pedacos_pela_ordem(real):
    porta, _ = real
    st, d = _fonte(porta, "manual", "caldeira#1")                    # o resultado da procura pode ser qualquer pedaço
    assert st == 200, f"o resultado da fonte externa não se abre: {st} {d}"
    assert d["fonte"] == "manual" and d["titulo"] == "Manual da caldeira"
    assert [p["ref"] for p in d["pedacos"]] == ["caldeira#0", "caldeira#1", "caldeira#2", "caldeira#10"], d["pedacos"]
    assert [p["texto"].split(":")[0] for p in d["pedacos"]] == ["PARTE ZERO", "PARTE UM", "PARTE DOIS", "PARTE DEZ"]
    assert d["omitidos"] == 1
    assert "Tr0ub4dor" not in json.dumps(d, ensure_ascii=False), "um pedaço com uma password saiu na vista"


def test_fonte_externa_com_um_so_texto(real):
    porta, _ = real
    st, d = _fonte(porta, "tarefas", "T-7")
    assert st == 200, (st, d)
    assert d["titulo"] == "Trocar a bomba de água" and [p["texto"] for p in d["pedacos"]] == [PEDACOS[5][3]]
    assert d["omitidos"] == 0


def test_refs_do_mesmo_sitio_mas_coisas_diferentes_nao_se_misturam(real):
    """«repo#12» e «repo#13» com títulos diferentes são dois pedidos, não dois pedaços do mesmo texto."""
    porta, _ = real
    st, d = _fonte(porta, "pedidos", "repo#12")
    assert st == 200, (st, d)
    assert [p["ref"] for p in d["pedacos"]] == ["repo#12"] and d["titulo"] == "Pedido 12: corrigir o menu"


def test_ref_excluida_nao_se_abre(real):
    """controlo: uma ref cujos pedaços têm todos uma password em claro não aparece na procura e também não se abre."""
    porta, _ = real
    st, d = _fonte(porta, "cofre", "C-1")
    assert st == 404 and "Tr0ub4dor" not in json.dumps(d, ensure_ascii=False), (st, d)
    st, res = _get(porta, "/api/procura?q=" + urllib.parse.quote("acessos caldeira"))
    assert st == 200 and not [r for r in res if r["fonte"] == "cofre"], res
    assert _fonte(porta, "cofre", "nao-existe")[0] == 404
    assert _fonte(porta, "", "C-1")[0] == 404


def test_o_que_a_procura_mostra_de_fora_abre_se(real):
    """a procura e a vista usam o mesmo filtro: cada resultado de uma fonte externa abre-se, e o texto dele está lá."""
    porta, _ = real
    st, res = _get(porta, "/api/procura?q=caldeira")
    externos = [r for r in res if r["fonte"] != "nota"]
    assert st == 200 and externos, res
    for r in externos:
        st, d = _fonte(porta, r["fonte"], r["ref"])
        assert st == 200, (r, st, d)
        assert r["trecho"] in [p["texto"] for p in d["pedacos"]]


def test_controlo_nota_normal_continua_editavel_e_nao_se_le_por_aqui(real):
    porta, _ = real
    st, n = _get(porta, "/api/nota?nome=barcos")
    assert st == 200 and n["editavel"] is True, (st, n)
    assert _fonte(porta, "nota", "barcos#0")[0] == 404            # as notas abrem-se na página, com as regras delas
    antes = _fonte(porta, "tarefas", "T-7")
    for metodo in ("PUT", "POST", "DELETE"):                     # e por aqui nada se escreve (501 = o servidor não tem o método)
        assert _get(porta, "/api/fonte?fonte=tarefas&ref=T-7", metodo)[0] in (403, 404, 405, 501)
    assert _fonte(porta, "tarefas", "T-7") == antes


def test_pergunta_diz_de_que_fonte_e_cada_excerto(monkeypatch):
    """as fichas da resposta da IA: uma fonte externa já não se faz passar por nota («T-7» não é uma nota)."""
    from basalt import web
    saida = {"resposta": "A bomba faz barulho [1] e os barcos saem às nove [2].",
             "fontes": [{"n": 1, "titulo": "Trocar a bomba de água", "ref": "T-7", "fonte": "tarefas"},
                        {"n": 2, "titulo": "barcos", "ref": "barcos#0", "fonte": "nota"}]}
    monkeypatch.setattr(web, "motor", lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(saida), ""))
    d = web.pergunta("o que faz barulho?")
    assert [f["nome"] for f in d["fontes"]] == [None, "barcos"], d["fontes"]
    antiga = {"resposta": "x", "fontes": [{"n": 1, "titulo": "barcos", "ref": "barcos#1"}]}     # um motor sem «fonte»: como antes
    monkeypatch.setattr(web, "motor", lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(antiga), ""))
    assert web.pergunta("x")["fontes"][0]["nome"] == "barcos"


def test_motor_diz_a_fonte_de_cada_excerto_da_pergunta(monkeypatch, capsys):
    """`sig pergunta --json` (o que a web lê) leva a fonte de cada excerto, para a página saber o que abrir."""
    from basalt import sig
    fortes = [{"titulo": "Trocar a bomba de água", "ref": "T-7", "fonte": "tarefas", "trecho": "x"},
              {"titulo": "barcos", "ref": "barcos#0", "fonte": "nota", "trecho": "y"}]
    monkeypatch.setattr(sig, "_para_pergunta", lambda termo: fortes)
    monkeypatch.setattr(sig, "_llm", lambda termo, f: "A bomba [1], os barcos [2].")
    sig.pergunta("o que faz barulho?", como_json=True)
    d = json.loads(capsys.readouterr().out)
    assert [(f["fonte"], f["ref"]) for f in d["fontes"]] == [("tarefas", "T-7"), ("nota", "barcos#0")], d


# ───────────────────────── página (navegador real, pt-PT) ─────────────────────────

def _pagina(navegador, porta):
    ctx = navegador.new_context(service_workers="block", locale="pt-PT")
    page = ctx.new_page()
    page.goto("http://127.0.0.1:%d/" % porta)
    assert esperar(page, "!!document.getElementById('cmd-input')", 15000)
    return ctx, page


def _procurar(page, termo):
    page.fill("#cmd-input", termo)
    assert esperar(page, "document.querySelectorAll('#cmd-panel .cmd-result').length > 0", 15000), "a procura não deu resultados"


@pytest.mark.lento
def test_pagina_resultado_de_fonte_externa_abre_vista_so_de_leitura(real, navegador):
    porta, _ = real
    ctx, page = _pagina(navegador, porta)
    try:
        _procurar(page, "caldeira")
        assert esperar(page, "!!document.querySelector('#cmd-panel a.cmd-result[href^=\"#/fonte/\"]')", 5000), \
            "o resultado da fonte externa não se abre (bloco parado, «não navegável»)"
        page.click("#cmd-panel a.cmd-result[href^='#/fonte/']")
        assert esperar(page, "!!document.querySelector('#fonte-vista pre.fonte-texto')", 15000), "a vista da fonte não abriu"
        v = page.evaluate("""() => ({
            titulo: document.querySelector('.view-heading h1').innerText,
            fonte: document.querySelector('#fonte-vista .badge-origem').innerText,
            so: !!document.querySelector('#fonte-vista .badge-readonly'),
            textos: [...document.querySelectorAll('#fonte-vista pre.fonte-texto')].map(p => p.innerText.split(':')[0]),
            botoes: [...document.querySelectorAll('#main button')].map(b => b.innerText),
            editar: !!document.getElementById('editar-btn'),
            tudo: document.getElementById('main').innerText})""")
        assert v["titulo"] == "Manual da caldeira" and v["fonte"].lower() == "manual" and v["so"], v
        assert v["textos"] == ["PARTE ZERO", "PARTE UM", "PARTE DOIS", "PARTE DEZ"], v["textos"]
        assert not v["editar"] and not v["botoes"], f"há botões na vista só de leitura: {v['botoes']}"
        assert "Tr0ub4dor" not in v["tudo"] and "não se mostra" in v["tudo"]
    finally:
        ctx.close()


@pytest.mark.lento
def test_pagina_controlo_nota_normal_continua_editavel(real, navegador):
    porta, _ = real
    ctx, page = _pagina(navegador, porta)
    try:
        _procurar(page, "barcos")
        page.click("#cmd-panel a.cmd-result[href^='#/nota/']")
        assert esperar(page, "!!document.getElementById('editar-btn')", 15000), "a nota normal deixou de ser editável"
        assert page.evaluate("location.hash") == "#/nota/barcos"
    finally:
        ctx.close()


@pytest.mark.lento
def test_pagina_ref_excluida_nao_abre(real, navegador):
    """controlo: com o endereço à mão, uma ref só com pedaços sensíveis dá «não encontrado» e nenhum texto."""
    porta, _ = real
    ctx, page = _pagina(navegador, porta)
    try:
        page.evaluate("() => { location.hash = '#/fonte/cofre/C-1'; }")
        assert esperar(page, "!!document.querySelector('#main .state-error')", 15000), "a página não diz que a ref não abre"
        v = page.evaluate("() => ({texto: document.getElementById('main').innerText, "
                          "pre: document.querySelectorAll('#main pre').length})")
        assert "Tr0ub4dor" not in v["texto"] and "Texto não encontrado" in v["texto"] and v["pre"] == 0, v
    finally:
        ctx.close()
