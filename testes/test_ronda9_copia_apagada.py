"""Ronda 9 (achados 2–5 da ronda 8) — «apagar a cópia deste aparelho» a meio de uma leitura ou de uma gravação.

Achados 2 e 3 (service worker, em node — ver sw_ambiente.js, que simula a CacheStorage, o fetch e os Web Locks):
  · `aceitar()` verificava «sem lápide» ANTES do `put`; com o trinco preso, um 404 posterior (ou «apagar a cópia») faz-se sem trinco
    e a entrada ficava lá, a servir-se sem rede. Agora volta a ver DEPOIS do `put` e tira a entrada que ele próprio guardou;
  · `daCache` servia uma entrada mais velha que a lápide da nota e não relia a cópia (desligada / geração) depois do `caches.match`.
Achado 3 (página): uma resposta da cópia do service worker que chega depois de «apagar a cópia» (ou de a apagar e religar, ou de
  outro separador a apagar) já não se mostra — «precisa de rede».
Achado 4 (página): o `guardadoEm` das gravações deste aparelho sai do contador partilhado (`meta.relogio`), como o dos pacotes — com
  o relógio atrasado, um pacote que outro separador reservou antes já não repõe o texto velho por cima de uma gravação mais nova.
Achado 5 (página): «voltar a ligar a cópia» só com o MESMO protocolo do service worker (um mais novo também não serve esta página).
Chromium do Playwright contra `pagina_falsa.py` (e o sw.js do pacote a sério, no P5). Cada teste falha no código anterior a esta
ronda pela razão certa (`BASALT_SW=<cópia antiga>/basalt/static/sw.js` e `BASALT_STATIC=<cópia antiga>/basalt/static`), menos os
controlos."""
import json

import pytest

from pagina_falsa import NOTA as NOTA_PAGINA, com_nota, com_sw, esperar, pronta, reset
from test_sw import API, DONO, NODE, NOTA, sw, tem_lapide

# o trinco do service worker larga-se ao fim de TRINCO_MS (3000 no sw.js); nos ensaios, 150 ms
TRINCO = dict(locks=True, trinco_ms=150)


# ───────────────────────── service worker (node) ─────────────────────────

class TestServiceWorker:
    pytestmark = pytest.mark.skipif(NODE is None, reason="precisa do node")

    # ── achado 2: aceitar() já viu «sem lápide»; antes de guardar, um 404 posterior (trinco preso → sem trinco) põe a lápide ──
    A_MEIO_DO_PUT = {"em": "put_api", "faz": [{"pedido": {"url": NOTA, "rede": "404"}}]}

    def test_um_200_que_estava_a_ser_guardado_quando_um_404_posterior_poe_a_lapide_nao_fica(self):
        r = sw([{"url": NOTA, "gancho": self.A_MEIO_DO_PUT}, {"url": NOTA, "rede": "rede"}], **TRINCO)
        g = r["passos"][0]["gancho"]
        assert g["disparou"] and g["resultados"][0]["status"] == 404, f"o ensaio não montou o 404 a meio do put: {g}"
        assert tem_lapide(r["caches"]), "o 404 posterior não deixou a lápide da nota"
        assert NOTA not in r["caches"].get(API, []), \
            "o 200 guardado depois da lápide ficou na cache da API: aceitar() não voltou a ver a lápide DEPOIS do put"
        assert "erro" in r["passos"][1], f"sem rede, a nota apagada serviu-se da cópia: {r['passos'][1]}"

    def test_controlo_sem_nada_a_meio_do_put_a_entrada_fica_e_serve_se_sem_rede(self):
        r = sw([{"url": NOTA, "gancho": {"em": "put_api", "faz": []}}, {"url": NOTA, "rede": "rede"}], **TRINCO)
        assert r["passos"][0]["gancho"]["disparou"]
        assert NOTA in r["caches"][API]
        assert r["passos"][1]["status"] == 200 and r["passos"][1]["deCache"] is True

    def test_controlo_uma_nota_recriada_depois_do_404_guarda_se_e_serve_se_sem_rede(self):
        r = sw([{"url": NOTA, "gancho": self.A_MEIO_DO_PUT}, {"url": NOTA}, {"url": NOTA, "rede": "rede"}], **TRINCO)
        assert r["passos"][2]["status"] == 200 and r["passos"][2]["deCache"] is True, \
            f"um GET depois do 404 (a nota recriada) deixou de se guardar: {r['passos'][2]}"
        assert r["passos"][2]["corpo"] == r["passos"][1]["corpo"]       # é a da recriação, não a que estava a ser guardada

    # ── achado 2 (leitura): uma entrada mais velha que a lápide da mesma nota não se serve sem rede ──
    def test_sem_rede_uma_entrada_mais_velha_que_a_lapide_da_nota_nao_se_serve(self):
        r = sw([{"url": NOTA}, {"url": NOTA, "rede": "rede"},
                {"acao": "lapide", "url": NOTA, "valor": "depois_da_entrada"}, {"url": NOTA, "rede": "rede"}])
        assert NOTA in r["caches"][API], "a entrada tinha de continuar guardada (só a lápide a deve calar)"
        assert r["passos"][1]["deCache"] is True, "antes da lápide a cópia serve-se (é o controlo disto)"
        assert "erro" in r["passos"][3], \
            f"serviu-se sem rede uma entrada de antes da lápide (o 404 é posterior): {r['passos'][3]}"

    def test_controlo_sem_lapide_a_entrada_serve_se_sem_rede(self):
        r = sw([{"url": NOTA}, {"url": NOTA, "rede": "rede"}, {"url": NOTA, "rede": "rede"}])
        assert all(p["status"] == 200 and p["deCache"] is True for p in r["passos"][1:])

    def test_controlo_uma_lapide_de_outra_nota_nao_cala_esta(self):
        r = sw([{"url": NOTA}, {"acao": "lapide", "url": "/api/nota?nome=outra", "valor": 10 ** 15}, {"url": NOTA, "rede": "rede"}])
        assert r["passos"][2]["status"] == 200 and r["passos"][2]["deCache"] is True

    # ── achado 3: a leitura sem rede está no caches.match quando «apagar a cópia» (trinco preso → sem trinco) corre ──
    @pytest.mark.parametrize("faz", [
        [{"desligar": True}],
        [{"nova_geracao": True}],
        [{"nova_geracao": True}, {"desligar": True}, {"apagar_caches_api": True}],
    ], ids=["marca de desligada", "geracao nova", "apagar a copia (as tres)"])
    def test_uma_leitura_sem_rede_a_meio_de_apagar_a_copia_nao_entrega_a_entrada_lida_antes(self, faz):
        r = sw([{"url": NOTA}, {"url": NOTA, "rede": "rede", "gancho": {"em": "match_api", "faz": faz}}], **TRINCO)
        p = r["passos"][1]
        assert p["gancho"]["disparou"], "o ensaio não chegou a apagar a cópia a meio do caches.match"
        assert "erro" in p, f"a cópia foi apagada a meio e o service worker entregou na mesma a entrada que lera antes: {p}"

    def test_controlo_sem_apagar_nada_a_meio_a_leitura_serve_se(self):
        for faz in (None, []):
            gancho = {"em": "match_api", "faz": faz} if faz is not None else None
            passo = {"url": NOTA, "rede": "rede"}
            if gancho:
                passo["gancho"] = gancho
            p = sw([{"url": NOTA}, passo], **TRINCO)["passos"][1]
            assert p["status"] == 200 and p["deCache"] is True, f"gancho {faz!r}: {p}"


# ───────────────────────── página (Chromium) ─────────────────────────

def _aberta(contexto, base, sw=False, **estado):
    reset(**estado)
    page = contexto(sw=sw).new_page()
    if sw:
        com_sw(page, base)
    else:
        page.goto(base + "/")
        assert pronta(page), "a página não arrancou"
    assert esperar(page, "window.__t.donoOk() === true"), "a página não confirmou o dono da cópia"
    return page


class TestPagina:
    pytestmark = pytest.mark.lento

    # ── achado 3: a resposta do service worker que chega depois de «apagar a cópia» ──
    LER = ("window.__t.apiGet('/api/nota?nome=n2').then(d => ({ok: d && d.texto}), "
           "e => ({erro: String(e && e.message || e), semRede: !!(e && e.semRede)}))")

    def _com_cache_do_sw(self, page):
        """`/api/nota?nome=n2` responde como o service worker sem rede (X-Basalt-From-Cache) e, depois de a resposta chegar à
        página, corre `window.__aMeio` (o «apagar a cópia» que o service worker não viu)."""
        page.route("**/api/nota?nome=n2", lambda route: route.fulfill(
            status=200, body=json.dumps(dict(NOTA_PAGINA, nome="n2", texto="APAGADO", hash="HC")),
            headers={"Content-Type": "application/json", "X-Basalt-From-Cache": "1", "X-Basalt-Protocolo": "5", "X-Basalt-Dono": "d-ana"}))
        page.evaluate("""() => { window.__f = window.fetch; window.__aMeio = null;
            window.fetch = async function(p, o){ var r = await window.__f(p, o);
                if(String(p).indexOf('nome=n2') !== -1 && window.__aMeio){ var f = window.__aMeio; window.__aMeio = null; await f(); }
                return r; }; }""")

    @pytest.mark.parametrize("apagar", [
        "() => window.__t.desligarCopiaLocal()",
        "async () => { await window.__t.desligarCopiaLocal(); await window.__t.religarCopiaLocal(); }",
        "() => caches.open('basaltcfg-copia-desligada')",
    ], ids=["apagada", "apagada e religada", "apagada por outro separador"])
    def test_resposta_da_copia_do_service_worker_que_chega_depois_de_apagar_a_copia_nao_se_mostra(self, base, contexto, apagar):
        page = _aberta(contexto, base)
        self._com_cache_do_sw(page)
        page.evaluate("window.__aMeio = " + apagar)
        r = page.evaluate(self.LER)
        assert r.get("ok") != "APAGADO", f"a página mostrou a nota que o service worker leu antes de «apagar a cópia»: {r}"
        assert r.get("semRede") is True and "apagada" in r.get("erro", ""), \
            f"devia dar o erro «precisa de rede — a cópia deste aparelho foi apagada» (semRede): {r}"

    def test_controlo_sem_apagar_nada_a_meio_a_resposta_da_copia_do_service_worker_mostra_se(self, base, contexto):
        page = _aberta(contexto, base)
        self._com_cache_do_sw(page)
        assert page.evaluate(self.LER) == {"ok": "APAGADO"}

    # ── achado 4: guardadoEm das gravações deste aparelho sai do contador partilhado (meta.relogio) ──
    def _com_relogio_adiantado(self, contexto, base):
        page = _aberta(contexto, base, pacote=[dict(NOTA_PAGINA, texto="S", hash="H1")])
        assert com_nota(page), "a nota do pacote não chegou à cópia"
        g = page.evaluate("window.__t.geracao()")
        R = page.evaluate("Date.now()") + 10_000_000      # outro separador (relógio adiantado) reservou R para um pacote que vem atrasado
        page.evaluate("r => window.__t.idbPut('meta', {chave: 'relogio', valor: r})", R)
        return page, g, R

    PACOTE_VELHO = "([n, g, R]) => window.__t.guardarPacoteLocal([n], 1, g, '', R)"

    def _texto(self, page):
        return page.evaluate("window.__t.idbGet('notas', 'n1').then(x => x && x.texto)")

    def test_gravacao_confirmada_com_o_relogio_atrasado_fica_acima_de_um_pacote_que_outro_separador_reservou_antes(self, base, contexto):
        page, g, R = self._com_relogio_adiantado(contexto, base)
        c = page.evaluate("g => window.__t.confirmarGravacaoComRede('n1', null, 'NOVO', 'HN', g)", g)
        assert c == "sem-fila", c
        page.evaluate(self.PACOTE_VELHO, [dict(NOTA_PAGINA, texto="VELHO", hash="H0"), g, R])
        assert self._texto(page) == "NOVO", \
            "o pacote atrasado (hora do contador) repôs o texto velho por cima de uma gravação mais nova: o guardadoEm não saiu de meta.relogio"

    def test_controlo_um_pacote_que_partiu_depois_da_gravacao_entra(self, base, contexto):
        page, g, R = self._com_relogio_adiantado(contexto, base)
        page.evaluate("g => window.__t.confirmarGravacaoComRede('n1', null, 'NOVO', 'HN', g)", g)
        R2 = page.evaluate("window.__t.reservarInstante()")
        assert R2 > R
        assert page.evaluate(self.PACOTE_VELHO, [dict(NOTA_PAGINA, texto="DEPOIS", hash="H5"), g, R2]) is True
        assert self._texto(page) == "DEPOIS"

    @pytest.mark.parametrize("gravar", [
        "g => window.__t.guardarEdicaoOffline('n1', 'S', 'EDITADO', g)",
        "g => window.__t.confirmarEnvioFila('n1', 'rev-x', 'EDITADO', 'DO SERVIDOR', 'HN', g)",
        "g => window.__t.confirmarGravacaoComRede('n1', null, 'GRAVADO', 'HN', g)",
    ], ids=["guardarEdicaoOffline", "confirmarEnvioFila", "confirmarGravacaoComRede"])
    def test_o_guardadoEm_da_gravacao_sai_do_contador_partilhado(self, base, contexto, gravar):
        page, g, R = self._com_relogio_adiantado(contexto, base)
        page.evaluate(gravar, g)
        gd = page.evaluate("window.__t.idbGet('notas', 'n1').then(x => x && x.guardadoEm)")
        assert gd is not None and gd > R, \
            f"guardadoEm {gd} não passou do contador partilhado ({R}): a hora vem do relógio local e não de meta.relogio"
        assert gd == R + 1 and page.evaluate("window.__t.idbGet('meta', 'relogio').then(x => x && x.valor)") == gd, \
            "a hora devia ser a seguinte do contador, subida na mesma transacção"

    def test_controlo_sem_relogio_adiantado_a_gravacao_continua_a_ter_hora_e_a_subir_o_contador(self, base, contexto):
        page = _aberta(contexto, base, pacote=[dict(NOTA_PAGINA, texto="S", hash="H1")])
        assert com_nota(page)
        g = page.evaluate("window.__t.geracao()")
        antes = page.evaluate("window.__t.idbGet('notas', 'n1').then(x => x.guardadoEm)")
        page.evaluate("g => window.__t.confirmarGravacaoComRede('n1', null, 'NOVO', 'HN', g)", g)
        gd = page.evaluate("window.__t.idbGet('notas', 'n1').then(x => x.guardadoEm)")
        assert gd > antes and self._texto(page) == "NOVO"

    # ── achado 5: «voltar a ligar a cópia» só com o MESMO protocolo do service worker ──
    def _protocolo_do_sw(self, page, n):
        """o service worker (o do pacote, a sério) passa a responder `n` à pergunta «que protocolo falas?» (a página só lhe
        pergunta isso por postMessage com um MessageChannel)."""
        page.evaluate("""n => { const orig = ServiceWorker.prototype.postMessage;
            ServiceWorker.prototype.postMessage = function(msg, transf){
                if(msg && msg.tipo === 'protocolo' && transf && transf[0]){ transf[0].postMessage({protocolo: n}); return; }
                return orig.apply(this, arguments); }; }""", n)

    def _pronto(self, page):
        return page.evaluate("window.__t.swDaCopiaPronto().then(r => r.ok)")

    def test_controlo_com_o_service_worker_a_serio_a_copia_pode_voltar_a_ligar_se(self, base, contexto):
        page = _aberta(contexto, base, sw=True)
        assert page.evaluate("!!navigator.serviceWorker.controller"), "o service worker não passou a controlar a página"
        assert self._pronto(page) is True

    @pytest.mark.parametrize("quantos", [1, 95], ids=["um a mais", "muito mais"])
    def test_um_service_worker_de_protocolo_maior_tambem_nao_serve_esta_pagina(self, base, contexto, quantos):
        page = _aberta(contexto, base, sw=True)
        assert page.evaluate("!!navigator.serviceWorker.controller")
        proto = page.evaluate("window.__t.protocolo()")
        self._protocolo_do_sw(page, proto + quantos)
        assert self._pronto(page) is False, f"a página aceitou um service worker de protocolo {proto + quantos} (a dela é {proto})"
        page.evaluate("window.__t.desligarCopiaLocal()")
        r = page.evaluate("window.__t.religarCopiaLocal().then(v => String(v), e => 'erro: ' + e.message)")
        assert r == "false" and page.evaluate("window.__t.desligada()") is True, \
            f"religou a cópia com um service worker de protocolo maior (religar → {r})"

    def test_controlo_um_service_worker_de_protocolo_menor_recusa_se_e_o_mesmo_protocolo_religa(self, base, contexto):
        page = _aberta(contexto, base, sw=True)
        assert page.evaluate("!!navigator.serviceWorker.controller")
        proto = page.evaluate("window.__t.protocolo()")
        self._protocolo_do_sw(page, proto - 1)
        assert self._pronto(page) is False                                   # mais antigo: já se recusava
        self._protocolo_do_sw(page, proto)
        assert self._pronto(page) is True                                    # o mesmo: serve
        page.evaluate("window.__t.desligarCopiaLocal()")
        page.evaluate("window.__t.religarCopiaLocal()")
        assert page.evaluate("window.__t.desligada()") is False
