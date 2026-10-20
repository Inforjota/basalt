"""Ronda 13b — «Traz a tua IA» (basalt/oauth.py): o que a revisão externa r13 ainda encontrou no servidor OAuth.

  1. (grave) a revogação das ligações de uma conta que mudou ficava só em memória até ao próximo uso da base: mudar a senha,
     reiniciar e repor o basalt.toml antigo fazia voltar a valer o acesso, a renovação e o token pessoal antigos. Agora grava-se
     já na releitura (o «Mudar a senha» só diz que correu bem com ela gravada) e a CLI (`utilizador senha`/`novo`) revoga na
     base ANTES de mudar o basalt.toml;
  2. (grave) um pedido já autenticado criava a ligação (token pessoal, ou o «Permitir» do OAuth) com a conta do início, mesmo que
     ela tivesse sido apagada a meio — e, recriada igual, o token passava a valer. Agora a criação confere a conta dentro da
     transacção que insere.

O reinício é um servidor NOVO sobre a mesma pasta (nada do que estava em memória passa). Cada teste de comportamento falha no
código anterior (3c47c86) por asserção; os controlos (marcados como tal) passam nos dois.
"""
import sqlite3
import threading
import urllib.parse

import pytest

from basalt import __main__ as cli, auth, config, oauth
from test_mudar_senha import NOVA, _esc
from test_mudar_senha import _mudar as _mudar_senha
from test_oauth import (ANA, RUI, SENHA, Srv, _relogio, campos, codigo_de, mcp, pedir_autorizacao, pid_de, pkce,  # noqa: F401
                        registar, renovar, srv, tokens, trocar)
from test_ronda13_oauth import CAB_ANA, SEM_SENHA, _ligar_cabecalho, _mudar, _oauth_de, _pessoal

SO_RUI = [{"email": RUI, "espaco": "rui"}]


def _pessoal_senha(s, cookie):
    st, p, _ = s.pedir("POST", "/api/ligacoes/token", {"nome": "Codex"}, _esc(s, cookie))
    assert st == 201, p
    return p["token"]


def _reiniciar(tmp_path, a, **k):
    """o servidor volta a arrancar sobre a mesma pasta — com o basalt.toml que o Srv escreve (o de antes: a cópia reposta)."""
    b = Srv(tmp_path, **k)
    b.url = b.cfg.url_publico = a.url                     # o mesmo endereço público (só a porta local muda)
    return b


def _vivas(s, email, tipo=None):
    c = sqlite3.connect(str(s.dados / "oauth.sqlite"))
    try:
        q = "SELECT COUNT(*) FROM familias WHERE email=? AND revogada IS NULL" + (" AND tipo=?" if tipo else "")
        return c.execute(q, (email, tipo) if tipo else (email,)).fetchone()[0]
    finally:
        c.close()


def _prender(s):
    """outro processo (uma cópia de segurança, p. ex.) segura a base para escrita."""
    c = sqlite3.connect(str(s.dados / "oauth.sqlite"), isolation_level=None)
    c.execute("BEGIN IMMEDIATE")
    return c


def _soltar(c):
    c.execute("ROLLBACK")
    c.close()


# ───────────────────────── 1. a revogação fica gravada antes de se dizer que correu bem ─────────────────────────
def test_mudar_senha_reiniciar_e_repor_o_toml_antigo(tmp_path):
    """mudar a senha (200) e o servidor reinicia logo a seguir, sem mais nenhum pedido; depois alguém repõe o basalt.toml de
    ontem. A revogação vivia só em memória: o acesso, a renovação e o token pessoal antigos voltavam a valer."""
    a = Srv(tmp_path)
    try:
        cid, t, cookie = tokens(a)
        pessoal = _pessoal_senha(a, cookie)
        st, r, _ = _mudar_senha(a, cookie)
        assert st == 200, r
    finally:
        a.parar()                                         # nenhum pedido depois do «Mudar a senha»
    b = _reiniciar(tmp_path, a)
    try:
        assert b.entrar()                                 # a senha antiga vale outra vez (o toml reposto)…
        assert mcp(b, t["access_token"])[0] == 401, "o acesso antigo voltou a valer depois do reinício com o toml reposto"
        assert renovar(b, cid, t["refresh_token"])[0] == 400, "a renovação antiga voltou a valer depois do reinício"
        assert mcp(b, pessoal)[0] == 401, "o token pessoal antigo voltou a valer depois do reinício"
    finally:
        b.parar()


def test_mudar_senha_com_a_base_presa_nao_diz_que_correu_bem(tmp_path, monkeypatch):
    """a base está presa por outro processo quando a senha muda: a revogação não se grava — e então o «Mudar a senha» não diz
    que correu bem, nenhum token passa até ela se gravar, e depois de gravada não volta atrás num reinício com o toml antigo."""
    monkeypatch.setattr(oauth, "ESPERA_S", 1)
    a = Srv(tmp_path)
    try:
        cid, t, cookie = tokens(a)
        pessoal = _pessoal_senha(a, cookie)
        esc = _esc(a, cookie)
        preso = _prender(a)
        try:
            st, r, cab = a.pedir("POST", "/api/senha", {"atual": SENHA, "nova": NOVA}, esc)
            assert st != 200 and "set-cookie" not in cab, ("disse que correu bem sem a revogação gravada", st, r)
            assert "gravado" in r["erro"], r
            assert mcp(a, t["access_token"])[0] == 401 and mcp(a, pessoal)[0] == 401     # pendente: nada passa
        finally:
            _soltar(preso)
        assert mcp(a, t["access_token"])[0] == 401        # o próximo uso da base grava-a
        assert _vivas(a, ANA) == 0
        assert a.entrar(senha=NOVA)                       # a senha nova ficou (a mensagem manda entrar com ela)
    finally:
        a.parar()
    b = _reiniciar(tmp_path, a)
    try:
        assert mcp(b, t["access_token"])[0] == 401 and mcp(b, pessoal)[0] == 401
        assert renovar(b, cid, t["refresh_token"])[0] == 400
    finally:
        b.parar()


def test_cli_senha_revoga_na_base_antes_de_mudar_o_toml(tmp_path, monkeypatch):
    """`utilizador senha` com o servidor parado; ele volta a arrancar com o basalt.toml de antes reposto. A CLI só mudava o toml
    (a revogação ficava para quando o servidor o relesse — e com o toml antigo de volta nunca acontecia)."""
    monkeypatch.delenv("BASALT_DADOS", raising=False)
    a = Srv(tmp_path)
    try:
        cid, t, cookie = tokens(a)
        pessoal = _pessoal_senha(a, cookie)
    finally:
        a.parar()
    cli.main(["--config", str(a.toml), "utilizador", "senha", ANA, "--senha", NOVA])
    assert auth.verificar_senha(config.carregar(a.toml, ambiente={}).utilizador(ANA).senha_hash, NOVA)
    b = _reiniciar(tmp_path, a)
    try:
        assert mcp(b, t["access_token"])[0] == 401, "o acesso antigo voltou a valer depois do `utilizador senha` e do toml reposto"
        assert renovar(b, cid, t["refresh_token"])[0] == 400
        assert mcp(b, pessoal)[0] == 401
    finally:
        b.parar()


def test_cli_senha_com_a_base_presa_nao_mexe_no_toml(tmp_path, monkeypatch):
    """se a revogação não se grava, a senha não muda (o comando falha e diz porquê) — nunca o contrário."""
    monkeypatch.delenv("BASALT_DADOS", raising=False)
    monkeypatch.setattr(oauth, "ESPERA_S", 1)
    a = Srv(tmp_path)
    try:
        cid, t, cookie = tokens(a)
    finally:
        a.parar()
    antes = a.toml.read_bytes()
    preso = _prender(a)
    try:
        with pytest.raises(SystemExit) as e:
            cli.main(["--config", str(a.toml), "utilizador", "senha", ANA, "--senha", NOVA])
    finally:
        _soltar(preso)
    assert "não mexi no basalt.toml" in str(e.value)
    assert a.toml.read_bytes() == antes


def test_cli_novo_com_o_email_de_uma_conta_apagada_revoga(tmp_path, monkeypatch):
    """modo cabecalho (a «impressão» da conta é vazia): a conta sai do basalt.toml com o servidor parado e volta com `utilizador
    novo`, igual. No arranque seguinte a revisão total via-a viva — as ligações antigas voltavam a valer sem novo consentimento."""
    monkeypatch.delenv("BASALT_DADOS", raising=False)
    a = Srv(tmp_path, modo="cabecalho", utilizadores=SEM_SENHA)
    try:
        cid, t = _ligar_cabecalho(a)
        st, p, _ = _pessoal(a, CAB_ANA)
        assert st == 201, p
    finally:
        a.parar()
    a.escrever(SO_RUI)                                    # apagada à mão, com o servidor parado
    cli.main(["--config", str(a.toml), "utilizador", "novo", ANA, "--espaco", "ana", "--admin"])
    assert config.carregar(a.toml, ambiente={}).utilizador(ANA).espaco == "ana"
    b = _reiniciar(tmp_path, a, modo="cabecalho", utilizadores=SEM_SENHA)
    try:
        assert mcp(b, t["access_token"])[0] == 401, "o acesso antigo voltou a valer com a conta recriada pela CLI"
        assert renovar(b, cid, t["refresh_token"])[0] == 400
        assert mcp(b, p["token"])[0] == 401
    finally:
        b.parar()


def test_controlo_reinicio_sem_mudar_nada_mantem_as_ias(tmp_path):
    """controlo: o mesmo reinício, sem mudar a senha — as ligações continuam a valer (o que os testes acima recusam é a mudança)."""
    a = Srv(tmp_path)
    try:
        cid, t, cookie = tokens(a)
        pessoal = _pessoal_senha(a, cookie)
    finally:
        a.parar()
    b = _reiniciar(tmp_path, a)
    try:
        assert mcp(b, t["access_token"])[0] == 200 and mcp(b, pessoal)[0] == 200
        assert renovar(b, cid, t["refresh_token"])[0] == 200
    finally:
        b.parar()


# ───────────────────────── 2. a conta apagada a meio do pedido não fica com ligação ─────────────────────────
def _parar_no_corpo(s, monkeypatch, caminho):
    """o pedido a `caminho` fica parado na leitura do corpo (já autenticado) até `soltar`. Só o primeiro."""
    H = s.srv.RequestHandlerClass
    real = H._corpo
    dentro, soltar = threading.Event(), threading.Event()

    def corpo(self):
        if urllib.parse.urlsplit(self.path).path == caminho and not dentro.is_set():
            dentro.set()
            assert soltar.wait(15)
        return real(self)
    monkeypatch.setattr(H, "_corpo", corpo)
    return dentro, soltar


def _em_fundo(f):
    r = {}
    fio = threading.Thread(target=lambda: r.setdefault("r", f()), daemon=True)
    fio.start()
    return fio, r


def test_token_pessoal_com_a_conta_apagada_a_meio(srv, monkeypatch):
    """o pedido do token pessoal autentica-se, fica a ler o corpo; entretanto a Ana sai do basalt.toml (e o servidor relê-o).
    Respondia 201 com a conta antiga — e, recriada igual, esse token passava a valer sem ninguém o ter pedido com ela."""
    s = srv(modo="cabecalho", utilizadores=SEM_SENHA)
    dentro, soltar = _parar_no_corpo(s, monkeypatch, "/api/ligacoes/token")
    fio, r = _em_fundo(lambda: _pessoal(s, CAB_ANA))
    assert dentro.wait(15)
    _mudar(s, SO_RUI)                                                               # a Ana sai…
    assert s.pedir("GET", "/api/eu", cab={"X-Basalt-Email": RUI})[0] == 200        # …e o servidor relê o basalt.toml
    soltar.set()
    fio.join(20)
    st, d, _ = r["r"]
    _mudar(s, SEM_SENHA)                                                            # a Ana volta igual
    assert s.pedir("GET", "/api/eu", cab=CAB_ANA)[0] == 200
    if st == 201:
        assert mcp(s, d["token"])[0] == 401, "o token criado para a conta apagada passou a valer com ela recriada"
    assert st == 403, ("criou um token para uma conta apagada a meio do pedido", st, d)
    assert _vivas(s, ANA, "pessoal") == 0
    st, d, _ = _pessoal(s, CAB_ANA)                                                 # a conta recriada pede outro: vale
    assert st == 201 and mcp(s, d["token"])[0] == 200, d


def test_permitir_com_a_conta_apagada_a_meio(srv, monkeypatch):
    """o mesmo no «Permitir» do OAuth: o POST /oauth/autorizar autentica-se e, antes de criar a ligação, a Ana sai. Dava o código
    (303) com a conta antiga — trocado depois da conta recriada igual, a IA ficava ligada sem consentimento da conta nova."""
    s = srv(modo="cabecalho", utilizadores=SEM_SENHA)
    st, cli_, _ = registar(s)
    assert st == 201, cli_
    v, c = pkce()
    st, pag, _ = pedir_autorizacao(s, cli_["client_id"], c)
    st, pag, _ = s.pedir("GET", f"/oauth/authorize?pedido={pid_de(pag)}", cab=CAB_ANA)
    assert st == 200, pag
    o = _oauth_de(s)
    real = o._chave
    dentro, soltar = threading.Event(), threading.Event()

    def chave(conn):                                   # já autenticado, antes da transacção que cria a ligação
        if not dentro.is_set():
            dentro.set()
            assert soltar.wait(15)
        return real(conn)
    monkeypatch.setattr(o, "_chave", chave)
    fio, r = _em_fundo(lambda: s.pedir("POST", "/oauth/autorizar", form={**campos(pag), "decisao": "permitir"},
                                       cab={**CAB_ANA, "Origin": s.origem}))
    assert dentro.wait(15)
    _mudar(s, SO_RUI)
    assert s.pedir("GET", "/api/eu", cab={"X-Basalt-Email": RUI})[0] == 200
    soltar.set()
    fio.join(20)
    st, _, cab = r["r"]
    _mudar(s, SEM_SENHA)
    assert s.pedir("GET", "/api/eu", cab=CAB_ANA)[0] == 200
    if st == 303 and codigo_de(cab["location"]).get("code"):
        st2, t, _ = trocar(s, cli_["client_id"], v, codigo_de(cab["location"])["code"])
        assert st2 != 200 or mcp(s, t["access_token"])[0] == 401, "a ligação criada para a conta apagada passou a valer"
    assert st == 403, ("criou uma ligação para uma conta apagada a meio do pedido", st, cab.get("location"))
    assert _vivas(s, ANA, "oauth") == 0
    _ligar_cabecalho(s)                                # a conta recriada liga outra vez, com consentimento novo


def test_controlo_outra_conta_muda_a_meio_cria_na_mesma(srv, monkeypatch):
    """controlo: só conta a conta do pedido — se a meio sai OUTRA conta, o token da Ana cria-se e vale."""
    s = srv(modo="cabecalho", utilizadores=SEM_SENHA)
    dentro, soltar = _parar_no_corpo(s, monkeypatch, "/api/ligacoes/token")
    fio, r = _em_fundo(lambda: _pessoal(s, CAB_ANA))
    assert dentro.wait(15)
    _mudar(s, [SEM_SENHA[0]])                                                       # o Rui sai
    assert s.pedir("GET", "/api/eu", cab=CAB_ANA)[0] == 200
    soltar.set()
    fio.join(20)
    st, d, _ = r["r"]
    assert st == 201 and mcp(s, d["token"])[0] == 200, (st, d)
