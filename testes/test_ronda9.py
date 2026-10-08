"""ronda 9 (08/10/2026) — a ronda seguinte da auditoria externa, portada da instalação de origem:
  servidor (basalt.segredos, basalt.unir)
    A1 «ronda 9 (contexto e descrições)» o prazo de uma declaração SEM «:» contaminava a seguinte: «senha válida até
       2026-10-06 password: 2026-10-07» (e com «;» no meio) passava — a declaração começa agora na ÚLTIMA palavra-chave e o
       texto de antes corta-se em separador, palavra-chave ou data/hora;
    A9 «ronda 9 (contexto e descrições)» a regra das passwords só de letras apanhava descrições: «Estado da senha:
       sincronizada», «Tipo de password: alfanumérica»;
    A10 «ronda 9 (releitura com erro de disco)» um EIO a reler a nota que já tinha falhado no confirmar() cortava o relato do
       recuo — saía «OSError: …» cru.
Cada teste de caso falha na versão anterior à ronda 9 e passa nesta; os controlos (C1, C2) passam nas duas.
"""
import errno
from pathlib import Path

import pytest

from basalt import cas, unir
from basalt.segredos import tem_segredo_texto
from test_ronda7 import _correr, _estado, _ler, rapido, _sem_disco  # noqa: F401
from test_unir import A, B, real, git  # noqa: F401  — a memória de rascunho com git e as notas A/B/outra/MEMORY


# A1 «ronda 9 (contexto e descrições)» — o prazo da declaração anterior não desculpa a seguinte
@pytest.mark.parametrize("linha, e_segredo", [
    ("senha válida até 2026-10-06 password: 2026-10-07", True),
    ("senha válida até 2026-10-06; password: 2026-10-07", True),
    ("a senha expira 06/10/2026 e a password: 10:30:59", True),
    ("validade 2027 senha: 2026-10-06", True),
    ("password válida até 2026-10-06 senha = 06/10/2026", True),
    ("senha válida até 2027 password: 2026-10-07", True),
    ("senha expira em 30 dias password: 10:30:59", True),
    # continuam a não ser password: o prazo é da própria declaração
    ("senha válida até: 2026-10-06", False),
    ("Validade da senha: 2026-10-06", False),
    ("a senha expira: 06/10/2026", False),
    ("senha válida até: 2026-10-06; password expira: 2026-10-07", False),
    ("próxima renovação da senha: 2026-12-01", False),
])
def test_prazo_de_uma_declaracao_nao_desculpa_a_seguinte(linha, e_segredo):
    assert bool(tem_segredo_texto(linha)) == e_segredo, f"«{linha}» devia ser {'segredo' if e_segredo else 'texto'}"


# A9 «ronda 9 (contexto e descrições)» — descrever a password não é escrevê-la
@pytest.mark.parametrize("linha, e_segredo", [
    ("Estado da senha: sincronizada", False),
    ("Tipo de password: alfanumérica", False),
    ("type of password: alphanumerical", False),
    ("Força da senha: intermédia", False),
    ("Formato das senhas: automaticamente", False),
    ("senha: criptografada", False),
    # as passwords só de letras de sempre continuam recusadas
    ("senha: minhapalavrasecreta", True),
    ("password: correcthorsebatterystaple", True),
    ("senha: TopSecreto", True),
    ("A senha do router: palavrasecretissima", True),
    ("senha do wifi: OlaMundoGrande", True),
])
def test_descricoes_da_password_sao_texto_e_as_passwords_de_letras_continuam_recusadas(linha, e_segredo):
    assert bool(tem_segredo_texto(linha)) == e_segredo, f"«{linha}» devia ser {'segredo' if e_segredo else 'texto'}"



# «ronda 9 (passwords só de letras, outra vez)» — a excepção das descrições deixava passar passwords: um sufixo de adjectivo
# chegava («-ada», «-ico», «-mente», «-ção»), «da»/«the» antes da palavra-chave também, e a maiúscula no meio via-se depois.
# Cada caso marcado True falha no código anterior; os False são controlos (descrições que continuam a ser texto).
@pytest.mark.parametrize("linha, e_segredo", [
    ("senha: minhapalavracomplicada", True),
    ("senha: meuacessomagico", True),
    ("senha: abacaximente", True),
    ("senha: superautenticação", True),
    ("O valor da senha: minhaSenhaSecreta", True),
    ("O valor da senha: minhapalavracomplicada", True),
    ("The password: correcthorsebattery", True),
    ("Estado da senha: minhapalavracomplicada", True),
    ("Estado da senha: sincronizada", False),
    ("Formato das senhas: automaticamente", False),
    ("senha: criptografada", False),
])
def test_descricao_so_com_propriedade_e_palavra_curta(linha, e_segredo):
    assert bool(tem_segredo_texto(linha)) == e_segredo, f"«{linha}» devia ser {'segredo' if e_segredo else 'texto'}"

# C1 — controlo (passa antes e depois)
@pytest.mark.parametrize("linha, e_segredo", [
    ("senha do router: Tr0ub4dor&3", True), ("password = hunter2!", True), ("senha: 2026-10-06", True),
    ("senha: minhapalavrasecreta", True), ("Validade da senha: 2026-10-06", False), ("senha: está no cofre", False),
    ("mudar a password todos os meses", False), ("senha: alterada", False), ("senha válida até: 2026-10-06", False),
])
def test_controlo_passwords_de_sempre_e_conversa_sobre_passwords(linha, e_segredo):
    assert bool(tem_segredo_texto(linha)) == e_segredo, f"«{linha}» devia ser {'segredo' if e_segredo else 'texto'}"


def _confirmar_falha_a(monkeypatch):
    """o confirmar() da fusão devolve a troca de nota_a como falhada («mudou no mesmo instante»); os seguintes são os de
    verdade. Devolve a lista que conta as chamadas."""
    original, fase = cas.confirmar, [0]

    def confirmar(lista, *a, **k):
        fase[0] += 1
        if fase[0] == 1:
            original(lista, *a, **k)
            return [t for t in lista if t is not None and Path(t.alvo).name == "nota_a.md"]
        return original(lista, *a, **k)

    monkeypatch.setattr(cas, "confirmar", confirmar)
    return fase


# A10 «ronda 9 (releitura com erro de disco)». Critério: o confirmar() original dá nota_a como falhada; o EIO só acontece a
# ler nota_a DEPOIS disso (no recuo). Antes: saía «OSError: …» cru, sem o relato das outras notas.
def test_recuo_eio_a_reler_a_nota_que_falhou_o_erro_e_o_relato_e_nao_um_oserror_cru(real, rapido, monkeypatch):
    outra = _ler(real, "outra.md")
    fase = _confirmar_falha_a(monkeypatch)
    ler_original, eio = cas._ler, []

    def ler_com_eio(p):
        if fase[0] >= 1 and Path(p).name == "nota_a.md":
            eio.append(1)
            raise OSError(errno.EIO, "Input/output error", str(p))
        return ler_original(p)

    monkeypatch.setattr(cas, "_ler", ler_com_eio)
    erro = _correr(real)
    monkeypatch.setattr(cas, "_ler", ler_original)
    estado = _estado(real, outra)
    assert eio and erro, f"o EIO e a falha deviam ter acontecido: EIO em {len(eio)} leitura(s), erro {erro!r}"   # controlo
    assert not erro.startswith("OSError"), f"o erro saiu cru, sem relato · erro: {erro!r}"
    assert "recuei o que pude" in erro and "«nota_a»" in erro and "Input/output error" in erro, f"o erro não é o relato: {erro!r}"
    assert (_ler(real, "nota_b.md"), _ler(real, "outra.md")) == (B, outra), estado


# C2 — controlo: a mesma falha no confirmar(), sem EIO → relato com nota_a; B e outra de antes
def test_controlo_confirmar_da_nota_a_como_falhada_sem_eio_relato_e_as_outras_de_antes(real, rapido, monkeypatch):
    outra = _ler(real, "outra.md")
    _confirmar_falha_a(monkeypatch)
    erro = _correr(real)
    estado = _estado(real, outra)
    assert erro and not erro.startswith("OSError") and "«nota_a»" in erro, f"erro: {erro!r}"
    assert (_ler(real, "nota_b.md"), _ler(real, "outra.md")) == (B, outra), estado
