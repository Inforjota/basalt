"""POST /mcp a sério, pelo servidor web do pacote (criar_servidor): Bearer obrigatório, GET → 405, tools/list e ler_nota."""
import http.client
import json
import threading

import pytest

from basalt import auth, config, web

TOML = '''[basalt]
dados = "{dados}"
escutar = "127.0.0.1"
[auth]
modo = "aberto"
[[utilizadores]]
email = "ana@exemplo.pt"
espaco = "ana"
nome = "Ana"
admin = true
mcp_token_hash = "{hash}"
[ia]
fornecedor = "nenhum"
'''


@pytest.fixture
def servidor(tmp_path):
    token, h = auth.gerar_token()
    t = tmp_path / "basalt.toml"
    t.write_text(TOML.format(dados=tmp_path / "dados", hash=h), encoding="utf-8")
    cfg = config.carregar(t, ambiente={})
    srv = web.criar_servidor(cfg, host="127.0.0.1", porta=0)
    th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
    esp = cfg.espaco("ana")
    for d in (esp.real, esp.copia):
        (d / "barcos.md").write_text("---\nname: barcos\ndescription: horários\ntype: reference\n---\nO barco sai às 8h.\n", encoding="utf-8")
    yield srv.server_address[1], token
    srv.shutdown(); srv.server_close(); th.join(5)


def pedir(porta, metodo, caminho, corpo=None, cab=None):
    c = http.client.HTTPConnection("127.0.0.1", porta, timeout=10)
    dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    c.request(metodo, caminho, body=dados, headers={"Content-Type": "application/json", **(cab or {})})
    r = c.getresponse(); b = r.read(); c.close()
    try:
        return r.status, json.loads(b.decode("utf-8") or "null"), dict(r.getheaders())
    except ValueError:
        return r.status, b, dict(r.getheaders())


def rpc(porta, token, metodo, params=None, id_=1):
    return pedir(porta, "POST", "/mcp", {"jsonrpc": "2.0", "id": id_, "method": metodo, "params": params or {}},
                 {"Authorization": "Bearer " + token})


def test_mcp_pelo_servidor_web(servidor):
    porta, token = servidor
    # sem Bearer → 401 com desafio; cookie/aberto NÃO servem (o modo é aberto e mesmo assim recusa)
    st, corpo, cab = pedir(porta, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert st == 401 and "Bearer" in (cab.get("WWW-Authenticate") or "")
    st, corpo, _ = pedir(porta, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, {"Authorization": "Bearer bslt_errado"})
    assert st == 401
    # GET → 405
    st, _, cab = pedir(porta, "GET", "/mcp", None, {"Authorization": "Bearer " + token})
    assert st == 405 and cab.get("Allow") == "POST"
    # com o token: initialize, tools/list, ler_nota dentro do espaço da Ana
    st, r, _ = rpc(porta, token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
    assert st == 200 and r.get("result", {}).get("serverInfo"), r
    st, r, _ = rpc(porta, token, "tools/list")
    nomes = {t["name"] for t in r["result"]["tools"]}
    assert {"procurar", "ler_nota", "escrever_nota", "listar_notas"} <= nomes, nomes
    st, r, _ = rpc(porta, token, "tools/call", {"name": "ler_nota", "arguments": {"nome": "barcos"}})
    assert st == 200 and "8h" in json.dumps(r["result"], ensure_ascii=False), r
    # notificação (sem id) → 202 sem corpo
    st, corpo, _ = pedir(porta, "POST", "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, {"Authorization": "Bearer " + token})
    assert st == 202
