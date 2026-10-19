"""«Traz a tua IA»: o servidor OAuth 2.1 do /mcp (basalt/oauth.py), a sério, pelo servidor web do pacote (criar_servidor).

Cada teste foi corrido contra o pacote de antes (3eb4288, sem OAuth) e falha lá — ver o registo da prova no pedido de auditoria.
O browser é simulado por HTTP (cookie de sessão + Origin), salvo no teste do SameSite, que usa o Chromium do Playwright.
"""
import base64
import hashlib
import html as html_mod
import http.client
import json
import re
import secrets
import threading
import urllib.parse

import pytest

from basalt import auth, config, oauth, web
from conftest import porta_livre

SENHA = "uma senha comprida"
HASH = auth.hash_senha(SENHA)
ANA, RUI = "ana@exemplo.pt", "rui@exemplo.pt"
REDIR = "https://claude.ai/api/mcp/auth_callback"
NOTA = "---\nname: barcos\ndescription: horários\ntype: reference\n---\nO barco sai às 8h.\n"
NOTA_RUI = "---\nname: segredo-do-rui\ndescription: só do Rui\ntype: reference\n---\nCódigo do cofre do Rui.\n"


def _toml(dados, porta, url, modo="senha", utilizadores=None, extra=""):
    us = utilizadores if utilizadores is not None else [
        {"email": ANA, "espaco": "ana", "admin": True, "senha_hash": HASH},
        {"email": RUI, "espaco": "rui", "senha_hash": HASH}]
    t = [f'[basalt]\ndados = "{dados}"\nporta = {porta}\nescutar = "127.0.0.1"\ngit = false\n'
         + (f'url_publico = "{url}"\n' if url else ""),
         f'[auth]\nmodo = "{modo}"\n', '[ia]\nfornecedor = "nenhum"\n']
    for u in us:
        t.append("[[utilizadores]]\n" + "".join(f'{k} = {json.dumps(v)}\n' for k, v in u.items()))
    t.append(extra)
    return "\n".join(t)


class Srv:
    """um Basalt em processo com o OAuth ligado (url_publico = http://<nome>:<porta>, loopback para os testes)."""

    def __init__(self, tmp_path, modo="senha", nome="127.0.0.1", url=True, utilizadores=None, extra=""):
        self.porta = porta_livre()
        self.nome = nome
        self.url = f"http://{nome}:{self.porta}" if url else ""
        self.toml = tmp_path / "basalt.toml"
        self.dados = tmp_path / "dados"
        self.tmp = tmp_path
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.utilizadores = utilizadores
        self.extra = extra
        self.modo = modo
        self.escrever()
        self.cfg = config.carregar(self.toml, ambiente={})
        self.srv = web.criar_servidor(self.cfg, host="127.0.0.1", porta=self.porta)
        self.fio = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.fio.start()
        for esp, texto, n in (("ana", NOTA, "barcos"), ("rui", NOTA_RUI, "segredo-do-rui")):
            if any(u["espaco"] == esp for u in (utilizadores or [{"espaco": "ana"}, {"espaco": "rui"}])):
                e = self.cfg.espaco(esp).criar()
                for d in (e.real, e.copia):
                    (d / f"{n}.md").write_text(texto, encoding="utf-8")

    def escrever(self, utilizadores=None):
        self.toml.write_text(_toml(self.dados, self.porta, self.url, self.modo, utilizadores or self.utilizadores, self.extra), encoding="utf-8")

    @property
    def origem(self):
        return f"http://{self.nome}:{self.porta}"

    def pedir(self, metodo, caminho, corpo=None, cab=None, form=None):
        c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=15)
        h = {"Host": f"{self.nome}:{self.porta}", **(cab or {})}
        if form is not None:
            dados = urllib.parse.urlencode(form).encode()
            h.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif corpo is not None:
            dados = json.dumps(corpo).encode() if not isinstance(corpo, bytes) else corpo
            h.setdefault("Content-Type", "application/json")
        else:
            dados = None
        c.request(metodo, caminho, body=dados, headers=h)
        r = c.getresponse()
        b = r.read()
        cabs = {k.lower(): v for k, v in r.getheaders()}
        c.close()
        try:
            j = json.loads(b.decode("utf-8")) if b and "json" in cabs.get("content-type", "") else None
        except ValueError:
            j = None
        return r.status, (j if j is not None else b.decode("utf-8", "replace")), cabs

    def entrar(self, email=ANA, senha=SENHA):
        st, r, cab = self.pedir("POST", "/api/entrar", {"email": email, "senha": senha}, {"X-Basalt": "1"})
        assert st == 200, r
        return cab["set-cookie"].split(";", 1)[0]

    def dono(self, cookie):
        st, r, _ = self.pedir("GET", "/api/eu", cab={"Cookie": cookie})
        return r["dono"]

    def parar(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def srv(tmp_path):
    feitos = []

    def fazer(**k):
        s = Srv(tmp_path / f"s{len(feitos)}", **k)
        feitos.append(s)
        return s
    yield fazer
    for s in feitos:
        s.parar()


@pytest.fixture(autouse=True)
def _relogio():
    antes = oauth.RELOGIO
    yield
    oauth.RELOGIO = antes


# ───────────────────────── o «cliente» (o que o claude.ai faz) ─────────────────────────
def pkce():
    v = secrets.token_urlsafe(48)
    c = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    return v, c


def pkce_s256(v):
    return base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()


def registar(s, uris=(REDIR,), nome="Claude", **extra):
    return s.pedir("POST", "/oauth/register", {"redirect_uris": list(uris), "client_name": nome, "token_endpoint_auth_method": "none",
                                                "grant_types": ["authorization_code", "refresh_token"], **extra})


def pedir_autorizacao(s, client_id, challenge, redirect=REDIR, state="xyz", **extra):
    q = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "code_challenge": challenge,
         "code_challenge_method": "S256", "state": state, "resource": s.url + "/mcp", "scope": "notas", **extra}
    return s.pedir("GET", "/oauth/authorize?" + urllib.parse.urlencode(q))


def pid_de(pagina):
    m = re.search(r"/oauth/authorize\?pedido=([A-Za-z0-9_-]+)", pagina)
    assert m, pagina[:400]
    return m.group(1)


def campos(pagina):
    return {n: html_mod.unescape(v) for n, v in re.findall(r"<input type=hidden name=(\w+) value='([^']*)'>", pagina)}


def consentir(s, cookie, pid, decisao="permitir", mudar=None, cab=None):
    st, pag, _ = s.pedir("GET", f"/oauth/authorize?pedido={pid}", cab={"Cookie": cookie})
    assert st == 200 and "<form" in pag, (st, pag[:300])
    f = {**campos(pag), "decisao": decisao, **(mudar or {})}
    h = {"Cookie": cookie, "Origin": s.origem, "Sec-Fetch-Site": "same-origin", **(cab or {})}
    return s.pedir("POST", "/oauth/autorizar", form=f, cab=h)


def codigo_de(location):
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(location).query))
    return q


def fluxo(s, email=ANA, uris=(REDIR,), redirect=REDIR, cookie=None):
    """registo → autorizar → consentir → código. Devolve (client_id, verifier, código, cookie)."""
    st, cli, _ = registar(s, uris)
    assert st == 201, cli
    v, c = pkce()
    st, pag, _ = pedir_autorizacao(s, cli["client_id"], c, redirect)
    assert st == 200, pag
    cookie = cookie or s.entrar(email)
    st, _, cab = consentir(s, cookie, pid_de(pag))
    assert st == 303, (st, _)
    q = codigo_de(cab["location"])
    assert cab["location"].startswith(redirect) and q.get("code"), cab["location"]
    return cli["client_id"], v, q["code"], cookie


def trocar(s, client_id, verifier, codigo, redirect=REDIR, **extra):
    return s.pedir("POST", "/oauth/token", form={"grant_type": "authorization_code", "code": codigo, "client_id": client_id,
                                                  "redirect_uri": redirect, "code_verifier": verifier, **extra})


def renovar(s, client_id, refresh):
    return s.pedir("POST", "/oauth/token", form={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id})


def mcp(s, token, metodo="tools/list", params=None, cab=None):
    return s.pedir("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": metodo, "params": params or {}},
                   {"Authorization": "Bearer " + token, **(cab or {})})


def ler(s, token, nome):
    return mcp(s, token, "tools/call", {"name": "ler_nota", "arguments": {"nome": nome}})


def tokens(s, email=ANA):
    cid, v, cod, cookie = fluxo(s, email)
    st, t, _ = trocar(s, cid, v, cod)
    assert st == 200, t
    return cid, t, cookie


# ───────────────────────── 1. descoberta ─────────────────────────
def test_oauth_descoberta(srv):
    s = srv()
    st, prm, cab = s.pedir("GET", "/.well-known/oauth-protected-resource")
    assert st == 200 and prm["resource"] == s.url + "/mcp" and prm["authorization_servers"] == [s.url], prm
    assert "no-store" in cab.get("cache-control", "")
    assert s.pedir("GET", "/.well-known/oauth-protected-resource/mcp")[1] == prm
    st, asm, _ = s.pedir("GET", "/.well-known/oauth-authorization-server")
    assert st == 200 and asm["issuer"] == s.url
    assert asm["authorization_endpoint"] == s.url + "/oauth/authorize" and asm["token_endpoint"] == s.url + "/oauth/token"
    assert asm["registration_endpoint"] == s.url + "/oauth/register"
    assert asm["code_challenge_methods_supported"] == ["S256"]                 # nada de «plain»
    assert asm["token_endpoint_auth_methods_supported"] == ["none"]
    assert asm["authorization_response_iss_parameter_supported"] is True
    assert "access-control-allow-origin" not in cab                            # sem CORS


# ───────────────────────── 2. o 401 do /mcp aponta para os metadados ─────────────────────────
def test_mcp_401_metadata(srv):
    s = srv()
    st, _, cab = s.pedir("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    w = cab.get("www-authenticate", "")
    assert st == 401 and f'resource_metadata="{s.url}/.well-known/oauth-protected-resource"' in w and 'scope="notas"' in w, w
    assert "invalid_token" not in w
    st, _, cab = mcp(s, "bsla_naoexiste")
    assert st == 401 and 'error="invalid_token"' in cab.get("www-authenticate", "")
    # o token nunca no endereço (ficava nos registos dos proxies)
    st, _, _ = s.pedir("POST", "/mcp?access_token=bsla_x", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert st == 400
    # com o OAuth ligado, o Origin do claude.ai passa (o conector pode mandá-lo); um Origin qualquer não
    _, t, _ = tokens(s)
    assert mcp(s, t["access_token"], cab={"Origin": "https://claude.ai"})[0] == 200
    assert mcp(s, t["access_token"], cab={"Origin": "https://mau.exemplo"})[0] == 403


# ───────────────────────── 3. registo dinâmico com lista fechada e limites ─────────────────────────
def test_dcr_limites(srv):
    s = srv()
    assert registar(s, ["https://mau.exemplo/cb"])[0] == 400                      # fora da lista fechada
    assert registar(s, [REDIR, "https://mau.exemplo/cb"])[0] == 400               # um mau estraga o registo inteiro
    assert registar(s, [])[0] == 400
    assert registar(s, [f"http://127.0.0.1:{4000 + i}/cb" for i in range(6)])[0] == 400   # no máximo 5
    assert registar(s, ["http://exemplo.pt/cb"])[0] == 400                         # http só em loopback
    assert registar(s, ["https://claude.ai/api/mcp/auth_callback#x"])[0] == 400
    assert registar(s, [REDIR], token_endpoint_auth_method="client_secret_basic")[0] == 400
    assert registar(s, [REDIR], grant_types=["client_credentials"])[0] == 400
    st, c, _ = registar(s, ["http://127.0.0.1:33418/callback", "https://chatgpt.com/connector_platform_oauth_redirect"])
    assert st == 201 and c["client_id"].startswith("bslc_") and c["token_endpoint_auth_method"] == "none"
    assert s.pedir("POST", "/oauth/register", b"nao e json")[0] == 400
    assert s.pedir("POST", "/oauth/register", b"x" * 20000)[0] == 400              # corpo grande demais: não se lê
    # 10 por IP e por hora (já houve 1)
    estados = [registar(s, [REDIR])[0] for _ in range(10)]
    assert estados[:9] == [201] * 9 and estados[9] == 429, estados


def test_dcr_redirects_extra(srv):
    s = srv(extra='[oauth]\nredirects_extra = ["https://app.exemplo.pt/oauth/cb"]\n')
    assert registar(s, ["https://app.exemplo.pt/oauth/cb"])[0] == 201
    assert registar(s, ["https://app.exemplo.pt/oauth/cb2"])[0] == 400             # à letra


# ───────────────────────── 4. o fluxo completo ─────────────────────────
def test_fluxo_completo(srv):
    s = srv()
    st, cli, _ = registar(s)
    v, c = pkce()
    st, pag, cab = pedir_autorizacao(s, cli["client_id"], c, state="estado-123")
    # 1.º passo: página mínima que se recarrega no próprio Basalt (é aí que o cookie SameSite=Strict se vê)
    assert st == 200 and "http-equiv=refresh" in pag and "<form" not in pag
    pid = pid_de(pag)
    # sem sessão (modo senha) → entrar, e voltar ao pedido
    st, _, cab = s.pedir("GET", f"/oauth/authorize?pedido={pid}")
    assert st == 302 and cab["location"] == "/entrar?" + urllib.parse.urlencode({"volta": f"/oauth/authorize?pedido={pid}"})
    cookie = s.entrar()
    st, _, cab = consentir(s, cookie, pid)
    assert st == 303
    q = codigo_de(cab["location"])
    assert cab["location"].startswith(REDIR + "?") and q["state"] == "estado-123" and q["iss"] == s.url   # RFC 9207
    st, t, cab = trocar(s, cli["client_id"], v, q["code"])
    assert st == 200 and t["token_type"] == "Bearer" and t["access_token"].startswith("bsla_") and t["refresh_token"].startswith("bslr_")
    assert t["expires_in"] == 3600 and "no-store" in cab.get("cache-control", "")
    st, r, _ = ler(s, t["access_token"], "barcos")
    assert st == 200 and "8h" in json.dumps(r, ensure_ascii=False), r
    # renovação: par novo, o acesso novo funciona
    st, t2, _ = renovar(s, cli["client_id"], t["refresh_token"])
    assert st == 200 and t2["access_token"] != t["access_token"] and t2["refresh_token"] != t["refresh_token"]
    assert ler(s, t2["access_token"], "barcos")[0] == 200
    # «Recusar» devolve access_denied à aplicação, com state e iss
    st, pag, _ = pedir_autorizacao(s, cli["client_id"], c, state="s2")
    st, _, cab = consentir(s, cookie, pid_de(pag), "recusar")
    q = codigo_de(cab["location"])
    assert st == 303 and q["error"] == "access_denied" and q["state"] == "s2" and q["iss"] == s.url and "code" not in q
    # na base só ficam hashes: nenhum token nem código em claro
    bruto = (s.dados / "oauth.sqlite").read_bytes()
    for x in (t["access_token"], t["refresh_token"], t2["access_token"], t2["refresh_token"]):
        assert x.encode() not in bruto
    assert oct((s.dados / "oauth.sqlite").stat().st_mode & 0o777) == "0o600"


def test_codigo_e_acesso_expiram(srv):
    s = srv()
    cid, v, cod, _ = fluxo(s)
    agora = oauth.RELOGIO()
    oauth.RELOGIO = lambda: agora + 601                                             # o código vale 10 min
    assert trocar(s, cid, v, cod)[1]["error"] == "invalid_grant"
    oauth.RELOGIO = lambda: agora
    cid, v, cod, _ = fluxo(s)
    _, t, _ = trocar(s, cid, v, cod)
    assert mcp(s, t["access_token"])[0] == 200
    oauth.RELOGIO = lambda: agora + 3601                                            # o acesso vale 1 h
    assert mcp(s, t["access_token"])[0] == 401
    assert renovar(s, cid, t["refresh_token"])[0] == 200                            # a renovação ainda vale
    oauth.RELOGIO = lambda: agora + 31 * 86400                                      # 30 dias
    assert renovar(s, cid, t["refresh_token"])[0] == 400


def test_redirect_e_cliente_na_troca(srv):
    s = srv()
    cid, v, cod, cookie = fluxo(s)
    assert trocar(s, cid, v, cod, redirect="https://claude.com/api/mcp/auth_callback")[1]["error"] == "invalid_grant"
    assert trocar(s, cid, v, cod)[0] == 400                                          # o código gastou-se na 1.ª tentativa
    cid, v, cod, _ = fluxo(s, cookie=cookie)
    _, outro, _ = registar(s)
    assert trocar(s, outro["client_id"], v, cod)[1]["error"] == "invalid_grant"      # código de outra aplicação
    # authorize com redirect_uri que não foi registado → página de erro, NUNCA redirige para lá
    st, cli, _ = registar(s)
    st, pag, cab = pedir_autorizacao(s, cli["client_id"], pkce()[1], redirect="https://chatgpt.com/connector_platform_oauth_redirect")
    assert st == 400 and "location" not in cab
    # loopback: a porta pode mudar (RFC 8252), o caminho não
    st, cli, _ = registar(s, ["http://127.0.0.1:5000/cb"])
    assert pedir_autorizacao(s, cli["client_id"], pkce()[1], redirect="http://127.0.0.1:6123/cb")[0] == 200
    assert pedir_autorizacao(s, cli["client_id"], pkce()[1], redirect="http://127.0.0.1:6123/outro")[0] == 400
    # recurso de outro servidor → invalid_target
    st, cli, _ = registar(s)
    st, _, cab = pedir_autorizacao(s, cli["client_id"], pkce()[1], resource="https://outro.exemplo/mcp")
    assert st == 303 and codigo_de(cab["location"])["error"] == "invalid_target"


# ───────────────────────── 5. PKCE ─────────────────────────
def test_pkce(srv):
    s = srv()
    cid, v, cod, cookie = fluxo(s)
    st, r, _ = trocar(s, cid, "x" * 43 + "errado", cod)
    assert st == 400 and r["error"] == "invalid_grant"
    assert trocar(s, cid, v, cod)[0] == 400                    # uma só tentativa: quem interceptou o código não vai experimentando
    cid, v, cod, _ = fluxo(s, cookie=cookie)
    assert trocar(s, cid, None, cod)[0] == 400                 # sem verifier
    # «plain» não existe: nem com o challenge igual ao verifier
    st, cli, _ = registar(s)
    v = secrets.token_urlsafe(48)[:43]
    st, _, cab = pedir_autorizacao(s, cli["client_id"], v, code_challenge_method="plain")
    assert st == 303 and codigo_de(cab["location"])["error"] == "invalid_request"
    st, _, cab = pedir_autorizacao(s, cli["client_id"], "")
    assert st == 303 and codigo_de(cab["location"])["error"] == "invalid_request"
    # e o servidor nunca compara «à plain»: challenge = o próprio verifier (43 caracteres válidos) não passa na troca
    st, pag, _ = pedir_autorizacao(s, cli["client_id"], v)
    st, _, cab = consentir(s, cookie, pid_de(pag))
    st, r, _ = trocar(s, cli["client_id"], v, codigo_de(cab["location"])["code"])
    assert st == 400 and r["error"] == "invalid_grant"
    assert oauth.pkce_ok(v, v) is False and oauth.pkce_ok(v, pkce_s256(v)) is True


# ───────────────────────── 6. código usado duas vezes ─────────────────────────
def test_codigo_reutilizado(srv):
    s = srv()
    cid, v, cod, _ = fluxo(s)
    st, t, _ = trocar(s, cid, v, cod)
    assert st == 200 and mcp(s, t["access_token"])[0] == 200
    st, r, _ = trocar(s, cid, v, cod)
    assert st == 400 and r["error"] == "invalid_grant"
    # alguém copiou o código: cai tudo o que ele deu
    assert mcp(s, t["access_token"])[0] == 401
    assert renovar(s, cid, t["refresh_token"])[0] == 400


# ───────────────────────── 7. renovação usada duas vezes ─────────────────────────
def test_refresh_reutilizado(srv):
    s = srv()
    cid, t, _ = tokens(s)
    st, t2, _ = renovar(s, cid, t["refresh_token"])
    assert st == 200
    st, r, _ = renovar(s, cid, t["refresh_token"])                 # o antigo outra vez: roubado
    assert st == 400 and r["error"] == "invalid_grant"
    assert mcp(s, t2["access_token"])[0] == 401                    # a família inteira caiu
    assert renovar(s, cid, t2["refresh_token"])[0] == 400
    # de outra aplicação também não
    cid, t, _ = tokens(s)
    _, outro, _ = registar(s)
    assert renovar(s, outro["client_id"], t["refresh_token"])[0] == 400


# ───────────────────────── 8. CSRF do consentimento ─────────────────────────
def test_csrf_consentimento(srv):
    s = srv()
    st, cli, _ = registar(s)
    st, pag, _ = pedir_autorizacao(s, cli["client_id"], pkce()[1])
    pid = pid_de(pag)
    cookie = s.entrar()
    for mudar, cab in (({"csrf": "0" * 64}, None),                               # CSRF errado
                       ({"dono": "outro"}, None),                                 # dono da página errado
                       ({}, {"Origin": "https://mau.exemplo"}),                   # outra origem
                       ({}, {"Origin": ""}),                                      # sem Origin
                       ({}, {"Sec-Fetch-Site": "cross-site"}),
                       ({}, {"Authorization": "Bearer bslt_x"})):                 # tokens não aprovam ligações
        st, pag2, c2 = consentir(s, cookie, pid, mudar=mudar, cab=cab)
        assert st == 403 and "location" not in c2, (mudar, cab, st)
    # o CSRF está preso à sessão: o de outra sessão da mesma conta não serve
    outra = s.entrar()
    st, pag_a, _ = s.pedir("GET", f"/oauth/authorize?pedido={pid}", cab={"Cookie": cookie})
    f = {**campos(pag_a), "decisao": "permitir"}
    st, _, _ = s.pedir("POST", "/oauth/autorizar", form=f, cab={"Cookie": outra, "Origin": s.origem})
    assert st == 403
    # sem sessão nenhuma
    st, _, _ = s.pedir("POST", "/oauth/autorizar", form=f, cab={"Origin": s.origem})
    assert st == 403
    # o pedido NÃO se gastou com as tentativas falhadas: o verdadeiro ainda passa
    st, _, cab = consentir(s, cookie, pid)
    assert st == 303 and "code=" in cab["location"]
    # e depois de decidido não se decide outra vez
    st, _, _ = s.pedir("POST", "/oauth/autorizar", form=f, cab={"Cookie": cookie, "Origin": s.origem})
    assert st == 400


# ───────────────────────── 9. SameSite=Strict a sério (Chromium): o 1.º pedido vem de outro sítio ─────────────────────────
def test_samesite_strict(srv, navegador, tmp_path):
    """o Basalt em «localhost», a aplicação em «127.0.0.1» (outro sítio para o browser). Com a sessão aberta, a navegação que
    vem da aplicação NÃO leva o cookie; a página mínima recarrega-se no Basalt e aí a sessão vê-se — chega-se ao consentimento,
    não ao «entrar». Depois de «Permitir», o browser vai para o endereço de regresso (loopback) com o código."""
    s = srv(nome="localhost")
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    recebido = {}

    class App(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/cb"):
                recebido["q"] = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            b = (f"<a id=ir href='{recebido.get('ir', '')}'>ligar</a>" if self.path == "/" else "ok").encode()
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(b)))
            self.end_headers(); self.wfile.write(b)

        def log_message(self, *a):
            pass
    app = ThreadingHTTPServer(("127.0.0.1", 0), App)
    threading.Thread(target=app.serve_forever, daemon=True).start()
    try:
        cb = f"http://127.0.0.1:{app.server_address[1]}/cb"
        st, cli, _ = registar(s, [cb])
        v, c = pkce()
        q = {"response_type": "code", "client_id": cli["client_id"], "redirect_uri": cb, "code_challenge": c,
             "code_challenge_method": "S256", "state": "st", "resource": s.url + "/mcp"}
        recebido["ir"] = s.url + "/oauth/authorize?" + urllib.parse.urlencode(q)
        ctx = navegador.new_context()
        try:
            p = ctx.new_page()
            p.goto(s.url + "/entrar")
            p.fill("#email", ANA); p.fill("#senha", SENHA); p.click("#ok")
            p.wait_for_url(s.url + "/", timeout=10000)
            p.goto(f"http://127.0.0.1:{app.server_address[1]}/")
            p.click("#ir")
            p.wait_for_selector("button[value=permitir]", timeout=10000)        # o consentimento, não o «entrar»
            assert "/entrar" not in p.url
            p.click("button[value=permitir]")
            p.wait_for_url(cb + "*", timeout=10000)
            assert recebido["q"]["state"] == "st" and recebido["q"]["iss"] == s.url and recebido["q"].get("code")
        finally:
            ctx.close()
        assert trocar(s, cli["client_id"], v, recebido["q"]["code"], redirect=cb)[0] == 200
    finally:
        app.shutdown(); app.server_close()


def test_entrar_volta_ao_pedido(srv, navegador):
    """sem sessão: o consentimento manda para /entrar?volta=…, e depois de entrar volta-se ao pedido (só a esse tipo de endereço)."""
    s = srv(nome="localhost")
    st, cli, _ = registar(s)
    st, pag, _ = pedir_autorizacao(s, cli["client_id"], pkce()[1])
    pid = pid_de(pag)
    ctx = navegador.new_context()
    try:
        p = ctx.new_page()
        p.goto(s.url + f"/oauth/authorize?pedido={pid}")
        p.wait_for_url(s.url + "/entrar?*")
        p.fill("#email", ANA); p.fill("#senha", SENHA); p.click("#ok")
        p.wait_for_selector("button[value=permitir]", timeout=10000)
        # um «volta» para fora é ignorado
        ctx.clear_cookies()
        for mau in ("https://mau.exemplo/x", "//mau.exemplo/oauth/authorize?pedido=" + "a" * 32, "/oauth/authorize?pedido=x&y=1"):
            ctx.clear_cookies()
            p.goto(s.url + "/entrar?volta=" + urllib.parse.quote(mau))
            p.fill("#email", ANA); p.fill("#senha", SENHA); p.click("#ok")
            p.wait_for_url(s.url + "/", timeout=10000)
    finally:
        ctx.close()


# ───────────────────────── 10. isolamento entre contas ─────────────────────────
def test_isolamento_donos(srv):
    s = srv()
    # caso bom: o token da Ana lê as notas da Ana
    cid_a, ta, cookie_a = tokens(s, ANA)
    assert "8h" in json.dumps(ler(s, ta["access_token"], "barcos")[1], ensure_ascii=False)
    # caso mau: o token da Ana não chega às do Rui (outro espaço)
    st, r, _ = ler(s, ta["access_token"], "segredo-do-rui")
    assert "cofre" not in json.dumps(r, ensure_ascii=False), r
    cid_r, tr, cookie_r = tokens(s, RUI)
    assert "cofre" in json.dumps(ler(s, tr["access_token"], "segredo-do-rui")[1], ensure_ascii=False)
    # um pedido aberto pela Ana fica dela: o Rui não o aprova (nem com o formulário da Ana)
    st, cli, _ = registar(s)
    st, pag, _ = pedir_autorizacao(s, cli["client_id"], pkce()[1])
    pid = pid_de(pag)
    st, pag_a, _ = s.pedir("GET", f"/oauth/authorize?pedido={pid}", cab={"Cookie": cookie_a})
    assert st == 200
    st, pag_r, _ = s.pedir("GET", f"/oauth/authorize?pedido={pid}", cab={"Cookie": cookie_r})
    assert st == 403 and "<form" not in pag_r
    f = {**campos(pag_a), "decisao": "permitir"}
    assert s.pedir("POST", "/oauth/autorizar", form=f, cab={"Cookie": cookie_r, "Origin": s.origem})[0] == 403
    # a página de ligações: cada um vê só as suas, e não revoga as do outro
    dono_a, dono_r = s.dono(cookie_a), s.dono(cookie_r)
    st, la, _ = s.pedir("GET", "/api/ligacoes", cab={"Cookie": cookie_a, "X-Basalt-Dono": dono_a})
    st, lr, _ = s.pedir("GET", "/api/ligacoes", cab={"Cookie": cookie_r, "X-Basalt-Dono": dono_r})
    ids_a = {x["id"] for x in la["ligacoes"]}
    ids_r = {x["id"] for x in lr["ligacoes"]}
    assert len(ids_a) == 1 and len(ids_r) == 1 and not ids_a & ids_r
    st, r, _ = s.pedir("POST", "/api/ligacoes/revogar", {"id": next(iter(ids_a))},
                       {"Cookie": cookie_r, "X-Basalt-Dono": dono_r, "X-Basalt": "1", "Origin": s.origem})
    assert st == 404
    assert mcp(s, ta["access_token"])[0] == 200
    # o dono da página conta: com o dono do Rui no pedido da Ana → recusado
    assert s.pedir("GET", "/api/ligacoes", cab={"Cookie": cookie_a, "X-Basalt-Dono": dono_r})[0] == 403


# ───────────────────────── 11. revogação ─────────────────────────
def test_revogacao(srv):
    s = srv()
    cid, t, cookie = tokens(s)
    dono = s.dono(cookie)
    esc = {"Cookie": cookie, "X-Basalt-Dono": dono, "X-Basalt": "1", "Origin": s.origem}
    st, l, _ = s.pedir("GET", "/api/ligacoes", cab={"Cookie": cookie, "X-Basalt-Dono": dono})
    assert st == 200 and l["oauth"] is True and l["mcp"] == s.url + "/mcp" and len(l["ligacoes"]) == 1
    lig = l["ligacoes"][0]
    assert lig["tipo"] == "oauth" and lig["nome"] == "Claude" and lig["onde"] == "claude.ai"
    # Revogar: o acesso e a renovação caem já
    assert s.pedir("POST", "/api/ligacoes/revogar", {"id": lig["id"]}, esc)[0] == 200
    assert mcp(s, t["access_token"])[0] == 401 and renovar(s, cid, t["refresh_token"])[0] == 400
    # Revogar todas (inclui tokens pessoais)
    _, t1, _ = tokens(s)
    st, p, _ = s.pedir("POST", "/api/ligacoes/token", {"nome": "Codex"}, esc)
    assert st == 201 and p["token"].startswith("bslt_")
    assert mcp(s, p["token"])[0] == 200
    st, r, _ = s.pedir("POST", "/api/ligacoes/revogar", {"todas": True}, esc)
    assert st == 200 and r["revogadas"] == 2
    assert mcp(s, t1["access_token"])[0] == 401 and mcp(s, p["token"])[0] == 401
    # mudar a senha desliga tudo o que a conta tinha ligado (também a renovação e os tokens pessoais)
    cid, t, cookie = tokens(s)
    esc = {"Cookie": cookie, "X-Basalt-Dono": s.dono(cookie), "X-Basalt": "1", "Origin": s.origem}
    p = s.pedir("POST", "/api/ligacoes/token", {"nome": "Codex"}, esc)[1]["token"]
    import os
    import time
    s.escrever([{"email": ANA, "espaco": "ana", "admin": True, "senha_hash": auth.hash_senha("outra senha comprida")},
                {"email": RUI, "espaco": "rui", "senha_hash": HASH}])
    os.utime(s.toml, (time.time() + 5, time.time() + 5))
    assert mcp(s, t["access_token"])[0] == 401 and mcp(s, p)[0] == 401
    assert renovar(s, cid, t["refresh_token"])[0] == 400
    # a conta apagada também
    s.escrever([{"email": RUI, "espaco": "rui", "senha_hash": HASH}])
    os.utime(s.toml, (time.time() + 10, time.time() + 10))
    cid_r, tr, _ = tokens(s, RUI)
    assert mcp(s, tr["access_token"])[0] == 200
    s.escrever([{"email": ANA, "espaco": "ana", "senha_hash": HASH}])
    os.utime(s.toml, (time.time() + 15, time.time() + 15))
    assert mcp(s, tr["access_token"])[0] == 401 and renovar(s, cid_r, tr["refresh_token"])[0] == 400


def test_toml_estragado_nao_gasta(srv):
    """com o basalt.toml estragado ninguém entra — mas a renovação não se gasta nem se revoga: volta a valer quando o corrigirem."""
    import os
    import time
    s = srv()
    cid, t, _ = tokens(s)
    bom = s.toml.read_text()
    s.toml.write_text("isto [não é toml")
    os.utime(s.toml, (time.time() + 5, time.time() + 5))
    assert mcp(s, t["access_token"])[0] == 401
    assert renovar(s, cid, t["refresh_token"])[0] == 400
    s.toml.write_text(bom)
    os.utime(s.toml, (time.time() + 10, time.time() + 10))
    assert renovar(s, cid, t["refresh_token"])[0] == 200


# ───────────────────────── 12. os tokens daqui só valem no /mcp ─────────────────────────
def test_bsla_fora_do_mcp(srv):
    admin, h_admin = auth.gerar_token()
    s = srv(utilizadores=[{"email": ANA, "espaco": "ana", "admin": True, "senha_hash": HASH, "mcp_token_hash": h_admin},
                          {"email": RUI, "espaco": "rui", "senha_hash": HASH}])
    # o token do basalt.toml (o de antes) continua a valer no /mcp e na API, como sempre
    assert mcp(s, admin)[0] == 200
    assert s.pedir("GET", "/api/notas", cab={"Authorization": "Bearer " + admin})[0] == 200
    cid, t, cookie = tokens(s)
    esc = {"Cookie": cookie, "X-Basalt-Dono": s.dono(cookie), "X-Basalt": "1", "Origin": s.origem}
    pessoal = s.pedir("POST", "/api/ligacoes/token", {"nome": "Codex"}, esc)[1]["token"]
    for tok in (t["access_token"], pessoal, t["refresh_token"]):
        for caminho in ("/api/notas", "/api/nota?nome=barcos", "/api/eu"):
            st, _, _ = s.pedir("GET", caminho, cab={"Authorization": "Bearer " + tok})
            assert st == 401, (tok[:5], caminho, st)
        st, _, _ = s.pedir("PUT", "/api/nota", {"nome": "x", "texto": "y"}, {"Authorization": "Bearer " + tok, "X-Basalt": "1"})
        assert st == 401
    assert mcp(s, t["refresh_token"])[0] == 401                     # a renovação não é um token de acesso
    # um token não cria tokens nem revoga ligações (nem com a sessão ao lado)
    for caminho, corpo in (("/api/ligacoes/token", {"nome": "x"}), ("/api/ligacoes/revogar", {"todas": True})):
        st, _, _ = s.pedir("POST", caminho, corpo, {**esc, "Authorization": "Bearer " + t["access_token"]})
        assert st == 403
    assert s.pedir("GET", "/api/ligacoes", cab={"Authorization": "Bearer " + pessoal})[0] == 403
    assert mcp(s, t["access_token"])[0] == 200 and mcp(s, pessoal)[0] == 200


# ───────────────────────── 13. a página de consentimento ─────────────────────────
def test_csp_consentimento(srv):
    s = srv()
    st, cli, _ = registar(s, nome="Agenda​ da Ana")
    st, pag, _ = pedir_autorizacao(s, cli["client_id"], pkce()[1])
    cookie = s.entrar()
    st, pag, cab = s.pedir("GET", f"/oauth/authorize?pedido={pid_de(pag)}", cab={"Cookie": cookie})
    assert st == 200
    csp = cab["content-security-policy"]
    for d in ("default-src 'none'", "frame-ancestors 'none'", "base-uri 'none'", "form-action 'self' https://claude.ai"):
        assert d in csp, csp
    assert "script-src" not in csp and "<script" not in pag
    assert cab["x-frame-options"] == "DENY" and "no-store" in cab["cache-control"] and cab["x-content-type-options"] == "nosniff"
    assert cab["referrer-policy"] == "same-origin"
    assert "[U+200B]" in pag and "​" not in pag                    # o que se esconde fica à vista
    assert ANA in pag and "claude.ai" in pag
    # um nome que se faz passar pelo Claude e volta para outro sítio → recusado antes de mostrar o formulário
    s2 = srv(extra='[oauth]\nredirects_extra = ["https://app.exemplo.pt/cb"]\n')
    st, cli, _ = registar(s2, ["https://app.exemplo.pt/cb"], nome="Claude Desktop")
    st, pag, _ = pedir_autorizacao(s2, cli["client_id"], pkce()[1], redirect="https://app.exemplo.pt/cb")
    st, pag2, _ = s2.pedir("GET", f"/oauth/authorize?pedido={pid_de(pag)}", cab={"Cookie": s2.entrar()})
    assert st == 403 and "<form" not in pag2
    # o mesmo nome, a voltar para o claude.ai, passa
    st, cli, _ = registar(s2, nome="Claude Desktop")
    st, pag, _ = pedir_autorizacao(s2, cli["client_id"], pkce()[1])
    assert s2.pedir("GET", f"/oauth/authorize?pedido={pid_de(pag)}", cab={"Cookie": s2.entrar()})[0] == 200


def test_modo_cabecalho(srv):
    """atrás de um proxy que identifica a conta: sem o cabeçalho → 403 (não há /entrar), com ele → consentimento."""
    s = srv(modo="cabecalho", utilizadores=[{"email": ANA, "espaco": "ana", "admin": True}])
    st, cli, _ = registar(s)
    v, c = pkce()
    st, pag, _ = pedir_autorizacao(s, cli["client_id"], c)
    pid = pid_de(pag)
    assert s.pedir("GET", f"/oauth/authorize?pedido={pid}")[0] == 403
    cab_p = {"X-Basalt-Email": ANA}
    st, pag, _ = s.pedir("GET", f"/oauth/authorize?pedido={pid}", cab=cab_p)
    assert st == 200 and "value=sair" not in pag
    f = {**campos(pag), "decisao": "permitir"}
    st, _, cab = s.pedir("POST", "/oauth/autorizar", form=f, cab={**cab_p, "Origin": s.origem})
    assert st == 303
    st, t, _ = trocar(s, cli["client_id"], v, codigo_de(cab["location"])["code"])
    assert st == 200 and ler(s, t["access_token"], "barcos")[0] == 200


def test_outra_conta(srv):
    """«Não és tu?»: termina a sessão, solta o pedido e manda entrar outra vez; quem entrar a seguir fica com ele."""
    s = srv()
    st, cli, _ = registar(s)
    st, pag, _ = pedir_autorizacao(s, cli["client_id"], pkce()[1])
    pid = pid_de(pag)
    cookie_a = s.entrar(ANA)
    st, _, cab = consentir(s, cookie_a, pid, "sair")
    assert st == 303 and cab["location"].startswith("/entrar?volta=") and "Max-Age=0" in cab["set-cookie"]
    assert s.pedir("GET", "/api/eu", cab={"Cookie": cookie_a})[0] == 401
    cookie_r = s.entrar(RUI)
    st, _, cab = consentir(s, cookie_r, pid)
    assert st == 303 and "code=" in cab["location"]


# ───────────────────────── 14. modo aberto: sem OAuth ─────────────────────────
def test_modo_aberto_sem_oauth(srv):
    s = srv(modo="aberto", utilizadores=[{"email": ANA, "espaco": "ana", "admin": True}])
    assert s.cfg.oauth_activo is False
    for m, c in (("GET", "/.well-known/oauth-protected-resource"), ("GET", "/.well-known/oauth-authorization-server"),
                 ("GET", "/oauth/authorize?client_id=x"), ("POST", "/oauth/token"), ("POST", "/oauth/autorizar")):
        assert s.pedir(m, c)[0] == 404, c
    assert registar(s)[0] == 404
    st, _, cab = s.pedir("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert st == 401 and "resource_metadata" not in cab.get("www-authenticate", "")
    # sem OAuth o Origin do claude.ai não passa no /mcp
    assert s.pedir("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, {"Origin": "https://claude.ai"})[0] == 403
    # os tokens pessoais funcionam (o Codex/Claude Code no próprio computador)
    st, d, _ = s.pedir("GET", "/api/eu")
    esc = {"X-Basalt-Dono": d["dono"], "X-Basalt": "1", "Origin": s.origem}
    st, l, _ = s.pedir("GET", "/api/ligacoes", cab={"X-Basalt-Dono": d["dono"]})
    assert st == 200 and l["oauth"] is False and l["motivo"] == "aberto"
    st, p, _ = s.pedir("POST", "/api/ligacoes/token", {"nome": "Codex"}, esc)
    assert st == 201 and ler(s, p["token"], "barcos")[0] == 200


def test_sem_url_publico(srv):
    s = srv(url=False)
    assert s.cfg.oauth_activo is False and s.pedir("GET", "/.well-known/oauth-authorization-server")[0] == 404
    cookie = s.entrar()
    st, l, _ = s.pedir("GET", "/api/ligacoes", cab={"Cookie": cookie, "X-Basalt-Dono": s.dono(cookie)})
    assert st == 200 and l["oauth"] is False and l["motivo"] == "sem_url_publico"


def test_config_url_publico(tmp_path):
    def cfg(url, anf=None, extra=""):
        t = tmp_path / "b.toml"
        a = f"anfitrioes = {json.dumps(anf)}\n" if anf is not None else ""
        t.write_text(f'[basalt]\ndados = "{tmp_path}/d"\nurl_publico = "{url}"\n{a}[auth]\nmodo = "cabecalho"\n'
                     f'[[utilizadores]]\nemail = "{ANA}"\n{extra}', encoding="utf-8")
        return config.carregar(t, ambiente={})
    c = cfg("https://notas.exemplo.pt/", ["notas.exemplo.pt"])
    assert c.url_publico == "https://notas.exemplo.pt" and c.recurso == "https://notas.exemplo.pt/mcp" and c.oauth_activo
    for mau, anf in (("http://notas.exemplo.pt", ["notas.exemplo.pt"]),        # http fora de loopback
                     ("https://notas.exemplo.pt/basalt", ["notas.exemplo.pt"]),  # com caminho
                     ("https://u:p@notas.exemplo.pt", ["notas.exemplo.pt"]),
                     ("https://notas.exemplo.pt?x=1", ["notas.exemplo.pt"]),
                     ("https://notas.exemplo.pt", None)):                       # o nome não está em anfitrioes
        with pytest.raises(config.ConfigInvalida):
            cfg(mau, anf)
    with pytest.raises(config.ConfigInvalida):
        cfg("https://notas.exemplo.pt", ["notas.exemplo.pt"], '[oauth]\nredirects_extra = ["http://app.exemplo.pt/cb"]\n')
    assert cfg("http://127.0.0.1:8080").oauth_activo


def test_limitador_token(srv):
    s = srv()
    estados = [s.pedir("POST", "/oauth/token", form={"grant_type": "authorization_code", "code": "nada", "client_id": "bslc_x"})[0]
               for _ in range(31)]
    assert estados[:30] == [400] * 30 and estados[30] == 429


def test_tokens_pessoais_limite_e_nome(srv):
    s = srv()
    cookie = s.entrar()
    esc = {"Cookie": cookie, "X-Basalt-Dono": s.dono(cookie), "X-Basalt": "1", "Origin": s.origem}
    for i in range(20):
        assert s.pedir("POST", "/api/ligacoes/token", {"nome": f"t{i}"}, esc)[0] == 201
    assert s.pedir("POST", "/api/ligacoes/token", {"nome": "mais um"}, esc)[0] == 429
    assert s.pedir("POST", "/api/ligacoes/token", {"nome": "x"}, {**esc, "X-Basalt": ""})[0] == 403      # escrita sem X-Basalt


def test_registos_sem_tokens(srv, capfd):
    s = srv()
    cid, t, _ = tokens(s)
    renovar(s, cid, t["refresh_token"])
    out, err = capfd.readouterr()
    tudo = out + err
    for x in (t["access_token"], t["refresh_token"], SENHA):
        assert x not in tudo
    assert "code=" not in tudo and ANA not in tudo
