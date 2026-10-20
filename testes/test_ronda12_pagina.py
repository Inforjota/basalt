"""Ronda 12 (A) — o que uma versão anterior da página deixou por enviar neste browser nunca fica escondido.

Uma página anterior (sem dono gravado na cópia) guarda edições feitas sem internet na fila do IndexedDB. Ao abrir a página nova, a
fila passa para `meta["pendentes-de:?"]` (de quem não se sabe — não sobe sozinha em nome de quem entrou) e esvazia-se: antes desta
ronda, a edição não subia, não se via e não havia aviso. Agora há um aviso «Há N edições guardadas neste aparelho que não chegaram
a ser enviadas» com Ver (sem enviar) · Enviar em meu nome (sobe como uma gravação normal, com a MESMA base de versão: um conflito
fica como conflito, nunca por cima) · Descarregar (.md, ou .zip com várias, sem rede) · Esconder aviso (os dados ficam e voltam a
ver-se no Início). Nada se apaga.
Controlo: a troca normal entre duas contas continua a NÃO subir as edições da outra conta, nem a mostrá-las.
Na página falsa (`pagina_falsa.py`) e, no fim, contra o servidor REAL num espaço temporário (a edição chega à nota; um conflito
deixa a nota igual e a versão guardada em offline-versoes).
Cada teste falha na página anterior a esta ronda pela razão certa (`BASALT_STATIC=<cópia antiga>/basalt/static` para a página
falsa, `TESTE_STATIC=…` para a do servidor real)."""
import json
import os
import shutil
import threading
import time
import zipfile
from pathlib import Path

import pytest

from pagina_falsa import ESTADO, NOTA, com_nota, conflito, esperar, esperar_py, pronta, reset

ANTIGA = "EDICAO FEITA NA PAGINA ANTIGA"
AVISO_VISIVEL = ("(() => { const a = document.getElementById('pendentes-aviso');"
                 " return !!a && !a.hidden && a.getClientRects().length > 0; })()")
AVISO_TEXTO = "(() => { const a = document.getElementById('pendentes-aviso'); return a && !a.hidden ? a.innerText : ''; })()"
A_PARTE = ("window.__t.idbGet('meta', 'pendentes-de:?').then(x => x ? {fila: (x.fila || []).map(i => i.texto),"
           " conflitos: (x.conflitos || []).map(c => c.teu)} : null)")
FILA = "window.__t.idbGetAll('fila').then(l => l.map(x => x.texto))"
GUARDAR = "([t, g]) => window.__t.guardarEdicaoOffline('n1', 'S novo', t, g).then(() => 'guardou', e => 'recusou: ' + e.message)"


def _enviar_em_meu_nome(page):
    """«Enviar em meu nome». Ronda 13 (c): já não está no aviso — abre-se a lista no Início, «Enviar em meu nome» mostra o início
    de cada edição e só «Confirmar e enviar» as põe na fila. (Na página da ronda 12 o botão estava no aviso e enviava logo.)"""
    if page.query_selector("#pendentes-aviso [data-pendentes=enviar]"):
        page.click("#pendentes-aviso [data-pendentes=enviar]")
        return
    page.click("#pendentes-link")
    page.click(".pendentes-modal [data-pendentes=enviar]")
    page.click(".pendentes-modal [data-pendentes=confirmar-enviar]")


def _confirmado(page, dono, ms=8000):
    return esperar(page, f"window.__t.donoOk() === null || (window.__t.donoOk() === true && window.__t.dono() === '{dono}')", ms)


def _item(texto, nome="n1", base="S novo"):
    """uma edição na fila, tal como a página anterior a guardava (sem dono em lado nenhum)."""
    return {"nome": nome, "base_texto": base, "texto": texto, "quando": 1792451549715, "rev": "antiga-" + nome}


def _conflito_antigo(nome, teu):
    """um conflito por resolver, no formato da página anterior (sem `rev`)."""
    return {"nome": nome, "actual": "TEXTO DOS AGENTES", "teu": teu, "hash_actual": "H9", "copia": None, "proposta": None,
            "proposta_hash": None, "proposta_tipo": None, "resumo": "", "quando": 1792451549000}


def _pagina_antiga_deixou(ctx, base, fila=(), conflitos=()):
    """o browser como a página anterior o deixou: a fila (e conflitos) por enviar e NENHUM dono gravado na cópia. Depois abre-se
    a página desta versão (recarregar): ela confirma a conta e põe isso à parte. No fim o servidor volta a aceitar envios."""
    reset(pacote=[dict(NOTA, texto="S novo", hash="H2")], sinc_503=True)
    ESTADO["sinc_solta"].set()
    page = ctx.new_page()
    page.on("dialog", lambda d: d.accept())
    page.goto(base + "/")
    pronta(page)
    com_nota(page)
    _confirmado(page, "d-ana")
    page.evaluate("""([fila, confl]) => Promise.all([].concat(fila.map(i => window.__t.idbPut('fila', i)),
                         confl.map(c => window.__t.idbPut('conflitos', c)), [window.__t.idbDelete('meta', 'dono')]))""",
                  [list(fila), list(conflitos)])
    page.reload()
    pronta(page)
    _confirmado(page, "d-ana")
    esperar(page, "window.__t.idbGet('meta', 'pendentes-de:?').then(x => !!x)", 5000)
    page.wait_for_timeout(300)
    ESTADO["sinc"].clear()
    ESTADO["sinc_base"].clear()
    ESTADO["sinc_503"] = False
    return page


@pytest.mark.lento
def test_a_edicao_da_pagina_antiga_tem_aviso_e_ve_se_sem_enviar_nada(base, contexto):
    page = _pagina_antiga_deixou(contexto(locale="pt-PT"), base, fila=[_item(ANTIGA)])
    guardado = page.evaluate(A_PARTE)
    assert guardado == {"fila": [ANTIGA], "conflitos": []}, f"pré-condição: a página pôs a fila à parte ({guardado})"
    visto = esperar(page, AVISO_VISIVEL, 5000)
    assert visto, f"a edição deixada pela página anterior ficou INVISÍVEL: à parte {guardado}, nenhum aviso na página"
    texto = page.evaluate(AVISO_TEXTO)
    assert "Há 1 edição guardada neste aparelho que não chegou a ser enviada" in texto, texto
    # ronda 13 (c): o aviso tem só a contagem + Descarregar + Esconder aviso; «Ver» (e «Enviar em meu nome») estão na lista do Início
    for botao in ("Descarregar", "Esconder aviso"):
        assert botao in texto, f"falta «{botao}» no aviso: {texto!r}"
    assert "Enviar em meu nome" not in texto, f"o aviso ainda envia sem confirmação: {texto!r}"
    page.click("#pendentes-link")
    assert esperar(page, "!!document.querySelector('.pendentes-modal')"), "«Ver» não abriu a lista"
    lista = page.inner_text(".pendentes-modal")
    assert "n1" in lista and ANTIGA in lista, f"a lista não mostra a nota e o texto: {lista!r}"
    page.wait_for_timeout(600)
    assert ESTADO["sinc"] == [], f"«Ver» enviou alguma coisa: {ESTADO['sinc']}"
    assert page.evaluate(A_PARTE) == guardado and page.evaluate(FILA) == [], "«Ver» mexeu no que estava guardado"


@pytest.mark.lento
def test_descarregar_uma_edicao_da_um_md_sem_rede(base, contexto):
    page = _pagina_antiga_deixou(contexto(locale="pt-PT"), base, fila=[_item(ANTIGA)])
    assert esperar(page, AVISO_VISIVEL, 5000), "sem aviso: não há onde descarregar"
    ESTADO["modo"] = "sem-rede"                                        # descarregar não precisa do servidor
    with page.expect_download(timeout=8000) as d:
        page.click("#pendentes-aviso [data-pendentes=descarregar]")
    f = d.value
    assert f.suggested_filename == "n1.md", f.suggested_filename
    assert Path(f.path()).read_text(encoding="utf-8") == ANTIGA
    ESTADO["modo"] = "ok"
    assert ESTADO["sinc"] == [] and page.evaluate(A_PARTE) == {"fila": [ANTIGA], "conflitos": []}, "descarregar mexeu nos dados"


@pytest.mark.lento
def test_descarregar_varias_da_um_zip_com_cada_nota(base, contexto):
    page = _pagina_antiga_deixou(contexto(locale="pt-PT"), base, fila=[_item(ANTIGA)],
                                 conflitos=[_conflito_antigo("n2", "Á OUTRA — também da página antiga\nlinha 2\n")])
    assert esperar(page, AVISO_VISIVEL, 5000), "sem aviso"
    assert "Há 2 edições guardadas neste aparelho que não chegaram a ser enviadas" in page.evaluate(AVISO_TEXTO)
    with page.expect_download(timeout=8000) as d:
        page.click("#pendentes-aviso [data-pendentes=descarregar]")
    f = d.value
    assert f.suggested_filename.startswith("basalt-edicoes-por-enviar-") and f.suggested_filename.endswith(".zip"), f.suggested_filename
    with zipfile.ZipFile(f.path()) as z:
        assert z.testzip() is None, "o .zip está estragado (CRC)"
        nomes = sorted(z.namelist())
        assert nomes == ["n1.md", "n2 (conflito).md"], nomes
        assert z.read("n1.md").decode("utf-8") == ANTIGA
        assert z.read("n2 (conflito).md").decode("utf-8") == "Á OUTRA — também da página antiga\nlinha 2\n"


@pytest.mark.lento
def test_enviar_em_meu_nome_sobe_com_a_mesma_base_e_sai_do_que_estava_a_parte(base, contexto):
    page = _pagina_antiga_deixou(contexto(locale="pt-PT"), base, fila=[_item(ANTIGA)])
    assert esperar(page, AVISO_VISIVEL, 5000), "sem aviso: não há como enviar"
    _enviar_em_meu_nome(page)
    assert esperar_py(page, lambda: ESTADO["sinc"], 8000), "«Enviar em meu nome» não enviou nada"
    assert ESTADO["sinc"] == [(ANTIGA, "d-ana")], f"enviou outra coisa, ou noutro nome: {ESTADO['sinc']}"
    assert ESTADO["sinc_base"] == ["S novo"], f"não subiu com a base de versão que a página antiga guardou: {ESTADO['sinc_base']}"
    assert esperar(page, "window.__t.idbGetAll('fila').then(l => l.length === 0)"), "ficou na fila depois de gravado"
    assert page.evaluate(A_PARTE) is None, "continua «à parte» depois de enviado"
    assert esperar(page, "!" + AVISO_VISIVEL), "o aviso continua depois de enviado"


@pytest.mark.lento
def test_enviar_em_meu_nome_com_conflito_fica_conflito_nunca_por_cima(base, contexto):
    page = _pagina_antiga_deixou(contexto(locale="pt-PT"), base, fila=[_item(ANTIGA)])
    ESTADO["sinc_409"] = {"estado": "conflito", "erro": "Os agentes mudaram a nota.", "actual": "TEXTO DOS AGENTES",
                          "hash_actual": "H9", "copia": "n1.20261020-100000.abcdef0123.md"}
    assert esperar(page, AVISO_VISIVEL, 5000), "sem aviso: não há como enviar"
    _enviar_em_meu_nome(page)
    assert esperar_py(page, lambda: ESTADO["sinc"], 8000), "não enviou"
    assert esperar(page, "window.__t.idbGet('conflitos', 'n1').then(c => !!c)"), "o conflito não ficou registado"
    c = conflito(page)
    assert c["teu"] == ANTIGA and c["actual"] == "TEXTO DOS AGENTES", c
    assert ESTADO["put"] == [] and ESTADO["nota_texto"] == "S novo", "escreveu por cima da nota"
    page.evaluate("location.hash = '#/nota/n1'")
    assert esperar(page, "!!document.querySelector('.conflito-aviso') && document.body.innerText.indexOf('%s') !== -1" % ANTIGA, 6000), \
        "a nota não mostra o conflito (com a edição da página antiga como «a tua»)"


@pytest.mark.lento
def test_esconder_aviso_guarda_tudo_e_o_inicio_continua_a_mostrar(base, contexto):
    page = _pagina_antiga_deixou(contexto(locale="pt-PT"), base, fila=[_item(ANTIGA)])
    assert esperar(page, AVISO_VISIVEL, 5000), "sem aviso"
    page.click("#pendentes-aviso [data-pendentes=esconder]")
    assert esperar(page, "!" + AVISO_VISIVEL), "«Esconder aviso» não escondeu"
    assert page.evaluate(A_PARTE) == {"fila": [ANTIGA], "conflitos": []}, "esconder mexeu nos dados"
    page.reload()
    pronta(page)
    _confirmado(page, "d-ana")
    page.wait_for_timeout(800)
    assert not page.evaluate(AVISO_VISIVEL), "o aviso escondido voltou sozinho ao recarregar"
    page.evaluate("location.hash = '#/'")
    assert esperar(page, "!!document.getElementById('pendentes-link')", 6000), "o Início não mostra as edições guardadas à parte"
    assert "(1)" in page.inner_text("#pendentes-link")
    page.click("#pendentes-link")
    assert esperar(page, "!!document.querySelector('.pendentes-modal')"), "o Início não abre a lista"
    assert ANTIGA in page.inner_text(".pendentes-modal")
    # chega outra edição à parte: o aviso volta (escondeu-se aquele conjunto, não os que vierem depois)
    page.evaluate("""() => window.__t.idbGet('meta', 'pendentes-de:?').then(v => { v.fila.push({nome: 'n2', base_texto: 'x',
        texto: 'OUTRA', quando: 1, rev: 'antiga-n2'}); return window.__t.idbPut('meta', v); })
        .then(() => window.__t.atualizarAvisoPendentes())""")
    assert esperar(page, AVISO_VISIVEL, 3000) and "Há 2 edições" in page.evaluate(AVISO_TEXTO), "o aviso não voltou com uma edição nova"
    assert ESTADO["sinc"] == [], "esconder/ver enviou alguma coisa"


# Controlo (ronda 8 continua): a troca normal de conta NÃO sobe nem mostra as edições da outra conta.
@pytest.mark.lento
def test_controlo_troca_de_conta_nao_sobe_nem_mostra_as_edicoes_da_outra(base, contexto):
    reset(pacote=[dict(NOTA, texto="S novo", hash="H2")], sinc_503=True)
    ESTADO["sinc_solta"].set()
    page = contexto(locale="pt-PT").new_page()
    page.on("dialog", lambda d: d.accept())
    page.goto(base + "/")
    pronta(page)
    com_nota(page)
    _confirmado(page, "d-ana")
    assert page.evaluate(GUARDAR, ["EDICAO DA ANA", page.evaluate("window.__t.geracao()")]) == "guardou"
    ESTADO.update(dono="d-bea", sinc_503=False)
    page.reload()
    pronta(page)
    assert _confirmado(page, "d-bea")
    page.evaluate("window.__t.sincronizarFila()")
    page.wait_for_timeout(1200)
    assert not [h for t, h in ESTADO["sinc"] if t == "EDICAO DA ANA" and h != "d-ana"], f"subiu em nome da bea: {ESTADO['sinc']}"
    assert not page.evaluate(AVISO_VISIVEL), "a bea vê um aviso com as edições da ana"
    assert "EDICAO DA ANA" not in page.content(), "o texto da ana aparece na página da bea"
    guardado = page.evaluate("window.__t.idbGet('meta', 'pendentes-de:d-ana').then(x => x ? x.fila.map(i => i.texto) : null)")
    assert guardado == ["EDICAO DA ANA"], f"a edição da ana devia continuar guardada à parte, em nome dela: {guardado}"


# As de OUTRA conta conhecida voltam sozinhas quando ela entrar; no Início só se diz quantas há (nunca o texto).
@pytest.mark.lento
def test_inicio_conta_as_de_outra_conta_sem_mostrar_o_texto(base, contexto):
    reset(pacote=[dict(NOTA, texto="S novo", hash="H2")], sinc_503=True)
    ESTADO["sinc_solta"].set()
    page = contexto(locale="pt-PT").new_page()
    page.goto(base + "/")
    pronta(page)
    com_nota(page)
    _confirmado(page, "d-ana")
    page.evaluate(GUARDAR, ["EDICAO DA ANA", page.evaluate("window.__t.geracao()")])
    ESTADO.update(dono="d-bea")
    page.reload()
    pronta(page)
    _confirmado(page, "d-bea")
    page.evaluate("location.hash = '#/'")
    assert esperar(page, "!!document.getElementById('pendentes-inicio') && !document.getElementById('pendentes-inicio').hidden", 6000), \
        "o Início não diz que há edições de outra conta guardadas neste browser"
    linha = page.inner_text("#pendentes-inicio")
    assert "outra conta" in linha and "EDICAO DA ANA" not in linha and not page.query_selector("#pendentes-link"), linha


# Depois de «Terminar sessão» a cópia fica com dono "" (ninguém). Uma edição que lá entrasse antes de outra conta entrar perdia-se
# (`trocarDono` só guardava à parte com um dono conhecido); passa a ficar em «pendentes-de:?», com o aviso.
@pytest.mark.lento
def test_fila_com_dono_vazio_nao_se_perde_ao_entrar_uma_conta(base, contexto):
    reset(pacote=[dict(NOTA, texto="S novo", hash="H2")], sinc_503=True)
    ESTADO["sinc_solta"].set()
    page = contexto(locale="pt-PT").new_page()
    page.goto(base + "/")
    pronta(page)
    com_nota(page)
    _confirmado(page, "d-ana")
    page.evaluate("""i => Promise.all([window.__t.idbPut('fila', i), window.__t.idbPut('meta', {chave: 'dono', valor: ''})])""",
                  _item("ESCRITA SEM DONO"))
    page.reload()
    pronta(page)
    _confirmado(page, "d-ana")
    page.wait_for_timeout(800)
    guardado, fila = page.evaluate(A_PARTE), page.evaluate(FILA)
    assert guardado == {"fila": ["ESCRITA SEM DONO"], "conflitos": []}, f"a edição perdeu-se (fila {fila}, à parte {guardado})"
    assert not [h for t, h in ESTADO["sinc"] if t == "ESCRITA SEM DONO"], "subiu sozinha em nome de quem entrou"


# ───────────── contra o servidor REAL (num espaço temporário) ─────────────

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
GANCHO_REAL = ('  window.__t = {idbGet: idbGet, idbGetAll: idbGetAll, idbPut: idbPut, idbDelete: idbDelete, '
               'dono: function(){ return typeof DONO === "undefined" ? null : DONO; }, '
               'donoOk: function(){ return typeof DONO_OK === "undefined" ? null : DONO_OK; }};\n  iniciarOffline();')


@pytest.fixture(scope="module")
def real(tmp_path_factory, request, navegador):
    from basalt import config, web
    tmp = tmp_path_factory.mktemp("r12")
    t = tmp / "basalt.toml"
    t.write_text(TOML.format(dados=tmp / "dados"), encoding="utf-8")
    cfg = config.carregar(t, ambiente={})
    origem = Path(os.environ.get("TESTE_STATIC") or web.STATIC).resolve()
    estatico = tmp / "static"
    shutil.copytree(origem, estatico)
    html = (estatico / "index.html").read_text(encoding="utf-8")
    assert html.count("  iniciarOffline();") == 1
    (estatico / "index.html").write_text(html.replace("  iniciarOffline();", GANCHO_REAL), encoding="utf-8")
    original = web.STATIC
    web.STATIC = estatico
    request.addfinalizer(lambda: setattr(web, "STATIC", original))
    srv = web.criar_servidor(cfg, host="127.0.0.1", porta=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    esp = cfg.espaco("ana")
    for d in (esp.real, esp.copia):
        d.mkdir(parents=True, exist_ok=True)
    yield "http://127.0.0.1:%d" % srv.server_address[1], esp
    srv.shutdown()
    srv.server_close()


def _nota_real(esp, nome, corpo):
    texto = FM.format(nome=nome) + corpo
    for d in (esp.real, esp.copia):
        (d / f"{nome}.md").write_text(texto, encoding="utf-8")
    return texto


def _real_antiga_deixou(navegador, base, item):
    ctx = navegador.new_context(service_workers="block", locale="pt-PT")
    page = ctx.new_page()
    page.goto(base + "/")
    assert esperar(page, "!!window.__t && window.__t.donoOk() === true", 15000), "a página não confirmou a conta"
    page.evaluate("i => Promise.all([window.__t.idbPut('fila', i), window.__t.idbDelete('meta', 'dono')])", item)
    page.reload()
    assert esperar(page, "!!window.__t && window.__t.donoOk() === true", 15000)
    return ctx, page


@pytest.mark.lento
def test_real_enviar_em_meu_nome_chega_a_nota(real, navegador):
    base, esp = real
    original = _nota_real(esp, "barcos", "# Barcos\nTexto com o Pico.\n")
    nova = original + "Linha escrita sem internet na página antiga.\n"
    ctx, page = _real_antiga_deixou(navegador, base, {"nome": "barcos", "base_texto": original, "texto": nova,
                                                      "quando": int(time.time() * 1000), "rev": "antiga-1"})
    try:
        assert esperar(page, AVISO_VISIVEL, 8000), "a edição da página antiga ficou invisível (sem aviso)"
        _enviar_em_meu_nome(page)
        assert esperar_py(page, lambda: (esp.copia / "barcos.md").read_text(encoding="utf-8") == nova, 15000), \
            "a edição não chegou à nota"
        assert esperar(page, "window.__t.idbGetAll('fila').then(l => l.length === 0)"), "ficou na fila"
    finally:
        ctx.close()


@pytest.mark.lento
def test_real_conflito_deixa_a_nota_igual_e_guarda_a_versao(real, navegador):
    base, esp = real
    actual = _nota_real(esp, "ilhas", "# Ilhas\nO Pico tem a montanha mais alta.\n")
    base_antiga = FM.format(nome="ilhas") + "# Ilhas\nO Pico tem uma montanha.\n"
    minha = FM.format(nome="ilhas") + "# Ilhas\nO Pico tem a montanha de Portugal.\n"
    ctx, page = _real_antiga_deixou(navegador, base, {"nome": "ilhas", "base_texto": base_antiga, "texto": minha,
                                                      "quando": int(time.time() * 1000), "rev": "antiga-2"})
    try:
        assert esperar(page, AVISO_VISIVEL, 8000), "a edição da página antiga ficou invisível (sem aviso)"
        _enviar_em_meu_nome(page)
        assert esperar(page, "window.__t.idbGet('conflitos', 'ilhas').then(c => !!c)", 15000), "o conflito não ficou registado"
        c = page.evaluate("window.__t.idbGet('conflitos', 'ilhas')")
        assert c["teu"] == minha and c["actual"] == actual, c
        assert (esp.copia / "ilhas.md").read_text(encoding="utf-8") == actual, "escreveu por cima da nota"
        guardadas = [p.read_text(encoding="utf-8") for p in esp.versoes_offline.glob("ilhas.*.md")]
        assert minha in guardadas, f"a versão sem internet não ficou guardada em {esp.versoes_offline.name}: {guardadas}"
    finally:
        ctx.close()
