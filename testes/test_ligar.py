"""«Ligar a tua IA» (#/ligar): a página que mostra o endereço, cria tokens e revoga ligações — num Chromium a sério contra o
servidor do pacote com o OAuth ligado (o Srv de test_oauth). Cada teste tem o caso bom e o mau."""
import re

import pytest

from test_oauth import ANA, RUI, SENHA, Srv, _relogio, ler, mcp, srv, tokens  # noqa: F401 (fixtures)


def _entrar(ctx, s, email=ANA, base=None):
    base = base or s.url
    p = ctx.new_page()
    p.goto(base + "/entrar")
    p.fill("#email", email); p.fill("#senha", SENHA); p.click("#ok")
    p.wait_for_url(base + "/", timeout=10000)
    return p


def _abrir_ligar(p, base):
    p.goto(base + "/#/ligar")
    p.wait_for_selector(".ligar-ias", timeout=10000)


def _ia(p, ia):
    p.click(f'.ligar-ia[data-ia="{ia}"]')
    p.wait_for_selector("#ligar-cartao:not([hidden])")


def test_ligar_copiar_mostra_o_url_certo(srv, navegador):
    s = srv(nome="localhost")                       # localhost: o clipboard só existe em contexto seguro
    ctx = navegador.new_context(permissions=["clipboard-read", "clipboard-write"])
    try:
        p = _entrar(ctx, s)
        _abrir_ligar(p, s.url)
        _ia(p, "claude")
        assert p.inner_text("#ligar-cartao .ligar-pre") == s.url + "/mcp"
        p.click("#ligar-cartao [data-copiar]")
        assert p.evaluate("navigator.clipboard.readText()") == s.url + "/mcp"
        _ia(p, "claude-code")
        assert p.inner_text("#ligar-cartao .ligar-pre") == "claude mcp add --transport http basalt " + s.url + "/mcp"
    finally:
        ctx.close()
    # mau: sem url_publico o Claude/ChatGPT não têm endereço para copiar — diz-se porquê
    s2 = srv(nome="localhost", url=False)
    ctx = navegador.new_context()
    try:
        p = _entrar(ctx, s2, base=s2.origem)
        _abrir_ligar(p, s2.origem)
        for ia in ("claude", "chatgpt"):
            _ia(p, ia)
            assert p.locator("#ligar-cartao .ligar-pre").count() == 0
            assert "url_publico" in p.inner_text("#ligar-cartao")
    finally:
        ctx.close()


def test_ligar_verificar(srv, navegador):
    # mau 1: sem url_publico → acusa a falta
    s = srv(nome="localhost", url=False)
    ctx = navegador.new_context()
    try:
        p = _entrar(ctx, s, base=s.origem)
        _abrir_ligar(p, s.origem)
        _ia(p, "claude")
        p.click("#ligar-cartao [data-verificar]")
        p.wait_for_selector("#ligar-cartao .ligar-verif-lista")
        assert "✗ falta o url_publico" in p.inner_text("#ligar-cartao .ligar-verif")
    finally:
        ctx.close()
    # bom: aberto pelo url_publico → metadados, servidor de autorização e o 401 do /mcp passam; http (dos testes) não é https
    s = srv(nome="localhost")
    ctx = navegador.new_context()
    try:
        p = _entrar(ctx, s)
        _abrir_ligar(p, s.url)
        _ia(p, "chatgpt")
        p.click("#ligar-cartao [data-verificar]")
        p.wait_for_selector("#ligar-cartao .ligar-verif-lista")
        v = p.inner_text("#ligar-cartao .ligar-verif-lista")
        assert "✓ os metadados do recurso respondem" in v and "✓ o servidor de autorização responde" in v, v
        assert "✓ o /mcp sem token pede a ligação" in v and "✗ o endereço público é https" in v, v
        assert "160.79.104.0/21" in p.inner_text("#ligar-cartao .ligar-verif")
        # mau 3: um proxy à frente que come o desafio (401 sem resource_metadata) — o claude.ai não descobriria o OAuth
        p.route("**/mcp", lambda r: r.fulfill(status=401, headers={"WWW-Authenticate": 'Bearer realm="proxy"'}, body="{}"))
        p.click("#ligar-cartao [data-verificar]")
        p.wait_for_selector("#ligar-cartao .ligar-verif-lista >> text=✗ o /mcp sem token")
    finally:
        ctx.close()
    # mau 2: aberto por outro endereço (127.0.0.1 em vez do url_publico localhost) → avisa em vez de dar por bom
    ctx = navegador.new_context()
    try:
        outro = f"http://127.0.0.1:{s.porta}"
        p = _entrar(ctx, s, base=outro)
        _abrir_ligar(p, outro)
        _ia(p, "claude")
        p.click("#ligar-cartao [data-verificar]")
        p.wait_for_selector("#ligar-cartao .ligar-verif-lista")
        v = p.inner_text("#ligar-cartao .ligar-verif-lista")
        assert "✗ estás a abrir o Basalt por " + outro in v and "✓ os metadados" not in v, v
    finally:
        ctx.close()


def test_ligar_token_mostra_uma_vez_e_revogar(srv, navegador):
    s = srv(nome="localhost")
    ctx = navegador.new_context()
    try:
        p = _entrar(ctx, s)
        _abrir_ligar(p, s.url)
        _ia(p, "codex")
        p.click("#ligar-cartao [data-token]")
        p.wait_for_selector("#ligar-cartao .ligar-token .ligar-pre")
        blocos = p.locator("#ligar-cartao .ligar-token .ligar-pre").all_inner_texts()
        assert 'url = "' + s.url + '/mcp"' in blocos[0] and 'bearer_token_env_var = "BASALT_TOKEN"' in blocos[0]
        token = re.fullmatch(r"export BASALT_TOKEN=(bslt_[A-Za-z0-9_-]+)", blocos[1]).group(1)
        assert ler(s, token, "barcos")[0] == 200
        p.wait_for_selector(".ligar-ligacao")
        assert "Codex" in p.inner_text("#ligar-lista")
        # mostra-se uma vez: ao voltar à página o token já não aparece em lado nenhum
        p.reload()
        p.wait_for_selector(".ligar-ligacao")
        _ia(p, "codex")
        assert token not in p.content()
        # revogar tira a linha e o token deixa de valer
        p.click(".ligar-ligacao [data-revogar]")
        p.wait_for_selector("#ligar-lista >> text=Ainda não há nenhuma IA ligada")
        assert mcp(s, token)[0] == 401
    finally:
        ctx.close()


def test_ligar_revogar_todas(srv, navegador):
    s = srv(nome="localhost")
    _, t1, _ = tokens(s)                              # uma ligação OAuth (o «claude.ai») + um token pessoal
    ctx = navegador.new_context()
    try:
        p = _entrar(ctx, s)
        _abrir_ligar(p, s.url)
        _ia(p, "outra")
        p.click("#ligar-cartao [data-token]")
        p.wait_for_selector("#ligar-cartao .ligar-token .ligar-pre")
        t2 = p.inner_text("#ligar-cartao .ligar-token .ligar-pre").split("Bearer ", 1)[1].strip()
        p.wait_for_selector(".ligar-ligacao >> nth=1")
        assert mcp(s, t1["access_token"])[0] == 200 and mcp(s, t2)[0] == 200
        p.once("dialog", lambda d: d.dismiss())        # mau: cancelar não revoga nada
        p.click("#ligar-revogar-todas")
        assert mcp(s, t1["access_token"])[0] == 200
        p.once("dialog", lambda d: d.accept())
        p.click("#ligar-revogar-todas")
        p.wait_for_selector("#ligar-lista >> text=Ainda não há nenhuma IA ligada")
        assert mcp(s, t1["access_token"])[0] == 401 and mcp(s, t2)[0] == 401
    finally:
        ctx.close()


def test_ligar_outra_conta_nao_ve(srv, navegador):
    s = srv(nome="localhost")
    tokens(s, ANA)
    for email, quantas in ((ANA, 1), (RUI, 0)):       # bom: a Ana vê a sua; mau: o Rui não vê a da Ana
        ctx = navegador.new_context()
        try:
            p = _entrar(ctx, s, email)
            _abrir_ligar(p, s.url)
            p.wait_for_selector("#ligar-lista")
            assert p.locator(".ligar-ligacao").count() == quantas, email
        finally:
            ctx.close()
