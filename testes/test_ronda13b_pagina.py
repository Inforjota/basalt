"""Ronda 13b (página) — achado 3 da revisão externa r13, em Chromium do Playwright contra `pagina_falsa.py`.

Achado 3: a página ainda entregava e mostrava o que leu da conta A depois de mudar de conta, «Sair» ou trancar a cópia.
    a) rede: o apiGet() verificava a cópia no fim só nas respostas da cópia do service worker — uma resposta da REDE com o corpo
       a ler (res.json) entregava-se mesmo com outra conta, «Sair», a cópia trancada ou outra conta noutro separador.
    b) vista: a vista da nota espera ainda pela geração, pela fila/conflito e pelo markdown (embeds) antes de desenhar, e só
       comparava o DONO — que neste separador pode não ter mudado (trancada, outra conta noutro separador).
Agora a mesma verificação final (dono, outra conta, geração, época, tranca) vale para as respostas da rede e da cópia, e as vistas
que ainda esperam depois de ler (nota, início, índice, entrada) voltam a verificar a seguir a cada await, antes de desenhar. As
caches em memória (embeds, pré-visualizações, grafo, salto rápido) deitam-se fora quando muda a conta ou a geração da cópia.
Cada teste falha na página anterior a esta ronda pela razão certa (`BASALT_STATIC=<cópia antiga>/basalt/static`), menos os
controlos."""
import pytest

from pagina_falsa import ESTADO, esperar, pronta, reset

pytestmark = pytest.mark.lento


def _aberta(contexto, base, **k):
    ctx = contexto(**k)
    page = ctx.new_page()
    page.goto(base + "/")
    pronta(page)
    assert esperar(page, "window.__t.donoOk() === true"), "a página não confirmou o dono da cópia"
    return ctx, page


def _b_entrou(page):
    """B entrou noutro separador: o servidor já responde com B e este separador ainda não confirmou o dono."""
    ESTADO["dono"] = "d-bea"
    page.evaluate("window.__t.donoPorConfirmar()")


# Pára o corpo (res.json) da 1.ª resposta da REDE cujo URL contém `alvo`, até window.__soltar().
PARAR_CORPO = """(alvo) => {
  window.__parado = false;
  var soltar; window.__solta = new Promise(function(r){ soltar = r; }); window.__soltar = soltar;
  var json = Response.prototype.json;
  Response.prototype.json = async function(){
    if(!window.__parado && String(this.url).indexOf(alvo) !== -1 && this.headers.get('X-Basalt-From-Cache') !== '1'){
      window.__parado = true; await window.__solta; }
    return json.call(this); };
}"""

# Pára a vista da nota na releitura da geração (a 1.ª coisa que ela espera depois de a nota chegar), até window.__soltar().
PARAR_GERACAO = """() => {
  window.__parado = false;
  var soltar; window.__solta = new Promise(function(r){ soltar = r; }); window.__soltar = soltar;
  var g = window.__t.geracaoActual;
  window.__t.setGeracaoActual(async function(){
    if(!window.__parado && /\\babrir\\b/.test(String(new Error().stack))){ window.__parado = true; await window.__solta; }
    return g.apply(this, arguments); });
}"""


def _mudar(ctx, page, base, mudanca):
    """o que muda com a leitura parada: outra conta (neste separador), «Sair», a cópia trancada (um 401 noutro pedido), ou B
    entrou noutro separador (este só sabe pela geração e pela época, no evento «storage»)."""
    if mudanca == "outra-conta":
        _b_entrou(page)
        page.evaluate("window.__t.verificarDono()")
        assert page.evaluate("window.__t.dono()") == "d-bea", "pré-condição: a cópia deste separador passou a B"
    elif mudanca == "sair":
        page.evaluate("window.basaltAntesDeSair()")
        assert page.evaluate("window.__t.dono()") == "", "pré-condição: a cópia ficou sem dono"
    elif mudanca == "trancada":
        page.evaluate("window.__t.bloquearCopiaLocal()")
        assert page.evaluate("window.__t.bloqueada()") is True
    elif mudanca == "outro-separador":
        g0 = page.evaluate("window.__t.geracao()")
        ESTADO["dono"] = "d-bea"
        outra = ctx.new_page()
        outra.goto(base + "/")
        pronta(outra)
        assert esperar(outra, "window.__t.dono() === 'd-bea' && window.__t.donoOk() === true"), "pré-condição: B entrou no outro"
        assert esperar(page, f"window.__t.geracao() > {g0}"), "pré-condição: este separador viu a geração nova"
        assert page.evaluate("window.__t.dono()") == "d-ana", "pré-condição: este separador ainda não mudou de dono"


# ───────────────────────── achado 3a: a resposta da rede com o corpo a ler ─────────────────────────
LER_N1 = """() => { window.__r = undefined;
  window.__t.apiGet('/api/nota?nome=n1').then(function(d){ window.__r = d && d.texto; },
                                             function(e){ window.__r = 'ERRO ' + (e && e.message); }); }"""

MUDANCAS_REDE = ["outra-conta", "sair", "trancada", "outro-separador"]


@pytest.mark.parametrize("mudanca", MUDANCAS_REDE)
def test_r13b_3a_resposta_da_rede_de_a_nao_se_entrega_se_a_copia_mudou_enquanto_o_corpo_se_lia(base, contexto, mudanca):
    reset(nota_texto="PRIVADO_A")
    ctx, page = _aberta(contexto, base)
    page.evaluate(PARAR_CORPO, "/api/nota?nome=n1")
    page.evaluate(LER_N1)
    assert esperar(page, "window.__parado === true"), "o ensaio não parou no corpo da resposta da rede"
    _mudar(ctx, page, base, mudanca)
    page.evaluate("window.__soltar()")
    assert esperar(page, "window.__r !== undefined"), "o pedido parado não acabou"
    res = page.evaluate("window.__r")
    assert res != "PRIVADO_A", f"a página entregou a resposta da rede de A depois de «{mudanca}» a meio do corpo"
    assert isinstance(res, str) and res.startswith("ERRO"), res


def test_controlo_r13b_3a_sem_nada_a_meio_a_resposta_da_rede_entrega_se(base, contexto):
    reset(nota_texto="PRIVADO_A")
    ctx, page = _aberta(contexto, base)
    page.evaluate(PARAR_CORPO, "/api/nota?nome=n1")
    page.evaluate(LER_N1)
    assert esperar(page, "window.__parado === true"), "o ensaio não parou no corpo da resposta da rede"
    page.wait_for_timeout(300)
    page.evaluate("window.__soltar()")
    assert esperar(page, "window.__r !== undefined"), "o pedido parado não acabou"
    assert page.evaluate("window.__r") == "PRIVADO_A"


# ───────────────────────── achado 3b: a vista da nota entre ler e desenhar ─────────────────────────
# «geracao»: a releitura da geração (o caso da revisão); «embed»: o corpo da nota embutida (![[n2]], numa linha só), que a
# vista espera antes de desenhar — os dois depois de o apiGet() já ter entregado a nota
CASOS_VISTA = [("geracao", "trancada"), ("geracao", "outro-separador"), ("embed", "trancada"), ("embed", "outro-separador")]


def _parar_vista(page, onde):
    if onde == "geracao":
        page.evaluate(PARAR_GERACAO)
    else:
        page.evaluate(PARAR_CORPO, "/api/nota?nome=n2")


@pytest.mark.parametrize("onde,mudanca", CASOS_VISTA, ids=[f"{o}-{m}" for o, m in CASOS_VISTA])
def test_r13b_3b_vista_da_nota_de_a_nao_se_desenha_se_a_copia_mudou_antes_de_desenhar(base, contexto, onde, mudanca):
    reset(nota_texto="PRIVADO_A\n\n![[n2]]")
    ctx, page = _aberta(contexto, base)
    _parar_vista(page, onde)
    page.evaluate("location.hash = '#/nota/n1'")
    assert esperar(page, "window.__parado === true"), f"o ensaio não parou em «{onde}»"
    _mudar(ctx, page, base, mudanca)
    page.evaluate("window.__soltar()")
    page.wait_for_timeout(1500)
    visto = page.inner_text("body")
    assert "PRIVADO_A" not in visto, f"a vista desenhou a nota de A depois de «{mudanca}» durante «{onde}»: {visto[:400]!r}"


@pytest.mark.parametrize("onde", ["geracao", "embed"])
def test_controlo_r13b_3b_sem_nada_a_meio_a_nota_desenha_se(base, contexto, onde):
    reset(nota_texto="PRIVADO_A\n\n![[n2]]")
    ctx, page = _aberta(contexto, base)
    _parar_vista(page, onde)
    page.evaluate("location.hash = '#/nota/n1'")
    assert esperar(page, "window.__parado === true"), f"o ensaio não parou em «{onde}»"
    page.wait_for_timeout(300)
    page.evaluate("window.__soltar()")
    assert esperar(page, "!!document.querySelector('.nota-corpo') && "
                         "document.querySelector('.nota-corpo').innerText.indexOf('PRIVADO_A') !== -1"), "a nota devia ver-se"
    assert esperar(page, "!!document.querySelector('.embed-nota-corpo')"), "e a nota embutida também"


# ───────────────────────── as vistas que juntam várias leituras (início, índice, entrada) ─────────────────────────
# a vista, a leitura que fica parada (as outras já entregaram, com A) e o que só se vê quando a vista se desenhou.
# «inicio-diario»: a leitura que pode falhar sem estragar o início (.catch → null) — o erro dela não chega à vista, que tem de
# ver sozinha que a cópia mudou
INICIO = "document.body.innerText.indexOf('2026-10-06 10:00') !== -1"
VISTAS = {
    "inicio": ("#/", "/api/indice", INICIO),
    "inicio-diario": ("#/", "/api/diario", INICIO),
    "indice": ("#/notas", "/api/categorias", "!!document.getElementById('indice-filtro')"),
    "entrada": ("#/entrada", "/api/entrada/auto", "!!document.getElementById('entrada-intro')"),
}
CASOS_VISTAS = [(v, m) for v in VISTAS for m in ("trancada", "outro-separador")]


def _abrir_vista_parada(page, vista):
    hash_, alvo, _ = VISTAS[vista]
    page.evaluate("location.hash = '#/recentes'")              # sai do início (aberto ao carregar), para o voltar a abrir
    page.wait_for_timeout(500)
    page.evaluate(PARAR_CORPO, alvo)
    page.evaluate(f"location.hash = '{hash_}'")
    assert esperar(page, "window.__parado === true"), f"o ensaio não parou em {alvo}"


@pytest.mark.parametrize("vista,mudanca", CASOS_VISTAS, ids=[f"{v}-{m}" for v, m in CASOS_VISTAS])
def test_r13b_3_vista_com_varias_leituras_nao_desenha_o_que_leu_de_a(base, contexto, vista, mudanca):
    ctx, page = _aberta(contexto, base)
    _abrir_vista_parada(page, vista)
    _mudar(ctx, page, base, mudanca)
    page.evaluate("window.__soltar()")
    page.wait_for_timeout(1500)
    assert not page.evaluate(VISTAS[vista][2]), f"a vista «{vista}» desenhou o que leu de A depois de «{mudanca}» a meio"


@pytest.mark.parametrize("vista", list(VISTAS))
def test_controlo_r13b_3_vista_com_varias_leituras_sem_nada_a_meio_desenha_se(base, contexto, vista):
    ctx, page = _aberta(contexto, base)
    _abrir_vista_parada(page, vista)
    page.wait_for_timeout(300)
    page.evaluate("window.__soltar()")
    assert esperar(page, VISTAS[vista][2]), f"controlo: a vista «{vista}» devia desenhar-se"


# ───────────────────────── as caches em memória: o embed lido por A não aparece na nota de B ─────────────────────────
def test_r13b_3_embed_lido_por_a_nao_aparece_na_nota_de_b_no_mesmo_separador(base, contexto):
    reset(nota_texto="CORPO_A\n\n![[n2]]\n\nEMBED_PRIVADO_A")
    ctx, page = _aberta(contexto, base)
    page.evaluate("location.hash = '#/nota/n1'")
    assert esperar(page, "!!document.querySelector('.embed-nota-corpo') && "
                         "document.querySelector('.embed-nota-corpo').innerText.indexOf('EMBED_PRIVADO_A') !== -1"), \
        "pré-condição: a nota de A com a nota embutida"
    page.evaluate("location.hash = '#/'")
    assert esperar(page, "document.body.innerText.indexOf('2026-10-06 10:00') !== -1"), "pré-condição: o início"
    _b_entrou(page)                                            # B entra; a próxima leitura confirma-o neste separador
    ESTADO["nota_texto"] = "CORPO_B\n\n![[n2]]"
    page.evaluate("location.hash = '#/nota/n1'")
    assert esperar(page, "!!document.querySelector('.nota-corpo') && "
                         "document.querySelector('.nota-corpo').innerText.indexOf('CORPO_B') !== -1"), "a nota de B devia ver-se"
    assert page.evaluate("window.__t.dono()") == "d-bea"
    page.wait_for_timeout(500)
    visto = page.inner_text("body")
    assert "EMBED_PRIVADO_A" not in visto, f"a nota de B mostrou a nota embutida que A leu (cache em memória): {visto[:400]!r}"
