"""anexos: nomes seguros, servir pelo sufixo, guardar sem sobrepor, referidos/órfãos."""
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from basalt import anexos                # noqa: E402
from basalt.config import Espaco         # noqa: E402


@pytest.fixture
def esp(tmp_path):
    return Espaco.de("ana", tmp_path / "dados").criar()


@pytest.mark.parametrize("nome", ["foto.png", "Pasted image 20260101.png", "relatório-final_v2.pdf", "a.b.c.webp", "x1.svg"])
def test_nomes_bons(nome):
    assert anexos.nome_ok(nome)


@pytest.mark.parametrize("nome", ["", "../foto.png", "a/b.png", "x.exe", ".png", "foto", "foto.PNG.", " foto.png", "nota.md",
                                  "c:\\x.png", "a" * 130 + ".png", "foto.png\n", "<img>.png"])
def test_nomes_maus(nome):
    assert not anexos.nome_ok(nome)


def test_ler(esp):
    assert anexos.ler(esp, "../etc/passwd.txt")[0] == 400
    assert anexos.ler(esp, "nada.png")[0] == 404
    esp.anexos.mkdir(parents=True, exist_ok=True)
    (esp.anexos / "foto.png").write_bytes(b"<script>alert(1)</script>")   # conteúdo hostil com sufixo de imagem
    est, cab, corpo = anexos.ler(esp, "foto.png")
    assert est == 200 and cab["Content-Type"] == "image/png" and cab["X-Content-Type-Options"] == "nosniff" and corpo.startswith(b"<script")
    (esp.anexos / "d.svg").write_bytes(b"<svg/>")
    assert "default-src 'none'" in anexos.ler(esp, "d.svg")[1]["Content-Security-Policy"]
    (esp.anexos / "n.json").write_bytes(b"{}")
    assert anexos.ler(esp, "n.json")[1]["Content-Type"] == "application/json; charset=utf-8"
    (esp.anexos / "sub").mkdir()
    assert anexos.ler(esp, "sub.png")[0] == 404


def test_guardar_nunca_sobrepoe(esp):
    assert anexos.guardar(esp, "foto.png", b"AAA") == "foto.png"
    assert anexos.guardar(esp, "foto.png", b"AAA") == "foto.png"          # mesmo conteúdo: reutiliza
    assert anexos.guardar(esp, "foto.png", b"BBB") == "foto_2.png"        # outro conteúdo: nome novo
    assert anexos.guardar(esp, "foto.png", b"CCC") == "foto_3.png"
    assert (esp.anexos / "foto.png").read_bytes() == b"AAA"
    with pytest.raises(ValueError):
        anexos.guardar(esp, "../x.png", b"x")
    with pytest.raises(ValueError):
        anexos.guardar(esp, "grande.png", b"x" * (anexos.TAMANHO_MAX + 1))
    assert [a["nome"] for a in anexos.listar(esp)] == ["foto.png", "foto_2.png", "foto_3.png"]
    assert anexos.listar(esp)[0]["imagem"] is True and anexos.listar(esp)[0]["bytes"] == 3


def test_referidos_e_orfaos(esp):
    t = "Ver ![[foto.png]] e ![[Pasted image 1.png|300]] e ![[outra_nota]] e ![[doc.pdf#p2]] e ![[../mau.png]]."
    assert anexos.referidos(t) == {"foto.png", "Pasted image 1.png", "doc.pdf"}
    anexos.guardar(esp, "foto.png", b"1"); anexos.guardar(esp, "solto.png", b"2")
    assert anexos.orfaos(esp, [t, "nada"]) == ["solto.png"]
