"""r22 — extra pedido com os achados da r13b: a reindexação do fim do auto_arrumar (basalt/auto_arrumar.py).

Antes, um `basalt.sig indexar` que não acabava em 1800 s rebentava com TimeoutExpired ANTES do resumo — o que se ligou/uniu/
arquivou nessa noite ficava por dizer — e um que saía com erro passava calado. Agora avisa no stderr e o resumo sai na mesma,
com a nota da reindexação.

Cada teste de comportamento falha no 15beb01 por asserção (a excepção é apanhada no teste); o controlo passa nos dois.
"""
import subprocess

import pytest

from basalt import auto_arrumar
from test_ronda20_arquivar_factos import _notas

RESUMO = "Basalt arrumou a memória: 0 unidas, 0 arquivadas, 1 ligadas"


@pytest.fixture
def ligadas(memoria, monkeypatch):
    """uma noite com uma ligação gravada (commit falso: o que interessa é o que vem depois)."""
    _notas(memoria, "a", "b")
    monkeypatch.setattr(auto_arrumar, "SIMULAR", False)
    monkeypatch.setattr(auto_arrumar.arr, "duplicados", lambda: [(0.97, "a", "b")])
    monkeypatch.setattr(auto_arrumar, "decidir", lambda x, y: {"acao": "ligar", "manter": x, "confianca": 0.9, "motivo": "m"})
    monkeypatch.setattr(auto_arrumar, "ligar", lambda x, y: "c0ffee")
    return memoria


def _reindex(monkeypatch, fazer):
    """troca só a chamada ao `basalt.sig indexar`; o resto do subprocess fica o real."""
    real, chamadas = subprocess.run, []

    def run(args, *a, **k):
        if "basalt.sig" in list(args):
            chamadas.append(k.get("timeout"))
            return fazer(args, *a, **k)
        return real(args, *a, **k)
    monkeypatch.setattr(subprocess, "run", run)
    return chamadas


def _correr():
    try:
        auto_arrumar._correr()
    except Exception as e:                       # o código antigo deixa a excepção sair: aqui, a asserção diz o que faltou
        return e
    return None


def test_reindexacao_que_nao_acaba_avisa_e_o_resumo_sai_na_mesma(ligadas, monkeypatch, capsys):
    def lenta(args, *a, **k):
        raise subprocess.TimeoutExpired(args, k.get("timeout"))
    chamadas = _reindex(monkeypatch, lenta)
    erro = _correr()
    s = capsys.readouterr()
    assert chamadas == [1800]
    assert RESUMO in s.out, ("o resumo da noite não saiu", s.out, erro)
    assert "reindexação não acabou em 1800 s" in s.err and "reindexação não acabou" in s.out, (s.err, s.out)


def test_reindexacao_com_erro_ja_nao_passa_calada(ligadas, monkeypatch, capsys):
    def partida(args, *a, **k):
        return subprocess.CompletedProcess(args, 1, b"", b"Traceback ...\nsqlite3.OperationalError: database is locked\n")
    _reindex(monkeypatch, partida)
    erro = _correr()
    s = capsys.readouterr()
    assert RESUMO in s.out, (s.out, erro)
    assert "reindexação saiu com o código 1" in s.err and "database is locked" in s.err, s.err
    assert "reindexação saiu com o código 1" in s.out, s.out


def test_reindexacao_que_nao_arranca_avisa_e_o_resumo_sai(ligadas, monkeypatch, capsys):
    def sem_python(args, *a, **k):
        raise PermissionError(13, "Permission denied", args[0])
    _reindex(monkeypatch, sem_python)
    erro = _correr()
    s = capsys.readouterr()
    assert RESUMO in s.out and "reindexação não arrancou (PermissionError)" in s.err, (s.out, s.err, erro)


def test_controlo_reindexacao_bem_sucedida_resumo_sem_aviso(ligadas, monkeypatch, capsys):
    chamadas = _reindex(monkeypatch, lambda args, *a, **k: subprocess.CompletedProcess(args, 0, b"", b""))
    assert _correr() is None
    s = capsys.readouterr()
    assert chamadas == [1800] and RESUMO in s.out and "reindexação" not in s.out + s.err, (s.out, s.err)


def test_reindexacao_que_falha_sai_com_codigo_3(ligadas, monkeypatch, capsys):
    # REINDEX_RC_v1: o resumo sai na mesma, mas o main devolve 3 para o cron/timer marcar a noite como falhada
    _reindex(monkeypatch, lambda args, *a, **k: subprocess.CompletedProcess(args, 1, b"", b"x\n"))
    monkeypatch.setattr(auto_arrumar.config, "definir_processo", lambda *a, **k: None)
    monkeypatch.setattr(auto_arrumar._ia, "actual", lambda *a, **k: type("I", (), {"chat_activo": True})())
    assert auto_arrumar.main([]) == 3
    assert RESUMO in capsys.readouterr().out


def test_controlo_reindexacao_bem_sucedida_sai_com_0(ligadas, monkeypatch, capsys):
    _reindex(monkeypatch, lambda args, *a, **k: subprocess.CompletedProcess(args, 0, b"", b""))
    monkeypatch.setattr(auto_arrumar.config, "definir_processo", lambda *a, **k: None)
    monkeypatch.setattr(auto_arrumar._ia, "actual", lambda *a, **k: type("I", (), {"chat_activo": True})())
    assert auto_arrumar.main([]) is None
    assert RESUMO in capsys.readouterr().out
