"""Ronda 20 (auditoria 12 h no lugar do auditor externo, 23/10): o ARQUIVAR automático
- verifica, como o UNIR, que a nota velha não tem factos que a principal não tenha (a regra já estava no pedido à IA e no
  CHANGELOG da r19, mas só a IA a cumpria — o código arquivava o que ela mandasse, até na direcção invertida);
- salta QUALQUER par com uma nota já arquivada (marca «🗄 Arquivada» ou alvo de «Histórico: [[X]]» noutra nota), não só o par
  (principal, velha) — a velha emparelhada com uma terceira nota voltava à IA todas as noites;
- conta o tecto de 3 por DIA (com o decididos.json), não por corrida — uma 2.ª corrida à mão no mesmo dia dava mais 3;
- uma nota que desaparece DURANTE a decisão da IA (até 2×600 s) salta o par em vez de matar o ciclo (o salto da r19 era só
  antes da IA).
"""
import json, os, time

import pytest

from basalt import auto_arrumar, unir

CAB = "---\nname: {n}\ndescription: d\n---\n"
VELHO = time.time() - 7 * 3600
PRINCIPAL = "O servidor fica no LXC 104 e responde na porta 8121.\n"
VELHA_CONTIDA = "Porta 8121 no LXC 104.\n"
VELHA_COM_FACTOS = "Porta 8121; o script é /opt/rag/rag_server.py e o comando `systemctl restart rag-server`, mudado a 25/09.\n"


@pytest.fixture
def mem(memoria, monkeypatch):
    monkeypatch.setattr(auto_arrumar, "SIMULAR", True)
    return memoria


def _notas(mem, *nomes, indice="", corpo=None):
    corpo = corpo or {}
    for n in nomes:
        mem.nota(n, CAB.format(n=n) + corpo.get(n, "Texto.\n"))
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


def _arq(manter, c=0.95):
    return {"acao": "arquivar", "manter": manter, "confianca": c, "motivo": "m"}


# ── A: factos da velha que a principal não tem ─────────────────────────────────────────────────────────────────────────────────

def test_arquivar_com_factos_so_na_velha_passa_a_ligar(mem, monkeypatch, capsys):
    """a IA diz «arquivar 0.95» mas a velha tem um caminho, um comando e uma data que a principal não tem: no ensaio de 23/10,
    em todos os 24 pares cada nota tinha factos só dela e o gabarito era LIGAR — o código tem de o garantir, não só o prompt."""
    _notas(mem, "principal", "velha", corpo={"principal": PRINCIPAL, "velha": VELHA_COM_FACTOS})
    out, _ = _correr(monkeypatch, capsys, [("principal", "velha")], {("principal", "velha"): _arq("principal")})
    assert "ARQUIVAR" not in out and "LIGAR" in out and "factos que a principal não tem" in out, out
    assert "/opt/rag/rag_server.py" in out or "systemctl restart rag-server" in out or "25/09" in out, out


def test_controlo_arquivar_com_todos_os_factos_na_principal_continua_a_arquivar(mem, monkeypatch, capsys):
    _notas(mem, "principal", "velha", corpo={"principal": PRINCIPAL, "velha": VELHA_CONTIDA})
    out, _ = _correr(monkeypatch, capsys, [("principal", "velha")], {("principal", "velha"): _arq("principal")})
    assert "ARQUIVAR principal ↔ velha" in out, out


def test_arquivar_na_direccao_invertida_nao_arquiva_a_principal(mem, monkeypatch, capsys):
    """a IA troca o «manter» (fica a nota de sessão, arquiva-se a principal): a principal tem factos que a sessão não tem →
    liga em vez de a tirar do MEMORY.md."""
    _notas(mem, "principal", "session_velha", corpo={"principal": PRINCIPAL, "session_velha": "Sessão de ontem.\n"})
    out, _ = _correr(monkeypatch, capsys, [("principal", "session_velha")], {("principal", "session_velha"): _arq("session_velha")})
    assert "ARQUIVAR" not in out and "factos que a principal não tem" in out, out


def test_a_verificacao_de_factos_e_a_mesma_do_unir():
    """o mesmo contador do unir.validar (100 % dos factos), para o arquivar e o unir discordarem o menos possível."""
    assert auto_arrumar._factos_so_na_velha(VELHA_COM_FACTOS, PRINCIPAL) == {x for x in unir.factos(VELHA_COM_FACTOS) if not unir.tem_facto(x, PRINCIPAL)}
    assert auto_arrumar._factos_so_na_velha(VELHA_CONTIDA, PRINCIPAL) == set()
    assert "8121" not in auto_arrumar._factos_so_na_velha("Porta 8121.\n", "Porta 81210.\n") or True  # tem_facto: inteiro, não sub-texto
    assert auto_arrumar._factos_so_na_velha("Porta 8121.\n", "Porta 81210.\n") == {"8121"}


# ── B: notas já arquivadas, com QUALQUER outra nota ───────────────────────────────────────────────────────────────────────────

def test_nota_arquivada_sem_marca_nao_vai_a_ia_com_uma_terceira_nota(mem, monkeypatch, capsys):
    """caso real (par 20 do ensaio): a session_2026_05_22 foi arquivada a 25/09 debaixo da project_saas (só «Histórico: [[…]]» na
    principal, sem marca na velha) e aparece emparelhada com a session_2026_05_24. Na r19 esse par ia à IA (o qwen arquivava-a
    outra vez, debaixo da nota errada); agora salta-se."""
    _notas(mem, "principal", "velha", "terceira", indice="- [principal](principal.md) — x\n- [terceira](terceira.md) — x\n")
    p = mem.real / "principal.md"
    p.write_text(CAB.format(n="principal") + "T.\n\nHistórico: [[velha]] (registo antigo, arquivado pelo Basalt a 2026-09-25)\n", encoding="utf-8")
    os.utime(p, (VELHO, VELHO))
    out, pedidos = _correr(monkeypatch, capsys, [("velha", "terceira")], {("velha", "terceira"): _arq("terceira")})
    assert pedidos == [], pedidos
    assert "ARQUIVAR" not in out and "já está arquivada" in out, out


def test_nota_com_marca_nao_vai_a_ia_com_uma_terceira_nota(mem, monkeypatch, capsys):
    _notas(mem, "principal", "snap", "terceira")
    p = mem.real / "snap.md"
    p.write_text(CAB.format(n="snap") + "> 🗄 Arquivada pelo Basalt a 2026-09-25: registo antigo. A informação actual está em [[principal]].\n\nT.\n", encoding="utf-8")
    os.utime(p, (VELHO, VELHO))
    out, pedidos = _correr(monkeypatch, capsys, [("terceira", "snap")], {("terceira", "snap"): _arq("terceira")})
    assert pedidos == [] and "já está arquivada" in out, (pedidos, out)


def test_nomes_arquivados_ve_a_marca_e_o_historico_de_qualquer_nota(mem):
    _notas(mem, "principal", "velha", "snap", "solta")
    (mem.real / "principal.md").write_text(CAB.format(n="principal") + "Histórico: [[velha]] (registo antigo, arquivado pelo Basalt a 2026-09-25)\n", encoding="utf-8")
    (mem.real / "snap.md").write_text(CAB.format(n="snap") + "> 🗄 Arquivada pelo Basalt a 2026-10-01: registo antigo. A informação actual está em [[principal]].\n", encoding="utf-8")
    assert auto_arrumar._nomes_arquivados() == {"velha", "snap"}


def test_controlo_par_sem_nota_arquivada_continua_a_ir_a_ia(mem, monkeypatch, capsys):
    _notas(mem, "a", "b")
    out, pedidos = _correr(monkeypatch, capsys, [("a", "b")], {("a", "b"): {"acao": "ligar", "manter": "a", "confianca": 0.9, "motivo": "m"}})
    assert pedidos == [("a", "b")] and "LIGAR    a ↔ b" in out, out


# ── C: o tecto é por dia ──────────────────────────────────────────────────────────────────────────────────────────────────────

def _decididos_de_hoje(mem, acao, n):
    f = mem.esp.auto_arrumar / "decididos.json"
    f.write_text(json.dumps({f"p{i}|q{i}": {"acao": acao, "ts": time.time() - 600, "data": auto_arrumar.HOJE, "motivo": "m"} for i in range(n)}), encoding="utf-8")


def test_o_tecto_de_3_arquivos_e_por_dia_nao_por_corrida(mem, monkeypatch, capsys):
    """2.ª corrida do `arrumar --auto` no mesmo dia (à mão, ou o timer com Persistent=true depois de um reinício): já houve 3 → trava."""
    _notas(mem, "principal", "velha", corpo={"principal": PRINCIPAL, "velha": VELHA_CONTIDA})
    _decididos_de_hoje(mem, "arquivar", 3)
    out, _ = _correr(monkeypatch, capsys, [("principal", "velha")], {("principal", "velha"): _arq("principal")})
    assert "ARQUIVAR" not in out and "3 arquivos esta noite" in out, out


def test_o_tecto_de_3_fusoes_e_por_dia_nao_por_corrida(mem, monkeypatch, capsys):
    _notas(mem, "a", "b")
    _decididos_de_hoje(mem, "unir", 3)
    out, _ = _correr(monkeypatch, capsys, [("a", "b")], {("a", "b"): {"acao": "unir", "manter": "a", "confianca": 0.95, "motivo": "m"}})
    assert "UNIR" not in out and "3 fusões esta noite" in out, out


def test_controlo_decididos_de_ontem_nao_contam_para_o_tecto(mem, monkeypatch, capsys):
    _notas(mem, "principal", "velha", corpo={"principal": PRINCIPAL, "velha": VELHA_CONTIDA})
    f = mem.esp.auto_arrumar / "decididos.json"
    f.write_text(json.dumps({f"p{i}|q{i}": {"acao": "arquivar", "ts": time.time() - 86400, "data": "2000-01-01", "motivo": "m"} for i in range(3)}), encoding="utf-8")
    out, _ = _correr(monkeypatch, capsys, [("principal", "velha")], {("principal", "velha"): _arq("principal")})
    assert "ARQUIVAR principal ↔ velha" in out, out


# ── D: nota apagada DURANTE a decisão da IA ───────────────────────────────────────────────────────────────────────────────────

def test_nota_apagada_durante_a_decisao_da_ia_salta_o_par(mem, monkeypatch, capsys):
    """o salto da r19 («já não existe») é ANTES da IA; a chamada demora até 2×600 s e uma sessão pode apagar a nota entretanto —
    o ler(outra) dos travões rebentava com FileNotFoundError e a noite acabava ali (sem decididos.json nem resumo)."""
    _notas(mem, "a", "b", "c", "d")
    monkeypatch.setattr(auto_arrumar.arr, "duplicados", lambda: [(0.97, "a", "b"), (0.97, "c", "d")])
    pedidos = []

    def decidir(x, y):
        pedidos.append((x, y))
        if (x, y) == ("a", "b"):
            (mem.real / "b.md").unlink()          # apagada por outra sessão enquanto a IA pensava
        return _arq(x) if (x, y) == ("a", "b") else {"acao": "ligar", "manter": x, "confianca": 0.9, "motivo": "m"}
    monkeypatch.setattr(auto_arrumar, "decidir", decidir)
    auto_arrumar._correr()
    out = capsys.readouterr().out
    assert pedidos == [("a", "b"), ("c", "d")], pedidos
    assert "ARQUIVAR" not in out and "desapareceu" in out and "LIGAR    c ↔ d" in out, out


def test_o_salto_por_nota_desaparecida_nao_engole_um_git_em_falta(mem, monkeypatch, capsys):
    """o except é só para notas (.md): um `git` ausente no PATH continua a rebentar, como antes, para se ver."""
    _notas(mem, "a", "b")
    monkeypatch.setattr(auto_arrumar, "SIMULAR", False)
    monkeypatch.setattr(auto_arrumar.arr, "duplicados", lambda: [(0.97, "a", "b")])
    monkeypatch.setattr(auto_arrumar, "decidir", lambda x, y: {"acao": "ligar", "manter": x, "confianca": 0.9, "motivo": "m"})

    def git_ausente(*a, **k):
        raise FileNotFoundError(2, "No such file or directory", "git")
    monkeypatch.setattr(auto_arrumar.unir, "_git", git_ausente)
    with pytest.raises(FileNotFoundError):
        auto_arrumar._correr()
