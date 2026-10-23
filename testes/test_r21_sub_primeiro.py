"""r21 (auditoria externa à r14, ponto 1) — com `[auth] cabecalho_sub`, uma conta SEM `sub` no basalt.toml aceitava qualquer
`sub`: a conta Google desse e-mail apagada e recriada (outra pessoa, outro `sub`) entrava nas notas da anterior.
Escolha (SUB_PRIMEIRO_v1, «trust-on-first-use»): a conta sem `sub` fica com o `sub` do 1.º login que o traga (gravado no
basalt.toml, com o trinco do toml) e daí em diante exige-se o mesmo. Um `sub` com caracteres estranhos não se grava (recusa); se
o toml não se deixar gravar, recusa-se (falha fechada) — aceitar sem gravar deixava o buraco aberto a cada arranque. Um pedido
SEM `sub` numa conta que ainda não tem nenhum entra como antes (só pelo e-mail) e não associa nada: o `sub` vem do proxy, o
visitante não o consegue tirar. Sem `cabecalho_sub` nada muda. Cada teste falha no 68ad69d (r20) pela razão certa."""
import threading

import pytest

from basalt import auth, config
from test_login_google import _eu, g  # noqa: F401 (fixture)
from test_oauth import ANA

SO_SUB = 'cabecalho_sub = "X-Basalt-Sub"\n'


def _sub_no_toml(s, email=ANA):
    return auth.chave_utilizador(s.toml.read_text(encoding="utf-8"), email, "sub")


def test_conta_sem_sub_fica_com_o_sub_do_primeiro_login_e_outro_sub_nao_entra(g):
    s = g(auth_extra=SO_SUB)
    assert config.carregar(s.toml).utilizador(ANA).sub == ""                     # controlo: a Ana não tem `sub`
    assert _eu(s, s.como(ANA, sub="pessoa-1"))[0] == 200
    assert _sub_no_toml(s) == "pessoa-1", "o `sub` do 1.º login devia ficar gravado no basalt.toml"
    # a conta Google com esse e-mail foi apagada e recriada: outro `sub` → é outra pessoa (antes: 200, entrava nas notas da Ana)
    st, r = _eu(s, s.como(ANA, sub="pessoa-2"))
    assert st in (401, 403), (st, r)
    assert _eu(s, s.como(ANA, sub="pessoa-1"))[0] == 200
    assert _sub_no_toml(s) == "pessoa-1"


def test_depois_de_associada_o_pedido_sem_sub_tambem_nao_entra(g):
    s = g(auth_extra=SO_SUB)
    assert _eu(s, s.como(ANA, sub="pessoa-1"))[0] == 200
    assert _eu(s, s.como(ANA))[0] in (401, 403)                                  # como numa conta criada já com `sub`


def test_conta_sem_sub_e_pedido_sem_sub_entra_como_antes_e_nao_associa(g):
    s = g(auth_extra=SO_SUB)
    antes = s.toml.read_text(encoding="utf-8")
    assert _eu(s, s.como(ANA))[0] == 200
    assert s.toml.read_text(encoding="utf-8") == antes


def test_sub_com_caracteres_estranhos_nao_se_associa_e_nao_entra(g):
    s = g(auth_extra=SO_SUB)
    antes = s.toml.read_text(encoding="utf-8")
    for mau in ("dois sub", 'a"b', "x" * 300, "sub\tcom-tab"):
        st, r = _eu(s, s.como(ANA, sub=mau))
        assert st in (400, 401, 403), (mau, st, r)                                 # antes: 200
    assert s.toml.read_text(encoding="utf-8") == antes


def test_toml_que_nao_se_deixa_gravar_recusa_em_vez_de_entrar_sem_associar(g, monkeypatch):
    s = g(auth_extra=SO_SUB)

    def falha(*a, **k):
        raise OSError(30, "Read-only file system")
    monkeypatch.setattr(auth, "editar_toml", falha)
    st, r = _eu(s, s.como(ANA, sub="pessoa-1"))
    assert st in (401, 403), (st, r)                                               # antes: 200 (e nada se gravava)
    assert _sub_no_toml(s) is None


def test_logins_ao_mesmo_tempo_com_subs_diferentes_so_um_fica_com_a_conta(g):
    s = g(auth_extra=SO_SUB)
    res = []
    fios = [threading.Thread(target=lambda sub=sub: res.append((sub, _eu(s, s.como(ANA, sub=sub))[0])))
            for sub in ["pessoa-1", "pessoa-2"] * 4]
    [f.start() for f in fios]; [f.join() for f in fios]
    gravado = _sub_no_toml(s)
    assert gravado in ("pessoa-1", "pessoa-2"), gravado
    assert all(st == 200 for sub, st in res if sub == gravado), res
    assert all(st in (401, 403) for sub, st in res if sub != gravado), res        # antes: as 8 entravam
    linhas = [ln for ln in s.toml.read_text(encoding="utf-8").splitlines() if ln.startswith("sub =")]
    assert len(linhas) == 1, linhas                                              # um só `sub` gravado (não dois, nem trocado)


def test_sem_cabecalho_sub_nada_muda(g):
    s = g(auth_extra="")
    antes = s.toml.read_text(encoding="utf-8")
    assert _eu(s, s.como(ANA, sub="pessoa-1"))[0] == 200
    assert _eu(s, s.como(ANA, sub="pessoa-2"))[0] == 200
    assert _eu(s, s.como(ANA))[0] == 200
    assert s.toml.read_text(encoding="utf-8") == antes


@pytest.mark.parametrize("extra", ["criar_contas = true\n" + SO_SUB])
def test_conta_criada_no_primeiro_login_continua_com_o_sub_dela(g, extra):
    # controlo: o caminho da r14 (conta criada já com o `sub`) não mudou
    s = g(auth_extra=extra)
    assert _eu(s, s.como("cliente@gmail.com", sub="1234567890"))[0] == 200
    assert _sub_no_toml(s, "cliente@gmail.com") == "1234567890"
    assert _eu(s, s.como("cliente@gmail.com", sub="outro"))[0] in (401, 403)
