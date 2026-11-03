"""Ronda 22 (LIGACAO_MUDA_v1): uma ligação que abre e não manda nada — ou manda meio pedido — não pode prender um fio do servidor
para sempre. O handler tem `timeout` (60 s; aqui 1 s): o servidor fecha-a. Controlo: um pedido normal continua a ser servido."""
import socket
import time

import pytest

from basalt import web

EMAIL = "ana@exemplo.pt"


def test_handler_tem_timeout_por_omissao():
    """sem o r22 o atributo vem do StreamRequestHandler com None (espera para sempre); os outros testes baixam-no para 1 s."""
    t = web.H.timeout
    assert t is not None and 10 <= t <= 300, f"timeout do handler = {t!r}"


@pytest.fixture
def s(servidor_local, monkeypatch):
    monkeypatch.setattr(web.H, "timeout", 1)
    return servidor_local(modo="cabecalho", utilizadores=[{"email": EMAIL, "espaco": "a"}])


def _fechada_em(porta, enviar=b"", espera=6):
    """segundos até o servidor fechar a ligação; None se não a fechou em `espera` s."""
    c = socket.create_connection(("127.0.0.1", porta))
    c.settimeout(espera)
    t = time.time()
    try:
        if enviar:
            c.sendall(enviar)
        while c.recv(4096):
            pass
        return time.time() - t
    except socket.timeout:
        return None
    finally:
        c.close()


def test_ligacao_que_nao_manda_nada_e_fechada(s):
    d = _fechada_em(s.porta)
    assert d is not None, "o servidor ficou à espera de uma ligação muda (fio preso)"
    assert d < 4


def test_meio_pedido_e_fechado(s):
    d = _fechada_em(s.porta, b"GET /api/eu HTTP/1.1\r\nHost: x\r\n")
    assert d is not None, "meio pedido prendeu o fio"


def test_pedido_normal_continua_a_ser_servido(s):
    """controlo: com o timeout curto, um pedido completo responde na mesma (e com uma ligação muda aberta ao lado)."""
    muda = socket.create_connection(("127.0.0.1", s.porta))
    try:
        r = s.get("/api/eu", email=EMAIL)
        assert r.codigo == 200, r.codigo
    finally:
        muda.close()
