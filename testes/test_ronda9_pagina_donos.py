"""Ronda 9 (achados G1–G3 da ronda 2) — na página, nada sai nem entra sem o dono da cópia.

  · o envio de anexos leva o `X-Basalt-Dono` (antes ia sem ele: com outra conta entrada noutro separador, gravava no espaço dela);
  · sem saber o dono (o servidor ainda não o disse), a página não escreve nada — nem nota nem anexo — e diz porquê;
  · uma resposta da cópia do service worker só vale se trouxer o dono desta cópia (um service worker antigo, de cache comum a
    todas as contas, podia servir a de outra);
  · «ficar com a do servidor» num conflito tira da fila só a REVISÃO que o conflito guardou como «a tua» — o texto igual não
    prova que é a mesma edição (outro separador pode ter gravado uma nova, com o mesmo texto e outra base).
A cópia sem dono gravado (de antes da ronda 8) com edições por enviar está em test_ronda8_contas.py.
Chromium do Playwright contra `pagina_falsa.py`. Cada teste falha na página anterior a esta ronda pela razão certa
(`BASALT_STATIC=<cópia antiga>/basalt/static`), menos os controlos."""
import json

import pytest

from pagina_falsa import ESTADO, NOTA, abrir_nota_e_esperar, esperar, por_conflito, pronta, reset

pytestmark = pytest.mark.lento

FOTO = "() => new File([new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10])], 'foto.png', {type: 'image/png'})"


def _aberta(contexto, base, confirmar=True):
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    if confirmar:
        assert esperar(page, "window.__t.donoOk() === true"), "a página não confirmou o dono da cópia"
    return page


def _enviar_anexo(page):
    return page.evaluate(f"() => window.__t.enviarAnexo(({FOTO})()).then(r => ({{ok: r}}), "
                         "e => ({erro: String(e && e.message || e), semDono: !!(e && e.semDono)}))")


def test_envio_de_anexo_leva_o_dono_da_copia(base, contexto):
    page = _aberta(contexto, base)
    r = _enviar_anexo(page)
    pedidos = [d for m, c, d in ESTADO["donos"] if c == "/api/anexos"]
    assert ESTADO["anexos"] and ESTADO["anexos"][0][2] == "d-ana", f"o anexo foi enviado sem o dono da cópia (donos: {pedidos}; {r})"
    assert r.get("ok", {}).get("nome") == "foto.png", r


def test_sem_saber_o_dono_a_pagina_nao_escreve_nada_e_diz_porque(base, contexto):
    reset(dono=None)                                       # o servidor não disse (ainda) de quem é a cópia
    page = _aberta(contexto, base, confirmar=False)
    page.wait_for_timeout(600)
    put = page.evaluate("window.__t.apiSend('PUT', '/api/nota?nome=n1', {texto: 'SEM DONO', base_hash: 'H2'})"
                        ".then(r => ({ok: r}), e => ({erro: String(e && e.message || e), semDono: !!(e && e.semDono)}))")
    anexo = _enviar_anexo(page)
    assert ESTADO["put"] == [] and ESTADO["anexos"] == [], \
        f"sem dono, a página escreveu na mesma (notas: {ESTADO['put']}; anexos: {[(n, d) for n, _, d in ESTADO['anexos']]})"
    assert put.get("semDono") and anexo.get("semDono"), (put, anexo)
    assert "não gravei" in put["erro"], put                # o utilizador sabe que não ficou gravado (o texto continua no editor)


def test_copia_do_service_worker_de_outra_conta_nao_e_aceite(base, contexto):
    page = _aberta(contexto, base)

    def servir(dono):
        def f(route):
            route.fulfill(status=200, body=json.dumps(dict(NOTA, nome="n2", texto="DA CACHE de " + dono, hash="HC")),
                          headers={"Content-Type": "application/json", "X-Basalt-From-Cache": "1", "X-Basalt-Protocolo": "5", "X-Basalt-Dono": dono})
        return f

    ler = "window.__t.apiGet('/api/nota?nome=n2').then(d => d && d.texto, e => 'ERRO ' + (e && e.message))"
    page.route("**/api/nota?nome=n2", servir("d-outra"))
    r = page.evaluate(ler)
    assert r != "DA CACHE de d-outra", "a página aceitou da cópia do service worker uma nota de outra conta"
    page.unroute("**/api/nota?nome=n2")
    page.route("**/api/nota?nome=n2", servir("d-ana"))
    assert page.evaluate(ler) == "DA CACHE de d-ana"        # controlo: a cópia da mesma conta serve-se


def test_ficar_com_a_do_servidor_num_conflito_antigo_nao_apaga_uma_edicao_nova_com_o_mesmo_texto(base, contexto):
    reset(sinc_503=True)                                   # a fila não sobe durante o teste
    page = _aberta(contexto, base)
    por_conflito(page, teu="MEU", rev="r-antiga")
    assert abrir_nota_e_esperar(page, "conf-servidor")
    page.evaluate("window.__t.guardarEdicaoOffline('n1', 'outra base', 'MEU')")   # outro separador: edição nova, o mesmo texto
    page.click("#conf-servidor")
    page.wait_for_timeout(1200)
    item = page.evaluate("window.__t.idbGet('fila', 'n1')")
    assert item and item.get("texto") == "MEU" and item.get("base_texto") == "outra base", \
        f"a edição nova (mesmo texto, outra revisão) saiu da fila: {item or 'VAZIA'}"


def test_controlo_ficar_com_a_do_servidor_tira_da_fila_a_revisao_do_conflito(base, contexto):
    reset(sinc_503=True)
    page = _aberta(contexto, base)
    page.evaluate("window.__t.guardarEdicaoOffline('n1', 'base', 'MEU')")
    rev = page.evaluate("window.__t.idbGet('fila', 'n1').then(i => i && i.rev)")
    assert rev
    por_conflito(page, teu="MEU", rev=rev)
    assert abrir_nota_e_esperar(page, "conf-servidor")
    page.click("#conf-servidor")
    assert esperar(page, "window.__t.idbGet('fila', 'n1').then(i => !i)"), "a revisão do conflito ficou na fila"
