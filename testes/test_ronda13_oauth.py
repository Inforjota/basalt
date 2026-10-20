"""Ronda 13 — «Traz a tua IA» (basalt/oauth.py): o que a revisão externa encontrou no servidor OAuth.

  1. (grave) um token antigo voltava a valer quando a conta reaparecia (apagada e recriada igual, ou o servidor arrancado com ela
     apagada): a família só se RECUSAVA, nunca se revogava na base. Agora fica revogada (OAuth e tokens pessoais);
  2. (grave) um endereço retirado de `[oauth] redirects_extra` ainda recebia o código de um pedido pendente (e trocava-o): agora
     confere-se a lista actual antes de mostrar o consentimento, antes de emitir o código e na troca;
  3. (grave) cada renovação deixava mais dois registos para sempre: uma conta enchia a base partilhada e as outras ficavam com 503.
     Agora cada ligação guarda poucos tokens, renova no máximo 60 vezes por hora, cada conta tem um tecto de ligações e de
     ligações revogadas guardadas, e a poda corre também no /oauth/token;
  4. (menor) depois de mudar a senha, os tokens pessoais que já não valiam continuavam a contar para o limite de 20;
  7. (menor) os servidores dos testes escolhiam uma porta «livre» antes do bind — outro processo podia ficar com ela entretanto;
  8. (menor) com o basalt.toml estragado a renovação respondia `invalid_grant` (definitivo para o cliente): agora 503
     `temporarily_unavailable`, e o mesmo token volta a valer depois de corrigido.

Cada teste de comportamento falha no código anterior (fd9adb7) por asserção; os controlos (marcados como tal) passam nos dois.
"""
import os
import socket
import sqlite3
import time

import pytest

import conftest
import test_oauth
from basalt import auth, oauth
from test_oauth import (ANA, HASH, REDIR, RUI, SENHA, Srv, _relogio, campos, codigo_de, consentir, fluxo, ler, mcp,  # noqa: F401
                        pedir_autorizacao, pid_de, pkce, registar, renovar, srv, tokens, trocar)

EXTRA = "https://app.exemplo.pt/volta"
CAB_ANA = {"X-Basalt-Email": ANA}
SEM_SENHA = [{"email": ANA, "espaco": "ana", "admin": True}, {"email": RUI, "espaco": "rui"}]


def _mudar(s, utilizadores, passo=[0]):
    """reescreve o basalt.toml (com outra data, para o servidor o reler no pedido seguinte)."""
    passo[0] += 5
    s.escrever(utilizadores)
    os.utime(s.toml, (time.time() + passo[0], time.time() + passo[0]))


def _ligar_cabecalho(s, email=ANA):
    """uma ligação OAuth no modo cabecalho (o proxy diz quem é). Devolve (client_id, tokens)."""
    st, cli, _ = registar(s)
    assert st == 201, cli
    v, c = pkce()
    st, pag, _ = pedir_autorizacao(s, cli["client_id"], c)
    cab = {"X-Basalt-Email": email}
    st, pag, _ = s.pedir("GET", f"/oauth/authorize?pedido={pid_de(pag)}", cab=cab)
    assert st == 200, pag
    st, _, r = s.pedir("POST", "/oauth/autorizar", form={**campos(pag), "decisao": "permitir"}, cab={**cab, "Origin": s.origem})
    assert st == 303, r
    st, t, _ = trocar(s, cli["client_id"], v, codigo_de(r["location"])["code"])
    assert st == 200, t
    return cli["client_id"], t


def _pessoal(s, cab):
    st, d, _ = s.pedir("GET", "/api/eu", cab=cab)
    assert st == 200, d
    esc = {**cab, "X-Basalt-Dono": d["dono"], "X-Basalt": "1", "Origin": s.origem}
    return s.pedir("POST", "/api/ligacoes/token", {"nome": "Agente"}, esc)


def _oauth_de(s):
    return s.srv.RequestHandlerClass.oauth


# ───────────────────────── 1. a conta que reaparece não ressuscita tokens ─────────────────────────
def test_conta_recriada_nao_ressuscita_tokens(srv):
    """modo cabecalho (sem senha: a «impressão» da conta é vazia, igual antes e depois): apagar a conta e recriá-la igual
    deixava o acesso, a renovação e o token pessoal antigos a valer outra vez, sem novo consentimento."""
    s = srv(modo="cabecalho", utilizadores=SEM_SENHA)
    cid, t = _ligar_cabecalho(s)
    st, p, _ = _pessoal(s, CAB_ANA)
    assert st == 201, p
    assert mcp(s, t["access_token"])[0] == 200 and mcp(s, p["token"])[0] == 200
    _mudar(s, [{"email": RUI, "espaco": "rui"}])                                   # a conta sai…
    assert mcp(s, t["access_token"])[0] == 401 and mcp(s, p["token"])[0] == 401
    _mudar(s, SEM_SENHA)                                                            # …e volta igual
    assert mcp(s, t["access_token"])[0] == 401, "o acesso antigo voltou a valer com a conta recriada"
    assert mcp(s, p["token"])[0] == 401, "o token pessoal antigo voltou a valer com a conta recriada"
    assert renovar(s, cid, t["refresh_token"])[0] == 400, "a renovação antiga voltou a valer com a conta recriada"


def test_conta_recriada_mesmo_sem_uso_entretanto(srv):
    """a conta sai e volta sem que ninguém use os tokens pelo meio (só um pedido qualquer que releia o basalt.toml)."""
    s = srv(modo="cabecalho", utilizadores=SEM_SENHA)
    cid, t = _ligar_cabecalho(s)
    _mudar(s, [{"email": RUI, "espaco": "rui"}])
    assert s.pedir("GET", "/api/eu", cab={"X-Basalt-Email": RUI})[0] == 200        # relê o basalt.toml; ninguém toca nos tokens
    _mudar(s, SEM_SENHA)
    assert mcp(s, t["access_token"])[0] == 401
    assert renovar(s, cid, t["refresh_token"])[0] == 400


def test_arranque_com_a_conta_apagada_revoga(tmp_path):
    """o servidor arranca com a conta apagada (mudou com ele parado) e depois volta a arrancar com ela recriada igual."""
    a = Srv(tmp_path, modo="cabecalho", utilizadores=SEM_SENHA)
    try:
        cid, t = _ligar_cabecalho(a)
    finally:
        a.parar()
    b = Srv(tmp_path, modo="cabecalho", utilizadores=[{"email": RUI, "espaco": "rui"}])
    b.url = b.cfg.url_publico = a.url                     # o mesmo endereço público (só a porta local muda)
    try:
        assert mcp(b, t["access_token"])[0] == 401
    finally:
        b.parar()
    c = Srv(tmp_path, modo="cabecalho", utilizadores=SEM_SENHA)
    c.url = c.cfg.url_publico = a.url
    try:
        assert mcp(c, t["access_token"])[0] == 401, "o acesso antigo voltou a valer depois de dois arranques"
        assert renovar(c, cid, t["refresh_token"])[0] == 400
    finally:
        c.parar()


def test_controlo_conta_recriada_liga_de_novo(srv):
    """controlo: a conta recriada pode ligar outra vez (com novo consentimento) e o resto das contas não é tocado."""
    s = srv(modo="cabecalho", utilizadores=SEM_SENHA)
    _, tr = _ligar_cabecalho(s, RUI)
    _ligar_cabecalho(s)
    _mudar(s, [{"email": RUI, "espaco": "rui"}])
    assert s.pedir("GET", "/api/eu", cab={"X-Basalt-Email": RUI})[0] == 200
    _mudar(s, SEM_SENHA)
    _, t2 = _ligar_cabecalho(s)
    assert mcp(s, t2["access_token"])[0] == 200 and mcp(s, tr["access_token"])[0] == 200


# ───────────────────────── 2. endereço retirado de redirects_extra ─────────────────────────
def test_redirect_retirado_nao_recebe_codigo(tmp_path):
    """registar com um endereço de `redirects_extra`, abrir o pedido, e reiniciar SEM esse endereço (mesma base): o pedido
    pendente já não mostra o consentimento nem dá código para lá; um código já emitido para lá também não se troca."""
    com = f'[oauth]\nredirects_extra = ["{EXTRA}"]\n'
    a = Srv(tmp_path, extra=com)
    try:
        st, cli, _ = registar(a, (EXTRA,), nome="App")
        assert st == 201, cli
        # um pedido pendente, já aberto (formulário na mão)
        v, c = pkce()
        st, pag, _ = pedir_autorizacao(a, cli["client_id"], c, EXTRA)
        pid = pid_de(pag)
        cookie = a.entrar()
        st, pag, _ = a.pedir("GET", f"/oauth/authorize?pedido={pid}", cab={"Cookie": cookie})
        assert st == 200 and "<form" in pag
        form = campos(pag)
        # e um código já emitido para lá, ainda por trocar
        v2, c2 = pkce()
        st, pag2, _ = pedir_autorizacao(a, cli["client_id"], c2, EXTRA)
        st, _, cab = consentir(a, cookie, pid_de(pag2))
        assert st == 303 and cab["location"].startswith(EXTRA)
        codigo = codigo_de(cab["location"])["code"]
    finally:
        a.parar()
    b = Srv(tmp_path, extra="")
    b.url = b.cfg.url_publico = a.url                     # o mesmo endereço público de antes (só a porta local é outra)
    try:
        st, pag, _ = b.pedir("GET", f"/oauth/authorize?pedido={pid}", cab={"Cookie": cookie})
        assert st == 400 and "<form" not in pag, (st, pag[:200])
        st, _, cab = b.pedir("POST", "/oauth/autorizar", form={**form, "decisao": "permitir"},
                             cab={"Cookie": cookie, "Origin": b.origem, "Sec-Fetch-Site": "same-origin"})
        assert st != 303 and not cab.get("location", "").startswith(EXTRA), (st, cab.get("location"))
        st, r, _ = trocar(b, cli["client_id"], v2, codigo, EXTRA)
        assert st == 400 and r["error"] == "invalid_grant", (st, r)
    finally:
        b.parar()


def test_controlo_redirect_retirado_pedido_novo(tmp_path):
    """controlo: depois de retirado, um pedido NOVO para esse endereço já era recusado; os endereços de origem continuam."""
    a = Srv(tmp_path, extra=f'[oauth]\nredirects_extra = ["{EXTRA}"]\n')
    try:
        st, cli, _ = registar(a, (EXTRA,), nome="App")
        assert st == 201
    finally:
        a.parar()
    b = Srv(tmp_path, extra="")
    try:
        assert pedir_autorizacao(b, cli["client_id"], pkce()[1], EXTRA)[0] == 400
        _, t, _ = tokens(b)
        assert mcp(b, t["access_token"])[0] == 200
    finally:
        b.parar()


# ───────────────────────── 3. uma conta não enche a base das outras ─────────────────────────
def test_renovacoes_nao_esgotam_a_base(srv, monkeypatch):
    """base pequena: a Ana renova 400 vezes (a um ritmo «legal», mais de 60 s entre cada) e o Rui tem de continuar a renovar
    e a ler. Antes cada renovação deixava dois registos e a base enchia (503 para todos)."""
    monkeypatch.setattr(oauth, "PAGINAS_MAX", 40)                                  # 160 KiB
    s = srv()
    cid_r, tr, _ = tokens(s, RUI)
    cid, t, _ = tokens(s)
    base = oauth.RELOGIO()
    ref = t["refresh_token"]
    for i in range(400):
        oauth.RELOGIO = lambda i=i: base + 70 * (i + 1)
        st, r, _ = renovar(s, cid, ref)
        assert st == 200, f"renovação {i + 1} da Ana: {st} {r}"
        ref = r["refresh_token"]
    st, r, _ = renovar(s, cid_r, tr["refresh_token"])
    assert st == 200, f"o Rui ficou sem renovar: {st} {r}"
    assert ler(s, r["access_token"], "segredo-do-rui")[0] == 200


def test_poda_por_ligacao(srv):
    """cada ligação guarda poucos tokens: os 2 acessos mais recentes, a renovação viva e as 5 últimas usadas."""
    s = srv()
    cid, t, _ = tokens(s)
    base = oauth.RELOGIO()
    ref = t["refresh_token"]
    for i in range(20):
        oauth.RELOGIO = lambda i=i: base + 70 * (i + 1)
        st, r, _ = renovar(s, cid, ref)
        assert st == 200, r
        ref = r["refresh_token"]
    with sqlite3.connect(str(s.dados / "oauth.sqlite")) as c:
        n = dict(c.execute("SELECT tipo, COUNT(*) FROM tokens GROUP BY tipo").fetchall())
    assert n.get("acesso", 0) <= 2 and n.get("renovar", 0) <= 6, n
    assert mcp(s, r["access_token"])[0] == 200


def test_renovacoes_por_hora(srv):
    """no máximo 60 renovações por ligação por hora: a 61.ª dá 429 sem gastar o token nem contar como tentativa falhada,
    e o mesmo token renova depois de passar a hora."""
    s = srv()
    cid, t, _ = tokens(s)
    base = oauth.RELOGIO()
    ref = t["refresh_token"]
    for i in range(60):
        oauth.RELOGIO = lambda i=i: base + i
        st, r, _ = renovar(s, cid, ref)
        assert st == 200, (i, r)
        ref = r["refresh_token"]
    oauth.RELOGIO = lambda: base + 61
    st, r, _ = renovar(s, cid, ref)
    assert st == 429 and r["error"] == "temporarily_unavailable", (st, r)
    assert not _oauth_de(s)._falhas, "a renovação adiada contou como tentativa falhada"
    oauth.RELOGIO = lambda: base + 3700
    st, r, _ = renovar(s, cid, ref)
    assert st == 200, r
    assert mcp(s, r["access_token"])[0] == 200


def test_tecto_de_ligacoes_por_conta(srv, monkeypatch):
    """cada «Permitir» abria mais uma família, sem limite. Agora há um tecto por conta (o pedido não se gasta), e as
    famílias cujo código nunca foi trocado deixam de contar quando ele expira."""
    monkeypatch.setattr(oauth, "OAUTH_POR_CONTA", 3, raising=False)
    s = srv()
    cookie = s.entrar()
    for _ in range(3):
        fluxo(s, cookie=cookie)                                                     # três códigos, nenhum trocado
    st, cli, _ = registar(s)
    st, pag, _ = pedir_autorizacao(s, cli["client_id"], pkce()[1])
    pid = pid_de(pag)
    st, pag, cab = consentir(s, cookie, pid)
    assert st == 429 and "location" not in cab, (st, cab.get("location"))
    # o Rui não é afectado pelo tecto da Ana
    fluxo(s, RUI)
    # passados os 10 min dos códigos, essas famílias deixam de contar; o mesmo pedido (não gasto) passa
    agora = oauth.RELOGIO()
    oauth.RELOGIO = lambda: agora + oauth.T_CODIGO + 5
    st, _, cab = consentir(s, cookie, pid)
    assert st == 303 and "code=" in cab["location"], (st, cab)


def test_revogadas_por_conta_e_sem_tokens(srv, monkeypatch):
    """as ligações revogadas perdem já os tokens e cada conta guarda no máximo REVOGADAS_POR_CONTA delas (antes: 90 dias,
    sem limite)."""
    monkeypatch.setattr(oauth, "REVOGADAS_POR_CONTA", 2, raising=False)
    s = srv()
    cookie = s.entrar()
    cab = {"Cookie": cookie}
    for _ in range(4):
        assert _pessoal(s, cab)[0] == 201
    st, d, _ = s.pedir("GET", "/api/eu", cab=cab)
    esc = {**cab, "X-Basalt-Dono": d["dono"], "X-Basalt": "1", "Origin": s.origem}
    assert s.pedir("POST", "/api/ligacoes/revogar", {"todas": True}, esc)[1]["revogadas"] == 4
    assert _pessoal(s, cab)[0] == 201                                              # mais uma escrita (a poda corre)
    with sqlite3.connect(str(s.dados / "oauth.sqlite")) as c:
        rev = c.execute("SELECT COUNT(*) FROM familias WHERE revogada IS NOT NULL").fetchone()[0]
        orfaos = c.execute("SELECT COUNT(*) FROM tokens WHERE familia IN (SELECT id FROM familias WHERE revogada IS NOT NULL)").fetchone()[0]
    assert rev <= 2, f"{rev} ligações revogadas guardadas"
    assert orfaos == 0, f"{orfaos} tokens de ligações revogadas ainda na base"


def test_controlo_renovacao_normal(srv):
    """controlo: uma renovação por hora durante um dia, como um cliente normal — tudo 200 e o acesso novo serve."""
    s = srv()
    cid, t, _ = tokens(s)
    base = oauth.RELOGIO()
    ref = t["refresh_token"]
    for i in range(24):
        oauth.RELOGIO = lambda i=i: base + 3600 * (i + 1)
        st, r, _ = renovar(s, cid, ref)
        assert st == 200, r
        ref = r["refresh_token"]
    assert ler(s, r["access_token"], "barcos")[0] == 200


# ───────────────────────── 4. a quota dos tokens pessoais depois de mudar a senha ─────────────────────────
def test_pessoais_da_senha_antiga_nao_contam(srv):
    s = srv()
    cab = {"Cookie": s.entrar()}
    for _ in range(oauth.PESSOAIS_MAX):
        assert _pessoal(s, cab)[0] == 201
    assert _pessoal(s, cab)[0] == 429                                              # o limite continua a valer
    outra = "outra senha comprida"
    _mudar(s, [{"email": ANA, "espaco": "ana", "admin": True, "senha_hash": auth.hash_senha(outra)},
               {"email": RUI, "espaco": "rui", "senha_hash": HASH}])
    cab = {"Cookie": s.entrar(ANA, outra)}
    st, r, _ = _pessoal(s, cab)
    assert st == 201, f"depois de mudar a senha: {st} {r}"
    assert mcp(s, r["token"])[0] == 200


# ───────────────────────── 7. porta dos servidores de teste ─────────────────────────
@pytest.fixture
def porta_ocupada(monkeypatch):
    """uma porta já à escuta, devolvida por `porta_livre` (o que acontece quando outro processo a apanha entretanto)."""
    sk = socket.socket()
    sk.bind(("127.0.0.1", 0))
    sk.listen(1)
    p = sk.getsockname()[1]
    monkeypatch.setattr(conftest, "porta_livre", lambda: p)
    monkeypatch.setattr(test_oauth, "porta_livre", lambda: p, raising=False)
    yield p
    sk.close()


def test_porta_do_servidor_em_processo(tmp_path, porta_ocupada):
    erro = None
    try:
        s = Srv(tmp_path)
    except OSError as e:
        erro = e
    assert erro is None, f"o servidor não arrancou: {erro}"
    try:
        assert s.porta != porta_ocupada and s.url.endswith(f":{s.porta}")
        _, t, _ = tokens(s)
        assert mcp(s, t["access_token"])[0] == 200
    finally:
        s.parar()


def test_porta_do_servidor_noutro_processo(tmp_path, porta_ocupada):
    erro, s = None, None
    try:
        s = conftest.Servidor(conftest.Memoria(tmp_path))
    except RuntimeError as e:
        erro = e
    try:
        assert erro is None, f"o servidor não arrancou: {erro}"
        assert s.porta != porta_ocupada, "o servidor ficou com a porta que outro processo já tinha"
        assert s.get("/", cabecalhos={"X-Basalt-Email": conftest.EMAIL}).codigo == 200
    finally:
        if s is not None:
            s.parar()


# ───────────────────────── 8. basalt.toml estragado: indisponível, não inválido ─────────────────────────
def test_toml_estragado_renovacao_temporaria(srv):
    s = srv()
    cid, t, _ = tokens(s)
    bom = s.toml.read_text()
    s.toml.write_text("isto [não é toml")
    os.utime(s.toml, (time.time() + 5, time.time() + 5))
    st, r, _ = renovar(s, cid, t["refresh_token"])
    assert st == 503 and r["error"] == "temporarily_unavailable", (st, r)
    s.toml.write_text(bom)
    os.utime(s.toml, (time.time() + 10, time.time() + 10))
    st, r, _ = renovar(s, cid, t["refresh_token"])
    assert st == 200, r
    assert ler(s, r["access_token"], "barcos")[0] == 200
