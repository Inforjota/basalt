"""r22 (página) — achado R3 (grave) da revisão externa da r13b (23/10), em Chromium do Playwright contra `pagina_falsa.py`.

A ronda 13b verificava a cópia (conta, outra conta, geração, época, tranca) antes de desenhar a nota — mas só na 1.ª vez:
`renderNotaCompleta()` deitava o verificador fora depois de o usar. Voltar à leitura pelo «Cancelar» do editor, ou o fim de
«Gravar», desenhava a nota de A com a cópia já de B (ou trancada). E a pré-visualização do editor esperava pelo
`prepararMarkdown()` (as notas embutidas) e desenhava a seguir, sem voltar a ver se a cópia mudou entretanto.
Agora todas as vezes que a nota se desenha passam pelo verificador da vista (VERIFICA_SEMPRE_v1), e a pré-visualização não se
desenha se a cópia mudou enquanto se preparava (o texto fica no editor). O «Juntar à mão» de um conflito também passa pelo
verificador, e o pedido «abre já no editor» que ele deixa (ABRIR_EM_EDICAO) cai quando a vista pára — senão a nota de B que se
reabre a seguir abria directamente no editor.

Cada teste falha na página do 15beb01 pela razão certa (`BASALT_STATIC=<cópia antiga>/basalt/static`), menos os controlos."""
import threading

import pytest

from pagina_falsa import ESTADO, esperar, reset
from test_ronda13b_pagina import PARAR_CORPO, _aberta, _mudar

pytestmark = pytest.mark.lento

MUDANCAS = ["outra-conta", "trancada", "outro-separador"]
CORPO_COM = "!!document.querySelector('.nota-corpo') && document.querySelector('.nota-corpo').innerText.indexOf('{t}') !== -1"
PREVIEW_COM = "!!document.querySelector('#ed-preview') && document.querySelector('#ed-preview').innerText.indexOf('{t}') !== -1"


def _abrir_editor(page):
    page.evaluate("location.hash = '#/nota/n1'")
    assert esperar(page, CORPO_COM.format(t="PRIVADO_A")), "pré-condição: a nota de A desenhada"
    page.click("#editar-btn")
    page.wait_for_selector("#ed-gravar", timeout=8000)
    page.wait_for_timeout(400)


def _escrever(page, texto):
    """uma alteração só (um evento): uma pré-visualização só."""
    page.click("#ed-editor-host")
    page.keyboard.press("Control+End")
    page.keyboard.insert_text(texto)


def _mudar_e_b_no_servidor(ctx, page, base, mudanca):
    _mudar(ctx, page, base, mudanca)
    ESTADO["nota_texto"] = "CORPO_B"                 # o que a nota tem agora para quem a ler (B): «PRIVADO_A» só de um desenho velho


# ───────────────────────── «Cancelar» o editor: a nota volta a desenhar-se ─────────────────────────
@pytest.mark.parametrize("mudanca", MUDANCAS)
def test_r22_r3_cancelar_o_editor_depois_de_a_copia_mudar_nao_desenha_a_nota_de_a(base, contexto, mudanca):
    reset(nota_texto="PRIVADO_A")
    ctx, page = _aberta(contexto, base)
    _abrir_editor(page)
    _mudar_e_b_no_servidor(ctx, page, base, mudanca)
    page.click("#ed-cancelar")
    page.wait_for_timeout(1500)
    visto = page.inner_text("body")
    assert "PRIVADO_A" not in visto, f"o «Cancelar» desenhou a nota de A depois de «{mudanca}»: {visto[:400]!r}"


def test_controlo_r22_r3_cancelar_sem_nada_a_meio_a_nota_volta(base, contexto):
    reset(nota_texto="PRIVADO_A")
    ctx, page = _aberta(contexto, base)
    _abrir_editor(page)
    page.click("#ed-cancelar")
    assert esperar(page, CORPO_COM.format(t="PRIVADO_A")), "controlo: a nota devia voltar a ver-se"
    assert not page.query_selector("#ed-gravar")


# ───────────────────────── «Gravar»: a nota desenha-se outra vez quando o PUT acaba ─────────────────────────
@pytest.mark.parametrize("mudanca", ["trancada", "outro-separador"])
def test_r22_r3_gravar_com_a_copia_mudada_durante_o_put_nao_desenha_a_nota_de_a(base, contexto, mudanca):
    reset(nota_texto="PRIVADO_A", put_solta=threading.Event())
    ctx, page = _aberta(contexto, base)
    _abrir_editor(page)
    _escrever(page, " NOVO_A")
    page.click("#ed-gravar")
    assert _esperar_put(page), "o ensaio não parou no PUT"
    _mudar(ctx, page, base, mudanca)
    ESTADO["put_solta"].set()
    page.wait_for_timeout(2000)
    assert not page.evaluate(CORPO_COM.format(t="NOVO_A")), \
        f"o fim de «Gravar» desenhou a nota de A depois de «{mudanca}»: {page.inner_text('body')[:400]!r}"


def test_controlo_r22_r3_gravar_sem_nada_a_meio_a_nota_gravada_ve_se(base, contexto):
    reset(nota_texto="PRIVADO_A", put_solta=threading.Event())
    ctx, page = _aberta(contexto, base)
    _abrir_editor(page)
    _escrever(page, " NOVO_A")
    page.click("#ed-gravar")
    assert _esperar_put(page), "o ensaio não parou no PUT"
    ESTADO["put_solta"].set()
    assert esperar(page, CORPO_COM.format(t="NOVO_A")), "controlo: a nota gravada devia ver-se"


def _esperar_put(page):
    for _ in range(80):
        if ESTADO["put"]:
            return True
        page.wait_for_timeout(100)
    return False


# ───────────────────────── a pré-visualização à espera da nota embutida ─────────────────────────
@pytest.mark.parametrize("mudanca", MUDANCAS)
def test_r22_r3_pre_visualizacao_parada_no_embed_nao_desenha_depois_de_a_copia_mudar(base, contexto, mudanca):
    reset(nota_texto="PRIVADO_A")
    ctx, page = _aberta(contexto, base)
    _abrir_editor(page)
    page.evaluate(PARAR_CORPO, "/api/nota?nome=n2")
    _escrever(page, "\n\n![[n2]]\n\nNOVO_A")
    assert esperar(page, "window.__parado === true"), "o ensaio não parou na nota embutida"
    _mudar_e_b_no_servidor(ctx, page, base, mudanca)
    page.evaluate("window.__soltar()")
    page.wait_for_timeout(1500)
    assert not page.evaluate(PREVIEW_COM.format(t="NOVO_A")), \
        f"a pré-visualização desenhou o texto de A depois de «{mudanca}»: {page.inner_text('#ed-preview')[:300]!r}"


def test_controlo_r22_r3_pre_visualizacao_sem_nada_a_meio_desenha_se(base, contexto):
    reset(nota_texto="PRIVADO_A")
    ctx, page = _aberta(contexto, base)
    _abrir_editor(page)
    page.evaluate(PARAR_CORPO, "/api/nota?nome=n2")
    _escrever(page, "\n\n![[n2]]\n\nNOVO_A")
    assert esperar(page, "window.__parado === true"), "o ensaio não parou na nota embutida"
    page.wait_for_timeout(300)
    page.evaluate("window.__soltar()")
    assert esperar(page, PREVIEW_COM.format(t="NOVO_A")), "controlo: a pré-visualização devia desenhar-se"


# ───────────────────────── o conflito: «Juntar à mão» desenha a nota (e abre o editor) mais tarde ─────────────────────────
CONFLITO = "window.__t.idbPut('conflitos', {nome: 'n1', actual: 'S velho', teu: 'TEU_PRIVADO_A', quando: 1})"


@pytest.mark.parametrize("mudanca", MUDANCAS)
def test_r22_r3_juntar_a_mao_depois_de_a_copia_mudar_nao_abre_o_texto_de_a(base, contexto, mudanca):
    reset(nota_texto="PRIVADO_A")
    ctx, page = _aberta(contexto, base)
    page.evaluate(CONFLITO)
    page.evaluate("location.hash = '#/nota/n1'")
    page.wait_for_selector("#conf-mao", timeout=8000)
    _mudar_e_b_no_servidor(ctx, page, base, mudanca)
    page.click("#conf-mao")
    page.wait_for_timeout(1500)
    visto = page.inner_text("body")
    assert "TEU_PRIVADO_A" not in visto, f"o «Juntar à mão» mostrou o texto de A depois de «{mudanca}»: {visto[:400]!r}"
    # e o pedido de A («abre esta nota já no editor») não fica para a nota de B que se reabre a seguir
    assert not page.query_selector("#ed-gravar"), f"o «Juntar à mão» de A abriu o editor depois de «{mudanca}»: {visto[:400]!r}"


def test_controlo_r22_r3_juntar_a_mao_sem_nada_a_meio_abre_o_editor(base, contexto):
    reset(nota_texto="PRIVADO_A")
    ctx, page = _aberta(contexto, base)
    page.evaluate(CONFLITO)
    page.evaluate("location.hash = '#/nota/n1'")
    page.wait_for_selector("#conf-mao", timeout=8000)
    page.click("#conf-mao")
    page.wait_for_selector("#ed-gravar", timeout=8000)
    assert esperar(page, "document.body.innerText.indexOf('TEU_PRIVADO_A') !== -1"), "controlo: o editor com as duas versões"
