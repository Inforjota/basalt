"""r17 — o `apagados eliminar` a cruzar-se com um apagamento, a pasta que não saiu do sítio e o nome de espaço «apagados».

Três maneiras de a promessa dos 30 dias falhar EM SILÊNCIO (a entrada dizia «eliminada» e os dados ficavam):
1. o timer corre `eliminar --sim` no instante em que um `utilizador apagar` / `reaplicar --sim` já registou a entrada (com a pasta
   decidida) mas ainda não moveu a pasta: o eliminar via «a pasta já não existia», marcava a entrada eliminada e a pasta chegava
   a seguir — ficava em apagados/ «sem registo» para sempre;
2. a pasta não saiu do sítio ao apagar (outro disco, permissões): ficava em <dados>/<espaco> e, aos 30 dias, a entrada era dada
   por eliminada na mesma;
3. um espaço chamado «apagados» (o e-mail apagados@… dá esse nome): a pasta tentava mover-se para dentro de si própria."""
import json
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from basalt import __main__ as cli, apagados, config  # noqa: E402
from test_r17_apagados import ANA, BEA, UM_DIA, Inst, aos, i, ultima  # noqa: E402,F401 (i: a instância de teste)


def _correr(inst, rcs, nome, *args):
    try:
        cli.main(["--config", str(inst.toml), *args])
        rcs[nome] = 0
    except SystemExit as x:
        rcs[nome] = 0 if x.code is None else x.code


# ───────────────────────────── 1. eliminar × apagar ao mesmo tempo ─────────────────────────────
def test_eliminar_a_correr_durante_um_apagamento_nao_deixa_a_pasta_sem_registo(i, monkeypatch, tmp_path):
    """uma cópia reposta trouxe a Bea de volta; o `reaplicar --sim` apaga-a outra vez com a data original (há 40 dias) e, nesse
    instante, o timer corre `eliminar --sim`. A entrada fica eliminada E a pasta desaparece — nunca «eliminada» com a pasta a
    chegar a seguir."""
    quando = apagados.carimbo(time.time() - 40 * UM_DIA)
    lista = tmp_path / "lista.jsonl"
    lista.write_text(json.dumps({"email_sha256": apagados.email_sha256(BEA), "conta_id": "c-bea", "apagado_em": quando}) + "\n")
    registada, pode_mover = threading.Event(), threading.Event()
    mover = shutil.move

    def move_devagar(origem, destino):
        registada.set()                 # a entrada já está no registo com a pasta decidida; o basalt.toml já não tem a conta
        assert pode_mover.wait(20)
        return mover(origem, destino)

    monkeypatch.setattr(cli.shutil, "move", move_devagar)
    rcs = {}
    a = threading.Thread(target=_correr, args=(i, rcs, "reaplicar", "apagados", "reaplicar", "--lista", str(lista), "--sim"))
    a.start()
    assert registada.wait(20)
    b = threading.Thread(target=_correr, args=(i, rcs, "eliminar", "apagados", "eliminar", "--sim"))
    b.start()
    b.join(1.5)                         # com o trinco bem posto, o eliminar fica aqui à espera de o apagamento acabar
    pode_mover.set()
    a.join(20)
    b.join(20)
    assert not a.is_alive() and not b.is_alive()
    assert rcs == {"reaplicar": 0, "eliminar": 0}, rcs
    e = i.entrada(BEA)
    pastas = sorted(p.name for p in (i.dados / "apagados").iterdir())
    assert e["eliminado_em"] and e["pasta"] is None and pastas == [], (e, pastas)
    assert apagados._sem_registo(i.dados, i.registo()) == []


# ───────────────────────────── 2. a pasta não saiu do sítio ─────────────────────────────
def test_a_pasta_que_nao_saiu_do_sitio_nao_se_da_por_eliminada(i, monkeypatch):
    mover = shutil.move

    def falha(origem, destino):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(cli.shutil, "move", falha)
    rc, out, err = i.cli("utilizador", "apagar", BEA, "--sim")
    assert rc != 0 and "move-a à mão" in err, err
    e = i.entrada(BEA)
    assert i.cfg().utilizador(BEA) is None and e["pasta"] and not (i.dados / e["pasta"]).exists()
    monkeypatch.setattr(cli.shutil, "move", mover)
    aos(monkeypatch, time.time() + 31 * UM_DIA)
    rc, out, err = i.cli("apagados", "eliminar", "--sim")
    assert rc == 1 and ultima(out) == "resumo: eliminadas=0 falhas=1 voltaram=0", (out, err)
    assert "continua em" in out and str(i.dados / "b") in out and BEA not in out + err
    assert i.entrada(BEA)["eliminado_em"] is None
    assert (i.dados / "b" / "memoria" / "nota.md").read_text(encoding="utf-8") == "nota do espaço b\n"
    # quem administra move-a para onde a entrada diz → no dia seguinte elimina-se
    shutil.move(str(i.dados / "b"), str(i.dados / e["pasta"]))
    rc, out, err = i.cli("apagados", "eliminar", "--sim")
    assert rc == 0 and ultima(out) == "resumo: eliminadas=1 falhas=0 voltaram=0", (out, err)
    assert i.entrada(BEA)["eliminado_em"] and not (i.dados / e["pasta"]).exists() and not (i.dados / "b").exists()


def test_o_espaco_reutilizado_por_uma_conta_nova_nao_trava_o_eliminar(i, monkeypatch):
    """a pasta não saiu, mas entretanto uma conta nova (o mesmo e-mail dá o mesmo nome de espaço) está a usar <dados>/b: é dela,
    não é «a pasta que não saiu» — a entrada antiga dá-se por eliminada (a pasta já não existia) e não se lhe mexe."""
    mover = shutil.move

    def falha(origem, destino):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(cli.shutil, "move", falha)
    rc, out, err = i.cli("utilizador", "apagar", BEA, "--sim")
    assert rc != 0, err
    monkeypatch.setattr(cli.shutil, "move", mover)
    rc, out, err = i.cli("utilizador", "novo", BEA, "--senha", "uma-senha-comprida", "--espaco", "b")
    assert rc == 0 and i.cfg().utilizador(BEA).espaco == "b", err
    aos(monkeypatch, time.time() + 31 * UM_DIA)
    rc, out, err = i.cli("apagados", "eliminar", "--sim")
    assert rc == 0 and ultima(out) == "resumo: eliminadas=1 falhas=0 voltaram=0", (out, err)
    assert "a pasta já não existia" in out and (i.dados / "b" / "memoria" / "nota.md").exists()


# ───────────────────────────── 3. «apagados» não é nome de espaço ─────────────────────────────
def test_o_nome_de_espaco_apagados_e_reservado(tmp_path, capsys):
    assert apagados.PASTA in config.ESPACOS_RESERVADOS
    with pytest.raises(config.ConfigInvalida, match="reservado"):
        Inst(tmp_path, capsys, [{"email": ANA, "conta_id": "c-ana", "espaco": "a", "admin": True},
                                {"email": "apagados@exemplo.pt", "conta_id": "c-x", "espaco": "apagados"}])
    (tmp_path / "ok").mkdir()
    i = Inst(tmp_path / "ok", capsys)
    assert cli._espaco_para(i.cfg(), "apagados@exemplo.pt", None) == "apagados_2"
    rc, out, err = i.cli("utilizador", "novo", "x@exemplo.pt", "--senha", "uma-senha-comprida", "--espaco", "apagados")
    assert rc != 0 and "reservado" in err and i.cfg().utilizador("x@exemplo.pt") is None, err
    rc, out, err = i.cli("utilizador", "novo", "apagados@exemplo.pt", "--senha", "uma-senha-comprida")
    assert rc == 0 and i.cfg().utilizador("apagados@exemplo.pt").espaco == "apagados_2", err
    assert not (i.dados / "apagados").exists() or not (i.dados / "apagados" / "memoria").exists()
