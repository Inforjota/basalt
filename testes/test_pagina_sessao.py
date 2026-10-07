"""A página e o service worker não mostram nem entregam uma nota privada depois de o servidor a recusar (403, 404, 410,
redireccionamento para o login) ou de a sessão expirar: a «cópia do aparelho» fica TRANCADA (uma tranca por pedido, cada
uma com o seu id) e só volta a servir quando a rede falha de verdade e a tranca já não existe.

Chromium do Playwright contra o servidor simulado de `pagina_falsa.py` (nunca a memória real). Saltam-se sem Playwright."""
import threading

import pytest

from pagina_falsa import (ESTADO, MARCA, NOTA, SW_REDE_ATRASADA, SW_TRANCA_NOVA, buscar, com_nota, com_sw,
                          esperar, fonte_sw, nome_cache_api, por_na_cache, pronta, reset, versao_cache)

pytestmark = pytest.mark.lento


def test_service_worker_recusa_nunca_devolve_a_nota_guardada_mas_sem_rede_continua_a_dar(base, contexto):
    page = contexto(sw=True).new_page()
    page.goto(base + "/")
    pronta(page)
    page.evaluate("navigator.serviceWorker.ready.then(() => true)")
    if not esperar(page, "!!navigator.serviceWorker.controller", 5000):
        page.reload()
        pronta(page)
        esperar(page, "!!navigator.serviceWorker.controller", 5000)
    ESTADO["nota_texto"] = "PRIVADO: não pode sair depois de recusado"
    res = {}
    for modo in ("403", "404", "login"):
        ESTADO["modo"] = "ok"
        buscar(page, "/api/nota?nome=n1")
        page.wait_for_timeout(600)                                  # fica em cache
        ESTADO["modo"] = modo
        r = buscar(page, "/api/nota?nome=n1")
        res[modo] = "FUGA (" + str(r.get("status")) + " da cache)" if "PRIVADO" in (r.get("texto") or "") else f"ok ({r.get('status')})"
    ESTADO["modo"] = "ok"
    buscar(page, "/api/nota?nome=n1")
    page.wait_for_timeout(600)
    ESTADO["modo"] = "sem-rede"
    r = buscar(page, "/api/nota?nome=n1")
    offline_ok = "PRIVADO" in (r.get("texto") or "") and r.get("cache") == "1"
    assert all(v.startswith("ok") for v in res.values()) and offline_ok, \
        " · ".join(f"{k}: {v}" for k, v in res.items()) + f" · sem rede: {'cache ok' if offline_ok else 'SEM cache'}"


def test_sessao_expirada_tranca_a_copia_do_aparelho_e_sem_rede_a_nota_privada_nao_aparece(base, contexto):
    reset(pacote=[dict(NOTA, texto="PRIVADO no aparelho", hash="h1")])
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    esperar(page, "window.__t.idbGetAll('notas').then(l => l.length === 1)")
    ESTADO["modo"] = "login"                                        # a sessão expirou
    page.evaluate("location.hash = '#/nota/n1'")
    page.wait_for_timeout(1500)
    expirou = page.evaluate("!!document.getElementById('sessao-expirada-titulo')")
    page.wait_for_timeout(4500)                                     # o diagnóstico guarda-se 4 s
    ESTADO["modo"] = "sem-rede"                                     # e depois fica sem rede
    page.evaluate("location.hash = '#/diario'")
    page.wait_for_timeout(300)
    page.evaluate("location.hash = '#/nota/n1'")
    page.wait_for_timeout(2500)
    fuga = page.evaluate("document.getElementById('main').innerText.indexOf('PRIVADO') !== -1")
    assert expirou and not fuga, f"aviso de sessão expirada: {'sim' if expirou else 'não'} · nota privada à vista sem rede: {'SIM' if fuga else 'não'}"


def test_nota_que_o_servidor_diz_ja_nao_existir_404_sai_da_copia_do_aparelho(base, contexto):
    reset(pacote=[dict(NOTA, texto="retirada por ter dados sensíveis", hash="h1")])
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    esperar(page, "window.__t.idbGetAll('notas').then(l => l.length === 1)")
    ESTADO["modo"] = "404"
    page.evaluate("location.hash = '#/nota/n1'")
    page.wait_for_timeout(1500)
    n = page.evaluate("window.__t.idbGetAll('notas').then(function(l){return l.length;})")
    assert n == 0, f"notas na cópia depois do 404: {n}"


def test_login_a_seguir_a_uma_falta_de_rede_curta_a_nota_privada_nao_se_mostra_e_a_sessao_expira(base, contexto):
    reset(pacote=[dict(NOTA, texto="PRIVADO no aparelho", hash="h1")])
    page = contexto(sw=True).new_page()
    com_sw(page, base)
    esperar(page, "window.__t.idbGetAll('notas').then(l => l.length === 1)")
    ESTADO["modo"] = "sem-rede"
    page.evaluate("location.hash = '#/nota/n1'")
    page.wait_for_timeout(1500)                                     # sem rede: mostra (é para isso)
    ESTADO["modo"] = "login"                                        # a rede volta, a sessão não
    page.evaluate("location.hash = '#/diario'")
    page.wait_for_timeout(300)
    page.evaluate("location.hash = '#/nota/n1'")
    page.wait_for_timeout(1500)
    fuga = page.evaluate("document.getElementById('main').innerText.indexOf('PRIVADO') !== -1")
    expirou = page.evaluate("!!document.getElementById('sessao-expirada-titulo')")
    assert expirou and not fuga, f"nota privada à vista: {'SIM' if fuga else 'não'} · aviso de sessão expirada: {'sim' if expirou else 'NÃO'}"


def test_sessao_expirada_num_separador_tranca_a_copia_nos_outros_separadores(base, contexto):
    reset(pacote=[dict(NOTA, texto="PRIVADO no aparelho", hash="h1")])
    ctx = contexto()
    a, b = ctx.new_page(), ctx.new_page()
    a.goto(base + "/")
    pronta(a)
    b.goto(base + "/")
    pronta(b)
    esperar(a, "window.__t.idbGetAll('notas').then(l => l.length === 1)")
    ESTADO["modo"] = "login"
    a.evaluate("location.hash = '#/nota/n1'")
    a.wait_for_timeout(1500)
    trancou = a.evaluate("!!document.getElementById('sessao-expirada-titulo')")
    ESTADO["modo"] = "sem-rede"
    b.evaluate("location.hash = '#/nota/n1'")
    b.wait_for_timeout(2500)
    fuga = b.evaluate("document.getElementById('main').innerText.indexOf('PRIVADO') !== -1")
    assert trancou and not fuga, \
        f"separador A trancou: {'sim' if trancou else 'não'} · separador B, sem rede, mostra a nota privada: {'SIM' if fuga else 'não'}"


def test_com_a_copia_trancada_uma_resposta_atrasada_que_o_service_worker_guarde_nao_se_serve_sem_rede(base, contexto):
    page = contexto(sw=True).new_page()
    com_sw(page, base)
    ESTADO["nota_texto"] = "PRIVADO"
    buscar(page, "/api/nota?nome=n1")
    page.wait_for_timeout(600)
    nome = nome_cache_api(page)
    ESTADO["modo"] = "403"
    buscar(page, "/api/nota?nome=n1")
    page.wait_for_timeout(300)
    por_na_cache(page, nome, "PRIVADO atrasado")
    ESTADO["modo"] = "sem-rede"
    r = buscar(page, "/api/nota?nome=n1")
    fuga = "PRIVADO" in (r.get("texto") or "")
    assert nome and not fuga, f"cache {nome or '?'} · sem rede devolveu {r.get('status')}{' com a nota PRIVADA' if fuga else ''}"


def test_service_worker_destrancar_por_resposta_boa_nao_apaga_a_tranca_que_outro_pedido_pos_no_meio(base, contexto):
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    r = page.evaluate(SW_TRANCA_NOVA, [fonte_sw(), MARCA])
    assert r["estado"] == 200 and r["injectada"] and r["trancada"], \
        (f"resposta {r['estado']} · tranca nova injectada: {'sim' if r['injectada'] else 'não'} · "
         f"depois: {'TRANCADA (certo)' if r['trancada'] else 'DESTRANCADA — a tranca nova perdeu-se'}")


def test_pagina_recusa_uma_resposta_da_cache_do_service_worker_se_a_copia_esta_trancada_na_pagina(base, contexto):
    reset(nota_texto="PRIVADO em cache")
    page = contexto(sw=True).new_page()
    com_sw(page, base)
    page.evaluate("fetch('/api/nota?nome=n1', {credentials:'same-origin'}).then(r => r.text())")
    page.wait_for_timeout(600)
    page.evaluate("localStorage.setItem('basalt-copia-bloqueada', 'zz')")   # trancada só na página (o service worker não sabe)
    ESTADO["modo"] = "sem-rede"
    r = page.evaluate("""async () => { try { const d = await window.__t.apiGet('/api/nota?nome=n1');
                                             return {ok: true, texto: d && d.texto || ''}; }
                                       catch (e) { return {ok: false, erro: e.message, expirada: !!e.sessaoExpirada}; } }""")
    fuga = r["ok"] and "PRIVADO" in r["texto"]
    assert not fuga and not r["ok"], \
        "nota privada entregue da cache com a cópia trancada: " + ("SIM" if fuga else "não") + (f" · recusa: {r.get('erro')}" if not r["ok"] else "")


def test_tranca_posta_so_pelo_service_worker_trava_a_copia_da_indexeddb_e_o_arranque_adopta_a(base, contexto):
    reset(pacote=[dict(NOTA, texto="PRIVADO IDB", hash="h1")])
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    esperar(page, "window.__t.idbGetAll('notas').then(l => l.length === 1)")
    page.evaluate(f"caches.open('{MARCA}').then(c => c.put('/__tranca/sw1', new Response(String(Date.now()))))")
    ESTADO["modo"] = "sem-rede"
    r = page.evaluate("""async () => { try { const d = await window.__t.tentarOffline('/api/nota?nome=n1');
                                             return {ok: true, texto: d && d.texto || ''}; }
                                       catch (e) { return {ok: false, erro: e.message}; } }""")
    fuga = r["ok"] and "PRIVADO" in r["texto"]
    page.evaluate("localStorage.clear()")
    page.evaluate("window.__t.idbDelete('meta', 'bloqueada')")      # só a marca do service worker
    page.reload()
    pronta(page)
    page.wait_for_timeout(1200)
    arranque = page.evaluate("window.__t.bloqueada()")
    assert not fuga and arranque, \
        f"tentarOffline deu a nota privada: {'SIM' if fuga else 'não'} · ao arrancar, trancada: {'sim' if arranque else 'NÃO'}"


def test_pacote_atrasado_nao_apaga_a_tranca_que_o_service_worker_pos_depois_de_ele_partir(base, contexto):
    reset(pacote=[dict(NOTA, texto="PRIVADO", hash="H2")])
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    com_nota(page)
    # a página está trancada com o id A (um 401 de há bocado) e a marca do service worker tem a tranca A, antiga
    page.evaluate("""async () => { await window.__t.idbPut('meta', {chave:'bloqueada', valor:true, id:'A'});
      var c = await caches.open('basaltcfg-copia-trancada'); await c.put('/__tranca/A', new Response(String(Date.now() - 100000))); }""")
    antes = page.evaluate("window.__t.tentarOffline('/api/nota?nome=n1').then(n => n ? 'DEU' : 'nada', e => 'recusou')")
    ESTADO["pacote_solta"] = threading.Event()
    page.evaluate("void (window.__pac = window.__t.sincronizarPacote())")      # o pacote parte agora e fica suspenso no servidor
    page.wait_for_timeout(300)
    page.evaluate("""async () => { var c = await caches.open('basaltcfg-copia-trancada');
      await c.put('/__tranca/B', new Response(String(Date.now()))); }""")        # entretanto outro pedido recebe 401: o service worker tranca B
    page.wait_for_timeout(50)
    ESTADO["pacote_solta"].set()
    page.evaluate("window.__pac")
    page.wait_for_timeout(300)                                                  # o pacote antigo chega e destranca «o que é seu»
    ids = page.evaluate("window.__t.trancasNaMarca().then(l => l.map(t => t.id))")
    ESTADO["modo"] = "sem-rede"
    depois = page.evaluate("window.__t.tentarOffline('/api/nota?nome=n1').then(n => n ? 'DEU' : 'nada', e => 'recusou')")
    assert antes == "recusou" and "B" in ids and depois == "recusou", \
        f"antes: {antes} · trancas depois do pacote: {ids} · ler a nota sem rede: {depois}"


def test_um_410_nota_apagada_de_vez_tira_a_nota_da_copia_do_aparelho_tal_como_o_404(base, contexto):
    reset(pacote=[dict(NOTA, texto="PRIVADO", hash="H2")], nota_status=410)
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    com_nota(page)
    ESTADO["pacote"] = []
    r = page.evaluate("window.__t.apiGet('/api/nota?nome=n1').then(() => 'ok', e => 'erro ' + e.status)")
    page.wait_for_timeout(200)
    n = page.evaluate("window.__t.idbGetAll('notas').then(l => l.length)")
    ESTADO["modo"] = "sem-rede"
    depois = page.evaluate("window.__t.tentarOffline('/api/nota?nome=n1').then(n => n ? 'DEU' : 'nada', e => 'recusou ' + e.status)")
    assert r == "erro 410" and n == 0 and depois.startswith("recusou"), f"resposta: {r} · notas na cópia: {n} · ler sem rede: {depois}"


def test_service_worker_um_get_antigo_que_chega_depois_de_um_410_nao_volta_a_guardar_a_nota_apagada(base, contexto):
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    r = page.evaluate(SW_REDE_ATRASADA, [fonte_sw(), versao_cache() + "-api"])
    assert r["r1"] == 200 and r["r2"] == 410 and not r["guardada"], \
        f"GET antigo: {r['r1']} · GET novo: {r['r2']} · nota apagada guardada na cache: {'SIM' if r['guardada'] else 'não'}"
