"""etiquetas: extrair como a interface (fora de código/URL/ligações, sem cores), cabeçalho tags:, contagem por espaço."""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from basalt import etiquetas                 # noqa: E402
from basalt.config import Espaco             # noqa: E402


def test_extrair():
    t = ("---\nname: x\ntags: [projecto, Ideia-boa]\n---\n"
         "Texto com #barcos e #Barcos e #mar/pico e cor #fff e #1a2b3c e `#codigo` e https://x.pt/#ancora\n"
         "```\n#dentro_de_cerca\n```\n[[nota#sec]] [[#outra]] a#b c#d #_x #-y #ok- #ok2/ #tag_final.\n")
    assert etiquetas.extrair(t) == ["projecto", "Ideia-boa", "barcos", "mar/pico", "ok", "ok2", "tag_final"]
    assert etiquetas.extrair("") == [] and etiquetas.extrair("sem nada # solto #") == []
    assert etiquetas.extrair("tags: a, b\n#c") == ["c"]           # «tags:» só conta no cabeçalho


def test_contar_e_espaco(tmp_path):
    esp = Espaco.de("ana", tmp_path / "d").criar()
    (esp.real / "a.md").write_text("#Barcos e #mar\n", encoding="utf-8")
    (esp.real / "b.md").write_text("#barcos outra vez\n", encoding="utf-8")
    (esp.real / "c.md").write_text("#zebra #mar\n", encoding="utf-8")
    (esp.real / "lixo.txt").write_text("#nao_conta\n", encoding="utf-8")
    assert etiquetas.do_espaco(esp) == [{"etiqueta": "Barcos", "notas": 2}, {"etiqueta": "mar", "notas": 2}, {"etiqueta": "zebra", "notas": 1}]
    assert etiquetas.contar(["#a #a #a", "#A"]) == {"a": 2}
