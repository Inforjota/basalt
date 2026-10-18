"""Ronda 10 (achados G1, G2 e G5 da auditoria r3) — na página, a identidade da conta acompanha cada edição e cada leitura.

  · G1: uma edição aberta com a conta A nunca é enviada (nem fica na fila) em nome de B, mesmo que o separador adopte B entre
    abrir e gravar; fica à parte, em nome de A (`pendentes-de:A`), e sobe quando A voltar;
  · G2: a leitura sem rede da IndexedDB verifica dono, geração e estado da cópia na MESMA transacção em que lê a nota;
  · G5: uma resposta da cópia do service worker é verificada outra vez DEPOIS de ler o corpo (`res.json()`), antes de a entregar.
Chromium do Playwright contra `pagina_falsa.py`. Cada teste falha na página de cbf5489 (`BASALT_STATIC=<cópia>/basalt/static`)."""
import json

import pytest

from pagina_falsa import ESTADO, NOTA, esperar, esperar_py, pronta, reset

pytestmark = pytest.mark.lento


def _aberta(contexto, base):
    page = contexto().new_page()
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
    page.wait_for_timeout(200)


# ───────────────────────── G1 ─────────────────────────
@pytest.mark.parametrize("rede", ["com-rede", "falha-no-put"])
def test_g1_edicao_aberta_com_a_conta_a_nunca_sobe_nem_fica_na_fila_em_nome_de_b(base, contexto, rede):
    reset(sinc_503=True)
    page = _aberta(contexto, base)
    _abrir_editor_e_escrever(page, " EDICAO_PRIVADA_A")
    # B entra noutro separador; este separador ainda não confirmou o dono (abriu sem rede com a cópia de A)
    ESTADO["dono"] = "d-bea"
    page.evaluate("window.__t.donoPorConfirmar()")
    if rede == "falha-no-put":
        ESTADO["put_falha_rede"] = True                      # o PUT (se partir) cai — a edição iria para a fila de B
    page.click("#ed-gravar")
    page.wait_for_timeout(2000)
    donos_put = [d for m, c, d in ESTADO["donos"] if m == "PUT"]
    assert "d-bea" not in donos_put, f"a edição de A foi enviada com o dono de B (PUT com donos {donos_put})"
    fila = page.evaluate("window.__t.idbGetAll('fila')")
    na_fila = [f.get("texto") for f in fila if "EDICAO_PRIVADA_A" in (f.get("texto") or "")]
    assert not na_fila, f"a edição de A ficou na fila da cópia (agora de {page.evaluate('window.__t.dono()')}): {na_fila}"
    sinc_b = [t for t, d in ESTADO["sinc"] if "EDICAO_PRIVADA_A" in t and d == "d-bea"]
    assert not sinc_b, f"a edição de A subiu pela fila em nome de B: {sinc_b}"
    # não se perde: fica à parte em nome de A
    pend = page.evaluate("window.__t.idbGet('meta', 'pendentes-de:d-ana')")
    assert pend and any("EDICAO_PRIVADA_A" in (i.get("texto") or "") for i in pend.get("fila", [])), \
        f"a edição de A não ficou guardada à parte para A: {pend}"
    banner = page.evaluate("(document.getElementById('ed-banner') || {}).textContent || ''")
    assert "outra conta" in banner, banner


def test_g1_controlo_sem_mudar_de_conta_grava_normalmente(base, contexto):
    reset(sinc_503=True)
    page = _aberta(contexto, base)
    _abrir_editor_e_escrever(page, " EDICAO_NORMAL")
    page.evaluate("window.__t.donoPorConfirmar()")           # por confirmar, mas a conta é a mesma
    page.click("#ed-gravar")
    assert esperar_py(page, lambda: any("EDICAO_NORMAL" in c.get("texto", "") for c in ESTADO["put"]))
    assert [d for m, c, d in ESTADO["donos"] if m == "PUT"] == ["d-ana"]
