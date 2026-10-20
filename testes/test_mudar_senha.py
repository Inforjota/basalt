"""19/10/2026 — «Mudar a senha» na interface (POST /api/senha): caso bom e casos maus."""
import pytest

from basalt import auth, config
from test_oauth import ANA, RUI, SENHA, Srv, _relogio, ler, mcp, renovar, srv, tokens  # noqa: F401 (fixtures)

NOVA = "outra senha bem comprida"


def _esc(s, cookie):
    return {"Cookie": cookie, "X-Basalt-Dono": s.dono(cookie), "X-Basalt": "1", "Origin": s.origem}


def _mudar(s, cookie, atual=SENHA, nova=NOVA, cab=None):
    return s.pedir("POST", "/api/senha", {"atual": atual, "nova": nova}, _esc(s, cookie) if cab is None else cab)


def test_mudar_senha_revoga_sessoes_e_ias_e_mantem_este_browser(srv):
    s = srv()
    cid, t, cookie = tokens(s)                       # uma IA ligada por OAuth
    outro = s.entrar()                               # outra sessão (outro aparelho)
    st, p, _ = s.pedir("POST", "/api/ligacoes/token", {"nome": "Codex"}, _esc(s, cookie))
    assert st == 201
    st, r, cab = _mudar(s, cookie)
    assert st == 200 and r["ok"], r
    novo = cab["set-cookie"].split(";", 1)[0]
    assert "HttpOnly" in cab["set-cookie"] and novo != cookie
    # o toml tem o hash novo, sem a senha em claro, e continua 0600
    texto = s.toml.read_text(encoding="utf-8")
    assert NOVA not in texto and (s.toml.stat().st_mode & 0o777) == 0o600
    u = config.carregar(s.toml, ambiente={}).utilizador(ANA)
    assert auth.verificar_senha(u.senha_hash, NOVA) and not auth.verificar_senha(u.senha_hash, SENHA)
    # tudo o que estava preso à senha antiga cai; este browser continua dentro
    assert s.pedir("GET", "/api/eu", cab={"Cookie": cookie})[0] == 401
    assert s.pedir("GET", "/api/eu", cab={"Cookie": outro})[0] == 401
    assert s.pedir("GET", "/api/eu", cab={"Cookie": novo})[0] == 200
    assert mcp(s, t["access_token"])[0] == 401 and renovar(s, cid, t["refresh_token"])[0] == 400
    assert mcp(s, p["token"])[0] == 401
    # entrar: a antiga já não serve, a nova sim
    assert s.pedir("POST", "/api/entrar", {"email": ANA, "senha": SENHA}, {"X-Basalt": "1"})[0] == 401
    assert s.entrar(senha=NOVA)
    # o outro utilizador não foi tocado
    assert s.entrar(RUI)
    assert not list(s.toml.parent.glob(".basalt.toml.*"))   # sem temporários deixados para trás



def test_mudar_senha_e_repor_o_toml_antigo_nao_ressuscita_as_ias(srv):
    """ronda 13 + «Mudar a senha»: a revogação fica gravada na base. Repor uma cópia antiga do basalt.toml (com o hash da
    senha antiga) deixava a «impressão» igual à de antes — e as ligações das IAs e o token pessoal voltavam a valer."""
    import os, time
    s = srv()
    cid, t, cookie = tokens(s)
    st, p, _ = s.pedir("POST", "/api/ligacoes/token", {"nome": "Codex"}, _esc(s, cookie))
    assert st == 201
    antigo = s.toml.read_text(encoding="utf-8")
    assert _mudar(s, cookie)[0] == 200
    s.toml.write_text(antigo, encoding="utf-8")                                     # alguém repõe a cópia de ontem
    os.utime(s.toml, (time.time() + 7, time.time() + 7))
    assert s.entrar()                                                                # a senha antiga vale outra vez…
    assert mcp(s, t["access_token"])[0] == 401, "o acesso antigo da IA voltou a valer com o toml reposto"
    assert renovar(s, cid, t["refresh_token"])[0] == 400, "a renovação antiga voltou a valer com o toml reposto"
    assert mcp(s, p["token"])[0] == 401, "o token pessoal antigo voltou a valer com o toml reposto"


def test_controlo_mudar_senha_e_ligar_de_novo(srv):
    """controlo: depois de mudar a senha, uma ligação NOVA (novo «Permitir», com a sessão nova deste browser) funciona."""
    from test_oauth import fluxo, trocar
    s = srv()
    _, _, cookie = tokens(s)
    st, _, cab = _mudar(s, cookie)
    assert st == 200
    novo = cab["set-cookie"].split(";", 1)[0]
    cid, v, cod, _ = fluxo(s, cookie=novo)
    st, t2, _ = trocar(s, cid, v, cod)
    assert st == 200, t2
    assert mcp(s, t2["access_token"])[0] == 200

@pytest.mark.parametrize("atual,nova,motivo", [
    ("senha errada", NOVA, "actual está errada"),
    (SENHA, "curta", "8 caracteres"),
    (SENHA, SENHA, "igual"),
])
def test_mudar_senha_recusa_sem_mexer_no_toml(srv, atual, nova, motivo):
    s = srv()
    cookie = s.entrar()
    antes = s.toml.read_bytes()
    st, r, cab = _mudar(s, cookie, atual, nova)
    assert st == 400 and motivo in r["erro"], r
    assert "set-cookie" not in cab and s.toml.read_bytes() == antes
    assert s.pedir("GET", "/api/eu", cab={"Cookie": cookie})[0] == 200


def test_mudar_senha_limite_de_tentativas(srv):
    s = srv()
    cookie = s.entrar()
    estados = [_mudar(s, cookie, "errada %d" % i)[0] for i in range(12)]
    assert 429 in estados
    assert _mudar(s, cookie)[0] == 429            # bloqueado também com a certa, até passar a janela


def test_mudar_senha_exige_escrita_do_browser(srv):
    s = srv()
    cookie = s.entrar()
    esc = _esc(s, cookie)
    sem = {k: v for k, v in esc.items() if k != "X-Basalt"}
    assert _mudar(s, cookie, cab=sem)[0] == 403
    assert _mudar(s, cookie, cab={**esc, "Sec-Fetch-Site": "cross-site"})[0] == 403
    assert _mudar(s, cookie, cab={})[0] == 401
    # um token de IA não muda a senha da conta
    _, t, _ = tokens(s)
    assert s.pedir("POST", "/api/senha", {"atual": SENHA, "nova": NOVA},
                   {"Authorization": "Bearer " + t["access_token"], "X-Basalt": "1"})[0] in (401, 403)
    assert auth.verificar_senha(config.carregar(s.toml, ambiente={}).utilizador(ANA).senha_hash, SENHA)


def test_mudar_senha_so_no_modo_senha(srv):
    s = srv(modo="cabecalho")
    cab = {"X-Basalt-Email": ANA}
    dono = s.pedir("GET", "/api/eu", cab=cab)[1]["dono"]
    st, r, _ = s.pedir("POST", "/api/senha", {"atual": SENHA, "nova": NOVA},
                       {**cab, "X-Basalt-Dono": dono, "X-Basalt": "1", "Origin": s.origem})
    assert st == 400 and "senha" in r["erro"], r


def test_pagina_mudar_senha_no_browser(srv, navegador):
    """num Chromium a sério: a secção aparece no modo senha, recusa senhas diferentes, muda e a página continua com sessão."""
    from test_ligar import _abrir_ligar, _entrar
    s = srv()
    ctx = navegador.new_context()
    try:
        p = _entrar(ctx, s)
        _abrir_ligar(p, s.url)
        p.wait_for_selector("#senha-form")
        p.on("dialog", lambda d: d.accept())
        p.fill("#senha-atual", SENHA); p.fill("#senha-nova", NOVA); p.fill("#senha-rep", NOVA + "x")
        p.click("#senha-form button")
        assert "não são iguais" in p.inner_text("#senha-msg")
        p.fill("#senha-rep", NOVA)
        p.click("#senha-form button")
        p.wait_for_selector('#senha-msg:has-text("Senha mudada")', timeout=10000)
        assert p.input_value("#senha-atual") == ""                 # o formulário limpa-se
        assert p.evaluate("fetch('/api/eu').then(r => r.status)") == 200   # este browser continua dentro (cookie novo)
    finally:
        ctx.close()
    assert auth.verificar_senha(config.carregar(s.toml, ambiente={}).utilizador(ANA).senha_hash, NOVA)
    # mau: no modo cabecalho (SSO) a secção não aparece
    s2 = srv(modo="cabecalho")
    ctx = navegador.new_context(extra_http_headers={"X-Basalt-Email": ANA})
    try:
        p = ctx.new_page()
        _abrir_ligar(p, s2.url)
        assert p.locator("#senha-form").count() == 0
    finally:
        ctx.close()
