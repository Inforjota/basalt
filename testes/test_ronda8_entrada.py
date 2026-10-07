"""A Entrada é partilhada por vários processos (web, MCP, CLI, arrumação): um só trinco para todas as escritas, carimbos únicos
e tolerância a uma linha partida. Cada teste falha na versão anterior (sem basalt.entrada) e passa com ela."""
import fcntl
import json
import threading
import time

import pytest

from basalt import config, mcp, sig, web
from basalt.config import AuthConfig, Config, Utilizador

CARIMBO = "/api/entrada/arquivar"


def _ficheiro(memoria):
    return memoria.partilhada / "entrada.jsonl"


def _mcp(memoria):
    """um servidor MCP cujo espaço é a memória de teste (o módulo entrada resolve o espaço pelo thread-local / processo)."""
    u = Utilizador(email="ana@exemplo.pt", espaco="ana")
    cfg = Config(dados=memoria.base / "dados-mcp", escutar="127.0.0.1", auth=AuthConfig(modo="aberto"), utilizadores=[u])
    return u, mcp.ServidorMCP(cfg)


def _no_espaco(memoria, fn, *a):
    with config.dentro(memoria.esp):
        return fn(*a)


def _inicio_de_segundo():
    """as três gravações seguintes cabem no mesmo segundo do relógio (o que o teste quer provocar)."""
    time.sleep(1 - (time.time() % 1) + 0.01)


def _pendentes(memoria):
    p = _ficheiro(memoria)
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


class TestUmSoTrinco:
    def test_acrescentar_durante_uma_reescrita_nao_perde_a_ideia(self, memoria):
        """a web reescreve o ficheiro (apagar/decidir) enquanto o CLI/MCP acrescenta: as duas ideias têm de sobreviver."""
        sig.guardar("primeira ideia", de="web")
        sig.guardar("segunda ideia", de="web")
        t2 = [r for r in _pendentes(memoria) if r["texto"] == "segunda ideia"][0]["t"]
        dentro, pode_sair = threading.Event(), threading.Event()

        def lenta(r):
            dentro.set(); pode_sair.wait(5); return None          # None = apagar a segunda ideia

        reescrita = threading.Thread(target=web._entrada_mudar, args=(t2, lenta)); reescrita.start()
        assert dentro.wait(5)
        acrescento = threading.Thread(target=sig.guardar, args=("terceira ideia",), kwargs={"de": "mcp"}); acrescento.start()
        time.sleep(0.3)
        pode_sair.set(); reescrita.join(5); acrescento.join(5)
        textos = [r["texto"] for r in _pendentes(memoria) if not r.get("arquivado")]
        assert textos == ["primeira ideia", "terceira ideia"], textos

    def test_o_trinco_da_entrada_bloqueia_todos_os_escritores(self, memoria, monkeypatch):
        """quem segurar .entrada.lock pára o CLI, o MCP, o arquivar e o apagar — nenhum escreve por fora."""
        sig.guardar("ideia já lá", de="web")
        t1 = _pendentes(memoria)[0]["t"]
        u, srv = _mcp(memoria)
        lk = open(memoria.partilhada / ".entrada.lock", "w"); fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            escritores = {
                "cli": threading.Thread(target=sig.guardar, args=("pelo cli",), kwargs={"de": "terminal"}),
                "mcp": threading.Thread(target=lambda: _no_espaco(memoria, srv._t_guardar_ideia, {"texto": "pelo mcp"}, u)),
                "arquivar": threading.Thread(target=lambda: sig.arquivar(1)),
                "apagar": threading.Thread(target=web.entrada_apagar, args=(t1,)),
            }
            for th in escritores.values():
                th.daemon = True; th.start()
            for th in escritores.values():
                th.join(0.4)
            presos = [n for n, th in escritores.items() if th.is_alive()]
            assert sorted(presos) == ["apagar", "arquivar", "cli", "mcp"], presos
            assert [r["texto"] for r in _pendentes(memoria)] == ["ideia já lá"]
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN); lk.close()
        for th in escritores.values():
            th.join(5)
        assert not any(th.is_alive() for th in escritores.values())


class TestCarimbosUnicos:
    def test_tres_ideias_no_mesmo_segundo_tem_t_diferentes_e_apagar_so_tira_uma(self, memoria):
        _inicio_de_segundo()
        for n in ("a", "b", "c"):
            sig.guardar(f"ideia {n}", de="web")
        regs = _pendentes(memoria)
        ts = [r["t"] for r in regs]
        assert len(set(ts)) == 3, ts
        assert all(t[:19] == ts[0][:19] for t in ts)                    # o mesmo segundo: o sufixo é que distingue
        c, _ = web.entrada_apagar(ts[1])
        assert c == 200
        assert [r["texto"] for r in _pendentes(memoria)] == ["ideia a", "ideia c"]

    def test_o_mcp_devolve_o_carimbo_unico(self, memoria, monkeypatch):
        u, srv = _mcp(memoria)
        _inicio_de_segundo()
        with config.dentro(memoria.esp):
            quandos = [srv._t_guardar_ideia({"texto": f"ideia {n}"}, u)[0]["quando"] for n in range(3)]
        assert len({q[:19] for q in quandos}) == 1, quandos                  # o mesmo segundo
        assert len(set(quandos)) == 3 and set(quandos) == {r["t"] for r in _pendentes(memoria)}
        assert all(r["autor"] == "ana@exemplo.pt" and r["de"] == "mcp" for r in _pendentes(memoria))


class TestLinhaPartida:
    def test_um_append_interrompido_nao_derruba_a_entrada(self, memoria):
        sig.guardar("ideia inteira", de="web")
        with open(_ficheiro(memoria), "a", encoding="utf-8") as f:
            f.write('{"t": "2026-01-01T00:00:00", "de": "mcp", "tex')        # processo morto a meio
        assert [r["texto"] for r in web._regs()] == ["ideia inteira"]
        sig.guardar("ideia seguinte", de="web")                               # o append seguinte começa numa linha nova
        assert [r["texto"] for r in web._regs()] == ["ideia inteira", "ideia seguinte"]
        assert len(web.entrada()) == 2


class TestArquivarPeloCarimbo:
    def test_a_pagina_arquiva_pelo_t_e_nao_pela_posicao(self, servidor_local):
        s = servidor_local()
        with config.dentro(s.espaco()):
            sig.guardar("ideia um", de="web"); sig.guardar("ideia dois", de="web")
            t1 = [r for r in web._regs() if r["texto"] == "ideia um"][0]["t"]
            web.entrada_apagar(t1)                         # a posição de «ideia dois» passou de 2 para 1
            t2 = web._regs()[-1]["t"]
        r = s.pedido("POST", CARIMBO, {"t": t2})
        assert r.codigo == 200, r.corpo
        with config.dentro(s.espaco()):
            assert [x["texto"] for x in web._regs() if x.get("arquivado")] == ["ideia dois"]
        assert s.pedido("POST", CARIMBO, {"t": t2}).codigo == 404     # já não está pendente
        pagina = s.get("/").corpo.decode("utf-8")
        assert 'data-t="' in pagina and "btn.dataset.t});" in pagina and 'data-n="' not in pagina
        s.parar()
