"""Os autotestes que os próprios módulos trazem (`python -m basalt.X --teste`), postos a correr pelo pytest.
Correm em processos à parte, no espaço temporário da fixture `memoria` (nunca em dados reais)."""
import subprocess
import sys

import pytest

from basalt import config


def correr(memoria, *args):
    return subprocess.run([sys.executable, "-m", *args], capture_output=True, text=True, timeout=300,
                          env=config.ambiente_filho(memoria.esp), cwd=str(memoria.base))


def test_autoteste_do_unir_passa(memoria):
    r = correr(memoria, "basalt.unir", "--teste")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "unir autoteste: OK" in r.stdout


def test_autoteste_do_vigia_passa(memoria):
    r = correr(memoria, "basalt.duasvias", "--teste")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "basalt.duasvias autoteste: OK" in r.stdout


def test_autoteste_do_sig_passa(memoria):
    r = correr(memoria, "basalt.sig", "--autoteste")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "basalt.sig autoteste: OK" in r.stdout


def test_os_autotestes_nao_tocam_na_memoria_do_espaco(memoria):
    memoria.nota("a", "a0")
    antes = sorted((q.name, q.read_bytes()) for q in memoria.real.iterdir()), sorted((q.name, q.read_bytes()) for q in memoria.copia.iterdir())
    for args in (("basalt.unir", "--teste"), ("basalt.duasvias", "--teste"), ("basalt.sig", "--autoteste")):
        assert correr(memoria, *args).returncode == 0
    depois = sorted((q.name, q.read_bytes()) for q in memoria.real.iterdir()), sorted((q.name, q.read_bytes()) for q in memoria.copia.iterdir())
    assert depois == antes
