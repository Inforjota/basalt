"""Ronda 13b — servidor: o trinco da fusão, o guarda de segredos no resultado da fusão, o verificador lento na sincronização e
as categorias das fontes externas com «#».

Cada grupo diz o defeito que a revisão externa encontrou. Tudo em pastas temporárias; a truncagem prova-se num ficheiro de teste.
"""
import http.client
import json
import subprocess
import sys
import time

import pytest

from conftest import FM, RAIZ, esperar_porta, porta_livre, toml_de
from test_ronda13_servidor import verificador  # noqa: F401 (a fixture)
from test_unir import A, B, BOA, git


@pytest.fixture
def real(memoria):
    r = memoria.real
    r.mkdir(parents=True, exist_ok=True)
    (r / "nota_a.md").write_text(A)
    (r / "nota_b.md").write_text(B)
    (r / "outra.md").write_text("vê [[nota_b]]\n")
    git(r, "init", "-q")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "inicio")
    return r


# ═════════════════════════ #4 — o trinco da fusão não segue ligações nem trunca ═════════════════════════
def _trinco_ligado(real):
    """o trinco da fusão é uma ligação para um ficheiro com conteúdo (o que um open(…, "w") esvaziava)."""
    vitima = real.parent / "vitima.txt"
    vitima.write_text("conteúdo que não pode desaparecer\n")
    (real.parent / ".basalt-unir.lock").symlink_to(vitima)
    return vitima


def test_trinco_da_fusao_ligado_a_outro_ficheiro_recusa_e_nao_trunca(real):
    from basalt import unir
    vitima = _trinco_ligado(real)
    with pytest.raises(RuntimeError):
        unir.aplicar(real, "nota_a", "nota_b", BOA, A, B)
    assert vitima.read_text() == "conteúdo que não pode desaparecer\n"
    assert (real / "nota_a.md").read_text() == A and (real / "nota_b.md").read_text() == B
    assert git(real, "log", "--format=%s").splitlines() == ["inicio"]


def test_trinco_do_auto_arrumar_ligado_a_outro_ficheiro_recusa_e_nao_trunca(real):
    from basalt import auto_arrumar
    vitima = _trinco_ligado(real)
    chamado = []
    with pytest.raises(RuntimeError):
        auto_arrumar.mudar("ligar", "teste", lambda: chamado.append(1) or True)
    assert vitima.read_text() == "conteúdo que não pode desaparecer\n" and chamado == []


def test_controlo_trinco_normal_continua_a_unir(real):
    from basalt import unir
    h, _, feito = unir.aplicar(real, "nota_a", "nota_b", BOA, A, B)
    assert feito and not (real / "nota_b.md").exists()
    assert (real.parent / ".basalt-unir.lock").is_file() and not (real.parent / ".basalt-unir.lock").is_symlink()


# ═════════════════════════ #5 — o resultado da fusão passa pelo guarda comum (passwords em claro e o verificador) ═════════════════════════
def test_resultado_da_fusao_com_password_em_claro_e_recusado(real):
    from basalt import unir
    m = BOA.rstrip("\n") + " (senha: minhapalavrasecreta)\n"
    ok, motivo, _ = unir.validar(A, B, m, "nota_a", "nota_b")
    assert not ok and "segredo" in motivo
    with pytest.raises(RuntimeError):                     # e o aplicar também recusa, mesmo sem validar antes
        unir.aplicar(real, "nota_a", "nota_b", m, A, B)
    assert (real / "nota_a.md").read_text() == A and (real / "nota_b.md").exists()


def test_resultado_da_fusao_que_so_o_verificador_recusa_e_recusado(real, verificador):
    from basalt import unir
    verificador.ligar()
    for marca in ("MARCA-SECRETA", "MARCA-FALHA"):       # recusado pelo verificador / o verificador falhou
        m = BOA.rstrip("\n") + f" {marca}\n"
        ok, motivo, _ = unir.validar(A, B, m, "nota_a", "nota_b")
        assert not ok, (marca, motivo)
        with pytest.raises(RuntimeError):
            unir.aplicar(real, "nota_a", "nota_b", m, A, B)
    assert any("MARCA-SECRETA" in t for c in verificador.chamadas() for t in c["textos"])
    assert (real / "nota_a.md").read_text() == A and (real / "nota_b.md").exists()
    assert git(real, "log", "--format=%s").splitlines() == ["inicio"]


def test_controlo_fusao_limpa_com_verificador_passa(real, verificador):
    from basalt import unir
    verificador.ligar()
    assert unir.validar(A, B, BOA, "nota_a", "nota_b")[0]
    assert unir.aplicar(real, "nota_a", "nota_b", BOA, A, B)[2]


# ═════════════════════════ #6 — um verificador lento ou partido não prende a sincronização nem o arranque ═════════════════════════
FALHA_SEMPRE = """import sys
open(sys.argv[1], "a").write("x\\n")
sys.exit(2)
"""


def _notas_novas_na_copia(memoria, n):
    nomes = [f"n{i:03d}" for i in range(n)]
    for nome in nomes:
        (memoria.copia / f"{nome}.md").write_text(FM.format(nome=nome) + f"\nO barco {nome} sai às 8h.\n", encoding="utf-8")
    return {f"{x}.md" for x in nomes}


def test_verificador_que_falha_uma_chamada_por_ciclo_e_as_notas_voltam(memoria, verificador, monkeypatch, tmp_path):
    from basalt import duasvias, segredos
    monkeypatch.setattr(duasvias, "GIT", False)
    monkeypatch.setattr(duasvias, "ALERTAS", False)
    alertas = []
    monkeypatch.setattr(segredos, "_alertar", lambda porque: alertas.append(porque))
    script, registo = tmp_path / "falha.py", tmp_path / "falha.log"
    script.write_text(FALHA_SEMPRE, encoding="utf-8")
    toml = tmp_path / "basalt-falha.toml"
    toml.write_text(toml_de(0, tmp_path / "raiz", extra=f"[segredos]\nverificador = {json.dumps([sys.executable, str(script), str(registo)])}\n"),
                    encoding="utf-8")
    monkeypatch.setenv("BASALT_CONFIG", str(toml))
    novas = _notas_novas_na_copia(memoria, 40)
    for _ in range(2):                                   # cada ciclo: uma só chamada e um só ALERTA; nada se perde nem avança
        n0, a0 = len(registo.read_text().splitlines()) if registo.exists() else 0, len(alertas)
        r = duasvias.ciclo()
        assert len(registo.read_text().splitlines()) - n0 == 1 and len(alertas) - a0 == 1
        assert set(r["adiados"]) == novas and not r["empurrados"], r
        assert not any((memoria.real / x).exists() for x in novas) and all((memoria.copia / x).exists() for x in novas)
    verificador.ligar()                                  # o verificador volta a responder → as adiadas passam no ciclo seguinte
    r = duasvias.ciclo()
    assert set(r["empurrados"]) == novas and not r["adiados"], r
    assert all((memoria.real / x).exists() for x in novas)


def test_verificador_que_demora_um_so_tempo_limite_por_ciclo(memoria, verificador, monkeypatch):
    from basalt import duasvias
    monkeypatch.setattr(duasvias, "GIT", False)
    monkeypatch.setattr(duasvias, "ALERTAS", False)
    verificador.ligar(tempo=1)
    for i in range(6):
        (memoria.copia / f"d{i}.md").write_text(FM.format(nome=f"d{i}") + "\ndemora: MARCA-DORME\n", encoding="utf-8")
    t = time.time()
    r = duasvias.ciclo()
    assert time.time() - t < 4 and len(verificador.chamadas()) == 1, (time.time() - t, len(verificador.chamadas()))
    assert len(r["adiados"]) == 6


def test_o_arranque_atende_com_o_verificador_preso(tmp_path, verificador):
    porta, dados = porta_livre(), tmp_path / "dados-arranque"
    toml = tmp_path / "basalt-arranque.toml"
    toml.write_text(toml_de(porta, dados, extra=verificador.extra(tempo=20)), encoding="utf-8")
    from basalt import config
    real = config.carregar(toml).espaco("t").criar().real
    for i in range(5):                                   # instalação nova: notas na memória real, a cópia vazia
        (real / f"d{i}.md").write_text(FM.format(nome=f"d{i}") + "\ndemora: MARCA-DORME\n", encoding="utf-8")
    codigo = f"import sys; from basalt import config, web; web.servir(config.carregar({str(toml)!r}), vigias=True)"
    p = subprocess.Popen([sys.executable, "-c", codigo], cwd=str(RAIZ), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        esperar_porta(porta, segundos=8, proc=p)         # antes: só abria depois de 5 notas × 20 s (+ a lista das retidas)
        c = http.client.HTTPConnection("127.0.0.1", porta, timeout=5)
        c.request("GET", "/api/saude")
        assert c.getresponse().status == 200
        c.close()
        fim = time.time() + 5
        while not verificador.chamadas() and time.time() < fim:
            time.sleep(0.05)
        assert len(verificador.chamadas()) == 1          # o arranque está no 1.º tempo limite, não percorreu as notas
    finally:
        p.terminate()
        p.wait(10)
