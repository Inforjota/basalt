"""r22 — o que a revisão externa da r13b (Astra, 23/10) ainda encontrou no «Traz a tua IA» (basalt/oauth.py, CLI):

  R1. (grave) a CLI (`utilizador senha`/`apagar`) revoga as ligações na base e SÓ DEPOIS substitui o basalt.toml. Com o servidor a
      correr, uma ligação criada nesse intervalo (a conta ainda com a senha antiga) ficava viva: reiniciar antes da releitura,
      com o basalt.toml antigo reposto, e ela valia. E um pedido já autenticado antes da troca criava a ligação depois dela (o
      servidor ainda não tinha relido). Agora a CLI revoga outra vez depois de gravar (com o trinco ainda tomado) e quem cria
      uma ligação confere, dentro da transacção, que o basalt.toml do disco é o que o servidor leu (senão 503, tenta outra vez).
  R2. (grave) apagar a ÚLTIMA conta deixa o basalt.toml inválido: o servidor fecha a porta, mas não subia a geração e um pedido
      já autenticado ainda criava a ligação (token pessoal, «Permitir»); com a conta reposta igual, essa ligação valia.

Cada teste de comportamento falha no 15beb01 por asserção; os controlos (marcados como tal) passam nos dois.
"""
import os
import threading
import time

from basalt import __main__ as cli, auth
from test_mudar_senha import NOVA, _esc
from test_oauth import (ANA, RUI, Srv, _toml, campos, codigo_de, mcp, pedir_autorizacao, pid_de, pkce, registar,  # noqa: F401
                        srv, tokens, trocar)
from test_ronda13_oauth import CAB_ANA, SEM_SENHA, _ligar_cabecalho, _mudar, _oauth_de, _pessoal
from test_ronda13b_oauth import _em_fundo, _parar_no_corpo, _pessoal_senha, _reiniciar, _vivas

SO_ANA = [SEM_SENHA[0]]


def _sem_ninguem(s, passo=[0]):
    """o basalt.toml sem nenhuma conta (a última foi apagada à mão): no modo cabecalho sem criar_contas, não carrega."""
    passo[0] += 7
    s.toml.write_text(_toml(s.dados, s.porta, s.url, s.modo, [], s.extra), encoding="utf-8")
    os.utime(s.toml, (time.time() + 100 + passo[0], time.time() + 100 + passo[0]))


# ───────────────────────── R2. a última conta apagada a meio do pedido ─────────────────────────
def test_token_pessoal_com_a_ultima_conta_apagada_a_meio(srv, monkeypatch):
    s = srv(modo="cabecalho", utilizadores=SO_ANA)
    dentro, soltar = _parar_no_corpo(s, monkeypatch, "/api/ligacoes/token")
    fio, r = _em_fundo(lambda: _pessoal(s, CAB_ANA))
    assert dentro.wait(15)
    _sem_ninguem(s)                                                                 # a Ana (a única) sai…
    assert s.pedir("GET", "/api/eu", cab=CAB_ANA)[0] != 200                         # …o servidor relê: porta fechada
    soltar.set()
    fio.join(20)
    st, d, _ = r["r"]
    _mudar(s, SO_ANA)                                                               # a Ana volta igual
    assert s.pedir("GET", "/api/eu", cab=CAB_ANA)[0] == 200
    if st == 201:
        assert mcp(s, d["token"])[0] == 401, "o token criado com o basalt.toml fechado passou a valer com a conta reposta"
    assert st in (403, 503), ("criou um token com a última conta apagada a meio do pedido", st, d)
    assert _vivas(s, ANA, "pessoal") == 0
    st, d, _ = _pessoal(s, CAB_ANA)                                                 # pedido DEPOIS de reposta: vale
    assert st == 201 and mcp(s, d["token"])[0] == 200, d


def test_permitir_com_a_ultima_conta_apagada_a_meio(srv, monkeypatch):
    s = srv(modo="cabecalho", utilizadores=SO_ANA)
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
    _sem_ninguem(s)
    assert s.pedir("GET", "/api/eu", cab=CAB_ANA)[0] != 200
    soltar.set()
    fio.join(20)
    st, _, cab = r["r"]
    _mudar(s, SO_ANA)
    assert s.pedir("GET", "/api/eu", cab=CAB_ANA)[0] == 200
    if st == 303 and codigo_de(cab["location"]).get("code"):
        st2, t, _ = trocar(s, cli_["client_id"], v, codigo_de(cab["location"])["code"])
        assert st2 != 200 or mcp(s, t["access_token"])[0] == 401, "a ligação criada com o basalt.toml fechado passou a valer"
    assert st in (403, 503), ("criou uma ligação com a última conta apagada a meio do pedido", st, cab.get("location"))
    assert _vivas(s, ANA, "oauth") == 0
    _ligar_cabecalho(s)                                # reposta, liga outra vez com consentimento novo


def test_token_pessoal_com_o_toml_fechado_e_reaberto_igual_a_meio(srv, monkeypatch):
    """a última conta apagada e reposta igual ENQUANTO o pedido está a meio (as duas releituras antes de ele criar): como «apagada
    e recriada igual a meio do pedido» (ronda 13b), conta como mudada — o fecho sobe a geração."""
    s = srv(modo="cabecalho", utilizadores=SO_ANA)
    dentro, soltar = _parar_no_corpo(s, monkeypatch, "/api/ligacoes/token")
    fio, r = _em_fundo(lambda: _pessoal(s, CAB_ANA))
    assert dentro.wait(15)
    _sem_ninguem(s)
    assert s.pedir("GET", "/api/eu", cab=CAB_ANA)[0] != 200
    _mudar(s, SO_ANA)
    assert s.pedir("GET", "/api/eu", cab=CAB_ANA)[0] == 200                         # já reaberta antes de o pedido criar
    soltar.set()
    fio.join(20)
    st, d, _ = r["r"]
    assert st in (403, 503), ("criou um token com a conta fechada e reaberta a meio do pedido", st, d)
    assert _vivas(s, ANA, "pessoal") == 0


def test_controlo_toml_fechado_e_reaberto_sem_pedidos_a_meio_as_ligacoes_de_antes_ficam(srv):
    """controlo: fechar e reabrir o basalt.toml igual não revoga nada (nada mudou nas contas) — o que os testes acima recusam é
    só a criação que estava a meio."""
    s = srv(modo="cabecalho", utilizadores=SO_ANA)
    st, d, _ = _pessoal(s, CAB_ANA)
    assert st == 201, d
    _sem_ninguem(s)
    assert s.pedir("GET", "/api/eu", cab=CAB_ANA)[0] != 200
    assert mcp(s, d["token"])[0] == 401                # fechado: nem por token
    _mudar(s, SO_ANA)
    assert s.pedir("GET", "/api/eu", cab=CAB_ANA)[0] == 200
    assert mcp(s, d["token"])[0] == 200


# ───────────────────────── R1. a CLI com o servidor a correr ─────────────────────────
def _na_gravacao(monkeypatch, antes_de_gravar):
    """corre `antes_de_gravar()` uma vez, já depois do `editar` da CLI (a revogação de antes) e ANTES de o basalt.toml mudar."""
    real = auth.gravar_toml
    feito = []

    def gravar(caminho, texto):
        if not feito:
            feito.append(antes_de_gravar())
        return real(caminho, texto)
    monkeypatch.setattr(auth, "gravar_toml", gravar)
    return feito


def test_cli_senha_com_o_servidor_a_correr_token_criado_antes_da_troca_nao_sobrevive(tmp_path, monkeypatch):
    """o servidor está a correr: entre a revogação da CLI e a troca do basalt.toml cria-se um token pessoal (ainda com a senha
    antiga). Depois, o servidor reinicia antes de reler, com o basalt.toml antigo reposto: o token valia."""
    monkeypatch.delenv("BASALT_DADOS", raising=False)
    a = Srv(tmp_path)
    try:
        cid, t, cookie = tokens(a)
        feito = _na_gravacao(monkeypatch, lambda: _pessoal_senha(a, cookie))
        cli.main(["--config", str(a.toml), "utilizador", "senha", ANA, "--senha", NOVA])
        assert feito and feito[0], "o token do intervalo não chegou a criar-se"
    finally:
        a.parar()                                      # sem nenhum pedido depois da troca: o servidor não a releu
    b = _reiniciar(tmp_path, a)                        # o basalt.toml antigo (a senha antiga) volta ao sítio
    try:
        assert mcp(b, feito[0])[0] == 401, "o token criado entre a revogação e a troca do basalt.toml sobreviveu"
        assert mcp(b, t["access_token"])[0] == 401
    finally:
        b.parar()


def test_cli_senha_pedido_autenticado_antes_da_troca_nao_cria_depois_dela(tmp_path, monkeypatch):
    """o pedido do token pessoal autentica-se (senha antiga) e fica a ler o corpo; a CLI muda a senha TODA (revogações e troca do
    basalt.toml); o pedido continua. Criava o token com a conta antiga — e, com o basalt.toml antigo reposto, valia."""
    monkeypatch.delenv("BASALT_DADOS", raising=False)
    a = Srv(tmp_path)
    try:
        cid, t, cookie = tokens(a)
        esc = _esc(a, cookie)
        dentro, soltar = _parar_no_corpo(a, monkeypatch, "/api/ligacoes/token")
        fio, r = _em_fundo(lambda: a.pedir("POST", "/api/ligacoes/token", {"nome": "Codex"}, esc))
        assert dentro.wait(15)
        cli.main(["--config", str(a.toml), "utilizador", "senha", ANA, "--senha", NOVA])
        soltar.set()
        fio.join(20)
        st, d, _ = r["r"]
    finally:
        a.parar()
    b = _reiniciar(tmp_path, a)
    try:
        if st == 201:
            assert mcp(b, d["token"])[0] == 401, "o token pedido antes da troca e criado depois dela sobreviveu"
        assert st in (403, 503), ("criou o token com a conta de antes da troca do basalt.toml", st, d)
        assert _vivas(b, ANA, "pessoal") == 0
    finally:
        b.parar()


def test_cli_apagar_com_o_servidor_a_correr_token_criado_antes_da_troca_nao_sobrevive(tmp_path, monkeypatch):
    """o mesmo no `utilizador apagar` (o `depois` dele também move a pasta): a conta reposta com o basalt.toml antigo não traz o
    token criado no intervalo."""
    monkeypatch.delenv("BASALT_DADOS", raising=False)
    a = Srv(tmp_path, modo="cabecalho", utilizadores=SEM_SENHA)
    try:
        feito = _na_gravacao(monkeypatch, lambda: _pessoal(a, {"X-Basalt-Email": RUI}))
        cli.main(["--config", str(a.toml), "utilizador", "apagar", RUI, "--sim"])
        assert feito and feito[0][0] == 201, feito
    finally:
        a.parar()
    b = _reiniciar(tmp_path, a, modo="cabecalho", utilizadores=SEM_SENHA)
    try:
        assert mcp(b, feito[0][1]["token"])[0] == 401, "o token criado entre a revogação e a troca do basalt.toml sobreviveu"
    finally:
        b.parar()


def test_controlo_cli_senha_com_o_servidor_a_correr_o_token_pedido_depois_com_a_senha_nova_vale(tmp_path, monkeypatch):
    """controlo: depois da CLI, o servidor relê; entrar com a senha nova e pedir um token — esse vale (a segunda revogação não
    apanha o que se cria depois dela, nem a verificação do basalt.toml recusa quando ele já foi relido)."""
    monkeypatch.delenv("BASALT_DADOS", raising=False)
    a = Srv(tmp_path)
    try:
        tokens(a)
        cli.main(["--config", str(a.toml), "utilizador", "senha", ANA, "--senha", NOVA])
        novo = _pessoal_senha(a, a.entrar(ANA, NOVA))
        assert mcp(a, novo)[0] == 200
    finally:
        a.parar()
