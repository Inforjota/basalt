"""Ronda 13c — a sincronização com um [segredos] verificador que falha SEMPRE numa nota concreta.

Ronda 13b: depois da 1.ª falha do verificador num ciclo, as notas seguintes ficam adiadas sem o chamar (uma chamada falhada por
ciclo). Mas o ciclo percorre as notas sempre pela mesma ordem: uma nota em que o verificador falha sempre (um tempo esgotado numa
nota enorme, um erro só com ela) adiava, em TODOS os ciclos, todas as que vêm depois dela — essas nunca chegavam a sincronizar.
Agora a nota que fez o verificador falhar fica para o FIM do ciclo seguinte: as outras verificam-se primeiro.
"""
import pytest

from conftest import FM
from test_ronda13_servidor import verificador  # noqa: F401 (a fixture)


@pytest.fixture
def sincronizacao(memoria, verificador, monkeypatch):
    from basalt import duasvias, segredos
    monkeypatch.setattr(duasvias, "GIT", False)
    monkeypatch.setattr(duasvias, "ALERTAS", False)
    monkeypatch.setattr(duasvias, "_CULPADAS", {}, raising=False)
    alertas = []
    monkeypatch.setattr(segredos, "_alertar", lambda porque: alertas.append(porque))
    verificador.ligar()
    memoria.alertas = alertas
    return memoria


def _na_copia(memoria, nome, texto):
    (memoria.copia / f"{nome}.md").write_text(FM.format(nome=nome) + f"\n{texto}\n", encoding="utf-8")


def test_a_nota_que_faz_o_verificador_falhar_nao_adia_as_outras_para_sempre(sincronizacao, verificador):
    from basalt import duasvias
    m = sincronizacao
    _na_copia(m, "a_limpa", "O barco sai às 8h.")
    _na_copia(m, "b_partida", "Esta faz o verificador falhar: MARCA-FALHA.")
    _na_copia(m, "c_limpa", "A reunião é às 10h.")
    r = duasvias.ciclo()                                 # 1.º ciclo: a passa, b falha (a única chamada falhada), c fica adiada
    assert r["empurrados"] == ["a_limpa.md"] and set(r["adiados"]) == {"b_partida.md", "c_limpa.md"}, r
    assert len(verificador.chamadas()) == 2 and len(m.alertas) == 1
    r = duasvias.ciclo()                                 # 2.º ciclo: b vai para o fim — c verifica-se e passa; b volta a falhar
    assert r["empurrados"] == ["c_limpa.md"] and r["adiados"] == ["b_partida.md"], r
    assert (m.real / "c_limpa.md").exists() and not (m.real / "b_partida.md").exists()
    assert len(verificador.chamadas()) == 4              # c e b: uma chamada cada, e só uma falhada
    r = duasvias.ciclo()                                 # 3.º ciclo: só b, que continua a falhar — nada se perde, nada avança nela
    assert r["adiados"] == ["b_partida.md"] and not r["empurrados"], r
    assert len(verificador.chamadas()) == 5


def test_a_nota_culpada_deixa_de_ir_para_o_fim_quando_passa(sincronizacao, verificador):
    from basalt import duasvias
    m = sincronizacao
    _na_copia(m, "b_partida", "Esta faz o verificador falhar: MARCA-FALHA.")
    _na_copia(m, "c_limpa", "A reunião é às 10h.")
    duasvias.ciclo()
    assert duasvias.culpadas() == {"b_partida.md"}
    r = duasvias.ciclo()
    assert r["empurrados"] == ["c_limpa.md"] and r["adiados"] == ["b_partida.md"], r
    assert duasvias.culpadas() == {"b_partida.md"}                           # continua culpada: volta ao fim no ciclo seguinte
    _na_copia(m, "b_partida", "Já não faz falhar nada.")                     # corrigida na cópia
    r = duasvias.ciclo()
    assert r["empurrados"] == ["b_partida.md"] and not r["adiados"], r
    assert not duasvias.culpadas()
    assert (m.real / "b_partida.md").read_text(encoding="utf-8").endswith("Já não faz falhar nada.\n")


def test_controlo_sem_falhas_a_ordem_e_a_de_sempre_e_uma_chamada_por_nota(sincronizacao, verificador):
    from basalt import duasvias
    m = sincronizacao
    for nome in ("c_limpa", "a_limpa", "b_limpa"):
        _na_copia(m, nome, "O barco sai às 8h.")
    r = duasvias.ciclo()
    assert r["empurrados"] == ["a_limpa.md", "b_limpa.md", "c_limpa.md"] and not r["adiados"], r
    assert len(verificador.chamadas()) == 3 and not m.alertas and not duasvias.culpadas()


def test_duas_notas_que_fazem_o_verificador_falhar_nao_adiam_as_outras_para_sempre(sincronizacao, verificador):
    """com DUAS notas em que o verificador falha sempre, as culpadas não podem revezar-se à frente das outras: se só a do último
    ciclo fosse para o fim, a outra voltava ao seu lugar, falhava lá e as notas depois das duas ficavam adiadas para sempre."""
    from basalt import duasvias
    m = sincronizacao
    _na_copia(m, "b_partida", "Esta faz o verificador falhar: MARCA-FALHA.")
    _na_copia(m, "d_partida", "Esta também: MARCA-FALHA.")
    _na_copia(m, "e_limpa", "A reunião é às 10h.")
    vistos = []
    for _ in range(5):
        vistos += duasvias.ciclo()["empurrados"]
    assert "e_limpa.md" in vistos and (m.real / "e_limpa.md").exists(), vistos
    assert not (m.real / "b_partida.md").exists() and not (m.real / "d_partida.md").exists()
    assert duasvias.culpadas() == {"b_partida.md", "d_partida.md"}


def test_culpada_de_passagem_nao_fica_para_sempre_atras_de_uma_que_falha_sempre(sincronizacao, verificador):
    """uma nota que fez o verificador falhar UMA vez (corrigida depois) não pode ficar presa atrás de outra que falha sempre: entre
    as culpadas, a que falhou há mais tempo vai primeiro."""
    from basalt import duasvias
    m = sincronizacao
    _na_copia(m, "b_partida", "Esta faz o verificador falhar: MARCA-FALHA.")
    _na_copia(m, "d_partida", "Esta também: MARCA-FALHA.")
    duasvias.ciclo()                                                         # b falha; d adiada
    duasvias.ciclo()                                                         # d falha (b foi para o fim e ficou adiada)
    _na_copia(m, "b_partida", "Já não faz falhar nada.")                     # b corrigida; d continua a falhar sempre
    vistos = []
    for _ in range(3):
        vistos += duasvias.ciclo()["empurrados"]
    assert "b_partida.md" in vistos, vistos
    assert duasvias.culpadas() == {"d_partida.md"}
