"""Ronda 11 — na página, uma resposta da cópia do service worker só se entrega se, no fim de TODAS as leituras, a cópia não
foi trancada, apagada nem passou para outra conta.

`apiGet` já voltava a ver, depois de ler o corpo (`res.json()`), se a cópia estava trancada ou apagada (ronda 10). Mas essas
verificações também esperam (a marca do service worker, a geração na IndexedDB): uma tranca posta nesse intervalo — um 401
neste separador ou noutro — não era vista, e a nota entregava-se com a cópia já trancada. Agora a decisão final é tomada sem
mais nenhuma espera depois da última leitura.

Casos:
  · «corpo»: apagar a cópia / trancá-la enquanto se lê o corpo — já cobertos desde a ronda 10 (ficam como regressão);
  · «marca» e «geração»: trancar (neste separador ou noutro) enquanto se lê a marca do SW ou a geração, depois do corpo;
    apagar a cópia enquanto se lê a geração.
Chromium do Playwright contra `pagina_falsa.py`; a página anterior prova-se com `BASALT_STATIC=<cópia>/basalt/static`."""
import json

import pytest

from pagina_falsa import NOTA, esperar, pronta

pytestmark = pytest.mark.lento

MARCA_DESLIGADA = "basaltcfg-copia-desligada"

# as acções que podem acontecer a meio: as de «outro separador» chegam pelo evento «storage», como no browser
ACCOES = {
    "apagar": "window.__t.desligarCopiaLocal()",
    "trancar": "window.__t.bloquearCopiaLocal()",
    "trancar-noutro": ("(function(){ var id = 'outro-' + Math.random().toString(36).slice(2, 8);"
                       " localStorage.setItem('basalt-copia-bloqueada', id);"
                       " window.dispatchEvent(new StorageEvent('storage', {key: 'basalt-copia-bloqueada', newValue: id})); })()"),
}

# instala os pontos de paragem: o corpo (res.json), a marca «desligada» do SW (caches.has) e a geração (IndexedDB meta.geracao).
# «depois» = o corpo da resposta da cópia já foi lido; só então valem a marca e a geração (as de antes do corpo já eram vistas).
PREPARAR = """([onde, accao]) => {
  window.__feito = false; window.__depois = false;
  var agir = function(){ window.__feito = true; return %s[accao] ? eval(%s[accao]) : null; };
  var json = Response.prototype.json;
  Response.prototype.json = async function(){
    var daCopia = this.headers.get('X-Basalt-From-Cache') === '1';
    if(daCopia && onde === 'corpo' && !window.__feito) await agir();
    var r = await json.call(this);
    if(daCopia) window.__depois = true;
    return r; };
  var has = caches.has.bind(caches);
  caches.has = async function(nome){
    if(onde === 'marca' && window.__depois && nome === '%s' && !window.__feito) await agir();
    return has(nome); };
  var get = IDBObjectStore.prototype.get;
  IDBObjectStore.prototype.get = function(chave){
    if(onde === 'geracao' && window.__depois && this.name === 'meta' && chave === 'geracao' && !window.__feito) agir();
    return get.call(this, chave); };
}""" % (json.dumps(ACCOES), json.dumps(ACCOES), MARCA_DESLIGADA)

LER = "window.__t.apiGet('/api/nota?nome=n2').then(d => d && d.texto, e => 'ERRO ' + (e && e.message))"


def _ler_da_copia(contexto, base, onde=None, accao=None):
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    assert esperar(page, "window.__t.donoOk() === true"), "a página não confirmou o dono da cópia"
    page.route("**/api/nota?nome=n2", lambda route: route.fulfill(
        status=200, body=json.dumps(dict(NOTA, nome="n2", texto="DA_COPIA", hash="HC")),
        headers={"Content-Type": "application/json", "X-Basalt-From-Cache": "1", "X-Basalt-Protocolo": "5", "X-Basalt-Dono": "d-ana"}))
    page.evaluate(PREPARAR, [onde or "", accao or ""])
    r = page.evaluate(LER)
    est = page.evaluate("({feito: window.__feito, depois: window.__depois, desligada: window.__t.desligada(), "
                        "bloqueada: window.__t.bloqueada()})")
    return r, est


CASOS = [
    ("corpo", "apagar"), ("corpo", "trancar"), ("corpo", "trancar-noutro"),
    ("marca", "trancar"), ("marca", "trancar-noutro"),
    ("geracao", "trancar"), ("geracao", "trancar-noutro"), ("geracao", "apagar"),
]


@pytest.mark.parametrize("onde,accao", CASOS, ids=[f"{o}-{a}" for o, a in CASOS])
def test_g4_resposta_da_copia_nao_se_entrega_se_a_copia_foi_trancada_ou_apagada_a_meio(base, contexto, onde, accao):
    r, est = _ler_da_copia(contexto, base, onde, accao)
    assert est["feito"], f"o ensaio não chegou a {accao} a meio ({onde}): {est}"
    assert r != "DA_COPIA", f"a página entregou a nota da cópia com a cópia {'apagada' if accao == 'apagar' else 'trancada'} a meio ({onde}): {est}"
    assert isinstance(r, str) and r.startswith("ERRO"), r
    if accao == "apagar":
        assert est["desligada"] is True, est
    else:
        assert est["bloqueada"] is True, est


@pytest.mark.parametrize("onde", ["corpo", "marca", "geracao"])
def test_controlo_g4_sem_nada_a_meio_a_resposta_da_copia_entrega_se(base, contexto, onde):
    # os mesmos pontos de paragem, sem acção: passam por lá e a nota entrega-se
    r, est = _ler_da_copia(contexto, base, onde, "nenhuma")
    assert est["feito"] and est["depois"], f"o ensaio não passou pelo ponto de paragem ({onde}): {est}"
    assert r == "DA_COPIA", (r, est)
