"""r16 (auditoria de isolamento 21/10, M4) — o modo cabecalho sem o segredo do proxy (ou com um de menos de 32 caracteres) não
arranca o servidor (web e /mcp): a r15 só avisava no stderr, e qualquer processo que chegasse à porta entrava com o e-mail que
escrevesse no X-Basalt-Email. Os comandos de linha (listar, entrada, mcp por stdio…) não falam com o proxy e continuam a correr.
Cada regra com o caso que arranca e o que é recusado."""
import os
import subprocess
import sys

import pytest

from basalt import config, web
from conftest import RAIZ

ANA = "ana@exemplo.pt"


def _toml(tmp_path, modo="cabecalho", extra=""):
    p = tmp_path / "basalt.toml"
    p.write_text(f'[basalt]\ndados = "{tmp_path}/d"\nescutar = "127.0.0.1"\nporta = 0\ngit = false\n[auth]\nmodo = "{modo}"\n{extra}'
                 f'[[utilizadores]]\nemail = "{ANA}"\nespaco = "ana"\nadmin = true\n', encoding="utf-8")
    return p


def _arranca(p, segredo=None, **amb):
    cfg = config.carregar(p, ambiente={**amb, **({"BASALT_SEGREDO_PROXY": segredo} if segredo is not None else {})})
    srv = web.criar_servidor(cfg, porta=0)
    srv.server_close()
    return True


def test_cabecalho_sem_segredo_nao_arranca_e_diz_o_que_fazer(tmp_path):
    p = _toml(tmp_path)
    with pytest.raises(config.ConfigInvalida) as e:
        _arranca(p)
    m = str(e.value)
    assert "BASALT_SEGREDO_PROXY" in m and "vazia" in m and "32" in m, m        # a variável, e com quantos caracteres
    assert "X-Basalt-Proxy-Segredo" in m and "GOOGLE.md §3" in m, m            # o mesmo valor no proxy, e onde se explica
    with pytest.raises(config.ConfigInvalida, match="vazia"):
        _arranca(p, segredo="   ")                                              # só espaços = vazia (o carregar faz strip)


def test_cabecalho_com_segredo_curto_nao_arranca(tmp_path):
    p = _toml(tmp_path)
    with pytest.raises(config.ConfigInvalida, match="tem só 31 caracteres"):
        _arranca(p, segredo="s" * 31)


def test_cabecalho_com_segredo_de_32_arranca(tmp_path):
    assert _arranca(_toml(tmp_path), segredo="s" * 32)


def test_a_mensagem_segue_a_variavel_e_o_cabecalho_do_toml(tmp_path):
    p = _toml(tmp_path, extra='segredo_proxy_env = "OUTRO_SEGREDO"\ncabecalho_segredo = "X-Outro"\n')
    with pytest.raises(config.ConfigInvalida) as e:
        _arranca(p, segredo="s" * 40)                                           # está na variável de omissão, não na do toml
    assert "OUTRO_SEGREDO" in str(e.value) and "X-Outro" in str(e.value), e.value
    assert _arranca(p, OUTRO_SEGREDO="s" * 40)


def test_modo_senha_sem_segredo_arranca(tmp_path):
    assert _arranca(_toml(tmp_path, modo="senha"))


def test_com_o_segredo_so_entra_quem_o_traz(servidor_local, monkeypatch):
    """a outra metade: arrancado (com segredo), o pedido sem ele ou com outro não entra — e o do proxy entra."""
    monkeypatch.setenv("BASALT_SEGREDO_PROXY", "p" * 32)
    s = servidor_local(modo="cabecalho", utilizadores=[{"email": ANA, "espaco": "a", "admin": True}])
    assert s.get("/api/eu", email=ANA).codigo == 200                            # o Cliente manda o do ambiente, como o proxy
    assert s.get("/api/eu", email=ANA, segredo_proxy=False).codigo in (401, 403)
    assert s.get("/api/eu", email=ANA, segredo_proxy="q" * 32).codigo in (401, 403)


# ───────────── os comandos de linha não precisam do segredo ─────────────
def _basalt(p, *args, entrada=""):
    env = {k: v for k, v in os.environ.items() if k != "BASALT_SEGREDO_PROXY"}
    env.update(BASALT_CONFIG="", BASALT_SENHA="")
    return subprocess.run([sys.executable, "-m", "basalt", "--config", str(p), *args], cwd=RAIZ, input=entrada,
                          capture_output=True, text=True, env=env, timeout=60)


def test_comandos_de_linha_em_cabecalho_sem_segredo_funcionam(tmp_path):
    p = _toml(tmp_path)
    r = _basalt(p, "utilizador", "listar")
    assert r.returncode == 0 and ANA in r.stdout, r.stdout + r.stderr
    r = _basalt(p, "entrada", "-u", ANA, "uma ideia sem proxy")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "uma ideia sem proxy" in (tmp_path / "d" / "ana" / "dados" / "entrada.jsonl").read_text(encoding="utf-8")
    pedido = '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}\n'
    r = _basalt(p, "mcp", "-u", ANA, entrada=pedido)                             # o MCP por stdio é local: não passa pelo proxy
    assert r.returncode == 0 and "guardar_ideia" in r.stdout, r.stdout + r.stderr


def test_serve_sem_segredo_sai_com_a_razao_e_sem_traceback(tmp_path):
    r = _basalt(_toml(tmp_path), "serve")
    assert r.returncode != 0
    assert "não arranca" in r.stderr and "BASALT_SEGREDO_PROXY" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr
