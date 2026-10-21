"""r14 — da auditoria do Fable à r13b (20/10): a lista das notas retidas (`refazer_exclusao`) no arranque e no vigia.

(a) O arranque (num fio desde a ronda 13b) e o vigia calculavam a lista FORA do trinco que o `excl()` usa: um pedido da página
    chegado a meio fazia uma 2.ª computação igual (outra ida ao [segredos] verificador) e ganhava a que acabasse por último —
    a mais antiga podia tapar a mais recente. Agora as três passam pelo mesmo trinco do espaço: uma computação de cada vez.
(b) Depois de um arranque com o verificador falhado, a cache dele está vazia e TODAS as notas ficam retidas (falha fechada) até
    à volta seguinte do vigia — 300 s. Agora, enquanto o verificador continuar a falhar, o vigia repete ao fim de 60 s (no
    máximo 5 vezes; depois volta aos 300 s, para não chamar um verificador partido de minuto a minuto para sempre).
"""
import threading
import time
from types import SimpleNamespace

import pytest

from basalt import config, web
from conftest import FM
from test_ronda13_servidor import verificador  # noqa: F401 (a fixture)


class _Fim(BaseException):
    """sai do ciclo infinito do vigia (BaseException: o vigia só apanha Exception)."""


class _Relogio:
    """o `time` do módulo web, com o sleep trocado: regista quanto o vigia ia dormir e pára-o à n-ésima vez."""

    def __init__(self, parar_em, ao_dormir=None):
        self.sonos, self.parar_em, self.ao_dormir = [], parar_em, ao_dormir

    def sleep(self, s):
        self.sonos.append(s)
        if self.ao_dormir:
            self.ao_dormir(len(self.sonos))
        if len(self.sonos) >= self.parar_em:
            raise _Fim()

    def __getattr__(self, nome):
        return getattr(time, nome)


def _cfg_de(memoria):
    """o bastante de um Config para o `_espacos`: sem utilizadores → o espaço por omissão, que é o da memória de teste."""
    return SimpleNamespace(utilizadores=[], espaco=lambda nome: memoria.esp)


def _sem_outros_vigias(monkeypatch):
    for nome in ("vigia_copia", "indexar_se_vazio", "vigia_entrada"):
        monkeypatch.setattr(web, nome, lambda *a, **k: None)


class _VerificadorLento:
    """o `retidos` do web, lento e a contar quantos o estão a correr ao mesmo tempo."""

    def __init__(self):
        self.dentro, self.maximo, self.chamadas = 0, 0, 0
        self.entrou, self._g = threading.Event(), threading.Lock()

    def __call__(self, textos, *a, **k):
        with self._g:
            self.dentro += 1
            self.chamadas += 1
            self.maximo = max(self.maximo, self.dentro)
        self.entrou.set()
        time.sleep(0.5)
        with self._g:
            self.dentro -= 1
        return [False] * len(textos)


def _correr_com_um_pedido_a_meio(alvo, lento):
    """corre `alvo` num fio e, quando ele estiver dentro do verificador, um pedido da página pede a lista (`excl()`)."""
    erros, saida = [], []

    def a():
        try:
            alvo()
        except _Fim:
            pass
        except BaseException as e:          # noqa: BLE001
            erros.append(repr(e))

    def pedido():
        try:
            assert lento.entrou.wait(10), "o cálculo da lista nunca chegou ao verificador"
            saida.append(set(web.excl()))
        except BaseException as e:          # noqa: BLE001
            erros.append(repr(e))
    fios = [threading.Thread(target=a, daemon=True), threading.Thread(target=pedido, daemon=True)]
    for f in fios:
        f.start()
    for f in fios:
        f.join(30)
    assert not erros, erros
    return saida


@pytest.fixture
def com_notas(memoria, monkeypatch):
    memoria.nota("vista", FM.format(nome="vista") + "\nO barco sai às 8h.\n")
    memoria.nota("retida", FM.format(nome="retida") + "\nsó na real\n", copia=False, alinhar=False)
    monkeypatch.setattr(web, "verificador_ligado", lambda: True)
    lento = _VerificadorLento()
    monkeypatch.setattr(web, "retidos", lento)
    _sem_outros_vigias(monkeypatch)
    return lento


# ───────────────────────── (a) arranque/vigia e a página: uma computação de cada vez ─────────────────────────
def test_a_arranque_e_um_pedido_da_pagina_nao_calculam_a_lista_ao_mesmo_tempo(memoria, com_notas, monkeypatch):
    monkeypatch.setattr(web, "vigia_exclusao", lambda *a, **k: None)
    saida = _correr_com_um_pedido_a_meio(lambda: web._arranque(_cfg_de(memoria), False), com_notas)
    assert com_notas.maximo == 1, "o pedido da página calculou a lista ao mesmo tempo que o arranque"
    assert com_notas.chamadas == 1, f"{com_notas.chamadas} computações: o pedido devia reutilizar a do arranque"
    assert saida == [{"retida"}]


def test_a_vigia_e_um_pedido_da_pagina_nao_calculam_a_lista_ao_mesmo_tempo(memoria, com_notas, monkeypatch):
    monkeypatch.setattr(web, "time", _Relogio(parar_em=1))
    saida = _correr_com_um_pedido_a_meio(lambda: web.vigia_exclusao(_cfg_de(memoria)), com_notas)
    assert com_notas.maximo == 1, "o pedido da página calculou a lista ao mesmo tempo que o vigia"
    assert com_notas.chamadas == 1, f"{com_notas.chamadas} computações: o pedido devia reutilizar a do vigia"
    assert saida == [{"retida"}]


# ───────────────────────── (b) arranque com o verificador falhado: repetir ao fim de 60 s ─────────────────────────
PARTIDA = FM.format(nome="partida") + "\nEsta faz o verificador falhar: MARCA-FALHA.\n"
LIMPA = FM.format(nome="partida") + "\nJá não faz falhar nada.\n"


def _arrancar_e_vigiar(memoria, monkeypatch, relogio):
    """o `_arranque` a sério (sem os outros vigias) e o vigia da exclusão que ele lança, até o relógio o parar."""
    monkeypatch.setattr(web, "time", relogio)
    original, feito = web.vigia_exclusao, threading.Event()

    def vigia(*a, **k):
        try:
            original(*a, **k)
        except _Fim:
            pass
        finally:
            feito.set()
    monkeypatch.setattr(web, "vigia_exclusao", vigia)
    _sem_outros_vigias(monkeypatch)
    web._arranque(_cfg_de(memoria), False)
    assert feito.wait(60), "o vigia da exclusão não acabou"


def test_b_depois_de_um_arranque_com_o_verificador_falhado_repete_ao_fim_de_60_s(memoria, verificador, monkeypatch):
    from basalt import segredos
    monkeypatch.setattr(segredos, "_alertar", lambda porque: None)
    verificador.ligar()
    memoria.nota("partida", PARTIDA)

    def corrigir(n):                                       # o verificador volta a responder depois da 1.ª espera
        if n == 1:
            memoria.nota("partida", LIMPA)
    relogio = _Relogio(parar_em=2, ao_dormir=corrigir)
    _arrancar_e_vigiar(memoria, monkeypatch, relogio)
    assert relogio.sonos == [60, 300], relogio.sonos      # antes: [300, 300] — todas retidas durante 5 minutos
    with config.dentro(memoria.esp):
        assert "partida" not in web.excl()


def test_b_com_o_verificador_sempre_partido_as_repeticoes_rapidas_tem_fim(memoria, verificador, monkeypatch):
    from basalt import segredos
    monkeypatch.setattr(segredos, "_alertar", lambda porque: None)
    verificador.ligar()
    memoria.nota("partida", PARTIDA)
    relogio = _Relogio(parar_em=7)
    _arrancar_e_vigiar(memoria, monkeypatch, relogio)
    assert relogio.sonos == [60] * 5 + [300, 300], relogio.sonos
    with config.dentro(memoria.esp):
        assert "partida" in web.excl()                     # continua retida (falha fechada)


def test_b_controlo_arranque_com_o_verificador_bom_espera_os_300_s_de_sempre(memoria, verificador, monkeypatch):
    verificador.ligar()
    memoria.nota("limpa", FM.format(nome="limpa") + "\nO barco sai às 8h.\n")
    relogio = _Relogio(parar_em=2)
    _arrancar_e_vigiar(memoria, monkeypatch, relogio)
    assert relogio.sonos == [300, 300], relogio.sonos
