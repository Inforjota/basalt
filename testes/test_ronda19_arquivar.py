"""Ronda 19 (ensaio dos pares de 23/10): o ARQUIVAR tira a nota do MEMORY.md, por isso passa a ter os travões do UNIR
(⭐⭐⭐, confiança ≥ 0.85, nada mexido nas últimas 6 h, máx. 3 por noite) — senão liga; e pares com uma nota já arquivada
(marca «🗄 Arquivada») não voltam à IA. No ensaio, os dois candidatos arquivavam uma nota ⭐⭐⭐ e voltavam a arquivar notas já arquivadas.
"""
import os, time

import pytest

from basalt import auto_arrumar

CAB = "---\nname: {n}\ndescription: d\n---\n"
VELHO = time.time() - 7 * 3600


@pytest.fixture
def mem(memoria, monkeypatch):
    monkeypatch.setattr(auto_arrumar, "SIMULAR", True)
    return memoria


def _notas(mem, *nomes, indice="", corpo="Texto.\n"):
    for n in nomes:
        mem.nota(n, CAB.format(n=n) + corpo)
        os.utime(mem.real / f"{n}.md", (VELHO, VELHO))
    (mem.real / "MEMORY.md").write_text(indice or "".join(f"- [{n}]({n}.md) — x\n" for n in nomes), encoding="utf-8")


def _correr(monkeypatch, capsys, pares, decisoes):
    monkeypatch.setattr(auto_arrumar.arr, "duplicados", lambda: [(0.97, a, b) for a, b in pares])
    pedidos = []

    def decidir(a, b):
        pedidos.append((a, b))
        return dict(decisoes[(a, b)])
    monkeypatch.setattr(auto_arrumar, "decidir", decidir)
    auto_arrumar._correr()
    return capsys.readouterr().out, pedidos


def test_arquivar_uma_nota_estrela3_passa_a_ligar(mem, monkeypatch, capsys):
    _notas(mem, "principal", "imersivo", indice="- [principal](principal.md) — x\n- ⭐⭐⭐ [imersivo](imersivo.md) — LER ANTES\n")
    out, _ = _correr(monkeypatch, capsys, [("imersivo", "principal")],
                     {("imersivo", "principal"): {"acao": "arquivar", "manter": "principal", "confianca": 0.95, "motivo": "m"}})
    assert "ARQUIVAR" not in out and "LIGAR" in out and "⭐⭐⭐" in out, out


def test_arquivar_com_confianca_abaixo_de_085_passa_a_ligar(mem, monkeypatch, capsys):
    _notas(mem, "a", "b")
    out, _ = _correr(monkeypatch, capsys, [("a", "b")], {("a", "b"): {"acao": "arquivar", "manter": "a", "confianca": 0.80, "motivo": "m"}})
    assert "ARQUIVAR" not in out and "confiança < 0.85" in out, out


def test_arquivar_uma_nota_mexida_ha_pouco_passa_a_ligar(mem, monkeypatch, capsys):
    _notas(mem, "a", "b")
    os.utime(mem.real / "b.md", None)
    out, _ = _correr(monkeypatch, capsys, [("a", "b")], {("a", "b"): {"acao": "arquivar", "manter": "a", "confianca": 0.95, "motivo": "m"}})
    assert "ARQUIVAR" not in out and "últimas 6 h" in out, out


def test_no_maximo_3_arquivos_por_noite(mem, monkeypatch, capsys):
    nomes = [f"n{i}" for i in range(10)]
    _notas(mem, *nomes)
    pares = [(nomes[2 * i], nomes[2 * i + 1]) for i in range(5)]
    out, _ = _correr(monkeypatch, capsys, pares, {p: {"acao": "arquivar", "manter": p[0], "confianca": 0.95, "motivo": "m"} for p in pares})
    assert out.count("ARQUIVAR") == 3 and out.count("3 arquivos esta noite") == 2, out


def test_par_com_uma_nota_ja_arquivada_nao_vai_a_ia(mem, monkeypatch, capsys):
    _notas(mem, "geral", "snapshot", "x", "y")
    p = mem.real / "snapshot.md"
    p.write_text(CAB.format(n="snapshot") + "> 🗄 Arquivada pelo Basalt a 2026-09-25: registo antigo. A informação actual está em [[geral]].\n\nT.\n", encoding="utf-8")
    os.utime(p, (VELHO, VELHO))
    out, pedidos = _correr(monkeypatch, capsys, [("geral", "snapshot"), ("x", "y")],
                           {("x", "y"): {"acao": "ligar", "manter": "x", "confianca": 0.9, "motivo": "m"}})
    assert pedidos == [("x", "y")], pedidos
    assert "já está arquivada" in out


def test_controlo_arquivar_legitimo_continua_a_arquivar(mem, monkeypatch, capsys):
    _notas(mem, "geral", "session_velha")
    out, _ = _correr(monkeypatch, capsys, [("geral", "session_velha")],
                     {("geral", "session_velha"): {"acao": "arquivar", "manter": "geral", "confianca": 0.92, "motivo": "m"}})
    assert "ARQUIVAR geral ↔ session_velha" in out, out


def test_controlo_unir_mantem_os_seus_travoes(mem, monkeypatch, capsys):
    _notas(mem, "a", "b", indice="- ⭐⭐⭐ [a](a.md) — x\n- [b](b.md) — y\n")
    out, _ = _correr(monkeypatch, capsys, [("a", "b")], {("a", "b"): {"acao": "unir", "manter": "a", "confianca": 0.95, "motivo": "m"}})
    assert "UNIR" not in out and "não uni: uma delas é ⭐⭐⭐" in out, out


def test_o_prompt_pede_os_factos_da_velha_na_principal(mem, monkeypatch):
    _notas(mem, "a", "b")
    visto = []

    class IA:
        def chat_json(self, prompt, tempo=0):
            visto.append(prompt)
            return {"acao": "ligar", "manter": "a", "confianca": 0.9, "motivo": "m"}
    monkeypatch.setattr(auto_arrumar._ia, "actual", lambda tarefa: IA())
    auto_arrumar.decidir("a", "b")
    assert "todos os factos da velha" in visto[0] and "é quase sempre arquivar" not in visto[0]
