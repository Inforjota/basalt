"""Ronda 12 — o título e a ref de um item de `[fontes_extra]` também passam pelo filtro de segredos.

O filtro olhava só para o texto de cada pedaço: um título como «password: hunter2xyz» entrava no índice e aparecia na procura,
nas citações e no título da vista só de leitura (`GET /api/fonte`). Agora (1) na indexação o item não entra, com um aviso que
diz a fonte e a contagem mas nunca o conteúdo; (2) na leitura, um índice feito antes também não o mostra (procura, vista).
Os casos maus falham no código anterior; o controlo (item limpo) passa antes e depois."""
import http.client
import json
import os
import shutil
import sys
import threading
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

from basalt import config as _config

_CARREGAR = _config.carregar            # o verdadeiro: a fixture `com_comando` substitui-o durante o teste

TOML = """[basalt]
dados = "{dados}"
escutar = "127.0.0.1"
[auth]
modo = "aberto"
[[utilizadores]]
email = "tu@exemplo.pt"
espaco = "ana"
nome = "Ana"
admin = true
[ia]
fornecedor = "nenhum"
"""
TIT_MAU = "password: hunter2xyz"
LIMPO = ("manual", "limpo-1", "Manual da caldeira", "A caldeira fica na cave, ao lado do depósito de água.")
TIT_SEGREDO = ("manual", "doc-a", TIT_MAU, "A caldeira fica na cave, ao lado do depósito de água.")
REF_SEGREDO = ("manual", "password: hunter2xyz", "Manual do depósito", "O depósito de água fica na cave, ao lado da caldeira.")


def _itens_json(*itens):
    return json.dumps([{"fonte": f, "ref": r, "titulo": t, "texto": x} for f, r, t, x in itens])


def _por_no_indice(esp, pedacos):
    """como o código ANTIGO deixava: o pedaço no índice com o título/ref tal e qual."""
    from basalt import config, sig
    with config.dentro(esp):
        c = sig.db()
        c.executemany("INSERT OR REPLACE INTO pedaco (fonte, ref, titulo, trecho, hash, vec) VALUES (?, ?, ?, ?, 'h', x'')", pedacos)
        c.commit()
        c.close()


# ───────────────────────── indexação ─────────────────────────

@pytest.fixture
def com_comando(tmp_path, monkeypatch):
    """`sig._extras()` a sério: um comando que imprime o JSON dado (sem BASALT_SIG_ISOLADO)."""
    from basalt import sig

    def preparar(*itens):
        script = tmp_path / "fonte.py"
        script.write_text("import sys\nprint(" + repr(_itens_json(*itens)) + ")\n", encoding="utf-8")
        cmd = f'"{sys.executable}" "{script}"'
        monkeypatch.delenv("BASALT_SIG_ISOLADO", raising=False)
        monkeypatch.setattr(sig.config, "carregar", lambda *a, **k: SimpleNamespace(fontes_extra=[cmd]))
        return sig
    return preparar


def test_titulo_com_password_nao_entra_na_indexacao(com_comando, capsys):
    sig = com_comando(LIMPO, TIT_SEGREDO)
    itens = sig._extras()
    err = capsys.readouterr().err
    assert [i[1] for i in itens] == ["limpo-1"], itens
    assert "hunter2xyz" not in json.dumps(itens) and "hunter2xyz" not in err
    assert "manual" in err and "1" in err, err                      # aviso: a fonte e a contagem


def test_ref_com_password_nao_entra_na_indexacao(com_comando, capsys):
    sig = com_comando(LIMPO, REF_SEGREDO)
    itens = sig._extras()
    err = capsys.readouterr().err
    assert [i[1] for i in itens] == ["limpo-1"], itens
    assert "hunter2xyz" not in err and "manual" in err, err


def test_titulo_com_password_nem_chega_a_procura(com_comando, tmp_path, monkeypatch, capsys):
    """de ponta a ponta: indexar + ler o índice; nem o título nem o texto desse item saem."""
    from basalt import config
    sig = com_comando(LIMPO, TIT_SEGREDO)
    monkeypatch.setattr(sig, "_com_embeddings", lambda: False)
    t = tmp_path / "basalt.toml"
    t.write_text(TOML.format(dados=tmp_path / "dados"), encoding="utf-8")
    esp = _CARREGAR(t, ambiente={}).espaco("ana")
    for d in (esp.real, esp.copia):
        d.mkdir(parents=True, exist_ok=True)
    with config.dentro(esp):
        sig.indexar()
        capsys.readouterr()
        c = sig.db()
        try:
            linhas = sig.carregar(c)
            guardados = [r[0] for r in c.execute("SELECT ref FROM pedaco WHERE fonte='manual'")]
        finally:
            c.close()
    assert guardados == ["limpo-1"], guardados                       # nem sequer ficou no índice
    assert [l[1] for l in linhas] == ["limpo-1"], linhas


def test_controlo_item_limpo_entra(com_comando, capsys):
    sig = com_comando(LIMPO)
    itens = sig._extras()
    assert itens == [LIMPO], itens
    assert "aviso" not in capsys.readouterr().err


# ───────────────────────── leitura (índice antigo) ─────────────────────────

@pytest.fixture(scope="module")
def real(tmp_path_factory, request):
    from basalt import config, segredos, web
    assert segredos.tem_segredo_texto(TIT_MAU) and not segredos.tem_segredo_texto(LIMPO[2]) and not segredos.tem_segredo_texto(LIMPO[3])
    tmp = tmp_path_factory.mktemp("r12extras")
    t = tmp / "basalt.toml"
    t.write_text(TOML.format(dados=tmp / "dados"), encoding="utf-8")
    cfg = config.carregar(t, ambiente={})
    origem = Path(os.environ.get("TESTE_STATIC") or web.STATIC).resolve()
    estatico = tmp / "static"
    shutil.copytree(origem, estatico)
    original = web.STATIC
    web.STATIC = estatico
    request.addfinalizer(lambda: setattr(web, "STATIC", original))
    esp = cfg.espaco("ana")
    for d in (esp.real, esp.copia):
        d.mkdir(parents=True, exist_ok=True)
    _por_no_indice(esp, [LIMPO, TIT_SEGREDO, REF_SEGREDO])         # um índice feito pelo código antigo
    srv = web.criar_servidor(cfg, host="127.0.0.1", porta=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _get(porta, caminho):
    c = http.client.HTTPConnection("127.0.0.1", porta, timeout=60)
    c.request("GET", "/api/eu")
    dono = json.loads(c.getresponse().read())["dono"]
    c.request("GET", caminho, headers={"X-Basalt-Dono": dono, "X-Basalt": "1"})
    r = c.getresponse()
    corpo = r.read()
    c.close()
    try:
        return r.status, json.loads(corpo)
    except ValueError:
        return r.status, corpo.decode("utf-8", "replace")


def _fonte(porta, fonte, ref):
    return _get(porta, "/api/fonte?" + urllib.parse.urlencode({"fonte": fonte, "ref": ref}))


def test_indice_antigo_titulo_com_password_nao_sai_na_procura(real):
    st, res = _get(real, "/api/procura?q=" + urllib.parse.quote("caldeira cave depósito"))
    assert st == 200, res
    assert "hunter2xyz" not in json.dumps(res, ensure_ascii=False), res
    assert not [r for r in res if r["ref"] in ("doc-a", REF_SEGREDO[1])], res


def test_api_fonte_titulo_com_password_404(real):
    st, d = _fonte(real, "manual", "doc-a")
    assert st == 404 and "hunter2xyz" not in json.dumps(d, ensure_ascii=False), (st, d)


def test_api_fonte_ref_com_password_404(real):
    st, d = _fonte(real, "manual", REF_SEGREDO[1])
    assert st == 404 and "hunter2xyz" not in json.dumps(d, ensure_ascii=False), (st, d)


def test_controlo_item_limpo_aparece_e_abre(real):
    st, res = _get(real, "/api/procura?q=" + urllib.parse.quote("caldeira cave depósito"))
    assert st == 200 and [r for r in res if r["ref"] == "limpo-1"], res
    st, d = _fonte(real, "manual", "limpo-1")
    assert st == 200 and d["titulo"] == "Manual da caldeira" and d["pedacos"][0]["texto"] == LIMPO[3], (st, d)
