"""«Apagar cópia deste aparelho» na página: depois de apagada, nada volta a guardar notas (nem o temporizador, nem o «online»,
nem um pacote que outro separador estava a descarregar, nem uma resposta que o service worker ainda guarde), a fila já
capturada pára, uma resposta pendente não repõe conflitos e uma edição descartada não ressuscita ao religar. Consola limpa
no computador e no telemóvel.

Chromium do Playwright contra o servidor simulado de `pagina_falsa.py` (nunca a memória real). Saltam-se sem Playwright."""
import json
import threading

import pytest

from pagina_falsa import (ESTADO, NOTA, buscar, com_nota, conta_notas, esperar, esperar_py, nome_cache_api, por_na_cache,
                          pronta, reset)

pytestmark = pytest.mark.lento


def _apagar_copia_pela_pagina(page):
    page.wait_for_selector("#apagar-copia-link", timeout=8000)
    page.click("#apagar-copia-link")
    page.wait_for_selector("#apagar-copia-confirmar")
    page.click("#apagar-copia-confirmar")
    page.wait_for_timeout(1500)


def test_depois_de_apagar_a_copia_nem_o_temporizador_nem_o_online_voltam_a_guardar_as_notas(base, contexto):
    reset(pacote=[dict(NOTA, texto="texto guardado no aparelho", hash="h1")])
    page = contexto().new_page()
    page.clock.install()
    page.goto(base + "/#/")
    pronta(page)
    esperar(page, "window.__t.idbGetAll('notas').then(l => l.length === 1)")
    antes = conta_notas(page)
    _apagar_copia_pela_pagina(page)
    pronta(page)
    logo = conta_notas(page)
    page.clock.run_for(11 * 60 * 1000)
    page.wait_for_timeout(1500)
    depois_timer = conta_notas(page)
    page.evaluate("window.dispatchEvent(new Event('online'))")
    page.wait_for_timeout(1500)
    depois_online = conta_notas(page)
    assert antes == 1 and logo == 0 and depois_timer == 0 and depois_online == 0, \
        f"antes {antes} → apagada {logo} → 11 min depois {depois_timer} → «online» {depois_online}"


def test_apagar_a_copia_num_separador_ganha_ao_pacote_que_outro_separador_estava_a_descarregar(base, contexto):
    reset(pacote=[dict(NOTA, texto="texto guardado no aparelho", hash="h1")])
    ctx = contexto()
    a, b = ctx.new_page(), ctx.new_page()
    a.goto(base + "/#/")
    pronta(a)
    b.goto(base + "/#/")
    pronta(b)
    esperar(a, "window.__t.idbGetAll('notas').then(l => l.length === 1)")
    # os pacotes do arranque (A e B) têm de ter acabado: um pacote ainda a correr em B fazia o pedido seguinte voltar logo
    for _ in range(80):
        if ESTADO["pacote_pedidos"] >= 2:
            break
        a.wait_for_timeout(100)
    a.wait_for_timeout(800)
    ESTADO["pacote_solta"] = threading.Event()
    antes = ESTADO["pacote_pedidos"]
    b.evaluate("() => { window.__pp = window.__t.sincronizarPacote(); return 0; }")
    for _ in range(50):
        if ESTADO["pacote_pedidos"] > antes:
            break
        b.wait_for_timeout(100)
    a_meio = ESTADO["pacote_pedidos"] > antes
    _apagar_copia_pela_pagina(a)
    pronta(a)
    ESTADO["pacote_solta"].set()
    b.evaluate("window.__pp")
    b.wait_for_timeout(800)
    n = conta_notas(a)
    assert a_meio and n == 0, f"pacote a meio: {a_meio} · notas no aparelho depois: {n}"


def test_depois_de_apagar_a_copia_uma_resposta_que_o_service_worker_ainda_guarde_nao_se_serve_sem_rede(base, contexto):
    reset(pacote=[dict(NOTA, texto="texto guardado", hash="h1")])
    page = contexto(sw=True).new_page()
    page.goto(base + "/#/")
    pronta(page)
    if not esperar(page, "!!navigator.serviceWorker.controller", 5000):
        page.reload()
        pronta(page)
        esperar(page, "!!navigator.serviceWorker.controller", 5000)
    ESTADO["nota_texto"] = "PRIVADO"
    buscar(page, "/api/nota?nome=n1")
    page.wait_for_timeout(600)
    nome = nome_cache_api(page)
    _apagar_copia_pela_pagina(page)
    pronta(page)
    por_na_cache(page, nome, "PRIVADO atrasado")
    ESTADO["modo"] = "sem-rede"
    r = buscar(page, "/api/nota?nome=n1")
    fuga = "PRIVADO" in (r.get("texto") or "")
    assert nome and not fuga, f"cache {nome or '?'} · sem rede devolveu {r.get('status')}{' com a nota PRIVADA' if fuga else ''}"


def test_apagar_a_copia_a_meio_da_fila_a_edicao_seguinte_ja_nao_sobe(base, contexto):
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    page.evaluate("window.__t.guardarEdicaoOffline('n1', 'S novo', 'A').then(() => window.__t.guardarEdicaoOffline('n2', 'S novo', 'B'))")
    page.evaluate("void window.__t.sincronizarFila()")
    em_curso = esperar_py(page, lambda: ESTADO["sinc_pedidos"] >= 1, 6000)   # A está no servidor, à espera
    page.evaluate("window.__t.desligarCopiaLocal()")
    ESTADO["sinc_solta"].set()
    page.wait_for_timeout(2000)
    fila = page.evaluate("window.__t.idbGetAll('fila').then(l => l.length)")
    assert em_curso and ESTADO["sinc_pedidos"] == 1 and fila == 0, \
        f"pedidos de sincronização: {ESTADO['sinc_pedidos']} (esperado 1) · fila depois: {fila}"


def test_apagar_a_copia_com_uma_resposta_pendente_um_409_que_chegue_depois_nao_repoe_o_conflito(base, contexto):
    reset(sinc_409={"erro": "conflito", "estado": "conflito", "actual": "S do servidor", "hash_actual": "H5"})
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    page.evaluate("window.__t.guardarEdicaoOffline('n1', 'S novo', 'A PRIVADO')")
    page.evaluate("void window.__t.sincronizarFila()")
    em_curso = esperar_py(page, lambda: ESTADO["sinc_pedidos"] >= 1, 6000)
    page.evaluate("window.__t.desligarCopiaLocal()")
    ESTADO["sinc_solta"].set()
    page.wait_for_timeout(2000)
    conflitos = page.evaluate("window.__t.idbGetAll('conflitos').then(l => l.length)")
    fila = page.evaluate("window.__t.idbGetAll('fila').then(l => l.length)")
    assert em_curso and conflitos == 0 and fila == 0, f"conflitos depois: {conflitos} · fila: {fila}"


def test_com_a_copia_apagada_guardar_uma_edicao_sem_rede_e_recusado_e_nao_volta_a_criar_fila(base, contexto):
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    page.evaluate("window.__t.desligarCopiaLocal()")
    r = page.evaluate("""async () => { try { await window.__t.guardarEdicaoOffline('n1', 'S novo', 'X'); return {ok: true}; }
                                       catch (e) { return {ok: false, erro: e.message}; } }""")
    fila = page.evaluate("window.__t.idbGetAll('fila').then(l => l.length)")
    assert (not r["ok"]) and fila == 0, f"guardou: {'SIM' if r['ok'] else 'não (' + str(r.get('erro'))[:60] + ')'} · fila: {fila}"


def test_put_suspenso_apagar_copia_religar_e_falha_de_rede_a_edicao_descartada_nao_volta_a_fila(base, contexto):
    reset(pacote=[dict(NOTA, texto="S novo", hash="H2")], put_solta=threading.Event())
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    com_nota(page)
    page.evaluate("location.hash = '#/nota/n1'")
    page.wait_for_selector("#editar-btn", timeout=8000)
    page.click("#editar-btn")
    page.wait_for_selector("#ed-gravar", timeout=8000)
    page.wait_for_timeout(400)
    page.click("#ed-editor-host")
    page.keyboard.press("End")
    page.keyboard.type(" EDICAO DESCARTADA")
    page.wait_for_timeout(200)
    page.click("#ed-gravar")                                        # o PUT parte e fica suspenso no servidor
    esperar_py(page, lambda: len(ESTADO["put"]) >= 1)
    ger_antes = page.evaluate("window.__t.geracao()")
    page.evaluate("window.__t.desligarCopiaLocal()")                # noutro separador: «apagar a cópia deste aparelho»
    page.evaluate("window.__t.religarCopiaLocal()")                 # …e logo a seguir «voltar a ligar» (a cópia volta limpa, geração nova)
    esperar(page, "window.__t.idbGetAll('notas').then(l => l.length === 1)")
    ESTADO["put_falha_rede"] = True                                 # agora o PUT antigo acaba em falha de rede (ligação cortada)
    ESTADO["modo"] = "sem-rede"
    ESTADO["put_solta"].set()
    page.wait_for_timeout(2000)
    fila = page.evaluate("window.__t.idbGetAll('fila')")
    banner = page.evaluate("(document.getElementById('ed-banner') || {}).textContent || ''")
    assert page.evaluate("window.__t.geracao()") > ger_antes and not fila and "apagada" in banner and "DESCARTADA" not in json.dumps(fila), \
        (f"geração {ger_antes}→{page.evaluate('window.__t.geracao()')} · fila depois: {[f.get('texto') for f in fila] or 'vazia'} · "
         f"editor: {banner[:70]!r}")


def test_consola_sem_erros_no_computador_e_no_telemovel_com_service_worker(base, contexto):
    reset(pacote=[dict(NOTA, texto="---\nname: n1\n---\n# Título\ntexto com [[n1]] e $x^2$", hash="h1")])
    erros = []
    for vp in ({"width": 1366, "height": 850}, {"width": 390, "height": 844}):
        page = contexto(sw=True, viewport=vp, is_mobile=vp["width"] < 500).new_page()
        page.on("console", lambda m, vp=vp: erros.append(f"{vp['width']}px {m.text[:120]}") if m.type == "error" else None)
        page.on("pageerror", lambda e, vp=vp: erros.append(f"{vp['width']}px {str(e)[:120]}"))
        for h in ("#/", "#/notas", "#/nota/n1", "#/grafo", "#/diario"):
            page.goto(base + "/" + h)
            page.wait_for_timeout(1200)
        largura = page.evaluate("document.documentElement.scrollWidth")
        if largura > vp["width"] + 1:
            erros.append(f"{vp['width']}px deslize horizontal: {largura}px")
    assert not erros, "; ".join(erros[:4])
