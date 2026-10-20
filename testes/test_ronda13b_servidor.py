"""Ronda 13b — servidor: o trinco da fusão, o guarda de segredos no resultado da fusão, o verificador lento na sincronização e
as categorias das fontes externas com «#».

Cada grupo diz o defeito que a revisão externa encontrou. Tudo em pastas temporárias; a truncagem prova-se num ficheiro de teste.
"""

import pytest

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
