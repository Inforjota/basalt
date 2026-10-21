"""r14 — login Google para clientes: modo cabecalho atrás do oauth2-proxy, com segredo partilhado do proxy e conta criada no
1.º login. Cada regra tem o caso bom e o mau (o ataque: o visitante escreve ele próprio o X-Basalt-Email)."""
import os
import threading

import pytest

from basalt import auth, config, web
from test_oauth import ANA, RUI, Srv, _relogio, mcp, tokens  # noqa: F401 (fixtures)

SEG = "s" * 40
NOVO = "cliente@gmail.com"


class SrvG(Srv):
    def __init__(self, tmp_path, auth_extra="", **k):
        self.auth_extra = auth_extra
        super().__init__(tmp_path, modo="cabecalho", **k)

    def escrever(self, utilizadores=None):
        super().escrever(utilizadores)
        t = self.toml.read_text(encoding="utf-8").replace('modo = "cabecalho"\n', 'modo = "cabecalho"\n' + self.auth_extra, 1)
        self.toml.write_text(t, encoding="utf-8")

    def como(self, email, segredo=SEG, sub=None, extra=None):
        h = {"X-Basalt-Email": email, **(extra or {})}
        if segredo is not None:
            h["X-Basalt-Proxy-Segredo"] = segredo
        if sub is not None:
            h["X-Basalt-Sub"] = sub
        return h


@pytest.fixture
def g(tmp_path, monkeypatch):
    monkeypatch.setenv("BASALT_SEGREDO_PROXY", SEG)
    orig = config.carregar
    monkeypatch.setattr(config, "carregar", lambda c=None, ambiente=None, validar=True:
                        orig(c, ambiente=None if ambiente is None else {**ambiente, "BASALT_SEGREDO_PROXY": SEG}, validar=validar))
    feitos = []

    def fazer(auth_extra='criar_contas = true\ncabecalho_sub = "X-Basalt-Sub"\n', **k):
        s = SrvG(tmp_path / f"g{len(feitos)}", auth_extra=auth_extra, **k)
        feitos.append(s)
        return s
    yield fazer
    for s in feitos:
        s.parar()


def _eu(s, cab):
    st, r, _ = s.pedir("GET", "/api/eu", cab=cab)
    return st, r


def test_sem_o_segredo_do_proxy_ninguem_entra(g):
    s = g()
    assert _eu(s, s.como(ANA))[0] == 200
    # o ataque: o visitante escreve o e-mail de outro numa rota que o proxy deixou passar sem login
    assert _eu(s, s.como(ANA, segredo=None))[0] in (401, 403)
    assert _eu(s, s.como(ANA, segredo="errado"))[0] in (401, 403)
    assert _eu(s, s.como(ANA, segredo=SEG[:-1]))[0] in (401, 403)
    # e não cria conta nenhuma
    assert _eu(s, s.como("intruso@gmail.com", segredo=None, sub="1"))[0] in (401, 403)
    assert "intruso" not in s.toml.read_text(encoding="utf-8")


def test_conta_criada_no_primeiro_login(g):
    s = g()
    antes = s.toml.read_text(encoding="utf-8")
    st, r = _eu(s, s.como(NOVO, sub="1234567890"))
    assert st == 200, r
    u = config.carregar(s.toml).utilizador(NOVO)
    assert u is not None and u.sub == "1234567890" and not u.admin and not u.senha_hash
    assert u.espaco.startswith("c") and len(u.espaco) == 13 and "cliente" not in u.espaco   # aleatório, não do e-mail
    assert (s.toml.stat().st_mode & 0o777) == 0o600
    assert s.toml.read_text(encoding="utf-8").startswith(antes.rstrip("\n"))   # só se acrescentou ao fim
    # as contas que já existiam ficam iguais
    c = config.carregar(s.toml)
    assert c.utilizador(ANA).espaco == "ana" and c.utilizador(RUI).espaco == "rui"
    # 2.º login: a mesma conta, não uma nova
    assert _eu(s, s.como(NOVO, sub="1234567890"))[0] == 200
    assert s.toml.read_text(encoding="utf-8").count(NOVO) == 1
    # a conta nova não vê as notas dos outros
    cab = s.como(NOVO, sub="1234567890")
    cab["X-Basalt-Dono"] = _eu(s, cab)[1]["dono"]
    st, r, _ = s.pedir("GET", "/api/nota?nome=barcos", cab=cab)
    assert st == 404, (st, r)


def test_sem_criar_contas_um_email_desconhecido_nao_entra(g):
    s = g(auth_extra='cabecalho_sub = "X-Basalt-Sub"\n')
    assert _eu(s, s.como(NOVO, sub="1"))[0] in (401, 403)
    assert NOVO not in s.toml.read_text(encoding="utf-8")


def test_sub_diferente_e_outra_pessoa(g):
    s = g()
    assert _eu(s, s.como(NOVO, sub="pessoa-1"))[0] == 200
    # a conta Google com esse e-mail foi apagada e recriada: outro `sub` → não entra nas notas da anterior
    assert _eu(s, s.como(NOVO, sub="pessoa-2"))[0] in (401, 403)
    assert _eu(s, s.como(NOVO))[0] in (401, 403)                  # sem sub, quando se exige
    assert _eu(s, s.como(NOVO, sub="pessoa-1"))[0] == 200


def test_dominios_e_tecto_de_contas(g):
    s = g(auth_extra='criar_contas = true\ndominios = ["empresa.pt"]\nmax_contas = 3\n')
    assert _eu(s, s.como("a@gmail.com"))[0] in (401, 403)          # fora da lista
    assert _eu(s, s.como("a@empresa.pt"))[0] == 200                 # 3.ª conta (Ana, Rui + esta)
    assert _eu(s, s.como("b@empresa.pt"))[0] in (401, 403)          # tecto
    assert _eu(s, s.como(ANA))[0] == 200                            # quem já existe continua a entrar
    for mau in ("x@empresa.pt admin = true", "x@empresa.pt\tadmin", 'x"@empresa.pt', "semarroba", "a@b"):
        assert _eu(s, s.como(mau))[0] in (400, 401, 403)
    texto = s.toml.read_text(encoding="utf-8")
    assert "b@empresa.pt" not in texto and texto.count("admin = true") == 1


def test_logins_ao_mesmo_tempo_criam_uma_conta_so(g):
    s = g(auth_extra="criar_contas = true\n")
    res = []
    fios = [threading.Thread(target=lambda: res.append(_eu(s, s.como(NOVO))[0])) for _ in range(8)]
    [f.start() for f in fios]; [f.join() for f in fios]
    assert res == [200] * 8
    assert s.toml.read_text(encoding="utf-8").count(NOVO) == 1


def test_configuracao_recusa_o_que_e_perigoso(tmp_path, monkeypatch):
    base = (f'[basalt]\ndados = "{tmp_path}/d"\nescutar = "127.0.0.1"\ngit = false\n[auth]\nmodo = "cabecalho"\n')
    p = tmp_path / "b.toml"
    p.write_text(base + "criar_contas = true\n", encoding="utf-8")
    # os comandos de administração carregam a config sem o segredo (ensaio de 20/10: recusavam todos correr)…
    sem = config.carregar(p, ambiente={})
    # …mas o servidor não arranca: sem segredo qualquer visitante criava contas
    with pytest.raises(config.ConfigInvalida, match="segredo"):
        web.criar_servidor(sem, porta=0)
    with pytest.raises(config.ConfigInvalida, match="32"):
        web.criar_servidor(config.carregar(p, ambiente={"BASALT_SEGREDO_PROXY": "curto"}), porta=0)
    # e, se alguém o pusesse a servir na mesma, não se cria conta nenhuma
    a = auth.Autenticador(sem); a._origem = p
    assert a._criar_conta("x@gmail.com", "") is None
    assert config.carregar(p, ambiente={"BASALT_SEGREDO_PROXY": SEG}).auth.criar_contas
    p.write_text(base + f'segredo_proxy = "{SEG}"\n', encoding="utf-8")
    with pytest.raises(config.ConfigInvalida, match="ambiente"):
        config.carregar(p, ambiente={})                              # o segredo nunca no toml
    p.write_text(base.replace('"cabecalho"', '"senha"') + "criar_contas = true\n", encoding="utf-8")
    with pytest.raises(config.ConfigInvalida, match="cabecalho"):
        config.carregar(p, ambiente={"BASALT_SEGREDO_PROXY": SEG})
    p.write_text(base + "criar_contas = true\n", encoding="utf-8")
    c = config.carregar(p, ambiente={"BASALT_SEGREDO_PROXY": SEG})
    assert SEG not in repr(c.auth)                                   # o segredo não aparece em registos


def test_mcp_continua_so_por_bearer(g):
    s = g()
    st, _, _ = s.pedir("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, s.como(ANA))
    assert st == 401


def test_conta_nova_fica_com_o_espaco_e_o_git_prontos(g, monkeypatch):
    """revisão Fable (20/10): as contas do arranque passam por esp.criar() + git_iniciar; a criada no 1.º login não passava —
    ficava sem repositório (sem histórico nem «desfazer», e o `git add -A` do duasvias a falhar em silêncio)."""
    monkeypatch.setenv("BASALT_GIT", "1")                        # o toml dos testes tem git = false; aqui queremos o git a sério
    s = g()
    assert _eu(s, s.como(NOVO, sub="1234567890"))[0] == 200
    u = config.carregar(s.toml).utilizador(NOVO)
    esp = s.cfg.espaco_de(u)
    assert esp.real.is_dir() and esp.copia.is_dir() and esp.dados.is_dir()
    assert (esp.real / ".git").is_dir()
    # o 2.º login não volta a mexer (o git_iniciar não toca num repositório que já existe)
    marca = (esp.real / ".git" / "HEAD").stat().st_mtime_ns
    assert _eu(s, s.como(NOVO, sub="1234567890"))[0] == 200
    assert (esp.real / ".git" / "HEAD").stat().st_mtime_ns == marca


# ───────────── auditoria de 21/10/2026 ao login Google ─────────────
def test_modo_cabecalho_sem_segredo_avisa_no_arranque(tmp_path, capsys):
    """sem criar_contas o servidor arranca sem o segredo do proxy (instalações anteriores à r14), mas fica escrito no stderr que toda a
    segurança está no proxy — antes arrancava em silêncio e aceitava o X-Basalt-Email de quem chegasse à porta (ensaio b2)."""
    base = (f'[basalt]\ndados = "{tmp_path}/d"\nescutar = "127.0.0.1"\ngit = false\n[auth]\nmodo = "cabecalho"\n'
            f'[[utilizadores]]\nemail = "{ANA}"\nespaco = "ana"\n')
    p = tmp_path / "b.toml"
    p.write_text(base, encoding="utf-8")
    srv = web.criar_servidor(config.carregar(p, ambiente={}), porta=0)
    srv.server_close()
    err = capsys.readouterr().err
    assert "aviso" in err and "segredo" in err and "BASALT_SEGREDO_PROXY" in err and "X-Basalt-Email" in err, err
    srv = web.criar_servidor(config.carregar(p, ambiente={"BASALT_SEGREDO_PROXY": SEG}), porta=0)
    srv.server_close()
    assert "aviso" not in capsys.readouterr().err                   # com o segredo, nada a dizer


def _editar_auth(s, *pares):
    """muda o [auth] do basalt.toml ao vivo, numa só escrita (o texto muda de tamanho: a marca do ficheiro é (mtime, tamanho, inode))."""
    t = antes = s.toml.read_text(encoding="utf-8")
    for velho, novo in zip(pares[::2], pares[1::2]):
        assert t.count(velho) == 1, velho
        t = t.replace(velho, novo)
    assert len(t) != len(antes)
    s.toml.write_text(t, encoding="utf-8")


def test_tecto_dominios_e_criar_contas_mudados_no_toml_valem_sem_reiniciar(g):
    """o tecto e os domínios que _criar_conta aplica são os do toml ACTUAL: antes ficavam os do arranque (reler_se_mudou só trazia
    os utilizadores) — descer o tecto ou apertar os domínios ao vivo não travava contas novas (ensaio b1: toml a 6, registo «tecto de 3»)."""
    s = g(auth_extra="criar_contas = true\nmax_contas = 3\n")
    assert _eu(s, s.como("a@empresa.pt"))[0] == 200                 # 3.ª conta (Ana, Rui + esta)
    assert _eu(s, s.como("b@empresa.pt"))[0] in (401, 403)          # tecto
    _editar_auth(s, "max_contas = 3\n", "max_contas = 10\n")        # subir o tecto, sem reiniciar
    assert _eu(s, s.como("b@empresa.pt"))[0] == 200
    _editar_auth(s, "max_contas = 10\n", 'max_contas = 100\ndominios = ["empresa.pt"]\n')   # apertar os domínios
    assert _eu(s, s.como("c@gmail.com"))[0] in (401, 403)
    assert _eu(s, s.como("c@empresa.pt"))[0] == 200
    _editar_auth(s, "max_contas = 100\n", "max_contas = 5\n")       # descer o tecto até ao que já há
    assert _eu(s, s.como("d@empresa.pt"))[0] in (401, 403)          # Ana, Rui, a, b, c = 5 → fechado
    assert _eu(s, s.como("c@empresa.pt"))[0] == 200                 # quem já existe continua a entrar
    _editar_auth(s, "criar_contas = true\n", "criar_contas = false\n", "max_contas = 5\n", "max_contas = 0\n")   # desligar ao vivo
    assert _eu(s, s.como("e@empresa.pt"))[0] in (401, 403)
    texto = s.toml.read_text(encoding="utf-8")
    assert "c@gmail.com" not in texto and "d@empresa.pt" not in texto and "e@empresa.pt" not in texto
