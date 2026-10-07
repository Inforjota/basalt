"""basalt.duasvias — sincronização nos dois sentidos entre a memória real e a cópia editável, com registo de hashes
(ledger), conflitos guardados à parte e trinco partilhado com a web. Tudo em pastas temporárias, sem git nem alertas."""
import fcntl
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from conftest import FM, RAIZ, sha


@pytest.fixture
def v(memoria, monkeypatch):
    from basalt import duasvias
    monkeypatch.setattr(duasvias, "GIT", False)
    monkeypatch.setattr(duasvias, "ALERTAS", False)
    return duasvias


def conflitos(memoria, nome):
    return sorted(memoria.real.glob(f"{nome}.CONFLITO-*.md"))


BASE = FM.format(nome="n") + "linha 1\nlinha 2\nlinha 3\nlinha 4\nlinha 5\n"


# ───────────────────────── mudanças que se juntam sozinhas ─────────────────────────
class TestJuntarSozinho:
    def test_edicao_na_copia_vai_para_a_real(self, memoria, v):
        memoria.nota("n", BASE)
        memoria.nota("n", BASE + "da cópia\n", real=False, alinhar=False)
        assert v.ciclo()["empurrados"] == ["n.md"]
        assert memoria.ler("real", "n") == BASE + "da cópia\n" == memoria.ler("copia", "n")

    def test_edicao_na_real_vem_para_a_copia(self, memoria, v):
        memoria.nota("n", BASE)
        memoria.nota("n", BASE + "do agente\n", copia=False, alinhar=False)
        assert v.ciclo()["puxados"] == ["n.md"]
        assert memoria.ler("copia", "n") == BASE + "do agente\n"

    def test_notas_diferentes_mudadas_em_lados_diferentes_juntam_se(self, memoria, v):
        memoria.nota("a", "a0")
        memoria.nota("b", "b0")
        memoria.nota("a", "a da cópia", real=False, alinhar=False)
        memoria.nota("b", "b do agente", copia=False, alinhar=False)
        r = v.ciclo()
        assert r["empurrados"] == ["a.md"] and r["puxados"] == ["b.md"] and not r["conflitos"]
        for lado in ("real", "copia"):
            assert memoria.ler(lado, "a") == "a da cópia" and memoria.ler(lado, "b") == "b do agente"
        assert v.ciclo() == dict(empurrados=[], puxados=[], conflitos=[], retidos=[], apagados=[], adiados=[])

    def test_nota_nova_de_cada_lado_chega_ao_outro(self, memoria, v):
        memoria.nota("da_copia", "nova na cópia", real=False, alinhar=False)
        memoria.nota("da_real", "nova na real", copia=False, alinhar=False)
        v.ciclo()
        assert memoria.ler("real", "da_copia") == "nova na cópia" and memoria.ler("copia", "da_real") == "nova na real"

    def test_mudancas_em_linhas_diferentes_da_mesma_nota_nao_perdem_nada(self, memoria, v):
        memoria.nota("n", BASE)
        agente = BASE.replace("linha 1", "linha 1 do agente")
        telemovel = BASE.replace("linha 5", "linha 5 do telemóvel")
        memoria.nota("n", agente, copia=False, alinhar=False)
        memoria.nota("n", telemovel, real=False, alinhar=False)
        v.ciclo()
        guardadas = [memoria.ler("real", "n")] + [p.read_text() for p in conflitos(memoria, "n")]
        assert agente in guardadas and telemovel in guardadas

    @pytest.mark.xfail(reason="por desenho o 2vias não junta: qualquer mudança dos dois lados na mesma nota vira "
                              ".CONFLITO (basalt.duasvias, _ciclo), mesmo em linhas diferentes — a junção linha a linha "
                              "só existe na web (/api/sincronizar, web.reconciliar)")
    def test_mudancas_em_linhas_diferentes_da_mesma_nota_juntam_se_sozinhas(self, memoria, v):
        memoria.nota("n", BASE)
        memoria.nota("n", BASE.replace("linha 1", "linha 1 do agente"), copia=False, alinhar=False)
        memoria.nota("n", BASE.replace("linha 5", "linha 5 do telemóvel"), real=False, alinhar=False)
        r = v.ciclo()
        final = memoria.ler("real", "n")
        assert not r["conflitos"] and "linha 1 do agente" in final and "linha 5 do telemóvel" in final


# ───────────────────────── conflitos ─────────────────────────
class TestConflitos:
    def test_mesmas_linhas_mudadas_dos_dois_lados_dao_conflito_sem_perder_nenhuma_versao(self, memoria, v):
        memoria.nota("n", BASE)
        agente = BASE.replace("linha 3", "linha 3 do agente")
        telemovel = BASE.replace("linha 3", "linha 3 do telemóvel")
        memoria.nota("n", agente, copia=False, alinhar=False)
        memoria.nota("n", telemovel, real=False, alinhar=False)
        r = v.ciclo()
        assert r["conflitos"] == ["n.md"]
        assert memoria.ler("real", "n") == agente                  # a real fica como está
        cs = conflitos(memoria, "n")
        assert [p.read_text() for p in cs] == [telemovel]           # a da cópia fica guardada à parte
        assert memoria.ler("copia", "n") == agente                  # e a cópia passa a mostrar a real
        assert json.loads(memoria.ledger.read_text())["n.md"] == sha(agente)

    def test_depois_do_conflito_os_ciclos_seguintes_nao_mexem_na_nota(self, memoria, v):
        memoria.nota("n", BASE)
        memoria.nota("n", BASE + "agente\n", copia=False, alinhar=False)
        memoria.nota("n", BASE + "telemóvel\n", real=False, alinhar=False)
        v.ciclo()
        for _ in range(3):
            r = v.ciclo()
            assert "n.md" not in r["empurrados"] + r["puxados"] + r["conflitos"]
        assert memoria.ler("real", "n") == BASE + "agente\n" and len(conflitos(memoria, "n")) == 1

    def test_conflito_com_segredo_na_copia_nao_leva_o_segredo_para_a_real(self, memoria, v):
        memoria.nota("n", BASE)
        memoria.nota("n", BASE + "agente\n", copia=False, alinhar=False)
        memoria.nota("n", BASE + "chave AKIA" + "ABCDEFGHIJKLMNOP\n", real=False, alinhar=False)
        r = v.ciclo()
        assert r["retidos"] == ["n.md"] and not conflitos(memoria, "n")
        assert "AKIA" not in "".join(p.read_text() for p in memoria.real.glob("*.md"))

    def test_copia_do_conflito_nao_aparece_como_nota_que_nao_se_abre(self, memoria, v):
        memoria.nota("n", BASE)
        memoria.nota("n", BASE + "agente\n", copia=False, alinhar=False)
        memoria.nota("n", BASE + "telemóvel\n", real=False, alinhar=False)
        v.ciclo()
        v.ciclo()
        from basalt import web as w
        for o in w.todas_notas():
            assert w.localizar(o["nome"]) is not None, f"a web lista «{o['nome']}» mas dá 404 ao abrir"


# ───────────────────────── apagar ─────────────────────────
class TestApagar:
    def test_apagar_na_copia_nao_apaga_na_memoria_real(self, memoria, v):
        memoria.nota("n", BASE)
        (memoria.copia / "n.md").unlink()
        r = v.ciclo()
        assert "n.md" not in r["apagados"]
        assert memoria.ler("real", "n") == BASE

    def test_apagar_na_real_apaga_na_copia(self, memoria, v):
        memoria.nota("n", BASE)
        (memoria.real / "n.md").unlink()
        assert v.ciclo()["apagados"] == ["n.md"]
        assert memoria.ler("copia", "n") is None
        assert "n.md" not in json.loads(memoria.ledger.read_text())

    def test_apagar_na_real_nao_apaga_uma_edicao_da_copia_por_levar(self, memoria, v):
        memoria.nota("n", BASE)
        (memoria.real / "n.md").unlink()
        memoria.nota("n", BASE + "edição por levar\n", real=False, alinhar=False)
        v.ciclo()
        assert (memoria.ler("copia", "n") or memoria.ler("real", "n")) == BASE + "edição por levar\n"

    def test_nota_com_segredo_apagada_da_copia_nao_volta(self, memoria, v):
        memoria.nota("n", BASE)
        memoria.nota("n", BASE + "chave AKIA" + "ABCDEFGHIJKLMNOP\n", copia=False, alinhar=False)
        v.ciclo()                                     # a real ganhou um segredo → sai da cópia
        assert memoria.ler("copia", "n") is None
        for _ in range(2):
            v.ciclo()
        assert memoria.ler("copia", "n") is None and "AKIA" in memoria.ler("real", "n")

    def test_nota_apagada_na_copia_e_mudada_na_real_volta_com_a_versao_nova(self, memoria, v):
        memoria.nota("n", BASE)
        (memoria.copia / "n.md").unlink()
        memoria.nota("n", BASE + "do agente\n", copia=False, alinhar=False)
        v.ciclo()
        assert memoria.ler("copia", "n") == BASE + "do agente\n" == memoria.ler("real", "n")

    def test_nota_apagada_na_copia_volta_no_ciclo_seguinte(self, memoria, v):
        memoria.nota("n", BASE)
        (memoria.copia / "n.md").unlink()
        v.ciclo()
        v.ciclo()
        assert memoria.ler("copia", "n") == BASE


# ───────────────────────── trinco ─────────────────────────
def segurar_trinco(caminho):
    f = open(caminho, "a+")
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return f


class TestTrinco:
    def test_trinco_ocupado_faz_esperar_e_acaba_por_desistir(self, memoria, v):
        f = segurar_trinco(memoria.trinco)
        try:
            t0 = time.time()
            with pytest.raises(TimeoutError):
                with v.trinco(espera=0.3):
                    pass
            assert 0.25 < time.time() - t0 < 3
        finally:
            f.close()
        with v.trinco(espera=0.3):          # depois de libertado, entra logo
            pass

    def test_ciclo_espera_pelo_trinco_e_nao_escreve_nada_entretanto(self, memoria, v):
        memoria.nota("n", BASE)
        memoria.nota("n", BASE + "da cópia\n", real=False, alinhar=False)
        ledger = memoria.ledger.read_text()
        f = segurar_trinco(memoria.trinco)
        out = {}
        fio = threading.Thread(target=lambda: out.update(r=v.ciclo()), daemon=True)
        fio.start()
        time.sleep(0.5)
        try:
            assert fio.is_alive() and memoria.ler("real", "n") == BASE and memoria.ledger.read_text() == ledger
        finally:
            f.close()
        fio.join(10)
        assert out["r"]["empurrados"] == ["n.md"] and memoria.ler("real", "n") == BASE + "da cópia\n"

    def test_duas_sincronizacoes_em_processos_diferentes_nao_correm_ao_mesmo_tempo(self, memoria):
        memoria.nota("n", BASE)
        memoria.nota("n", BASE + "da cópia\n", real=False, alinhar=False)
        env = dict(os.environ, **memoria.ambiente(), BASALT_GIT="0", HOME=str(memoria.home), PYTHONPATH=str(RAIZ))
        f = segurar_trinco(memoria.trinco)       # «outra sincronização» a correr
        try:
            p = subprocess.Popen([sys.executable, "-m", "basalt.duasvias"], env=env, cwd=str(memoria.base),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(1.0)
            assert p.poll() is None, "o segundo processo não esperou pelo trinco"
            assert memoria.ler("real", "n") == BASE
        finally:
            f.close()
        out, err = p.communicate(timeout=20)
        assert p.returncode == 0, err
        assert "empurrados:1" in out and memoria.ler("real", "n") == BASE + "da cópia\n"

    def test_web_nao_grava_enquanto_o_2vias_segura_o_trinco(self, memoria, v, monkeypatch):
        from basalt import web as w
        monkeypatch.setattr(w, "TRINCO_ESPERA", 0.3)
        texto = FM.format(nome="n") + "v0\n"
        memoria.nota("n", texto)
        with v.trinco(espera=1):
            cod, r = w.gravar("n", texto + "da web\n", sha(texto))
        assert cod == 503 and r["estado"] == "ocupado"
        assert memoria.ler("copia", "n") == texto
        assert w.gravar("n", texto + "da web\n", sha(texto))[0] == 200     # trinco livre → grava
