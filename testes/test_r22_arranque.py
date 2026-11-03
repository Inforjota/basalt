"""r22 — achado R6 (menor) da revisão externa da r13b (Astra, 23/10): uma excepção no fio do arranque (`web._arranque`), fora dos
blocos protegidos, matava o arranque dos vigias com o HTTP vivo — sem sincronização periódica, sem refresco das notas retidas,
sem Entrada. Reprodução do Astra: BASALT_VIGIA_SEGUNDOS=10s → ValueError → nenhum dos 4 fios arrancava. Agora o valor inválido
avisa e fica nos 10 s; outra falha dá um ALERTA «arranque incompleto» e os vigias arrancam na mesma.

Cada teste de comportamento falha no 15beb01 por asserção (a excepção do arranque é apanhada no teste); o controlo passa nos dois.
"""
import threading

from basalt import duasvias, web
from test_r14_exclusao import _cfg_de

NOMES = ("vigia_copia", "vigia_exclusao", "indexar_se_vazio", "vigia_entrada")


def _vigias_falsos(monkeypatch):
    """troca os 4 vigias por gravadores: {nome: args} de quem arrancou."""
    arrancaram, todos = {}, threading.Event()
    trinco = threading.Lock()
    for nome in NOMES:
        def gravar(*a, _nome=nome, **k):
            with trinco:
                arrancaram[_nome] = a
                if len(arrancaram) == len(NOMES):
                    todos.set()
        monkeypatch.setattr(web, nome, gravar)
    return arrancaram, todos


def _alertas(monkeypatch):
    ditos = []
    monkeypatch.setattr(duasvias, "alerta", lambda m: ditos.append(str(m)))
    return ditos


def _arrancar(memoria):
    try:
        web._arranque(_cfg_de(memoria), True)
    except Exception as e:                       # o código antigo deixa a excepção sair do fio: aqui, a asserção diz o que faltou
        return e
    return None


def test_vigia_segundos_invalido_nao_impede_os_vigias(memoria, monkeypatch, capfd):
    monkeypatch.setattr(duasvias, "ciclo", lambda: None)
    monkeypatch.setenv("BASALT_VIGIA_SEGUNDOS", "10s")
    arrancaram, todos = _vigias_falsos(monkeypatch)
    erro = _arrancar(memoria)
    todos.wait(10)
    assert set(arrancaram) == set(NOMES), ("com BASALT_VIGIA_SEGUNDOS=10s, os vigias não arrancaram", sorted(arrancaram), erro)
    assert arrancaram["vigia_copia"][1] == 10
    assert "BASALT_VIGIA_SEGUNDOS" in capfd.readouterr().err


def test_falha_inesperada_no_arranque_avisa_e_os_vigias_arrancam(memoria, monkeypatch):
    def partido(cfg):
        raise RuntimeError("espaços ilegíveis")
    monkeypatch.setattr(web, "_espacos", partido)
    ditos = _alertas(monkeypatch)
    arrancaram, todos = _vigias_falsos(monkeypatch)
    erro = _arrancar(memoria)
    todos.wait(10)
    assert set(arrancaram) == set(NOMES), ("uma falha no arranque deixou os vigias por arrancar", sorted(arrancaram), erro)
    assert arrancaram["vigia_exclusao"][1] is True, "a exclusão tem de repetir cedo, como depois de um verificador falhado"
    assert any("arranque incompleto" in m for m in ditos), ditos


def test_controlo_arranque_normal_os_quatro_vigias_sem_alerta(memoria, monkeypatch):
    monkeypatch.setattr(duasvias, "ciclo", lambda: None)
    monkeypatch.setenv("BASALT_VIGIA_SEGUNDOS", "7")
    ditos = _alertas(monkeypatch)
    arrancaram, todos = _vigias_falsos(monkeypatch)
    assert _arrancar(memoria) is None
    assert todos.wait(10)
    assert set(arrancaram) == set(NOMES) and arrancaram["vigia_copia"][1] == 7
    assert not [m for m in ditos if "arranque incompleto" in m], ditos
