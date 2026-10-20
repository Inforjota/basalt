"""Ronda 13 (página e service worker) — o lado da página (basalt/static/index.html), em Chromium do Playwright contra
`pagina_falsa.py` (com o service worker a sério onde a corrida passa por ele).

Achados 1A/1B do porte e 1/2 da revisão externa r10: uma resposta da cópia do service worker já em mão (o corpo a ler, ou a
    geração a reler no fim) entregava-se depois de o service worker ter revogado a cópia sozinho — um 404 que pôs a lápide e
    apagou a entrada, um 401 que a trancou. A página não via nada: nem a geração nem as trancas dela mudam. Agora há a ÉPOCA da
    cópia (memória deste separador + «basalt-copia-epoca» + «/__epoca»): o service worker muda-a e avisa as páginas antes de
    acabar, e o apiGet() lê-a como último await e só entrega se nada mudou desde que o pedido partiu.
Achado 4 do porte / 3 da r10: os separadores abertos com a página de antes ficavam com o apiGet() de antes (0 recargas). O
    protocolo passa a 5: a página de antes (4) recarrega quando o service worker novo a passa a controlar; com alterações por
    gravar no editor só avisa — e o service worker novo já não lhe serve a cópia. A página nova só aceita a cópia do protocolo 5.
r4-1: anexos e notas novas seguem com a conta com que o editor / o modal abriu (antes: a conta de agora).
r4-2: a vista da nota lê a fila e o conflito numa transacção, com as guardas de notasDaCopia() (antes: fora, sem dono nem geração).
r4-3: guardarEdicaoDeOutraConta() com a cópia de volta à conta da edição também verifica «cópia apagada» e a geração.
Pendentes: a) «Descarregar» (com confirmação) para as edições de outra conta conhecida; b) /api/fonte sem rede, da cópia do
    service worker, com «cópia de <data>»; c) «Enviar em meu nome» só depois de confirmar, com o início de cada edição.
Cada teste falha na página anterior a esta ronda pela razão certa (`BASALT_STATIC=<cópia antiga>/basalt/static`), menos os
controlos. `BASALT_STATIC_ANTERIOR=<pasta com index.html e sw.js>` troca a «página de antes» dos testes de separadores abertos
pela versão em produção (prova de que uma página dessas, aberta, se acerta quando este service worker entra)."""
import io
import json
import os
import re
import zipfile
from pathlib import Path

import pytest

import pagina_falsa
from pagina_falsa import ESTADO, NOTA, buscar, com_sw, esperar, esperar_py, pronta, reset

pytestmark = pytest.mark.lento

FILA = "window.__t.idbGetAll('fila').then(l => l.map(x => x.texto))"


def _aberta(contexto, base, **k):
    page = contexto(**k).new_page()
    page.goto(base + "/")
    pronta(page)
    assert esperar(page, "window.__t.donoOk() === true"), "a página não confirmou o dono da cópia"
    return page


def _abrir_editor_e_escrever(page, texto):
    page.evaluate("location.hash = '#/nota/n1'")
    page.wait_for_selector("#editar-btn", timeout=8000)
    page.click("#editar-btn")
    page.wait_for_selector("#ed-gravar", timeout=8000)
    page.wait_for_timeout(400)
    page.click("#ed-editor-host")
    page.keyboard.press("End")
    page.keyboard.type(texto)
    page.wait_for_timeout(600)


def _b_entrou(page):
    """B entrou noutro separador: o servidor já responde com B e este separador ainda não confirmou o dono."""
    ESTADO["dono"] = "d-bea"
    page.evaluate("window.__t.donoPorConfirmar()")


# ───────────────────────── achados 1A/1B do porte, 1/2 da r10: o service worker revoga a meio ─────────────────────────
# Pontos de paragem: «corpo» = o res.json() da resposta da cópia; «geracao» = a releitura da geração no fim (dentro de
# copiaApagadaDesde, depois do corpo). O pedido arranca e fica parado; o teste faz o 404/401 passar pelo service worker (pedido
# directo, que só ele vê) e solta.
PARAR = """(onde) => {
  window.__parado = false; window.__depois = false; window.__r = undefined;
  var soltar; window.__solta = new Promise(function(r){ soltar = r; }); window.__soltar = soltar;
  var json = Response.prototype.json;
  Response.prototype.json = async function(){
    var daCopia = this.headers.get('X-Basalt-From-Cache') === '1';
    if(daCopia && onde === 'corpo' && !window.__parado){ window.__parado = true; await window.__solta; }
    var r = await json.call(this);
    if(daCopia) window.__depois = true;
    return r; };
  var g = window.__t.geracaoActual;
  window.__t.setGeracaoActual(async function(){
    if(onde === 'geracao' && window.__depois && !window.__parado && /copiaApagadaDesde/.test(String(new Error().stack))){
      window.__parado = true; await window.__solta; }
    return g.apply(this, arguments); });
  window.__t.apiGet('/api/nota?nome=n1').then(function(d){ window.__r = d && d.texto; },
                                             function(e){ window.__r = 'ERRO ' + (e && e.message); });
}"""


def _copia_no_sw(contexto, base):
    """a nota n1 («DA_COPIA») na cópia do service worker; depois sem rede, a cópia serve-se."""
    reset(nota_texto="DA_COPIA")
    ctx = contexto(sw=True)
    page = ctx.new_page()
    com_sw(page, base)
    assert esperar(page, "window.__t.donoOk() === true"), "a página não confirmou o dono da cópia"
    r = buscar(page, "/api/nota?nome=n1")
    assert r["status"] == 200 and not r["cache"], f"pré-condição: com rede, do servidor ({r})"
    return ctx, page


def _sem_rede_serve_da_copia(page):
    ESTADO["modo"] = "sem-rede"
    r = buscar(page, "/api/nota?nome=n1")
    assert r["cache"] == "1" and "DA_COPIA" in r["texto"], f"pré-condição: sem rede, a cópia do service worker serve-se ({r})"


CASOS_SW = [("corpo", 404, "este"), ("corpo", 404, "outro"), ("geracao", 401, "este"), ("geracao", 401, "outro")]


@pytest.mark.parametrize("onde,estado,separador", CASOS_SW, ids=[f"{o}-{e}-{s}" for o, e, s in CASOS_SW])
def test_porte_1_r10_resposta_da_copia_a_meio_nao_se_entrega_depois_de_o_sw_a_revogar(base, contexto, onde, estado, separador):
    ctx, page = _copia_no_sw(contexto, base)
    quem = page
    if separador == "outro":                                  # o 404/401 chega a outro separador da mesma origem
        quem = ctx.new_page()
        com_sw(quem, base)
    _sem_rede_serve_da_copia(page)
    page.evaluate(PARAR, onde)
    assert esperar(page, "window.__parado === true"), f"o ensaio não parou em «{onde}»"
    # a rede volta só para isto: o servidor responde 404 (a nota foi apagada) ou 401 (a sessão caiu). O pedido não é da página
    # parada nem passa pelo apiGet de nenhuma — só o service worker o vê (põe a lápide e apaga / tranca) e acaba.
    ESTADO.update(modo="ok", nota_status=estado)
    r = buscar(quem, "/api/nota?nome=n1")
    assert r["status"] == estado, f"o service worker não recebeu o {estado}: {r}"
    ESTADO["modo"] = "sem-rede"                               # e volta a não haver rede
    page.evaluate("window.__soltar()")
    assert esperar(page, "window.__r !== undefined"), "o pedido parado não acabou"
    res = page.evaluate("window.__r")
    assert res != "DA_COPIA", (f"a página entregou a nota da cópia depois de o service worker a ter revogado a meio ({estado} "
                               f"durante «{onde}», noutro separador: {separador == 'outro'})")
    assert isinstance(res, str) and res.startswith("ERRO"), res
    # e o service worker continua a não a servir
    assert buscar(page, "/api/nota?nome=n1").get("cache") != "1"


@pytest.mark.parametrize("onde", ["corpo", "geracao"])
def test_controlo_porte_1_sem_nada_a_meio_a_resposta_da_copia_entrega_se(base, contexto, onde):
    ctx, page = _copia_no_sw(contexto, base)
    _sem_rede_serve_da_copia(page)
    page.evaluate(PARAR, onde)
    assert esperar(page, "window.__parado === true"), f"o ensaio não parou em «{onde}»"
    page.wait_for_timeout(300)
    page.evaluate("window.__soltar()")
    assert esperar(page, "window.__r !== undefined"), "o pedido parado não acabou"
    assert page.evaluate("window.__r") == "DA_COPIA"


# ───────────────────────── achado 4 do porte / 3 da r10 (página nova): só a cópia do protocolo 5 ─────────────────────────
LER_N2 = "window.__t.apiGet('/api/nota?nome=n2').then(d => d && d.texto, e => 'ERRO ' + (e && e.message))"


def _copia_com_protocolo(contexto, base, protocolo):
    page = _aberta(contexto, base)
    cab = {"Content-Type": "application/json", "X-Basalt-From-Cache": "1", "X-Basalt-Dono": "d-ana"}
    if protocolo:
        cab["X-Basalt-Protocolo"] = protocolo
    page.route("**/api/nota?nome=n2", lambda route: route.fulfill(
        status=200, body=json.dumps(dict(NOTA, nome="n2", texto="DA_COPIA", hash="HC")), headers=cab))
    return page.evaluate(LER_N2)


@pytest.mark.parametrize("protocolo", ["", "4"])
def test_porte_4_a_copia_de_um_service_worker_de_antes_nao_se_entrega(base, contexto, protocolo):
    r = _copia_com_protocolo(contexto, base, protocolo)
    assert r != "DA_COPIA", f"a página aceitou a cópia de um service worker de protocolo «{protocolo or 'nenhum'}» (sem a época)"


def test_controlo_porte_4_a_copia_do_protocolo_5_entrega_se(base, contexto):
    assert _copia_com_protocolo(contexto, base, "5") == "DA_COPIA"


# ───────────────────────── achado 4 do porte / 3 da r10: os separadores abertos com a página de antes ─────────────────────────
def _pasta_anterior(tmp_path):
    """a «página de antes»: BASALT_STATIC_ANTERIOR (a versão em produção) ou esta, no protocolo 4 da produção (com a versão da
    cache dela e o service worker sempre diferente em bytes, para haver actualização)."""
    fora = os.environ.get("BASALT_STATIC_ANTERIOR")
    if fora:
        return Path(fora)
    d = tmp_path / "anterior"
    d.mkdir()
    html = (pagina_falsa.STATIC / "index.html").read_text(encoding="utf-8")
    sw = (pagina_falsa.STATIC / "sw.js").read_text(encoding="utf-8")
    html = re.sub(r"var PROTOCOLO_COPIA = \d+;", "var PROTOCOLO_COPIA = 4;", html)
    sw = re.sub(r"var PROTOCOLO = \d+;", "var PROTOCOLO = 4;", sw)
    sw = re.sub(r'CACHE_VERSAO = "basalt-v\d+"', 'CACHE_VERSAO = "basalt-v13"', sw) + "\n// página de antes (ensaio)\n"
    (d / "index.html").write_text(html, encoding="utf-8")
    (d / "sw.js").write_text(sw, encoding="utf-8")
    return d


TOASTS = """() => { window.__toasts = [];
  new MutationObserver(function(){ document.querySelectorAll('#toast-root .toast').forEach(function(t){
    if(window.__toasts.indexOf(t.textContent) === -1) window.__toasts.push(t.textContent); }); })
    .observe(document.getElementById('toast-root'), {childList: true, subtree: true}); }"""


def _avaliar(page, js):
    try:
        return page.evaluate(js)
    except Exception:                                         # a meio de uma recarga
        return None


def _separador_de_antes(contexto, base, tmp_path):
    """um separador aberto com a página de antes, controlado pelo service worker de antes, com uma edição na fila."""
    reset(pacote=[dict(NOTA, texto="S novo", hash="H2")], sinc_503=True)
    pagina_falsa.STATIC = _pasta_anterior(tmp_path)
    ctx = contexto(sw=True)
    page = ctx.new_page()
    com_sw(page, base)
    assert esperar(page, "!!navigator.serviceWorker.controller && window.__t.donoOk() === true"), "a página de antes não arrancou"
    assert page.evaluate("window.__t.protocolo()") == 4, "pré-condição: a página de antes fala o protocolo 4"
    r = page.evaluate("window.__t.guardarEdicaoOffline('n1', 'S novo', 'EDICAO_NA_FILA', window.__t.geracao(), window.__t.dono())"
                      ".then(() => 'guardou', e => 'recusou: ' + e.message)")
    assert r == "guardou", r
    page.evaluate("window.__antes = 1")
    page.evaluate(TOASTS)
    return page


def _sw_novo(page, atual):
    pagina_falsa.STATIC = atual
    page.evaluate("navigator.serviceWorker.getRegistration().then(r => r.update())")


def test_porte_4_r10_3_separador_com_a_pagina_de_antes_recarrega_quando_o_sw_novo_entra_sem_perder_a_fila(base, contexto, tmp_path):
    atual = pagina_falsa.STATIC
    try:
        page = _separador_de_antes(contexto, base, tmp_path)
        _sw_novo(page, atual)
        recarregou = esperar_py(page, lambda: _avaliar(page, "window.__antes === undefined && !!window.__t") is True, 12000)
        avisos = _avaliar(page, "window.__toasts") or []
        assert recarregou, f"o separador com a página de antes não recarregou quando o service worker novo entrou (avisos: {avisos})"
        pronta(page)
        assert page.evaluate("window.__t.protocolo()") == 5, "depois de recarregar não ficou com a página nova"
        assert esperar(page, "window.__t.idbGetAll('fila').then(l => l.some(x => x.texto === 'EDICAO_NA_FILA'))"), \
            f"a edição que estava na fila perdeu-se ao recarregar: {page.evaluate(FILA)}"
    finally:
        pagina_falsa.STATIC = atual


def test_porte_4_r10_3_com_alteracoes_por_gravar_so_avisa_e_o_sw_novo_ja_nao_lhe_serve_a_copia(base, contexto, tmp_path):
    atual = pagina_falsa.STATIC
    try:
        page = _separador_de_antes(contexto, base, tmp_path)
        _abrir_editor_e_escrever(page, " POR_GRAVAR")
        assert page.evaluate("(document.querySelector('#ed-dirty-marca') || {}).textContent || ''").strip(), "pré-condição: editor por gravar"
        _sw_novo(page, atual)
        avisou = esperar(page, "window.__toasts.some(t => t.indexOf('a app foi actualizada') !== -1)", 12000)
        assert avisou, f"o separador com alterações por gravar não foi avisado (avisos: {page.evaluate('window.__toasts')})"
        assert page.evaluate("window.__antes") == 1, "recarregou com alterações por gravar no editor"
        assert "POR_GRAVAR" in page.evaluate("document.getElementById('ed-editor-host').innerText"), "o texto do editor perdeu-se"
        # o service worker novo guarda a cópia (pedido da página nova, protocolo 5) e, sem rede, não a serve à de antes (4)
        assert buscar(page, "/api/nota?nome=n1")["status"] == 200
        ESTADO["modo"] = "sem-rede"
        antes = page.evaluate("""() => fetch('/api/nota?nome=n1', {headers: {'X-Basalt-Dono': 'd-ana', 'X-Basalt-Protocolo': '4'}})
            .then(r => r.headers.get('X-Basalt-From-Cache'), e => 'falhou')""")
        assert antes != "1", "o service worker novo serviu a cópia a uma página de protocolo 4"
        assert buscar(page, "/api/nota?nome=n1").get("cache") == "1", "controlo: a página nova (5) recebe a cópia"
    finally:
        pagina_falsa.STATIC = atual


# ───────────────────────── r4-1: anexos e notas novas com a conta com que se abriu ─────────────────────────
LARGAR = """() => { var dt = new DataTransfer(); dt.items.add(new File(['conteudo do anexo'], 'imagem.png', {type: 'image/png'}));
  document.getElementById('ed-editor-host').dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true})); }"""


@pytest.mark.parametrize("trocou", [True, False], ids=["entrou-b", "controlo"])
def test_r4_1_anexo_largado_no_editor_de_a_nao_sobe_em_nome_de_b(base, contexto, trocou):
    page = _aberta(contexto, base)
    _abrir_editor_e_escrever(page, " X")
    if trocou:
        _b_entrou(page)
    page.evaluate(LARGAR)
    if trocou:
        page.wait_for_timeout(1500)
        donos = [hd for _, _, hd in ESTADO["anexos"]]
        assert "d-bea" not in donos, f"o anexo largado no editor aberto com A subiu em nome de B ({donos})"
        assert donos == [], f"o anexo subiu com a conta mudada: {donos}"
    else:
        assert esperar_py(page, lambda: len(ESTADO["anexos"]) == 1), "controlo: o anexo devia subir"
        assert ESTADO["anexos"][0][2] == "d-ana"


@pytest.mark.parametrize("trocou", [True, False], ids=["entrou-b", "controlo"])
def test_r4_1_nota_nova_preparada_com_a_nao_se_cria_em_nome_de_b(base, contexto, trocou):
    page = _aberta(contexto, base)
    page.evaluate("window.__t.openNovaNotaModal()")
    page.fill("#nn-nome", "nota_nova_de_a")
    if trocou:
        _b_entrou(page)
    page.click("#nn-criar")
    criadas = lambda: [d for m, c, d in ESTADO["donos"] if m == "POST" and c == "/api/nota"]   # noqa: E731
    if trocou:
        page.wait_for_timeout(1500)
        assert "d-bea" not in criadas(), f"a nota nova preparada com A foi criada em nome de B ({criadas()})"
        assert criadas() == [], criadas()
        assert "outra conta" in page.inner_text("#nn-erro-geral"), page.inner_text("#nn-erro-geral")
    else:
        assert esperar_py(page, lambda: criadas() == ["d-ana"]), f"controlo: a nota devia criar-se em nome de A ({criadas()})"


# ───────────────────────── r4-2: a vista da nota não mostra a fila / o conflito de outra conta ─────────────────────────
# Depois de a nota de A chegar (apiGet), outro separador passa a cópia para B (geração seguinte) com uma edição (ou um conflito)
# de B da nota com o mesmo nome. Sem «storage»: este separador não sabe de nada (o localStorage pode não funcionar).
TROCA_NA_VISTA = """([fila, conflito]) => {
  var orig = window.__t.apiGet; window.__trocou = false;
  window.__t.setApiGet(async function(path){
    var r = await orig.apply(this, arguments);
    if(!window.__trocou && String(path).indexOf('/api/nota?nome=n1') === 0){
      var g = await window.__t.idbGet('meta', 'geracao'), n = ((g && Number(g.valor)) || 0) + 1;
      await window.__t.idbPut('meta', {chave: 'dono', valor: 'd-bea'});
      await window.__t.idbPut('meta', {chave: 'geracao', valor: n});
      if(fila) await window.__t.idbPut('fila', fila);
      if(conflito) await window.__t.idbPut('conflitos', conflito);
      window.__trocou = true;
    }
    return r; });
}"""


def _conflito(teu):
    return {"nome": "n1", "actual": "S novo", "teu": teu, "hash_actual": "H2", "copia": None, "proposta": None,
            "proposta_hash": None, "proposta_tipo": None, "resumo": "", "quando": 2}


@pytest.mark.parametrize("o_que", ["fila", "conflito"])
def test_r4_2_vista_da_nota_de_a_nao_mostra_a_edicao_nem_o_conflito_de_b(base, contexto, o_que):
    page = _aberta(contexto, base)
    fila = {"nome": "n1", "base_texto": "S novo", "texto": "PRIVADO_B", "quando": 2, "rev": "rb"} if o_que == "fila" else None
    confl = _conflito("CONFLITO_PRIVADO_B") if o_que == "conflito" else None
    page.evaluate(TROCA_NA_VISTA, [fila, confl])
    page.evaluate("location.hash = '#/nota/n1'")
    assert esperar(page, "window.__trocou === true"), "o ensaio não trocou a cópia a meio da abertura"
    page.wait_for_timeout(1500)
    visto = page.inner_text("body")
    assert "PRIVADO_B" not in visto, f"a vista da nota de A mostrou a {o_que} de B: {visto[:400]!r}"


def test_controlo_r4_2_a_edicao_por_enviar_da_propria_conta_mostra_se(base, contexto):
    page = _aberta(contexto, base)
    page.evaluate("window.__t.idbPut('fila', {nome: 'n1', base_texto: 'S novo', texto: 'MINHA_EDICAO', quando: 2, rev: 'ra'})")
    page.evaluate("location.hash = '#/nota/n1'")
    assert esperar(page, "document.body.innerText.indexOf('MINHA_EDICAO') !== -1"), "controlo: a edição por enviar de A devia ver-se"


# ───────────────────────── r4-3: a edição de A não volta à fila com a cópia apagada ─────────────────────────
APAGOU_NOUTRO = """async (aviso) => {
  await window.__t.idbPut('meta', {chave: 'dono', valor: 'd-ana'});
  if(aviso === 'nada') return;
  await window.__t.idbPut('meta', {chave: 'copiaDesligada', valor: true, id: 'outro-separador'});
  if(aviso === 'com-aviso'){
    localStorage.setItem('basalt-copia-desligada', '1');
    window.dispatchEvent(new StorageEvent('storage', {key: 'basalt-copia-desligada', newValue: '1'}));
  }
}"""


def _editor_de_a_com_b_e_a_de_volta(contexto, base, aviso):
    """editor de A aberto; o separador adopta B; A volta noutro separador (a cópia é outra vez de A) e — salvo «nada» — apaga a
    cópia (com ou sem o aviso do «storage»)."""
    reset(sinc_503=True)
    page = _aberta(contexto, base)
    _abrir_editor_e_escrever(page, " EDICAO_DE_A")
    _b_entrou(page)
    page.evaluate("window.__t.verificarDono()")
    assert esperar(page, "window.__t.dono() === 'd-bea'"), "pré-condição: o separador adoptou B"
    page.evaluate(APAGOU_NOUTRO, aviso)
    page.click("#ed-gravar")
    page.wait_for_timeout(1500)
    return page


@pytest.mark.parametrize("aviso", ["com-aviso", "sem-aviso"])
def test_r4_3_edicao_de_a_gravada_depois_de_a_apagar_a_copia_nao_volta_a_fila(base, contexto, aviso):
    page = _editor_de_a_com_b_e_a_de_volta(contexto, base, aviso)
    fila = page.evaluate(FILA)
    assert not [t for t in fila if "EDICAO_DE_A" in (t or "")], f"a edição voltou à fila com a cópia apagada: {fila}"
    banner = page.inner_text("#ed-banner")
    assert "apagada" in banner, banner


def test_controlo_r4_3_com_a_copia_de_volta_a_a_e_ligada_a_edicao_vai_para_a_fila_dela(base, contexto):
    page = _editor_de_a_com_b_e_a_de_volta(contexto, base, "nada")
    assert esperar(page, "window.__t.idbGetAll('fila').then(l => l.some(x => (x.texto || '').indexOf('EDICAO_DE_A') !== -1))"), \
        page.evaluate(FILA)


# ───────────────────────── pendentes ─────────────────────────
def _item(texto, nome="n1", rev="r1"):
    return {"nome": nome, "base_texto": "S novo", "texto": texto, "quando": 1792451549715, "rev": rev}


def _por_a_parte(page, chave, fila, conta=None):
    v = {"chave": chave, "fila": fila, "conflitos": []}
    if conta:
        v["conta"] = conta
    page.evaluate("v => window.__t.idbPut('meta', v).then(() => window.__t.atualizarAvisoPendentes())", v)


# a) edições de outra conta conhecida: «Descarregar» com confirmação, nunca «Enviar»
DE_BEA = [_item("EDICAO_DE_BEA_1", "n1", "b1"), _item("EDICAO_DE_BEA_2", "n2", "b2")]


def test_pendente_a_descarregar_as_edicoes_de_outra_conta_pede_confirmacao_e_nao_as_mostra(base, contexto):
    reset(sinc_503=True)
    page = _aberta(contexto, base, locale="pt-PT")
    baixados = []
    page.on("download", lambda d: baixados.append(d.suggested_filename))
    _por_a_parte(page, "pendentes-de:d-bea", DE_BEA, conta="bea@exemplo.pt")
    assert esperar(page, "!!document.querySelector('#pendentes-inicio') && !document.querySelector('#pendentes-inicio').hidden")
    botao = "#pendentes-inicio [data-pendentes=descarregar-outra]"
    assert page.query_selector(botao), f"não há «Descarregar» para as edições de outra conta: {page.inner_text('#pendentes-inicio')!r}"
    page.click(botao)
    assert esperar(page, "!!document.querySelector('.pendentes-modal')"), "«Descarregar» não pediu confirmação"
    modal = page.inner_text(".pendentes-modal")
    assert "São edições de outra conta (bea@exemplo.pt). Descarregar para este aparelho?" in modal, modal
    assert "EDICAO_DE_BEA" not in page.inner_text("body"), "o texto das edições de outra conta mostrou-se"
    assert not page.query_selector(".pendentes-modal [data-pendentes=enviar], .pendentes-modal [data-pendentes=confirmar-enviar]"), \
        "as edições de outra conta podem enviar-se em nome de quem está"
    page.click(".pendentes-modal [data-pendentes=fechar]")                  # cancelar: nada se descarrega
    page.wait_for_timeout(500)
    assert baixados == [], baixados
    page.click(botao)
    with page.expect_download(timeout=8000) as d:
        page.click(".pendentes-modal [data-pendentes=confirmar-descarregar-outra]")
    f = d.value
    assert f.suggested_filename.startswith("basalt-edicoes-de-outra-conta-") and f.suggested_filename.endswith(".zip"), f.suggested_filename
    with zipfile.ZipFile(io.BytesIO(Path(f.path()).read_bytes())) as z:
        textos = sorted(z.read(n).decode("utf-8") for n in z.namelist())
    assert textos == ["EDICAO_DE_BEA_1", "EDICAO_DE_BEA_2"], textos
    guardado = page.evaluate("window.__t.idbGet('meta', 'pendentes-de:d-bea').then(v => (v.fila || []).map(i => i.texto))")
    assert guardado == ["EDICAO_DE_BEA_1", "EDICAO_DE_BEA_2"], f"descarregar mexeu no que estava guardado: {guardado}"
    assert page.evaluate(FILA) == [] and ESTADO["sinc"] == [], "descarregar enviou alguma coisa"


# c) edições de conta desconhecida: o aviso só conta, descarrega e esconde; enviar pede confirmação com o início de cada uma
EDICAO_C = "---\nname: n1\n---\n\n# Título da edição\n\nlinha um\nlinha dois\nlinha três\nLINHA_QUATRO_NAO_SE_VE"
AVISO = "(() => { const a = document.getElementById('pendentes-aviso'); return a && !a.hidden ? a.innerText : ''; })()"
A_PARTE = "window.__t.idbGet('meta', 'pendentes-de:?').then(x => x ? (x.fila || []).map(i => i.texto) : null)"


def _desconhecidas(contexto, base, fila):
    reset(sinc_503=True)
    page = _aberta(contexto, base, locale="pt-PT")
    _por_a_parte(page, "pendentes-de:?", fila)
    assert esperar(page, f"({AVISO}).length > 0"), "pré-condição: o aviso das edições guardadas à parte"
    return page


def test_pendente_c_o_aviso_so_tem_a_contagem_descarregar_e_esconder(base, contexto):
    page = _desconhecidas(contexto, base, [_item(EDICAO_C)])
    texto = page.evaluate(AVISO)
    assert "Há 1 edição guardada neste aparelho que não chegou a ser enviada" in texto, texto
    accoes = page.evaluate("[...document.querySelectorAll('#pendentes-aviso [data-pendentes]')].map(b => b.getAttribute('data-pendentes'))")
    assert accoes == ["descarregar", "esconder"], f"o aviso tem mais do que Descarregar e Esconder aviso: {accoes} ({texto!r})"


def test_pendente_c_enviar_mostra_o_inicio_de_cada_edicao_e_so_envia_o_que_se_viu(base, contexto):
    page = _desconhecidas(contexto, base, [_item(EDICAO_C)])
    page.click("#pendentes-link")
    assert esperar(page, "!!document.querySelector('.pendentes-modal [data-pendentes=enviar]')"), "a lista não tem «Enviar em meu nome»"
    page.click(".pendentes-modal [data-pendentes=enviar]")
    previsto = esperar(page, "!!document.querySelector('.pendentes-modal .pendentes-previsao')", 3000)
    page.wait_for_timeout(500)
    assert previsto, f"«Enviar em meu nome» não pediu confirmação com o início das edições (fila: {page.evaluate(FILA)})"
    assert page.evaluate(FILA) == [], "entrou na fila antes de confirmar"
    modal = page.inner_text(".pendentes-modal")
    assert "Título da edição" in modal and "linha um" in modal and "linha três" in modal, modal
    assert "LINHA_QUATRO_NAO_SE_VE" not in page.inner_text(".pendentes-modal .pendentes-previsao"), "a pré-visualização não é só o início"
    # chega outra edição depois de se ver a confirmação: essa não se viu, não entra
    _por_a_parte(page, "pendentes-de:?", [_item(EDICAO_C), _item("CHEGOU_DEPOIS", "n2", "r2")])
    page.click(".pendentes-modal [data-pendentes=confirmar-enviar]")
    assert esperar(page, "window.__t.idbGetAll('fila').then(l => l.length > 0)"), "«Confirmar e enviar» não pôs nada na fila"
    page.wait_for_timeout(400)
    assert page.evaluate(FILA) == [EDICAO_C], page.evaluate(FILA)
    assert page.evaluate(A_PARTE) == ["CHEGOU_DEPOIS"], "a edição que chegou depois da confirmação não ficou à parte"


def test_pendente_c_enviar_sem_confirmacao_nao_poe_nada_na_fila(base, contexto):
    page = _desconhecidas(contexto, base, [_item(EDICAO_C)])
    page.evaluate("window.__t.enviarPendentesEmMeuNome()")
    page.wait_for_timeout(1500)
    assert page.evaluate(FILA) == [], "enviou sem a confirmação com a pré-visualização"
    assert page.evaluate(A_PARTE) == [EDICAO_C]


# b) /api/fonte sem rede, da cópia do service worker, «cópia de <data>»
def _abrir_fonte(page):
    page.evaluate("location.hash = '#/notas'")
    page.wait_for_timeout(300)
    page.evaluate("location.hash = '#/fonte/docs/a.md'")
    return esperar(page, "!!document.getElementById('fonte-vista') || !!document.querySelector('.view-body .state-error')", 8000)


def test_pendente_b_fonte_sem_rede_mostra_a_copia_com_a_data_e_404_tira_a(base, contexto):
    reset()
    page = contexto(sw=True, locale="pt-PT").new_page()
    com_sw(page, base)
    assert esperar(page, "window.__t.donoOk() === true")
    assert _abrir_fonte(page) and page.query_selector("#fonte-vista"), "com rede, a fonte devia abrir"
    assert not page.query_selector("#fonte-copia-de"), "com rede não é cópia"
    ESTADO["modo"] = "sem-rede"
    _abrir_fonte(page)
    assert page.query_selector("#fonte-vista"), f"sem rede, a fonte não abriu da cópia: {page.inner_text('.view-body')!r}"
    assert "TEXTO DA FONTE" in page.inner_text("#fonte-vista")
    marca = page.inner_text("#fonte-copia-de") if page.query_selector("#fonte-copia-de") else ""
    assert re.match(r"cópia de \S", marca), f"sem rede, a fonte não diz de quando é a cópia: {marca!r}"
    # o texto saiu da fonte: um 404 tira-o da cópia (lápide), e sem rede já não se mostra
    ESTADO.update(modo="ok", fonte_status=404)
    _abrir_fonte(page)
    ESTADO["modo"] = "sem-rede"
    _abrir_fonte(page)
    assert not page.query_selector("#fonte-vista"), "o texto apagado na fonte continuou a mostrar-se sem rede"
