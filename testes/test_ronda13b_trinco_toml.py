"""Ronda 13b — o trinco do basalt.toml (auth.trinco_toml) abria-se com open(…, "a"): não truncava, mas seguia uma ligação simbólica
posta no lugar dele — tomava o ficheiro para onde ela apontasse, ou criava-o. É o defeito que a ronda 13 (#4) corrigiu nos outros
trincos. Agora abre-se como eles (caminhos.abrir_trinco: sem seguir ligações nem truncar) e uma ligação faz a operação recusar,
na linha de comandos e no «Mudar a senha». Os três testes falham no código anterior (3c47c86) por asserção.
"""
import os

import pytest

from basalt import __main__ as cli, config
from test_mudar_senha import NOVA, _mudar
from test_oauth import ANA, SENHA, _toml, srv  # noqa: F401 (fixtures)

ALVO = "uma nota de outra pessoa, que o serviço consegue escrever\n"


def _ligar_trinco(toml, alvo):
    """põe uma ligação simbólica no lugar do trinco (basalt.toml.trinco) a apontar para `alvo`."""
    trinco = toml.with_name(toml.name + ".trinco")
    if os.path.lexists(trinco):
        os.unlink(trinco)
    os.symlink(alvo, trinco)
    return trinco


def _alvo(pasta):
    alvo = pasta / "outra" / "nota.md"
    alvo.parent.mkdir(parents=True)
    alvo.write_text(ALVO, encoding="utf-8")
    return alvo


def test_cli_com_o_trinco_ligado_a_outro_ficheiro_recusa_e_o_alvo_fica(tmp_path, monkeypatch):
    monkeypatch.delenv("BASALT_DADOS", raising=False)
    toml = tmp_path / "basalt.toml"
    toml.write_text(_toml(tmp_path / "dados", 8121, ""), encoding="utf-8")
    os.chmod(toml, 0o600)
    alvo = _alvo(tmp_path)
    mtime = alvo.stat().st_mtime_ns
    trinco = _ligar_trinco(toml, alvo)
    antes = toml.read_bytes()
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", str(toml), "utilizador", "senha", ANA, "--senha", NOVA])
    assert "não mexi" in str(e.value)
    assert toml.read_bytes() == antes                                          # a senha não mudou
    assert alvo.read_text(encoding="utf-8") == ALVO and alvo.stat().st_mtime_ns == mtime
    assert os.readlink(trinco) == str(alvo)                                    # e a ligação não foi substituída


def test_mudar_senha_com_o_trinco_ligado_a_outro_ficheiro_recusa_e_o_alvo_fica(srv):
    s = srv()
    cookie = s.entrar()
    alvo = _alvo(s.tmp)
    mtime = alvo.stat().st_mtime_ns
    _ligar_trinco(s.toml, alvo)
    antes = s.toml.read_bytes()
    st, r, cab = _mudar(s, cookie)
    assert st == 400 and "a antiga continua a valer" in r["erro"], (st, r)
    assert "set-cookie" not in cab
    assert s.toml.read_bytes() == antes
    assert alvo.read_text(encoding="utf-8") == ALVO and alvo.stat().st_mtime_ns == mtime
    assert config.carregar(s.toml, ambiente={}).utilizador(ANA).senha_hash      # a de antes continua lá
    assert s.entrar(senha=SENHA)


def test_mudar_senha_com_o_trinco_ligado_a_um_ficheiro_que_nao_existe_nao_o_cria(srv):
    s = srv()
    cookie = s.entrar()
    alvo = s.tmp / "outra" / "criado-pelo-trinco"
    alvo.parent.mkdir()
    _ligar_trinco(s.toml, alvo)
    antes = s.toml.read_bytes()
    st, r, _ = _mudar(s, cookie)
    assert not os.path.lexists(alvo), "o trinco seguiu a ligação e criou o ficheiro para onde ela aponta"
    assert st == 400, (st, r)
    assert s.toml.read_bytes() == antes
    assert s.entrar(senha=SENHA)
