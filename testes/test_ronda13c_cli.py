"""Ronda 13c — `basalt utilizador senha`/`novo` cortam as ligações das IAs na base ANTES de gravar o basalt.toml (ronda 13b).

Se a gravação falhar DEPOIS do corte (o ficheiro novo não carrega, o disco cheio, a pasta sem permissão), o corte já está feito
e fica — é a direcção segura — mas o comando dizia só «não mexi no basalt.toml», como se nada tivesse acontecido: quem o correu
não sabia que as IAs da conta tinham ficado de fora. Agora diz também que as ligações ficaram cortadas e que o resto não mudou.
"""
import pytest

from basalt import __main__ as cli, auth
from test_mudar_senha import NOVA
from test_oauth import ANA, Srv, tokens
from test_ronda13b_oauth import _pessoal_senha, _vivas


def _gravacao_que_falha(caminho, texto):
    raise OSError(28, "No space left on device")


def test_cli_senha_com_a_gravacao_a_falhar_depois_do_corte_diz_que_as_ligacoes_ficaram_cortadas(tmp_path, monkeypatch):
    monkeypatch.delenv("BASALT_DADOS", raising=False)
    a = Srv(tmp_path)
    try:
        tokens(a)
        _pessoal_senha(a, tokens(a)[2])
    finally:
        a.parar()
    assert _vivas(a, ANA) >= 2
    antes = a.toml.read_bytes()
    monkeypatch.setattr(auth, "gravar_toml", _gravacao_que_falha)
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", str(a.toml), "utilizador", "senha", ANA, "--senha", NOVA])
    msg = str(e.value)
    assert "não mexi" in msg and "cortadas" in msg and ANA in msg, msg
    assert a.toml.read_bytes() == antes                                         # a senha não mudou…
    assert _vivas(a, ANA) == 0                                                  # …mas o corte das IAs ficou (e o aviso di-lo)


def test_controlo_mcp_token_com_a_gravacao_a_falhar_nao_fala_de_ligacoes_cortadas(tmp_path, monkeypatch):
    """`mcp-token` não corta ligações — a mensagem não pode dizer que cortou."""
    monkeypatch.delenv("BASALT_DADOS", raising=False)
    a = Srv(tmp_path)
    try:
        tokens(a)
    finally:
        a.parar()
    vivas = _vivas(a, ANA)
    antes = a.toml.read_bytes()
    monkeypatch.setattr(auth, "gravar_toml", _gravacao_que_falha)
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", str(a.toml), "utilizador", "mcp-token", ANA])
    msg = str(e.value)
    assert "não mexi" in msg and "cortadas" not in msg, msg
    assert a.toml.read_bytes() == antes and _vivas(a, ANA) == vivas
