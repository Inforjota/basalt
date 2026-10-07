"""importar um cofre Obsidian: nomes, ligações reescritas, cabeçalho, anexos, nunca sobrepor; exportar."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from basalt import config, obsidian   # noqa: E402
from basalt.config import Espaco      # noqa: E402


@pytest.fixture
def cofre(tmp_path):
    c = tmp_path / "Cofre"
    (c / "Tarefas").mkdir(parents=True); (c / "anexos").mkdir(); (c / ".obsidian").mkdir(); (c / ".trash").mkdir()
    (c / "Barcos do Pico.md").write_text("# Barcos do Pico\n\nSaem às 8.\n\n![[foto.png]]\n", encoding="utf-8")
    (c / "Tarefas" / "Hoje.md").write_text("Ver [[Barcos do Pico|barcos]].\n", encoding="utf-8")
    (c / "Com cabeçalho.md").write_text("---\ndescription: já tinha\n---\nVer [[Barcos do Pico|barcos]], [[Barcos do Pico#Horário]], "
                                        "[[Inexistente]] e [[Barcos do Pico]]; ![[foto.png]] e ![[falta.png]].\n", encoding="utf-8")
    (c / "anexos" / "foto.png").write_bytes(b"\x89PNG\r\n\x1a\n...")
    (c / ".obsidian" / "app.json").write_text("{}"); (c / ".trash" / "Velha.md").write_text("lixo")
    return c


@pytest.fixture
def esp(tmp_path):
    e = Espaco.de("ana", tmp_path / "dados").criar()
    (e.real / "hoje.md").write_text("---\nname: hoje\n---\nA MINHA nota de hoje.\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(e.real)], check=True)
    return e


def test_simular_nao_escreve(cofre, esp):
    antes = sorted(p.name for p in esp.real.rglob("*") if ".git" not in p.parts)
    r = obsidian.importar(cofre, esp, simular=True)
    assert sorted(p.name for p in esp.real.rglob("*") if ".git" not in p.parts) == antes
    assert r["importadas"] == ["barcos_do_pico", "com_cabecalho"] and r["anexos"] == ["foto.png"]
    assert [s[0] for s in r["saltadas"]] == ["Tarefas/Hoje.md", "falta.png"]


def test_importar(cofre, esp):
    r = obsidian.importar(cofre, esp)
    assert r["nomes"] == {"Barcos do Pico": "barcos_do_pico", "Hoje": "hoje", "Com cabeçalho": "com_cabecalho"}
    b = (esp.real / "barcos_do_pico.md").read_text(encoding="utf-8")
    assert b.startswith("---\nname: barcos_do_pico\ndescription: Barcos do Pico\nmetadata:\n  type: project\n---\n\n# Barcos do Pico")
    assert "![[foto.png]]" in b
    c = (esp.real / "com_cabecalho.md").read_text(encoding="utf-8")
    assert c.startswith("---\ndescription: já tinha\nname: com_cabecalho\n---\n")
    assert "[[barcos_do_pico|barcos]], [[barcos_do_pico|Barcos do Pico]], [[Inexistente]] e [[barcos_do_pico|Barcos do Pico]]" in c
    assert "![[foto.png]] e ![[falta.png]]" in c                                        # os embeds ficam como estão
    assert (esp.real / "hoje.md").read_text(encoding="utf-8").endswith("A MINHA nota de hoje.\n")   # nunca sobrepor
    assert ("Tarefas/Hoje.md", "já existe «hoje» no espaço") in r["saltadas"]
    assert (esp.anexos / "foto.png").read_bytes() == (cofre / "anexos" / "foto.png").read_bytes()
    assert not (esp.real / "velha.md").exists() and not (esp.real / "app.json").exists()
    log = subprocess.run(["git", "-C", str(esp.real), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "importação de 2 notas" in log
    # importar outra vez: nada se sobrepõe, tudo «já existe»
    r2 = obsidian.importar(cofre, esp)
    assert r2["importadas"] == [] and len(r2["saltadas"]) == 3   # os anexos só se verificam nas notas que entram


def test_colisoes_e_slug():
    assert obsidian.slug("Água, Pão & Vinho!") == "agua_pao_vinho" and obsidian.slug("é") == "nota_e" and obsidian.slug("") == "nota"
    assert len(obsidian.slug("x" * 300)) == 100


def test_exportar(cofre, esp, tmp_path):
    obsidian.importar(cofre, esp)
    fora = tmp_path / "fora"
    assert obsidian.exportar(fora, esp) == 3
    assert (fora / "anexos" / "foto.png").exists() and not (fora / ".git").exists()
    with pytest.raises(FileExistsError):
        obsidian.exportar(fora, esp)


def test_cli_importar_exportar(cofre, tmp_path):
    env = {**os.environ, "BASALT_CONFIG": ""}
    d = tmp_path / "dados"
    def cli(*a):
        return subprocess.run([sys.executable, "-m", "basalt", *a], cwd=RAIZ, capture_output=True, text=True, env=env)
    assert cli("iniciar", "--dados", str(d), "--email", "ana@exemplo.pt", "--senha", "segredo-forte").returncode == 0
    toml = str(d / "basalt.toml")
    r = cli("--config", toml, "importar", str(cofre), "--simular")
    assert r.returncode == 0 and "[simulação] 3 notas importadas, 1 anexos, 1 saltadas" in r.stdout, r.stderr
    assert not (d / "ana" / "memoria" / "barcos_do_pico.md").exists()
    r = cli("--config", toml, "importar", str(cofre))
    assert r.returncode == 0 and "3 notas importadas" in r.stdout and (d / "ana" / "memoria" / "hoje.md").exists()
    r = cli("--config", toml, "exportar", str(tmp_path / "fora"))
    assert r.returncode == 0 and "3 notas" in r.stdout and (tmp_path / "fora" / "hoje.md").exists()
