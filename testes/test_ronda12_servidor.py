"""Ronda 12 («actualizar sem perder nada») — o lado do servidor.

  · B — `python -m basalt.categorias` reescrevia o categorias.json só com as notas do espaço: as chaves das fontes externas
    (`<fonte>:<ref>`, de [fontes_extra] ou de uma instalação anterior) caíam a cada volta. Agora ficam enquanto a fonte as tiver
    no índice (ou, sem índice, sempre); só sai a chave de uma nota do espaço que já não existe.
  · C — IA por tarefa: [ia.unir] e [ia.arrumar] sobrepõem fornecedor, url, modelo_chat, chave e tempo_limite de [ia]
    (o resto vem de [ia]; os limites por hora continuam os de [ia]). O botão Unir e o `arrumar --auto` usam a da tarefa;
    o `estado` e o /api/eu dizem qual está em uso.
  · E — `python -m basalt entrada "texto" [--de FONTE]`: guardar na Entrada pela linha de comandos com o MESMO caminho e trinco
    da página (segredos recusados com código de saída ≠ 0, aviso para fontes que não são de confiança). Um programa de fora que
    escreva directamente no entrada.jsonl perde linhas numa corrida com a página; pelo comando não perde.
Cada teste de comportamento falha no código anterior a esta ronda (aa346d2) por asserção e pela razão certa (menos os controlos,
marcados como tal)."""
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from basalt import auto_arrumar, categorias, config, entrada, ia, unir, web
from basalt.config import ConfigInvalida, IAConfig
from basalt.segredos import MOTIVO
from conftest import FM, toml_de, usar_ia
from ollama_falso import OllamaFalso
from test_unir import A, B, BOA

RAIZ = Path(__file__).resolve().parent.parent
ANA = "ana@exemplo.pt"
T = "2026-10-08T10:00:00"
LIXO = json.dumps({"acao": "lixo", "nota": "", "confianca": 0.95, "motivo": "só uma saudação"})


def _env_limpo(tmp_path):
    """o ambiente de um subprocesso sem nenhuma variável BASALT_* (só a --config manda), com HOME temporário."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("BASALT_") or k in ("BASALT_SIG_ISOLADO", "BASALT_CAS_GRACA")}
    env["HOME"] = str(tmp_path)
    return env


def _basalt(tmp_path, toml, *args, entrada_txt=None):
    r = subprocess.run([sys.executable, "-m", "basalt", "--config", str(toml), *args], cwd=RAIZ, env=_env_limpo(tmp_path),
                       input=entrada_txt, capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout, r.stderr


def _toml(tmp_path, ollama=None, extra=""):
    p = tmp_path / "basalt.toml"
    p.write_text(toml_de(0, tmp_path / "dados", "aberto", ollama, [{"email": ANA, "espaco": "t", "admin": True}], extra), encoding="utf-8")
    return p


def _git(real, *args):
    return subprocess.run(["git", "-C", str(real), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout


# ═════════════════════════ B — as categorias das fontes externas não se perdem ═════════════════════════
def _cats(memoria, chaves):
    memoria.esp.categorias.parent.mkdir(parents=True, exist_ok=True)
    memoria.esp.categorias.write_text(json.dumps({k: {"cats": ["Trabalho"], "hash": "x"} for k in chaves}), encoding="utf-8")


def _cats_lidas(memoria):
    return json.loads(memoria.esp.categorias.read_text(encoding="utf-8"))


def _indice(memoria, pedacos):
    """um índice com estes (fonte, ref) — o mesmo esquema que o basalt.sig cria."""
    memoria.esp.semantica.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(memoria.esp.semantica)
    c.execute("CREATE TABLE IF NOT EXISTS pedaco (fonte TEXT, ref TEXT, titulo TEXT, trecho TEXT, hash TEXT, "
              "vec BLOB, atualizado TEXT DEFAULT (datetime('now')), PRIMARY KEY (fonte, ref))")
    c.executemany("INSERT INTO pedaco (fonte, ref, titulo, trecho, hash) VALUES (?, ?, ?, ?, ?)",
                  [(f, r, r, "texto", "h") for f, r in pedacos])
    c.commit(); c.close()


@pytest.fixture
def com_notas(memoria):
    usar_ia(fornecedor="nenhum")                 # só as regras pelo nome: nada vai à IA
    memoria.nota("nota_viva", FM.format(nome="nota_viva") + "\nainda cá está\n")
    return memoria


def test_sem_indice_as_chaves_das_fontes_externas_ficam(com_notas):
    _cats(com_notas, ["nota_viva", "outro_projecto:feedback_x", "agenda:decisao-1"])
    categorias.main()
    d = _cats_lidas(com_notas)
    assert {"outro_projecto:feedback_x", "agenda:decisao-1"} <= set(d), f"as categorias das fontes externas caíram: {sorted(d)}"
    assert d["agenda:decisao-1"]["cats"] == ["Trabalho"]          # ficam como estavam (não se recategorizam)


def test_com_indice_fica_o_que_a_fonte_ainda_tem_e_sai_o_que_ela_deixou_de_ter(com_notas):
    _indice(com_notas, [("nota", "nota_viva#0"), ("agenda", "decisao-1"), ("agenda", "decisao-2#0"), ("diario", "diario:2026-10-01#1")])
    _cats(com_notas, ["nota_viva", "agenda:decisao-1", "agenda:decisao-2", "agenda:decisao-9", "diario:2026-10-01",
                      "outro_projecto:feedback_x"])
    categorias.main()
    d = set(_cats_lidas(com_notas))
    assert {"agenda:decisao-1", "agenda:decisao-2", "diario:2026-10-01"} <= d, f"caiu uma chave que o índice ainda tem: {sorted(d)}"
    assert "agenda:decisao-9" not in d, "a fonte «agenda» está no índice e já não tem a decisao-9: a categoria dela devia sair"
    assert "outro_projecto:feedback_x" in d, "uma fonte que o índice não conhece (instalação anterior) não pode perder as categorias"


def test_categorizar_tudo_de_novo_tambem_mantem_as_fontes_externas(com_notas):
    _cats(com_notas, ["nota_viva", "agenda:decisao-1"])
    categorias.main(tudo=True)
    assert "agenda:decisao-1" in _cats_lidas(com_notas)


def test_controlo_uma_nota_apagada_perde_a_categoria(com_notas):
    """controlo (já era assim): a chave de uma nota do espaço que já não existe sai; a da nota viva fica."""
    _cats(com_notas, ["nota_viva", "nota_apagada"])
    categorias.main()
    d = _cats_lidas(com_notas)
    assert "nota_apagada" not in d and "nota_viva" in d
