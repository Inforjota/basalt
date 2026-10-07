"""Ronda 8 (escrita sem sobrepor): a importação Obsidian e a arrumação automática escrevem com CAS — nunca sobrepõem o que outro escreveu entretanto.
Ronda 8 (ligações simbólicas): uma ligação simbólica com nome de nota ou de anexo não sai do espaço — não se lista, não se serve, não se importa nem exporta.
Cada teste falha na versão anterior e passa com esta."""
import subprocess

import pytest

from basalt import anexos, auto_arrumar, duasvias, obsidian, sig, unir, web
from basalt.config import Espaco

CAB = "---\nname: {n}\n---\n"


@pytest.fixture
def esp(tmp_path):
    e = Espaco.de("ana", tmp_path / "dados").criar()
    subprocess.run(["git", "init", "-q", str(e.real)], check=True)
    return e


def _git(memoria):
    subprocess.run(["git", "init", "-q", str(memoria.real)], check=True)


class TestG9EscreverComCAS:
    def test_importar_nao_sobrepoe_uma_nota_que_apareceu_entretanto(self, tmp_path, esp, monkeypatch):
        cofre = tmp_path / "Cofre"; cofre.mkdir()
        (cofre / "Barcos.md").write_text("# Barcos\n\nDo cofre.\n", encoding="utf-8")
        n = obsidian.importar(cofre, esp, simular=True)["importadas"][0]
        destino = esp.real / f"{n}.md"
        original = obsidian._com_nome

        def entretanto(nome, texto):                 # outro processo cria a mesma nota entre o «existe?» e a escrita
            destino.write_text("concorrente\n", encoding="utf-8")
            return original(nome, texto)
        monkeypatch.setattr(obsidian, "_com_nome", entretanto)
        r = obsidian.importar(cofre, esp)
        assert destino.read_text(encoding="utf-8") == "concorrente\n", "a importação sobrepôs a nota criada entretanto"
        assert n not in r["importadas"]
        assert any(f"já existe «{n}»" in porque for _, porque in r["saltadas"])

    def test_ligar_nao_perde_uma_edicao_feita_entre_a_leitura_e_a_escrita(self, memoria, monkeypatch):
        memoria.nota("a", CAB.format(n="a") + "A.\n"); memoria.nota("b", CAB.format(n="b") + "B.\n")
        original = unir.ligacoes

        def edita(t):                                # a nota muda enquanto a arrumação decide o que lhe acrescentar
            (memoria.real / "a.md").write_text(CAB.format(n="a") + "A.\n\nEDIÇÃO feita entretanto.\n", encoding="utf-8")
            return original(t)
        monkeypatch.setattr(unir, "ligacoes", edita)
        ok = auto_arrumar._acrescentar("a", "Relacionado: [[b]] (ligado pelo Basalt, hoje)", "b")
        assert "EDIÇÃO feita entretanto" in memoria.ler("real", "a"), "a arrumação automática apagou a edição"
        assert ok is False

    def test_arquivar_nao_sobrepoe_a_principal_editada_entretanto(self, memoria, monkeypatch):
        memoria.nota("principal", CAB.format(n="principal") + "P.\n"); memoria.nota("velha", CAB.format(n="velha") + "V.\n")
        (memoria.real / "MEMORY.md").write_text("- [velha](velha.md) — x\n- [principal](principal.md) — y\n", encoding="utf-8")
        _git(memoria)
        feito = []
        for nome in ("ler", "_ler2"):               # a versão anterior lê com ler(); esta com _ler2()
            orig = getattr(auto_arrumar, nome, None)
            if orig is None:
                continue

            def tarde(n, _orig=orig):
                r = _orig(n)
                if n == "principal" and not feito:
                    feito.append(n)
                    (memoria.real / "principal.md").write_text(CAB.format(n="principal") + "P.\n\nEDIÇÃO.\n", encoding="utf-8")
                return r
            monkeypatch.setattr(auto_arrumar, nome, tarde)
        auto_arrumar.arquivar("principal", "velha")
        assert "EDIÇÃO." in memoria.ler("real", "principal"), "arquivar sobrepôs a principal editada entretanto"
        assert "🗄 Arquivada" in memoria.ler("real", "velha")            # o resto da arrumação continua
        assert "(velha.md)" not in (memoria.real / "MEMORY.md").read_text(encoding="utf-8")


class TestG10LigacoesSimbolicas:
    def test_um_anexo_que_e_ligacao_simbolica_nao_se_serve_nem_se_reutiliza(self, tmp_path, esp):
        fora = tmp_path / "fora.txt"; fora.write_bytes(b"conteudo de fora do espaco\n")
        anexos.pasta(esp).mkdir(parents=True, exist_ok=True)
        (anexos.pasta(esp) / "fuga.txt").symlink_to(fora)
        est, _, corpo = anexos.ler(esp, "fuga.txt")
        assert est == 404 and b"fora do espaco" not in corpo, "serviu o ficheiro para onde a ligação aponta"
        assert anexos.guardar(esp, "fuga.txt", fora.read_bytes()) == "fuga_2.txt"   # nunca «reutiliza» uma ligação
        (anexos.pasta(esp) / "ok.txt").write_bytes(b"ok")                              # controlo
        assert anexos.ler(esp, "ok.txt")[0] == 200

    def test_importar_nao_segue_uma_ligacao_simbolica_do_cofre(self, tmp_path, esp):
        fora = tmp_path / "fora.md"; fora.write_text("# Fora\n\nnão é do cofre\n", encoding="utf-8")
        cofre = tmp_path / "Cofre"; cofre.mkdir()
        (cofre / "Dentro.md").write_text("# Dentro\n\nsim\n", encoding="utf-8")
        (cofre / "Fuga.md").symlink_to(fora)
        r = obsidian.importar(cofre, esp)
        assert not any("não é do cofre" in p.read_text(encoding="utf-8") for p in esp.real.glob("*.md")), "importou o alvo da ligação"
        assert len(r["importadas"]) == 1 and r["importadas"][0] in r["nomes"].values()
        assert any(f == "Fuga.md" and "simbólica" in porque for f, porque in r["saltadas"])

    def test_exportar_nao_copia_o_que_uma_ligacao_simbolica_aponta(self, tmp_path, esp):
        fora = tmp_path / "fora.md"; fora.write_text("fora\n", encoding="utf-8")
        (esp.real / "fuga.md").symlink_to(fora)
        (esp.real / "dentro.md").write_text(CAB.format(n="dentro") + "d\n", encoding="utf-8")
        destino = tmp_path / "export"
        obsidian.exportar(destino, esp)
        assert not (destino / "fuga.md").exists() and not (destino / "fuga.md").is_symlink(), "exportou o alvo da ligação"
        assert (destino / "dentro.md").is_file()

    def test_uma_nota_simbolica_nao_se_lista_nem_se_indexa(self, tmp_path, memoria):
        fora = tmp_path / "fora-do-espaco.md"; fora.write_text(CAB.format(n="fuga") + "fora do espaço\n", encoding="utf-8")
        memoria.nota("dentro", CAB.format(n="dentro") + "d\n")
        for d in (memoria.real, memoria.copia):
            (d / "fuga.md").symlink_to(fora)
        assert "fuga" not in {o["nome"] for o in web.todas_notas()}, "a página lista a ligação como nota"
        assert "fuga.md" not in duasvias.notas_de(memoria.real), "o 2vias sincroniza a ligação"
        assert not any(ref.startswith("fuga#") for _, ref, _, _ in sig.fontes()), "o índice lê o alvo da ligação"
        assert web.localizar("fuga") is None
        assert "dentro" in {o["nome"] for o in web.todas_notas()} and web.localizar("dentro") is not None   # controlo
