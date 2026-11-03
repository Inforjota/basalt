"""r22 (PREVIEW_EPOCA_v1, achado m1/R3 da auditoria eu-xhigh de 24/10) — em Chromium do Playwright contra `pagina_falsa.py`.

A pré-visualização do editor só fazia a verificação SÍNCRONA da cópia (`copiaMudouDesde(retratoVista, null)`: a memória e o
localStorage). Uma troca de conta noutro separador que, neste, só a época PERSISTENTE (a cache `basaltcfg-epoca`) já mostra —
antes de chegar o evento «storage» ou o aviso do service worker — deixava a pré-visualização desenhar-se com a vista de A.
Agora espera pela `leituraMudou()`, que lê a época persistente como último await, e não desenha se ela mudou.

O teste falha na página do 27a68f2 (`BASALT_STATIC=<cópia antiga>/basalt/static`); o controlo passa nas duas."""
import pytest

from pagina_falsa import esperar, reset
from test_r22_pagina import PREVIEW_COM, _abrir_editor, _escrever
from test_ronda13b_pagina import _aberta

pytestmark = pytest.mark.lento

# o que um separador de B deixa na cache da época quando começa a revogação (comecarRevogacao), SEM o localStorage nem o
# aviso do service worker terem chegado a este
EPOCA_SO_PERSISTENTE = """async () => {
  await (await caches.open('basaltcfg-epoca')).put('/__epoca', new Response('9999999999.outro-separador'));
}"""


def test_r22_m1_pre_visualizacao_nao_desenha_com_a_epoca_persistente_mudada(base, contexto):
    reset(nota_texto="PRIVADO_A")
    ctx, page = _aberta(contexto, base)
    _abrir_editor(page)
    assert page.evaluate("window.__t.dono()") == "d-ana", "pré-condição: a cópia é de A"
    page.evaluate(EPOCA_SO_PERSISTENTE)
    _escrever(page, " NOVO_A")
    page.wait_for_timeout(1500)
    assert not page.evaluate(PREVIEW_COM.format(t="NOVO_A")), \
        f"a pré-visualização desenhou-se com a época persistente mudada: {page.inner_text('#ed-preview')[:300]!r}"
    assert "mudou" in page.inner_text("#ed-preview"), "a pré-visualização devia dizer porque não se desenha"


def test_controlo_r22_m1_pre_visualizacao_com_a_epoca_igual_desenha_se(base, contexto):
    reset(nota_texto="PRIVADO_A")
    ctx, page = _aberta(contexto, base)
    _abrir_editor(page)
    _escrever(page, " NOVO_A")
    assert esperar(page, PREVIEW_COM.format(t="NOVO_A")), "controlo: a pré-visualização devia desenhar-se"
