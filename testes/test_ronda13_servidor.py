"""Ronda 13 — servidor: refs com segredo, categorias das fontes externas, ambiente do Claude CLI, verificador e avisos.

Cada grupo diz o defeito que a revisão externa encontrou; os «controlo» são o comportamento que já estava certo e tem de ficar.
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FM, RAIZ


def _por_no_indice(esp, pedacos):
    """(fonte, ref, titulo, trecho) no índice do espaço, com o esquema que o basalt.sig cria."""
    from basalt import config, sig
    with config.dentro(esp):
        c = sig.db()
        c.executemany("INSERT OR REPLACE INTO pedaco (fonte, ref, titulo, trecho, hash, vec) VALUES (?, ?, ?, ?, 'h', x'')", pedacos)
        c.commit()
        c.close()


# ═════════════════════════ A — uma ref com segredo não abre pelos irmãos ═════════════════════════
REF_SECRETA = "doc#password: testeFicticio9876"


def test_ref_com_segredo_e_recusada_antes_de_juntar_os_irmaos(memoria):
    from basalt import sig
    _por_no_indice(memoria.esp, [("manual", "doc#0", "Manual", "parte limpa"), ("manual", REF_SECRETA, "Manual", "outra parte")])
    assert sig.ler_fonte("manual", REF_SECRETA) is None


def test_ref_com_segredo_o_comando_fonte_recusa_sem_a_repetir(memoria):
    _por_no_indice(memoria.esp, [("manual", "doc#0", "Manual", "parte limpa"), ("manual", REF_SECRETA, "Manual", "outra parte")])
    r = subprocess.run([sys.executable, "-m", "basalt.sig", "fonte", "manual", REF_SECRETA], cwd=RAIZ,
                       env={**os.environ, **memoria.ambiente()}, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "testeFicticio9876" not in r.stdout + r.stderr


def test_controlo_a_ref_limpa_continua_a_abrir_com_os_irmaos(memoria):
    from basalt import sig
    _por_no_indice(memoria.esp, [("manual", "doc#0", "Manual", "parte um"), ("manual", "doc#1", "Manual", "parte dois")])
    d = sig.ler_fonte("manual", "doc#0")
    assert d and [p["ref"] for p in d["pedacos"]] == ["doc#0", "doc#1"]
