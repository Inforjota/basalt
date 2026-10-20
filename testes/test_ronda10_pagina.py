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


# ───────────────────────── G2 ─────────────────────────
def _copia_de_a(contexto, base):
    reset(pacote=[dict(NOTA, texto="PRIVADO_A", hash="HA")])
    page = _aberta(contexto, base)
    assert esperar(page, "window.__t.idbGet('notas', 'n1').then(n => !!n && n.texto === 'PRIVADO_A')"), "a cópia de A não ficou no aparelho"
    return page


LER_OFFLINE = "window.__t.tentarOffline('/api/nota?nome=n1').then(d => d === undefined ? 'NADA' : (d && d.texto), e => 'ERRO ' + (e && e.message))"


def test_g2_leitura_sem_rede_nao_devolve_a_nota_de_b_que_entrou_entre_ver_o_dono_e_ler(base, contexto):
    page = _copia_de_a(contexto, base)
    # depois de a leitura ver meta.dono = A, OUTRO separador troca a cópia para B e guarda o pacote de B (dono, geração, notas)
    page.evaluate("""() => { var orig = window.__t.idbGet; window.__feito = false;
      window.__t.setIdbGet(async function(loja, chave){ var r = await orig(loja, chave);
        if(loja === 'meta' && chave === 'dono' && !window.__feito){ window.__feito = true;
          var g = await orig('meta', 'geracao');
          await window.__t.idbPut('meta', {chave: 'dono', valor: 'd-bea'});
          await window.__t.idbPut('meta', {chave: 'geracao', valor: ((g && Number(g.valor)) || 0) + 1});
          await window.__t.idbPut('notas', {nome: 'n1', titulo: 'n1', texto: 'PRIVADO_B', hash: 'HB', editavel: true, categorias: []}); }
        return r; }); }""")
    r = page.evaluate(LER_OFFLINE)
    assert page.evaluate("window.__feito"), "o ensaio não trocou a conta a meio da leitura"
    assert r != "PRIVADO_B", "a leitura sem rede de A devolveu a nota de B (dono visto numa transacção, nota lida noutra)"


def test_g2_controlo_sem_troca_a_leitura_sem_rede_devolve_a_nota_de_a(base, contexto):
    page = _copia_de_a(contexto, base)
    assert page.evaluate(LER_OFFLINE) == "PRIVADO_A"


# ───────────────────────── G5 ─────────────────────────
APAGAR_NESTE = "await window.__t.desligarCopiaLocal();"
APAGAR_NOUTRO = ("var c = await caches.open('basaltcfg-copia-desligada'); await c.put('/__desligada', new Response('1'));"
                 "var g = await window.__t.idbGet('meta', 'geracao');"
                 "await window.__t.idbPut('meta', {chave: 'geracao', valor: ((g && Number(g.valor)) || 0) + 5});"
                 "await window.__t.idbPut('meta', {chave: 'copiaDesligada', valor: true});")


@pytest.mark.parametrize("onde", ["neste-separador", "noutro-separador"])
def test_g5_resposta_da_copia_do_sw_nao_se_entrega_se_a_copia_foi_apagada_a_ler_o_corpo(base, contexto, onde):
    page = _aberta(contexto, base)
    page.route("**/api/nota?nome=n2", lambda route: route.fulfill(
        status=200, body=json.dumps(dict(NOTA, nome="n2", texto="COPIA_APAGADA", hash="HC")),
        headers={"Content-Type": "application/json", "X-Basalt-From-Cache": "1", "X-Basalt-Protocolo": "5", "X-Basalt-Dono": "d-ana"}))
    apagar = APAGAR_NESTE if onde == "neste-separador" else APAGAR_NOUTRO
    page.evaluate("""() => { var orig = Response.prototype.json; window.__apagou = false;
      Response.prototype.json = async function(){
        if(this.headers.get('X-Basalt-From-Cache') === '1' && !window.__apagou){ window.__apagou = true; %s }
        return orig.call(this); }; }""" % apagar)
    r = page.evaluate("window.__t.apiGet('/api/nota?nome=n2').then(d => d && d.texto, e => 'ERRO ' + (e && e.message))")
    assert page.evaluate("window.__apagou"), "o ensaio não apagou a cópia a meio do res.json()"
    assert r != "COPIA_APAGADA", f"a página entregou a resposta da cópia apagada durante a leitura do corpo ({onde})"


def test_g5_controlo_sem_apagar_a_copia_do_sw_entrega_se(base, contexto):
    page = _aberta(contexto, base)
    page.route("**/api/nota?nome=n2", lambda route: route.fulfill(
        status=200, body=json.dumps(dict(NOTA, nome="n2", texto="DA CACHE", hash="HC")),
        headers={"Content-Type": "application/json", "X-Basalt-From-Cache": "1", "X-Basalt-Protocolo": "5", "X-Basalt-Dono": "d-ana"}))
    assert page.evaluate("window.__t.apiGet('/api/nota?nome=n2').then(d => d && d.texto, e => 'ERRO ' + (e && e.message))") == "DA CACHE"
