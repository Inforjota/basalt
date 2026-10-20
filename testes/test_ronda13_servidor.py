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


# ═════════════════════════ B — o `claude` do fornecedor claude-cli não herda as chaves do processo ═════════════════════════
CHAVE_FICTICIA = "sk-ant-api03-" + "x" * 40


def _claude_falso(tmp_path, monkeypatch):
    """um `claude` no PATH que responde OK e guarda o ambiente que recebeu."""
    pasta = tmp_path / "bin"
    pasta.mkdir()
    saida = tmp_path / "ambiente.json"
    p = pasta / "claude"
    p.write_text(f"#!{sys.executable}\nimport json, os, sys\nsys.stdin.read()\n"
                 f"open({str(saida)!r}, 'w').write(json.dumps(dict(os.environ)))\n"
                 "print(json.dumps({'result': 'OK', 'is_error': False}))\n", encoding="utf-8")
    p.chmod(0o755)
    monkeypatch.setenv("PATH", f"{pasta}{os.pathsep}{os.environ.get('PATH', '')}")
    return saida


def test_claude_cli_nao_recebe_as_chaves_do_processo(tmp_path, monkeypatch):
    from basalt import config, ia
    saida = _claude_falso(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", CHAVE_FICTICIA)
    monkeypatch.setenv("BASALT_IA_CHAVE", CHAVE_FICTICIA)
    monkeypatch.setenv("OPENAI_API_KEY", CHAVE_FICTICIA)
    geral = config.IAConfig(fornecedor="anthropic", url="http://127.0.0.1:9", chave_env="ANTHROPIC_API_KEY",
                            tarefas={"unir": {"fornecedor": "claude-cli", "modelo_chat": "sonnet"}})
    assert ia.IA(geral.para("unir")).chat("ola") == "OK"
    env = json.loads(saida.read_text())
    assert CHAVE_FICTICIA not in env.values(), sorted(k for k, v in env.items() if v == CHAVE_FICTICIA)
    assert env.get("PATH") and env.get("HOME")                       # o que o CLI precisa para correr continua lá


def test_claude_cli_recebe_so_a_chave_que_a_tarefa_declara(tmp_path, monkeypatch):
    from basalt import config, ia
    saida = _claude_falso(tmp_path, monkeypatch)
    monkeypatch.setenv("CHAVE_DA_TAREFA", "chave-ficticia-da-tarefa")
    monkeypatch.setenv("ANTHROPIC_API_KEY", CHAVE_FICTICIA)
    geral = config.IAConfig(fornecedor="nenhum", tarefas={"unir": {"fornecedor": "claude-cli", "chave_env": "CHAVE_DA_TAREFA"}})
    ia.IA(geral.para("unir")).chat("ola")
    env = json.loads(saida.read_text())
    assert env.get("ANTHROPIC_API_KEY") == "chave-ficticia-da-tarefa" and "CHAVE_DA_TAREFA" not in env
