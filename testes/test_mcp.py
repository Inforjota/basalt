"""basalt.mcp — o servidor MCP (JSON-RPC) em pastas temporárias: ferramentas, CAS por base_hash, recursos, stdio e HTTP só-Bearer.

O «web» é o basalt.web do pacote, com o ambiente do Espaço de teste. Nada toca em notas reais nem chama IA.
"""
import importlib
import io
import json
import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("BASALT_CAS_GRACA", "0.05")

from basalt import auth, config, mcp                                   # noqa: E402
from basalt.config import AuthConfig, Config, Utilizador                # noqa: E402

FM = "---\nname: {nome}\ndescription: {desc}\nmetadata:\n  type: {tipo}\n---\n"
NOTAS = {"barcos": (FM.format(nome="barcos", desc="horários dos barcos", tipo="reference") + "\nO barco para o Pico sai às 8h.\n"),
         "tarefas": (FM.format(nome="tarefas", desc="o que falta fazer", tipo="project") + "\nVer [[barcos]] antes de marcar.\n")}


def _web_para(esp, monkeypatch):
    """basalt.web com o ambiente do Espaço (as variáveis BASALT_*); sem índice semântico → a procura é lexical."""
    for k, v in esp.ambiente().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("BASALT_WEB_DEV", "1")
    monkeypatch.setenv("BASALT_WEB_HOST", "127.0.0.1")
    return importlib.import_module("basalt.web")


@pytest.fixture
def amb(tmp_path, monkeypatch):
    token, h = auth.gerar_token()
    u = Utilizador(email="ana@exemplo.pt", espaco="ana", nome="Ana", mcp_token_hash=h)
    cfg = Config(dados=tmp_path / "dados", escutar="127.0.0.1", auth=AuthConfig(modo="aberto"), utilizadores=[u],
                 anfitrioes=("basalt.exemplo.pt",))      # ronda 8 (Host/Origin): em modo aberto o Host não conta; o nome público vem da lista
    cfg.validar()
    esp = cfg.espaco_de(u).criar()
    for nome, texto in NOTAS.items():
        (esp.copia / f"{nome}.md").write_text(texto, encoding="utf-8")
        (esp.real / f"{nome}.md").write_text(texto, encoding="utf-8")
    web = _web_para(esp, monkeypatch)
    srv = mcp.ServidorMCP(cfg, web=web)
    return dict(cfg=cfg, u=u, esp=esp, web=web, srv=srv, token=token)


def pedir(srv, u, metodo, params=None, id_=1):
    return srv.tratar({"jsonrpc": "2.0", "id": id_, "method": metodo, "params": params or {}}, u)


def ferramenta(srv, u, qual, **args):
    r = pedir(srv, u, "tools/call", {"name": qual, "arguments": args})
    assert "result" in r, r
    res = r["result"]
    assert json.loads(res["content"][0]["text"]) == res["structuredContent"]
    return res["structuredContent"], res["isError"]


def test_initialize_e_lista_de_ferramentas(amb):
    r = pedir(amb["srv"], amb["u"], "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t"}})
    assert r["result"]["protocolVersion"] == "2025-03-26"            # ecoa a versão pedida quando a suportamos
    assert r["result"]["serverInfo"]["name"] == "basalt" and "base_hash" in r["result"]["instructions"]
    r = pedir(amb["srv"], amb["u"], "initialize", {"protocolVersion": "1999-01-01"})
    assert r["result"]["protocolVersion"] == mcp.VERSOES[0]          # desconhecida → a nossa
    nomes = {t["name"] for t in pedir(amb["srv"], amb["u"], "tools/list")["result"]["tools"]}
    assert nomes == {"procurar", "ler_nota", "escrever_nota", "acrescentar_nota", "criar_nota", "listar_notas", "guardar_ideia"}
    assert "apagar" not in " ".join(nomes)
    assert amb["srv"].tratar({"jsonrpc": "2.0", "method": "notifications/initialized"}, amb["u"]) is None
    assert pedir(amb["srv"], amb["u"], "ping")["result"] == {}
    assert pedir(amb["srv"], amb["u"], "nada/disto")["error"]["code"] == -32601
    assert pedir(amb["srv"], amb["u"], "tools/call", {"name": "apagar_nota", "arguments": {}})["error"]["code"] == -32602


def test_ler_e_escrever_com_cas(amb):
    srv, u, esp = amb["srv"], amb["u"], amb["esp"]
    d, erro = ferramenta(srv, u, "ler_nota", nome="barcos")
    assert not erro and d["hash"] == amb["web"].sha(NOTAS["barcos"].encode()) and d["tipo"] == "reference"
    assert d["backlinks"] == [{"nome": "tarefas", "contexto": "Ver [[barcos]] antes de marcar."}]
    novo = NOTAS["barcos"].replace("8h", "9h")
    d2, erro = ferramenta(srv, u, "escrever_nota", nome="barcos", texto=novo, base_hash="0" * 40)
    assert erro and d2["estado"] == "conflito" and d2["hash_actual"] == d["hash"] and "8h" in d2["actual"]
    assert (esp.copia / "barcos.md").read_text() == NOTAS["barcos"]   # nada sobreposto
    d3, erro = ferramenta(srv, u, "escrever_nota", nome="barcos", texto=novo, base_hash=d["hash"])
    assert not erro and d3["ok"] and d3["hash"] == amb["web"].sha(novo.encode())
    assert (esp.copia / "barcos.md").read_text() == novo
    d4, erro = ferramenta(srv, u, "ler_nota", nome="nao_existe")
    assert erro and "não encontrada" in d4["erro"]


def test_escrever_recusa_segredo_e_texto_vazio(amb):
    srv, u = amb["srv"], amb["u"]
    d, _ = ferramenta(srv, u, "ler_nota", nome="barcos")
    d2, erro = ferramenta(srv, u, "escrever_nota", nome="barcos", texto=NOTAS["barcos"] + "\nchave: sk-ant-api03-" + "a" * 40 + "\n", base_hash=d["hash"])
    assert erro and ("password" in d2["erro"].lower() or "chave" in d2["erro"].lower()), d2
    assert "sk-ant" not in (amb["esp"].copia / "barcos.md").read_text()            # nada gravado
    r = pedir(srv, u, "tools/call", {"name": "escrever_nota", "arguments": {"nome": "barcos", "texto": "", "base_hash": d["hash"]}})
    assert r["error"]["code"] == -32602 and "texto" in r["error"]["message"]


def test_acrescentar_e_criar(amb):
    srv, u, esp = amb["srv"], amb["u"], amb["esp"]
    d, erro = ferramenta(srv, u, "acrescentar_nota", nome="tarefas", texto="Comprar bilhete.")
    assert not erro and (esp.copia / "tarefas.md").read_text().endswith("antes de marcar.\n\nComprar bilhete.\n")
    d, erro = ferramenta(srv, u, "criar_nota", nome="ideia_x", texto="Corpo da ideia.", descricao="uma ideia", tipo="feedback")
    assert not erro and d["ok"]
    t = (esp.copia / "ideia_x.md").read_text()
    assert t.startswith("---\nname: ideia_x\ndescription: uma ideia\nmetadata:\n  type: feedback\n---\n") and t.endswith("Corpo da ideia.")
    d, erro = ferramenta(srv, u, "criar_nota", nome="ideia_x", texto="outra")
    assert erro and "existe" in d["erro"]
    d, erro = ferramenta(srv, u, "criar_nota", nome="Ideia-Má", texto="x")
    assert erro and "inválido" in d["erro"]
    r = pedir(srv, u, "tools/call", {"name": "criar_nota", "arguments": {"nome": "y_z", "texto": "x", "tipo": "segredo"}})
    assert r["error"]["code"] == -32602


def test_listar_e_procurar_sem_embeddings(amb):
    srv, u = amb["srv"], amb["u"]
    d, _ = ferramenta(srv, u, "listar_notas")
    assert {n["nome"] for n in d["notas"]} == {"barcos", "tarefas"} and d["total"] == 2
    d, _ = ferramenta(srv, u, "listar_notas", tipo="reference")
    assert [n["nome"] for n in d["notas"]] == ["barcos"]
    d, _ = ferramenta(srv, u, "listar_notas", contem="falta")
    assert [n["nome"] for n in d["notas"]] == ["tarefas"]
    d, erro = ferramenta(srv, u, "procurar", consulta="barco para o Pico", n=3)
    assert not erro and d["motor"] == "lexical" and d["resultados"][0]["nome"] == "barcos"
    assert "Pico" in d["resultados"][0]["trecho"] and 0 < d["resultados"][0]["semelhanca"] <= 1


def test_guardar_ideia_vai_para_a_entrada(amb):
    d, erro = ferramenta(amb["srv"], amb["u"], "guardar_ideia", texto="  ver se o barco das 8h muda no inverno  ")
    assert not erro and d["guardada_em"] == "entrada"
    regs = [json.loads(l) for l in amb["esp"].inbox.read_text().splitlines()]
    assert regs == [{"t": d["quando"], "de": "mcp", "autor": "ana@exemplo.pt", "texto": "ver se o barco das 8h muda no inverno", "arquivado": False}]


def test_recursos(amb):
    srv, u = amb["srv"], amb["u"]
    r = pedir(srv, u, "resources/list")["result"]["resources"]
    assert {x["uri"] for x in r} == {"basalt://nota/barcos", "basalt://nota/tarefas"} and all(x["mimeType"] == "text/markdown" for x in r)
    r = pedir(srv, u, "resources/read", {"uri": "basalt://nota/barcos"})["result"]["contents"][0]
    assert r["text"] == NOTAS["barcos"] and r["uri"] == "basalt://nota/barcos"
    assert pedir(srv, u, "resources/read", {"uri": "basalt://nota/nao"})["error"]["code"] == -32602
    assert pedir(srv, u, "resources/read", {"uri": "file:///etc/passwd"})["error"]["code"] == -32602
    assert pedir(srv, u, "resources/templates/list")["result"]["resourceTemplates"][0]["uriTemplate"] == "basalt://nota/{nome}"


def test_stdio_uma_linha_por_mensagem(amb):
    linhas = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}),
              json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}), "", "isto não é json",
              json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "listar_notas", "arguments": {}}})]
    saida = io.StringIO()
    mcp.stdio(amb["srv"], amb["u"], entrada=io.StringIO("\n".join(linhas) + "\n"), saida=saida)
    resps = [json.loads(l) for l in saida.getvalue().splitlines()]
    assert [r.get("id") for r in resps] == [1, None, 2]            # a notificação não tem resposta; o JSON inválido tem erro
    assert resps[1]["error"]["code"] == -32700 and resps[2]["result"]["structuredContent"]["total"] == 2


class _Pedido:
    command = "POST"
    client_address = ("127.0.0.1", 4321)

    def __init__(self, corpo, **cab):
        c = {"Host": "basalt.exemplo.pt"}
        c.update(cab)
        if corpo is not None:
            c["Content-Length"] = str(len(corpo))
        self.headers, self.rfile, self.wfile, self.estado, self.enviados = c, io.BytesIO(corpo or b""), io.BytesIO(), None, {}

    def send_response(self, s):
        self.estado = s

    def send_header(self, k, v):
        self.enviados[k] = v

    def end_headers(self):
        pass

    def corpo(self):
        return json.loads(self.wfile.getvalue() or b"null")


def test_http_so_bearer(amb):
    srv, cfg, token = amb["srv"], amb["cfg"], amb["token"]
    autent = auth.Autenticador(cfg)
    pedido = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"}).encode()
    p = _Pedido(pedido)                                                   # sem Authorization → 401 com desafio
    mcp.http(p, srv, autent)
    assert p.estado == 401 and p.enviados["WWW-Authenticate"].startswith("Bearer")
    p = _Pedido(pedido, Cookie=f"{auth.COOKIE}=qualquer")                  # cookie nunca chega (CSRF)
    mcp.http(p, srv, autent)
    assert p.estado == 401
    p = _Pedido(pedido, Authorization="Bearer bslt_errado")
    mcp.http(p, srv, autent)
    assert p.estado == 401 and p.corpo()["erro"] == "token inválido"
    p = _Pedido(pedido, Authorization="Bearer " + token, Origin="https://outro.exemplo.com")
    mcp.http(p, srv, autent)
    assert p.estado == 403
    p = _Pedido(pedido, Authorization="Bearer " + token, Origin="https://basalt.exemplo.pt")
    mcp.http(p, srv, autent)
    assert p.estado == 200 and p.enviados["Content-Type"].startswith("application/json") and len(p.corpo()["result"]["tools"]) == 7
    p = _Pedido(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode(), Authorization="Bearer " + token)
    mcp.http(p, srv, autent)
    assert p.estado == 202 and p.wfile.getvalue() == b""
    p = _Pedido(b"{nada", Authorization="Bearer " + token)
    mcp.http(p, srv, autent)
    assert p.estado == 400 and p.corpo()["error"]["code"] == -32700
    p = _Pedido(pedido, Authorization="Bearer " + token)
    p.command = "GET"
    mcp.http(p, srv, autent)
    assert p.estado == 405 and p.enviados["Allow"] == "POST"
    p = _Pedido(pedido, Authorization="Bearer " + token)
    p.headers["Content-Length"] = str(mcp.CORPO_MAX + 1)
    mcp.http(p, srv, autent)
    assert p.estado == 413
    # o utilizador do token é quem escreve: a ideia leva o e-mail dele
    chamada = json.dumps({"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "guardar_ideia", "arguments": {"texto": "olá"}}}).encode()
    p = _Pedido(chamada, Authorization="Bearer " + token)
    mcp.http(p, srv, autent)
    assert p.estado == 200 and json.loads(amb["esp"].inbox.read_text().splitlines()[-1])["autor"] == "ana@exemplo.pt"


def test_utilizador_local_para_stdio(amb):
    cfg = amb["cfg"]
    assert mcp.utilizador_local(cfg).email == "ana@exemplo.pt"           # só um → esse
    assert mcp.utilizador_local(cfg, "ana@exemplo.pt").email == "ana@exemplo.pt"
    with pytest.raises(SystemExit):
        mcp.utilizador_local(cfg, "ninguem@exemplo.pt")
    cfg.utilizadores.append(Utilizador(email="bea@exemplo.pt", espaco="bea"))
    with pytest.raises(SystemExit):
        mcp.utilizador_local(cfg)                                          # vários → tem de indicar
    assert mcp.utilizador_local(Config(dados=cfg.dados)).email == "local@basalt"


def test_origem():
    nossos = frozenset({"basalt.exemplo.pt", "localhost", "127.0.0.1", "::1"})
    assert mcp.origem_ok("https://basalt.exemplo.pt", nossos)
    assert mcp.origem_ok("http://localhost:8121", nossos)
    assert not mcp.origem_ok("https://mau.exemplo.com", nossos)
    assert not mcp.origem_ok("null", nossos)
    assert not mcp.origem_ok("http://127.0.0.1.mau.exemplo.com", nossos)     # ronda 8 (Host/Origin): o nome inteiro, não um prefixo


def test_o_mcp_respeita_o_tecto_de_disco_da_conta(tmp_path, monkeypatch):
    """revisão Fable (20/10): criar_nota/escrever_nota/acrescentar_nota/guardar_ideia pelo /mcp contornavam o
    [auth] espaco_max_mb que a página já cumpria."""
    token, h = auth.gerar_token()
    u = Utilizador(email="ana@exemplo.pt", espaco="ana", nome="Ana", mcp_token_hash=h)
    cfg = Config(dados=tmp_path / "dados", escutar="127.0.0.1", auth=AuthConfig(modo="aberto", espaco_max_mb=1), utilizadores=[u],
                 anfitrioes=("basalt.exemplo.pt",))
    cfg.validar()
    esp = cfg.espaco_de(u).criar()
    (esp.copia / "tarefas.md").write_text(NOTAS["tarefas"], encoding="utf-8")
    (esp.real / "tarefas.md").write_text(NOTAS["tarefas"], encoding="utf-8")
    srv = mcp.ServidorMCP(cfg, web=_web_para(esp, monkeypatch))
    grande = "x" * 390_000                                                            # abaixo dos 400 000 do «texto» do MCP
    d, erro = ferramenta(srv, u, "criar_nota", nome="n1", texto=grande)
    assert not erro and d["ok"]
    # r21 (QUOTA_COPIAS_v1): a n1 conta 2 vezes (a cópia + a memória, para onde o vigia a vai copiar) = 780 KB; o que se escreve
    # à mão vai para as duas pastas (como depois do vigia), senão contava outra vez como «por igualar». Antes: 400 KB só na cópia.
    for d in (esp.copia, esp.real):
        (d / "enche.md").write_text("e" * 5_000, encoding="utf-8")                     # ~790 KB usados; sobram ~250 KB
    d, erro = ferramenta(srv, u, "criar_nota", nome="n2", texto=grande)                 # passava 1 MB
    assert erro and "limite" in d["erro"] and not (esp.copia / "n2.md").exists()
    d, erro = ferramenta(srv, u, "acrescentar_nota", nome="tarefas", texto=grande)
    assert erro and "limite" in d["erro"]
    d, _ = ferramenta(srv, u, "ler_nota", nome="tarefas")
    d2, erro = ferramenta(srv, u, "escrever_nota", nome="tarefas", texto=d["texto"] + grande, base_hash=d["hash"])
    assert erro and "limite" in d2["erro"]
    d2, erro = ferramenta(srv, u, "escrever_nota", nome="tarefas", texto="encolhida\n", base_hash=d["hash"])   # encolher passa
    assert not erro and d2["ok"]
    d, erro = ferramenta(srv, u, "guardar_ideia", texto="cabe")
    assert not erro and d["ok"]
    for d in (esp.copia, esp.real):                                                   # r21: 2 × 125 KB (ver acima)
        (d / "enche2.md").write_text("e" * 125_000, encoding="utf-8")                  # sobram < 10 KB
    d, erro = ferramenta(srv, u, "guardar_ideia", texto="i" * 19_000)
    assert erro and "limite" in d["erro"]
