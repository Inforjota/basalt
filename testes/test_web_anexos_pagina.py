"""Lado da PÁGINA, no Chromium (Playwright), contra o servidor REAL do pacote num espaço temporário:
  · colar um ficheiro no editor → POST /api/anexos → ![[nome]] fica no texto e o ficheiro na pasta dos anexos;
  · largar (drag & drop) faz o mesmo; sem rede, avisa e não insere nada;
  · as #etiquetas já usadas aparecem no autocompletar do editor.
TESTE_STATIC=<pasta> corre contra outra versão da interface (serve para provar que a antiga falha)."""
import http.client
import os
import shutil
import threading
from pathlib import Path

import pytest

from basalt import anexos, config, web

pytest.importorskip("playwright.sync_api", reason="precisa do Playwright para Python (pip install playwright)")

TOML = """[basalt]
dados = "{dados}"
escutar = "127.0.0.1"
[auth]
modo = "aberto"
[[utilizadores]]
email = "ana@exemplo.pt"
espaco = "ana"
nome = "Ana"
admin = true
[ia]
fornecedor = "nenhum"
"""
NOTA = "---\nname: barcos\ndescription: barcos do Pico\ntype: project\ntags: [mar]\n---\n# Barcos\nTexto com #pico e #praia.\n"

# o evento é construído no browser: um File dentro de um DataTransfer, colado ou largado no contentor do editor
COLAR = """([nome, tipo, conteudo, evento]) => {
  const host = document.querySelector('#ed-editor-host');
  const dt = new DataTransfer(); dt.items.add(new File([conteudo], nome, {type: tipo}));
  const alvo = host.querySelector('.cm-content') || host.querySelector('textarea') || host;
  const ev = evento === 'paste' ? new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})
                                : new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true});
  alvo.dispatchEvent(ev);
  return ev.defaultPrevented;
}"""


GANCHO = ("  window.__bt = {editor: function(){ return EDITOR_ACTIVO; }, "
          "etiquetas: function(){ try{ return ETIQUETAS_CACHE; }catch(_e){ return null; } }};\n  iniciarOffline();")


@pytest.fixture(scope="module")
def servidor(tmp_path_factory, request):
    tmp = tmp_path_factory.mktemp("pagina")
    t = tmp / "basalt.toml"
    t.write_text(TOML.format(dados=tmp / "dados"), encoding="utf-8")
    cfg = config.carregar(t, ambiente={})
    # a interface fica numa cópia com um gancho (o script da página é uma função fechada; o teste precisa de ver o editor)
    origem = Path(os.environ.get("TESTE_STATIC") or web.STATIC).resolve()
    estatico = tmp / "static"
    shutil.copytree(origem, estatico)
    html = (estatico / "index.html").read_text(encoding="utf-8")
    assert html.count("  iniciarOffline();") == 1
    (estatico / "index.html").write_text(html.replace("  iniciarOffline();", GANCHO), encoding="utf-8")
    original = web.STATIC
    web.STATIC = estatico
    request.addfinalizer(lambda: setattr(web, "STATIC", original))
    srv = web.criar_servidor(cfg, host="127.0.0.1", porta=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    esp = cfg.espaco("ana")
    for d in (esp.real, esp.copia):                     # a web serve a cópia editável; a memória é a verdade
        d.mkdir(parents=True, exist_ok=True)
        (d / "barcos.md").write_text(NOTA, encoding="utf-8")
    yield "http://127.0.0.1:%d" % srv.server_address[1], esp
    srv.shutdown(); srv.server_close()


@pytest.fixture(scope="module")
def browser(navegador):
    """o MESMO Chromium da sessão (conftest.navegador). Abrir aqui um segundo sync_playwright() no mesmo processo dava
    «Playwright Sync API inside the asyncio loop» quando um test_pagina_*.py corria antes: o primeiro deixa o loop «a correr»
    entre chamadas (greenlet) e o segundo recusa-se a arrancar. Um só Chromium por sessão, como nos outros testes de página."""
    return navegador


def editor_aberto(browser, base):
    ctx = browser.new_context(service_workers="block")
    page = ctx.new_page()
    page.goto(base + "/#/nota/barcos")
    page.wait_for_selector("#editar-btn", timeout=15000)
    page.click("#editar-btn")
    page.wait_for_selector("#ed-editor-host .cm-content, #ed-editor-host textarea", timeout=15000)
    page.wait_for_function("() => window.__bt && window.__bt.editor() && window.__bt.editor().obterTexto().includes('Barcos')", timeout=15000)
    return ctx, page


def texto(page):
    return page.evaluate("() => window.__bt.editor().obterTexto()")


def cursor_no_fim(page):
    """como quem clica no fim da nota antes de colar; a inserção é no cursor (Obsidian faz o mesmo)."""
    page.click("#ed-editor-host .cm-content")
    page.keyboard.press("Control+End")
    page.keyboard.press("Enter")


def test_colar_imagem_insere_embed_e_guarda(servidor, browser):
    base, esp = servidor
    ctx, page = editor_aberto(browser, base)
    try:
        cursor_no_fim(page)
        assert page.evaluate(COLAR, ["foto.png", "image/png", "PNGdados", "paste"]) is True
        page.wait_for_function("() => window.__bt.editor().obterTexto().includes('![[foto.png]]')", timeout=10000)
        assert (anexos.pasta(esp) / "foto.png").read_bytes() == b"PNGdados"
        # o que se cola sem nome (o browser chama-lhe image.png) fica com data e hora, nunca sobrepõe
        cursor_no_fim(page)
        page.evaluate(COLAR, ["image.png", "image/png", "OUTRA", "paste"])
        page.wait_for_function("() => /!\\[\\[colado-\\d{8}-\\d{6}\\.png\\]\\]/.test(window.__bt.editor().obterTexto())", timeout=10000)
        assert any(d["nome"].startswith("colado-") for d in anexos.listar(esp))
        # e a pré-visualização (lado a lado no ecrã largo) mostra a imagem a partir de /anexos/
        page.wait_for_selector("#ed-preview img.embed-img[src='/anexos/foto.png']", state="attached", timeout=10000)
        c = http.client.HTTPConnection(base[7:].split(":")[0], int(base.rsplit(":", 1)[1]), timeout=10)
        c.request("GET", "/anexos/foto.png"); r = c.getresponse(); assert r.status == 200 and r.read() == b"PNGdados"
    finally:
        ctx.close()


def test_largar_ficheiro(servidor, browser):
    base, esp = servidor
    ctx, page = editor_aberto(browser, base)
    try:
        assert page.evaluate(COLAR, ["relatorio.pdf", "application/pdf", "%PDF-1.4 x", "drop"]) is True
        page.wait_for_function("() => window.__bt.editor().obterTexto().includes('![[relatorio.pdf]]')", timeout=10000)
        assert (anexos.pasta(esp) / "relatorio.pdf").read_bytes() == b"%PDF-1.4 x"
    finally:
        ctx.close()


def test_sem_rede_avisa_e_nao_insere(servidor, browser):
    base, esp = servidor
    ctx, page = editor_aberto(browser, base)
    try:
        ctx.set_offline(True)
        page.evaluate(COLAR, ["offline.png", "image/png", "X", "paste"])
        page.wait_for_selector("#toast-root .toast", timeout=10000)
        assert "rede" in page.inner_text("#toast-root")
        page.wait_for_timeout(600)
        assert "![[offline.png]]" not in texto(page)
        assert not (anexos.pasta(esp) / "offline.png").exists()
    finally:
        ctx.set_offline(False); ctx.close()


def test_autocompletar_etiquetas(servidor, browser):
    base, esp = servidor
    ctx, page = editor_aberto(browser, base)
    try:
        page.wait_for_function("() => Array.isArray(window.__bt.etiquetas()) && window.__bt.etiquetas().indexOf('pico') >= 0", timeout=10000)
        page.click("#ed-editor-host .cm-content")
        page.keyboard.press("Control+End")
        page.keyboard.type("\nOutra frase #pi", delay=30)
        page.wait_for_selector(".cm-tooltip-autocomplete li", timeout=10000)
        page.wait_for_timeout(300)   # a lista acabou de abrir; o CodeMirror ainda está a seleccionar a 1.ª opção
        opcoes = page.eval_on_selector_all(".cm-tooltip-autocomplete li", "ls => ls.map(l => l.textContent.trim())")
        assert "#pico" in opcoes, opcoes
        page.keyboard.press("Enter")
        page.wait_for_function("() => window.__bt.editor().obterTexto().includes('Outra frase #pico')", timeout=10000)
    finally:
        ctx.close()
