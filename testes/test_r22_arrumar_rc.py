"""r22 (ARRUMAR_RC_v1): a reindexação que fica a meio porque o fornecedor de embeddings não responde tem de chegar ao código de
saída de quem corre a tarefa (cron/timer), como prometia o REINDEX_RC_v1.

Antes: o cron e o timer correm `basalt arrumar --auto` (e `basalt indexar`), que ignoravam o que o `_correr()` e o `sig.indexar()`
diziam; e, desde o EMB_SEM_RESPOSTA_v1, o `basalt.sig indexar` com o fornecedor calado acabava com código 0 — o `_reindexar` do
auto_arrumar nunca via a falha. A noite de 24/10 (exit 1 por TimeoutExpired no pacote antigo) passava a dar exit 0 e o hb
marcava «ok». Agora: código 3, com o porquê no stderr; o que a noite fez fica gravado e o relatório sai na mesma.

Cada teste de comportamento falha no 00e5a9e (código 0 / sem SystemExit); os controlos passam nos dois.
"""
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from basalt import config, ia, sig
from conftest import FM, toml_de
from ollama_falso import OllamaFalso
from test_r22_emb_sem_resposta import IABoa, IAMuda, nota

RAIZ = Path(__file__).resolve().parent.parent
ANA = "ana@exemplo.pt"


@pytest.fixture(autouse=True)
def _repor_ia():
    yield
    ia.definir(None)


# ───────────────────────── `python -m basalt.sig indexar` (o que o `_reindexar` do auto_arrumar corre) ─────────────────────────
def test_sig_indexar_com_fornecedor_mudo_sai_com_codigo_3(memoria, capsys):
    nota(memoria, "barcos", "As barcos do porto precisam de manutenção.")
    ia.definir(IAMuda())
    with pytest.raises(SystemExit) as e:
        sig._comando(["indexar"])
    assert e.value.code == 3, f"saiu com {e.value.code!r}"
    assert "não respondeu" in capsys.readouterr().err


def test_sig_indexar_que_responde_continua_a_sair_com_zero(memoria):
    """controlo: com resposta não há SystemExit (o `__main__` acaba com 0)."""
    nota(memoria, "barcos", "As barcos do porto precisam de manutenção.")
    ia.definir(IABoa())
    assert sig._comando(["indexar"]) is None


# ───────────────────────── a CLI, noutro processo (como o cron 03:35 e o timer 04:10) ─────────────────────────
@pytest.fixture
def pendurado():
    """um «fornecedor de embeddings» que aceita a ligação e nunca responde (o Ollama ocupado de 24/10)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0)); srv.listen(64)
    presas = []

    def aceitar():
        while True:
            try:
                presas.append(srv.accept()[0])
            except OSError:
                return
    threading.Thread(target=aceitar, daemon=True).start()
    yield f"http://127.0.0.1:{srv.getsockname()[1]}"
    try:
        srv.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    srv.close()
    for p in presas:
        p.close()


def _espaco(tmp_path, url_chat, url_emb):
    """um espaço «t» com 2 notas por indexar; [ia] tempo_limite = 1 s (cada «sem resposta» leva 1 s, não 120)."""
    forn = type("F", (), {"url_chat": url_chat, "url_emb": url_emb})
    toml = tmp_path / "basalt.toml"
    t = toml_de(0, tmp_path / "dados", "aberto", forn, [{"email": ANA, "espaco": "t", "admin": True}])
    toml.write_text(t.replace('modelo_chat = "modelo-de-teste"\n', 'modelo_chat = "modelo-de-teste"\ntempo_limite = 1\n', 1),
                    encoding="utf-8")
    esp = config.carregar(toml).espaco("t").criar()
    for n in ("barcos", "velas"):
        (esp.real / f"{n}.md").write_text(FM.format(nome=n) + f"\nOs {n} do porto precisam de manutenção.\n", encoding="utf-8")
    return toml


def _basalt(toml, tmp_path, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("BASALT_") or k in ("BASALT_SIG_ISOLADO", "BASALT_CAS_GRACA")}
    env["HOME"] = str(tmp_path)
    r = subprocess.run([sys.executable, "-m", "basalt", "--config", str(toml), *args], cwd=RAIZ, env=env,
                       capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout, r.stderr


def test_basalt_indexar_com_fornecedor_mudo_sai_com_codigo_3(tmp_path, pendurado):
    ollama = OllamaFalso()
    try:
        rc, out, err = _basalt(_espaco(tmp_path, ollama.url_chat, pendurado), tmp_path, "indexar")
    finally:
        ollama.parar()
    assert rc == 3, f"código {rc} — o cron das 03:35 marcava «ok» com o índice por fazer\n{out}{err}"
    assert "não respondeu" in err, err


def test_arrumar_auto_com_fornecedor_mudo_sai_com_codigo_3_e_o_relatorio_sai(tmp_path, pendurado):
    ollama = OllamaFalso()
    try:
        rc, out, err = _basalt(_espaco(tmp_path, ollama.url_chat, pendurado), tmp_path, "arrumar", "--auto")
    finally:
        ollama.parar()
    assert rc == 3, f"código {rc} — o timer das 04:10 marcava «ok» com a reindexação a meio\n{out}{err}"
    assert "reindexação" in err and "não respondeu" in err, err
    assert "Basalt — arrumação" in out, f"o relatório da noite não saiu\n{out}"


def test_controlo_com_fornecedor_que_responde_tudo_sai_com_zero(tmp_path):
    ollama = OllamaFalso()
    try:
        toml = _espaco(tmp_path, ollama.url_chat, ollama.url_emb)
        r1 = _basalt(toml, tmp_path, "indexar")
        r2 = _basalt(toml, tmp_path, "arrumar", "--auto")
    finally:
        ollama.parar()
    assert r1[0] == 0 and r2[0] == 0, (r1, r2)


def test_arrumar_auto_leva_o_aviso_do_auto_arrumar_ao_codigo_de_saida(tmp_path, monkeypatch, capsys):
    """a reindexação do fim da noite (depois de ligar/unir/arquivar) que falha: o `_correr()` devolve o aviso — o cron via 0."""
    from basalt import __main__ as cli, auto_arrumar, categorias
    ollama = OllamaFalso()
    try:
        toml = _espaco(tmp_path, ollama.url_chat, ollama.url_emb)
        aviso = "Atenção: a reindexação saiu com o código 3; a pesquisa pode mostrar as notas de antes até à próxima indexação."
        monkeypatch.setattr(auto_arrumar, "_correr", lambda: aviso)
        monkeypatch.setattr(sig, "indexar", lambda: None)
        monkeypatch.setattr(categorias, "main", lambda *a, **k: None)
        with pytest.raises(SystemExit) as e:
            cli.main(["--config", str(toml), "arrumar", "--auto"])
    finally:
        ollama.parar()
    assert e.value.code == 3, f"saiu com {e.value.code!r}"
    assert aviso in capsys.readouterr().err
