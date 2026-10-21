"""r16 (auditoria de isolamento 21/10, H1) — o tecto de disco da conta ([auth] espaco_max_mb) aguenta pedidos EM SIMULTÂNEO.

Até à r15, «ver se cabe» e «escrever» não corriam sob o mesmo trinco (anexos, criar, /api/guarda, guardar_ideia do MCP; a `gravar`
tinha o da cópia, que os anexos não tomam): 8 pedidos ao mesmo tempo viam todos o mesmo uso, passavam todos e o espaço ficava
muito acima do tecto — e o disco é de toda a gente. Para a corrida aparecer SEMPRE (e não só numa máquina lenta), o `uso_bytes`
fica lento de propósito: mede e só depois espera `JANELA` segundos; sem trinco, todos medem antes de alguém escrever.
Cada teste: tecto de 1 MB quase cheio (cabem 2 escritas), 8 fios a arrancar juntos (threading.Barrier), e no fim o espaço nunca
passa o tecto, passam no máximo as que cabem e as outras levam 413 (ou o erro de limite do MCP)."""
import json
import threading
import time

import pytest

from conftest import ServidorLocal, toml_de

ANA, BEA = "ana@exemplo.pt", "bea@exemplo.pt"
TECTO_MB = 1
TECTO = TECTO_MB * 1024 * 1024
JANELA = 0.3                     # segundos entre medir o uso e devolvê-lo (a janela da corrida)
FIOS = 8


def _servidor(tmp_path):
    """um `ServidorLocal` com `[auth] espaco_max_mb = 1` (a linha entra a seguir ao `modo`: o TOML não deixa reabrir [auth])."""
    from basalt import config, web
    s = object.__new__(ServidorLocal)
    s.dados = tmp_path / "dados-do-servidor"
    s.toml = tmp_path / "basalt.toml"
    texto = toml_de(0, s.dados, "cabecalho", None, [{"email": ANA, "espaco": "a"}, {"email": BEA, "espaco": "b"}], "")
    s.toml.write_text(texto.replace('modo = "cabecalho"\n', f'modo = "cabecalho"\nespaco_max_mb = {TECTO_MB}\n', 1), encoding="utf-8")
    s.cfg = config.carregar(s.toml)
    s.srv = web.criar_servidor(s.cfg, host="127.0.0.1", porta=0)
    s.porta = s.srv.server_address[1]
    s.fio = threading.Thread(target=s.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    s.fio.start()
    return s


@pytest.fixture
def s(tmp_path):
    srv = _servidor(tmp_path)
    yield srv
    srv.parar()


@pytest.fixture
def uso_lento(monkeypatch):
    """troca o `Espaco.uso_bytes` por um que mede e depois espera; devolve o original (para medir no fim sem esperar)."""
    from basalt import config
    original = config.Espaco.uso_bytes

    def lento(self):
        v = original(self)
        time.sleep(JANELA)
        return v
    monkeypatch.setattr(config.Espaco, "uso_bytes", lento)
    return original


def _encher(s, deixar_livre):
    """o espaço da Ana fica com `deixar_livre` bytes até ao tecto (um ficheiro de enchimento nos dados do espaço, que contam)."""
    assert s.get("/api/eu", email=ANA).codigo == 200
    assert s.pedido("GET", "/api/notas", email=ANA).codigo == 200          # cria o espaço e guarda o dono da conta (pedidos ao lado)
    esp = s.espaco("a").criar()
    falta = TECTO - esp.uso_bytes() - deixar_livre
    assert falta > 0
    (esp.dados / "enchimento.bin").write_bytes(b"\0" * falta)
    return esp


def _em_simultaneo(fns):
    """corre cada fn num fio; todos arrancam juntos. Devolve os resultados pela mesma ordem."""
    barreira = threading.Barrier(len(fns))
    res = [None] * len(fns)

    def correr(i, fn):
        barreira.wait(10)
        try:
            res[i] = fn()
        except Exception as e:                       # um fio que rebenta tem de aparecer na asserção, não sumir
            res[i] = f"excepção: {type(e).__name__}: {e}"
    fios = [threading.Thread(target=correr, args=(i, fn), daemon=True) for i, fn in enumerate(fns)]
    for f in fios:
        f.start()
    for f in fios:
        f.join(120)
    assert not any(f.is_alive() for f in fios), "um pedido ficou preso"
    return res


RECUSA_CEDO = "413 antes de ler o corpo"


def _anexo(s, nome, n):
    """envia um anexo de `n` bytes. A recusa CEDO dos anexos (r14: já cheio antes de ler o corpo → 413 sem ler os MB) fecha a
    ligação com o corpo por ler e o sistema responde-lhe com um RST: o cliente às vezes vê a ligação cortada em vez do 413. Conta
    como recusa (e o teste confirma no disco que esse anexo não ficou lá)."""
    def f():
        try:
            return s.pedido("POST", f"/api/anexos?nome={nome}", bruto=b"a" * n, email=ANA, cabecalhos={"Content-Type": "image/png"}).codigo
        except (ConnectionResetError, BrokenPipeError):
            return RECUSA_CEDO
    return f


def _verificar(esp, original, res, ok, cabem):
    passaram = sum(r in ok for r in res)
    assert original(esp) <= TECTO, f"o espaço ficou com {original(esp)} bytes; o tecto é {TECTO} (passaram {passaram}: {res})"
    assert 1 <= passaram <= cabem, f"passaram {passaram} de {len(res)} com lugar para {cabem}: {res}"
    recusas = {413, RECUSA_CEDO}
    assert set(res) <= set(ok) | recusas, res              # nem 500 nem 503 (ninguém ficou à espera do trinco 20 s)
    assert sum(r in recusas for r in res) == len(res) - passaram, res


# ───────────────────────────── anexos ─────────────────────────────
def test_anexos_em_simultaneo_nao_passam_o_tecto(s, uso_lento):
    tam = 200 * 1024
    esp = _encher(s, int(2.5 * tam))                                     # cabem 2
    res = _em_simultaneo([_anexo(s, f"foto{i}.png", tam) for i in range(FIOS)])
    _verificar(esp, uso_lento, res, {201}, 2)
    assert len(list((esp.real / "anexos").glob("foto*.png"))) == res.count(201)       # nenhuma recusa deixou o anexo no disco
    # a conta vizinha não sofre com o tecto da Ana (e o trinco é do espaço dela, não do servidor)
    assert _anexo(s, "foto.png", tam)() in (413, RECUSA_CEDO)               # a Ana está cheia
    r = s.pedido("POST", "/api/anexos?nome=foto.png", bruto=b"b" * tam, email=BEA, cabecalhos={"Content-Type": "image/png"})
    assert r.codigo == 201, r.corpo


# ───────────────────────────── criar ─────────────────────────────
def test_criar_notas_em_simultaneo_nao_passa_o_tecto(s, uso_lento):
    tam = 200 * 1024
    esp = _encher(s, int(2.5 * tam))
    res = _em_simultaneo([lambda i=i: s.pedido("POST", "/api/nota", {"nome": f"n{i}", "texto": "x" * tam}, email=ANA).codigo
                          for i in range(FIOS)])
    _verificar(esp, uso_lento, res, {201}, 2)
    assert len(list(esp.copia.glob("n*.md"))) == res.count(201)


# ───────────────────────────── gravar + anexos misturados ─────────────────────────────
def test_gravar_e_anexos_em_simultaneo_nao_passam_o_tecto(s, uso_lento):
    tam = 200 * 1024
    hashes = {}
    for i in range(4):                                                   # notas pequenas, já criadas antes do enchimento
        assert s.pedido("POST", "/api/nota", {"nome": f"g{i}", "texto": "curta\n"}, email=ANA).codigo == 201
        hashes[i] = s.get(f"/api/nota?nome=g{i}", email=ANA).json["hash"]
    esp = _encher(s, int(2.5 * tam))
    fns = []
    for i in range(4):
        fns.append(lambda i=i: s.pedido("PUT", f"/api/nota?nome=g{i}", {"texto": "curta\n" + "y" * tam, "base_hash": hashes[i]},
                                        email=ANA).codigo)
        fns.append(_anexo(s, f"mistura{i}.png", tam))
    res = _em_simultaneo(fns)
    _verificar(esp, uso_lento, res, {200, 201}, 2)
    assert len(list((esp.real / "anexos").glob("mistura*.png"))) == res.count(201)
    cresceram = sum(len((esp.copia / f"g{i}.md").read_bytes()) > 1000 for i in range(4))
    assert cresceram == res.count(200)


# ───────────────────────────── Entrada (web) + MCP (guardar_ideia, criar_nota) ─────────────────────────────
def test_entrada_e_mcp_em_simultaneo_nao_passam_o_tecto(s, uso_lento):
    from basalt import mcp
    tam = 4000                                                           # o /api/guarda corta a 4000 caracteres
    # a 1.ª ideia cria o índice do espaço (semantica.sqlite, 12 KB) além da linha da Entrada: fica feita ANTES de encher, para o
    # que se mede ser só a corrida (esse ficheiro aparece uma vez por espaço, não é por ele que se passa o tecto)
    assert s.pedido("POST", "/api/guarda", {"texto": "aquecimento"}, email=ANA).codigo == 200
    esp = _encher(s, int(2.5 * tam))
    u = next(x for x in s.cfg.utilizadores if x.email == ANA)
    srv = mcp.ServidorMCP(s.cfg)

    def ferramenta(qual, **args):
        def f():
            r = srv.tratar({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": qual, "arguments": args}}, u)
            d = r["result"]["structuredContent"]
            if not r["result"]["isError"]:
                return "ok"
            return 413 if "limite de espaço" in d.get("erro", "") else json.dumps(d, ensure_ascii=False)
        return f

    def guarda(i):
        def f():
            r = s.pedido("POST", "/api/guarda", {"texto": f"ideia {i} " + "z" * tam}, email=ANA)
            return 200 if r.codigo == 200 and r.json.get("ok") else (r.codigo if r.codigo != 200 else r.corpo.decode())
        return f
    fns = [guarda(i) for i in range(3)]
    fns += [ferramenta("guardar_ideia", texto=f"ideia mcp {i} " + "w" * tam) for i in range(3)]
    fns += [ferramenta("criar_nota", nome=f"mcp_{i}", texto="v" * tam) for i in range(2)]
    res = _em_simultaneo(fns)
    _verificar(esp, uso_lento, res, {200, "ok"}, 2)
    ideias = [ln for ln in esp.inbox.read_text(encoding="utf-8").splitlines() if ln.strip()] if esp.inbox.exists() else []
    assert len(ideias) - 1 + len(list(esp.copia.glob("mcp_*.md"))) == res.count(200) + res.count("ok")      # - o aquecimento
