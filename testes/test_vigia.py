"""O vigia (basalt.duasvias) e a web (basalt.web) a escreverem na mesma nota ao mesmo tempo: nenhuma escrita aceite se perde.
As «corridas» são provocadas pelos ganchos do cas (`antes_de_trocar`) e por ganchos em funções do vigia, todos em processo."""
import contextlib
from pathlib import Path

import pytest

from basalt import cas, web
from conftest import sha, usar_ia


@pytest.fixture
def v(memoria, monkeypatch):
    from basalt import duasvias
    monkeypatch.setattr(duasvias, "GIT", False)
    monkeypatch.setattr(duasvias, "ALERTAS", False)
    monkeypatch.setattr(web, "TRINCO_ESPERA", 1)
    usar_ia("")
    return duasvias


def conflitos(memoria, nome):
    return [q.read_text(encoding="utf-8") for q in sorted(memoria.real.glob(f"{nome}.CONFLITO-*.CONFLITO-*.md"))] or \
           [q.read_text(encoding="utf-8") for q in sorted(memoria.real.glob(f"{nome}.CONFLITO-*.md"))]


def textos(memoria, nome):
    """tudo o que existe de uma nota: a real, a cópia e as cópias de conflito."""
    return [memoria.ler("real", nome), memoria.ler("copia", nome)] + [q.read_text(encoding="utf-8") for q in memoria.real.glob(f"{nome}.CONFLITO-*.md")]


# ───────────────────────── a web grava enquanto o vigia verifica segredos ─────────────────────────
class TestWebDuranteOCiclo:
    def _preparar(self, memoria, v, monkeypatch):
        memoria.nota("a", "a0")
        (memoria.real / "a.md").write_text("a AGENTE", encoding="utf-8")        # a real mudou e a cópia não → o vigia vai puxar
        visto = {}
        original = v.tem_segredo

        def espia(q):
            if "cod" not in visto:                                              # no meio do ciclo, antes de trocar a cópia
                visto["cod"], visto["resp"] = web.gravar("a", "a EDIÇÃO WEB (sem rede, a sincronizar)", sha((memoria.copia / "a.md").read_bytes()))
            return original(q)
        monkeypatch.setattr(v, "tem_segredo", espia)
        return visto

    def test_web_que_grava_a_meio_do_ciclo_nao_perde_nada(self, memoria, v, monkeypatch):
        visto = self._preparar(memoria, v, monkeypatch)
        v.ciclo()
        monkeypatch.undo()
        v.ciclo()
        assert "cod" in visto, "o gancho não correu: o teste não provou nada"
        assert memoria.ler("real", "a") == "a AGENTE"                             # o agente nunca é pisado
        assert visto["cod"] != 200 or any("EDIÇÃO WEB" in (t or "") for t in textos(memoria, "a"))

    def test_a_web_e_recusada_enquanto_o_vigia_segura_o_trinco(self, memoria, v, monkeypatch):
        visto = self._preparar(memoria, v, monkeypatch)
        v.ciclo()
        assert visto["cod"] == 503 and visto["resp"]["estado"] == "ocupado"
        assert "EDIÇÃO WEB" not in (memoria.ler("copia", "a") or "")

    def test_mesmo_que_a_web_passe_o_trinco_e_a_verificacao_a_troca_atomica_guarda_as_duas(self, memoria, v, monkeypatch):
        # tira à web as duas guardas (trinco e «a real mudou, espera»): fica só o CAS do vigia a defender a escrita aceite
        monkeypatch.setattr(web, "trinco_copia", lambda espera=None: contextlib.nullcontext())
        monkeypatch.setattr(web, "_hash_no_ledger", lambda nome: sha((memoria.real / nome).read_bytes()))
        visto = self._preparar(memoria, v, monkeypatch)
        v.ciclo()
        monkeypatch.undo()
        v.ciclo()
        v.ciclo()
        assert visto["cod"] == 200, visto                                       # a web gravou a meio do ciclo e foi aceite
        todos = textos(memoria, "a")
        assert "a AGENTE" in todos and "a EDIÇÃO WEB (sem rede, a sincronizar)" in todos      # e as duas versões existem


# ───────────────────────── depois de um conflito ─────────────────────────
class TestDepoisDoConflito:
    def test_o_ciclo_seguinte_nao_empurra_a_copia_por_cima_da_real(self, memoria, v):
        memoria.nota("e", "e0")
        (memoria.real / "e.md").write_text("e AGENTE", encoding="utf-8")
        (memoria.copia / "e.md").write_text("e TELEMÓVEL", encoding="utf-8")
        r1 = v.ciclo()
        r2 = v.ciclo()
        assert r1["conflitos"] == ["e.md"] and r2 == dict(empurrados=[], puxados=[], conflitos=[], retidos=[], apagados=[], adiados=[])
        assert memoria.ler("real", "e") == "e AGENTE" and memoria.ler("copia", "e") == "e AGENTE"
        assert [q.read_text(encoding="utf-8") for q in memoria.real.glob("e.CONFLITO-*.md")] == ["e TELEMÓVEL"]

    def test_a_web_recusa_gravar_com_mudanca_da_real_por_trazer_e_o_vigia_traz_a_do_agente(self, memoria, v):
        memoria.nota("b", "b0")
        (memoria.real / "b.md").write_text("b AGENTE", encoding="utf-8")
        cod, r = web.gravar("b", "b WEB", sha("b0"))
        v.ciclo()
        v.ciclo()
        assert cod == 409 and r.get("pendente") is True and r["estado"] == "ocupado"
        assert memoria.ler("real", "b") == "b AGENTE" and memoria.ler("copia", "b") == "b AGENTE"
        assert not list(memoria.real.glob("b.CONFLITO-*.md"))                    # a web não gravou nada: não há o que guardar


# ───────────────────────── o editor da cópia grava enquanto o vigia confirma a real ─────────────────────────
class TestEditorDaCopiaDuranteOCiclo:
    @pytest.mark.parametrize("janela", ["ao_ler_a_real", "antes_de_trocar"])
    def test_a_escrita_do_editor_nao_se_perde(self, memoria, v, monkeypatch, janela):
        memoria.nota("a", "BASE")
        (memoria.real / "a.md").write_text("AGENTE", encoding="utf-8")
        pc, pr, feito = memoria.copia / "a.md", memoria.real / "a.md", []

        def editor():
            if not feito:
                feito.append(1)
                pc.write_text("EDITOR NOVO", encoding="utf-8")
        if janela == "ao_ler_a_real":
            ler = v.ler
            monkeypatch.setattr(v, "ler", lambda p: (lambda b: (editor() if Path(p) == pr else None, b)[1])(ler(p)))
        else:
            monkeypatch.setattr(cas, "antes_de_trocar", lambda alvo: editor() if Path(alvo) == pc else None)
        r1 = v.ciclo()
        monkeypatch.undo()
        v.ciclo()
        v.ciclo()
        todos = textos(memoria, "a")
        assert feito, "o gancho não correu: o teste não provou nada"
        assert "EDITOR NOVO" in todos and "AGENTE" in todos, (janela, r1, todos)
