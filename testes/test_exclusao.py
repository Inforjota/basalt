"""A lista de notas excluídas (`excluidas.txt`) calcula-se na 1.ª vez que alguém a pede. A página pede /api/notas, /api/nota e
/api/pacote quase ao mesmo tempo, em fios diferentes: nenhum pedido pode rebentar nem pisar a escrita do outro."""
import threading

import pytest

from basalt import web


class _Lenta(set):
    """o `IGNORA` do web, mas cada consulta demora um pouco: alarga a janela em que os fios se encontram a calcular a lista."""
    def __contains__(self, x):
        import time
        time.sleep(0.02)
        return super().__contains__(x)


def test_varios_fios_a_pedir_a_lista_de_excluidas_ao_mesmo_tempo(memoria, monkeypatch):
    for i in range(6):
        memoria.nota(f"n{i}", f"nota {i}", copia=False, alinhar=False)         # só na real = retida (excluída)
    memoria.nota("ok", "nota visível")
    monkeypatch.setattr(web, "IGNORA", _Lenta(web.IGNORA))
    assert not memoria.excluir.exists() and not web._excls
    n, arranque, erros, saidas, calculos = 3, threading.Barrier(3), [], [], []
    original = web.refazer_exclusao
    monkeypatch.setattr(web, "refazer_exclusao", lambda: (calculos.append(1), original())[1])

    def pedir():
        try:
            arranque.wait(5)
            saidas.append(set(web.excl()))
        except BaseException as e:           # noqa: BLE001 — é isso mesmo que o teste procura
            erros.append(repr(e))
    fios = [threading.Thread(target=pedir, daemon=True) for _ in range(n)]
    for f in fios:
        f.start()
    for f in fios:
        f.join(30)
    assert not erros, erros
    esperado = {f"n{i}" for i in range(6)}
    assert saidas == [esperado] * n
    assert len(calculos) == 1, "a lista devia calcular-se uma vez só; os outros fios reutilizam-na"
    assert set(memoria.excluir.read_text(encoding="utf-8").split()) == esperado
    assert not list(memoria.excluir.parent.glob("*.tmp")), "ficou um temporário por apagar"


def test_refazer_exclusao_em_fios_diferentes_nao_se_pisa(memoria, monkeypatch):
    # o vigia (de 5 em 5 min) e um pedido da página podem refazer a lista no mesmo instante
    for i in range(4):
        memoria.nota(f"n{i}", f"nota {i}", copia=False, alinhar=False)
    monkeypatch.setattr(web, "IGNORA", _Lenta(web.IGNORA))
    arranque, erros = threading.Barrier(3), []

    def refazer():
        try:
            arranque.wait(5)
            web.refazer_exclusao()
        except BaseException as e:           # noqa: BLE001
            erros.append(repr(e))
    fios = [threading.Thread(target=refazer, daemon=True) for _ in range(3)]
    for f in fios:
        f.start()
    for f in fios:
        f.join(30)
    assert not erros, erros
    assert set(memoria.excluir.read_text(encoding="utf-8").split()) == {f"n{i}" for i in range(4)}
