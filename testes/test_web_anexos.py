"""Rotas dos anexos e das etiquetas no servidor web: GET /anexos/<nome>, POST /api/anexos?nome=…, GET /api/etiquetas.
Servidor real em 127.0.0.1 (porta livre), modo aberto, sem IA."""
import http.client
import json
import threading

import pytest

from basalt import anexos, config, web

TOML = """[basalt]
dados = "{dados}"
escutar = "127.0.0.1"
[auth]
modo = "aberto"
[[utilizadores]]
email = "ana@exemplo.pt"
espaco = "ana"
nome = "Ana"
admin = true
[ia]
fornecedor = "nenhum"
"""


@pytest.fixture
def servidor(tmp_path):
    t = tmp_path / "basalt.toml"
    t.write_text(TOML.format(dados=tmp_path / "dados"), encoding="utf-8")
    cfg = config.carregar(t, ambiente={})
    srv = web.criar_servidor(cfg, host="127.0.0.1", porta=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv.server_address[1], cfg.espaco("ana")
    finally:
        srv.shutdown(); srv.server_close()


def dono(porta):
    """o dono da cópia (/api/eu): ronda 9, a página manda-o em todos os pedidos à API e aos anexos."""
    c = http.client.HTTPConnection("127.0.0.1", porta, timeout=10)
    c.request("GET", "/api/eu"); r = c.getresponse(); d = json.loads(r.read())["dono"]; c.close()
    return d


def pedir(porta, metodo, caminho, corpo=None, cab=None, com_dono=True):
    cab = dict(cab or {})
    if com_dono and caminho.startswith(("/api/", "/anexos/")) and not caminho.startswith("/api/eu"):
        cab.setdefault("X-Basalt-Dono", dono(porta))
    c = http.client.HTTPConnection("127.0.0.1", porta, timeout=10)
    c.request(metodo, caminho, body=corpo, headers=cab)
    r = c.getresponse(); dados = r.read(); c.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, dados


ESCRITA = {"X-Basalt": "1", "Content-Type": "image/png"}
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 40


def test_enviar_e_ler(servidor):
    porta, esp = servidor
    st, _, b = pedir(porta, "POST", "/api/anexos?nome=foto%20um.png", PNG, ESCRITA)
    assert st == 201, b
    r = json.loads(b)
    assert r == {"nome": "foto um.png", "bytes": len(PNG)}
    assert (anexos.pasta(esp) / "foto um.png").read_bytes() == PNG
    st, cab, b = pedir(porta, "GET", "/anexos/foto%20um.png")        # com espaço: só funciona com unquote
    assert st == 200 and b == PNG
    assert cab["content-type"] == "image/png" and cab["x-content-type-options"] == "nosniff"
    assert cab["content-length"] == str(len(PNG))
    # o mesmo conteúdo → o mesmo nome; conteúdo diferente → nome novo, nunca sobrepõe
    assert json.loads(pedir(porta, "POST", "/api/anexos?nome=foto%20um.png", PNG, ESCRITA)[2])["nome"] == "foto um.png"
    assert json.loads(pedir(porta, "POST", "/api/anexos?nome=foto%20um.png", PNG + b"!", ESCRITA)[2])["nome"] == "foto um_2.png"
    assert (anexos.pasta(esp) / "foto um.png").read_bytes() == PNG
    assert pedir(porta, "GET", "/anexos/nao-existe.png")[0] == 404
    assert pedir(porta, "GET", "/anexos/%2e%2e/foto%20um.png")[0] == 400
    assert pedir(porta, "GET", "/anexos/")[0] == 400


def test_escrita_exige_cabecalho(servidor):
    porta, esp = servidor
    st, _, _ = pedir(porta, "POST", "/api/anexos?nome=x.png", PNG, {"Content-Type": "image/png"})
    assert st == 403
    assert not (anexos.pasta(esp) / "x.png").exists()


def test_recusas(servidor, monkeypatch):
    porta, esp = servidor
    assert pedir(porta, "POST", "/api/anexos?nome=x.exe", PNG, ESCRITA)[0] == 400
    assert pedir(porta, "POST", "/api/anexos?nome=x.png", b"", ESCRITA)[0] == 400
    monkeypatch.setattr(anexos, "TAMANHO_MAX", 10)
    st, _, b = pedir(porta, "POST", "/api/anexos?nome=grande.png", b"x" * 11, ESCRITA)
    assert st == 413, b
    assert not (anexos.pasta(esp) / "grande.png").exists()
    assert pedir(porta, "POST", "/api/anexos?nome=ok.png", b"x" * 10, ESCRITA)[0] == 201


def test_etiquetas(servidor):
    porta, esp = servidor
    (esp.real / "barcos.md").write_text("---\nname: barcos\ntags: [Mar, barcos]\n---\n# Barcos\nTexto com #pico e #mar.\n", encoding="utf-8")
    (esp.real / "outra.md").write_text("---\nname: outra\n---\nSó #pico aqui; `#nao` em código não conta.\n", encoding="utf-8")
    st, _, b = pedir(porta, "GET", "/api/etiquetas")
    assert st == 200
    lista = json.loads(b)["etiquetas"]
    assert [(e["etiqueta"].lower(), e["notas"]) for e in lista] == [("pico", 2), ("barcos", 1), ("mar", 1)]
