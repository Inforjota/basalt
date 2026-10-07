"""basalt.cas: escrever uma nota SÓ se ainda tem o conteúdo que se verificou (troca atómica + confirmação do que saiu).
Os «intrusos» (um editor, um agente, o telemóvel) escrevem sem trinco: simulam-se pelos ganchos do módulo
(`antes_de_trocar`, `durante_graca`, `durante_reposicao`) e por ficheiros abertos de propósito."""
import os
import threading
import time
from pathlib import Path

import pytest

from basalt import cas
from conftest import sha


@pytest.fixture
def alvo(memoria):
    p = memoria.real / "n.md"
    p.write_bytes(b"BASE")
    return p


def salvos(memoria):
    d = memoria.esp.cas_salvos
    return sorted(p for p in d.glob("*") if p.is_file()) if d.exists() else []


def sobras(memoria):
    return [p.name for p in memoria.real.iterdir() if p.name.endswith(".tmp")]


# ───────────────────────── o caminho feliz e as recusas simples ─────────────────────────
class TestEscrever:
    def test_troca_o_conteudo_quando_ninguem_mexeu(self, memoria, alvo):
        assert cas.escrever(alvo, b"NOVO", sha(b"BASE")) == ""
        assert alvo.read_bytes() == b"NOVO" and not sobras(memoria) and not salvos(memoria)

    def test_cria_so_se_a_nota_ainda_nao_existe(self, memoria):
        p = memoria.real / "nova.md"
        assert cas.escrever(p, b"x", None) == "" and p.read_bytes() == b"x" and not sobras(memoria)

    def test_criar_nao_pisa_quem_a_criou_entretanto(self, memoria, monkeypatch):
        p = memoria.real / "nova.md"
        monkeypatch.setattr(cas, "antes_de_trocar", lambda a: Path(a).write_bytes(b"DO OUTRO"))
        assert cas.escrever(p, b"MINHA", None) == "a nota apareceu entretanto"
        assert p.read_bytes() == b"DO OUTRO" and not sobras(memoria)

    def test_apagar_so_apaga_o_que_se_verificou(self, memoria, alvo):
        assert cas.escrever(alvo, None, sha(b"BASE")) == "" and not alvo.exists() and not sobras(memoria)

    def test_apagar_com_gravacao_no_mesmo_instante_devolve_a_nota_do_outro(self, memoria, alvo, monkeypatch):
        monkeypatch.setattr(cas, "antes_de_trocar", lambda a: Path(a).write_bytes(b"AGENTE"))
        motivo = cas.escrever(alvo, None, sha(b"BASE"))
        assert motivo.startswith("alguém gravou esta nota no mesmo instante")
        assert alvo.read_bytes() == b"AGENTE" and not sobras(memoria)

    def test_escrita_do_intruso_antes_da_troca_ganha_e_a_nossa_fica_sem_efeito(self, memoria, alvo, monkeypatch):
        monkeypatch.setattr(cas, "antes_de_trocar", lambda a: Path(a).write_bytes(b"INTRUSO"))
        assert cas.escrever(alvo, b"WEB", sha(b"BASE")).startswith("alguém gravou esta nota no mesmo instante")
        assert alvo.read_bytes() == b"INTRUSO" and not sobras(memoria) and not salvos(memoria)

    def test_escrita_tardia_num_ficheiro_ja_aberto_e_apanhada_pela_graca(self, memoria, alvo, monkeypatch):
        aberto = []
        monkeypatch.setattr(cas, "antes_de_trocar", lambda a: aberto.append(os.open(str(a), os.O_WRONLY)))

        fios = []

        def tarde(a):                       # quem já tinha o ficheiro aberto escreve no antigo, uns ms depois da troca
            def escreve():
                time.sleep(0.01)
                os.ftruncate(aberto[0], 0)
                os.pwrite(aberto[0], b"TARDIO", 0)
                os.close(aberto[0])
            fios.append(threading.Thread(target=escreve))
            fios[0].start()
        monkeypatch.setattr(cas, "durante_graca", tarde)
        assert cas.escrever(alvo, b"WEB", sha(b"BASE")).startswith("alguém gravou")
        fios[0].join(2)
        assert alvo.read_bytes() == b"TARDIO" and not sobras(memoria)

    def test_nota_que_desapareceu_ou_nunca_existiu(self, memoria):
        p = memoria.real / "fantasma.md"
        assert cas.escrever(p, b"x", sha(b"BASE")) == "a nota desapareceu entretanto"
        assert cas.escrever(p, None, sha(b"BASE")) == "a nota já não existia"
        assert cas.escrever(p, None, None) == "nada a fazer"
        assert not p.exists() and not sobras(memoria)

    def test_confirmar_devolve_so_as_trocas_que_falharam(self, memoria, monkeypatch):
        a, b = memoria.real / "a.md", memoria.real / "b.md"
        a.write_bytes(b"A"), b.write_bytes(b"B")
        monkeypatch.setattr(cas, "antes_de_trocar", lambda x: Path(x).write_bytes(b"INTRUSO") if Path(x) == b else None)
        ta, _ = cas.trocar(a, b"A2", sha(b"A"))
        tb, _ = cas.trocar(b, b"B2", sha(b"B"))
        falhas = cas.confirmar([ta, tb])
        assert falhas == [tb] and tb.intruso == b"INTRUSO"
        assert a.read_bytes() == b"A2" and b.read_bytes() == b"INTRUSO"


# ───────────────────────── a reposição não perde uma 3.ª escrita ─────────────────────────
class TestReposicao:
    def test_terceira_escrita_entre_a_verificacao_e_a_troca_de_volta_nao_se_perde(self, memoria, alvo, monkeypatch):
        monkeypatch.setattr(cas, "antes_de_trocar", lambda a: Path(a).write_bytes(b"AGENTE 1"))   # escreve sem trinco antes da troca
        original, feito = cas._ler, []

        def ler(p):
            d = original(p)
            if not feito and Path(p) == alvo and d == b"WEB":                  # a verificação «o alvo ainda tem o nosso» leu WEB…
                feito.append(1)
                alvo.write_bytes(b"AGENTE 2")                                  # …e logo a seguir entra a 3.ª escrita
            return d
        monkeypatch.setattr(cas, "_ler", ler)
        t, _ = cas.trocar(alvo, b"WEB", sha(b"BASE"))
        falhas = cas.confirmar([t])
        guardados = [p.read_bytes() for p in salvos(memoria)]
        assert feito and falhas == [t]
        assert alvo.read_bytes() == b"AGENTE 2" or b"AGENTE 2" in guardados
        assert alvo.read_bytes() in (b"AGENTE 1", b"AGENTE 2")

    def test_terceira_escrita_ainda_a_decorrer_na_troca_de_volta_nao_se_perde(self, memoria, alvo, monkeypatch):
        monkeypatch.setattr(cas, "antes_de_trocar", lambda p: Path(p).write_bytes(b"AGENTE 1") if Path(p) == alvo else None)
        original, n, fios = cas._renameat2, [0], []

        def espiao(a, b, flags):
            if flags == cas._EXCHANGE:
                n[0] += 1
                if n[0] == 2:                    # a troca de volta: alguém abre o ficheiro publicado mesmo antes…
                    fd = os.open(str(b), os.O_WRONLY)

                    def escreve():               # …e acaba de escrever 5 ms depois, já no inode que saiu
                        time.sleep(0.005)
                        os.ftruncate(fd, 0)
                        os.pwrite(fd, b"AGENTE 3", 0)
                        os.close(fd)
                    f = threading.Thread(target=escreve)
                    fios.append(f)
                    f.start()
            return original(a, b, flags)
        monkeypatch.setattr(cas, "_renameat2", espiao)
        t, _ = cas.trocar(alvo, b"WEB", sha(b"BASE"))
        falhas = cas.confirmar([t])
        for f in fios:
            f.join(2)
        conteudos = {alvo.read_bytes()} | {p.read_bytes() for p in salvos(memoria)}
        assert falhas == [t] and len(fios) == 1 and alvo.read_bytes() == b"AGENTE 1"
        assert b"AGENTE 3" in conteudos

    def test_o_que_vai_para_salvos_e_o_ficheiro_inteiro_nao_a_leitura_a_meio(self, memoria, alvo, monkeypatch):
        monkeypatch.setattr(cas, "antes_de_trocar", lambda a: Path(a).write_bytes(b"AGENTE 1 parc"))
        original, feito = cas._ler, []
        completo = b"AGENTE 1 parcial, agora COMPLETO"

        def ler(p):
            d = original(p)
            if not feito and Path(p).name.startswith(".") and ".cas-" in Path(p).name:     # 1.ª leitura do que saiu: a meio
                feito.append(1)
                Path(p).write_bytes(completo)            # o escritor acaba (mesmo inode)
                alvo.write_bytes(b"AGENTE 3")            # e o alvo muda outra vez → não dá para repor
            return d
        monkeypatch.setattr(cas, "_ler", ler)
        t, _ = cas.trocar(alvo, b"WEB", sha(b"BASE"))
        falhas = cas.confirmar([t])
        guardados = [p.read_bytes() for p in salvos(memoria)]
        assert feito and falhas == [t] and completo in guardados and b"AGENTE 1 parc" not in guardados
        assert t.salvo is not None and t.salvo.read_bytes() == completo and alvo.read_bytes() == b"AGENTE 3"


# ───────────────────────── a pasta dos «salvos» ─────────────────────────
class TestSalvos:
    def _pasta(self, memoria):
        memoria.esp.cas_salvos.mkdir(parents=True, exist_ok=True)
        return memoria.esp.cas_salvos

    def test_tem_limite_de_dias_e_de_ficheiros_e_nao_repete_o_mesmo_conteudo(self, memoria, alvo, monkeypatch):
        monkeypatch.setattr(cas, "SALVOS_DIAS", 1)
        monkeypatch.setattr(cas, "SALVOS_MAX", 3)
        pasta = self._pasta(memoria)
        velho = pasta / "n.20260101-000000.aaaaaaaaaa.md"
        velho.write_bytes(b"velho")
        os.utime(velho, (time.time() - 3 * 86400,) * 2)
        for i in range(5):
            (pasta / f"n.20261001-00000{i}.bbbbbbbbb{i}.md").write_bytes(b"recente %d" % i)
        monkeypatch.setattr(cas, "antes_de_trocar", lambda a: Path(a).write_bytes(b"AGENTE 1"))
        monkeypatch.setattr(cas, "durante_graca", lambda a: Path(a).write_bytes(b"AGENTE 3"))   # o alvo muda outra vez → vai para SALVOS

        def uma_vez():
            alvo.write_bytes(b"BASE")
            t, _ = cas.trocar(alvo, b"WEB", sha(b"BASE"))
            return cas.confirmar([t])
        f1 = uma_vez()
        time.sleep(1.1)
        f2 = uma_vez()                                   # o mesmo intruso duas vezes (segundos diferentes)
        lista = salvos(memoria)
        iguais = [p for p in lista if p.name.endswith(f".{sha(b'AGENTE 1')[:10]}.md")]
        assert f1 and f2 and not velho.exists() and len(lista) <= 3 and len(iguais) == 1

    def test_repetir_e_por_conteudo_e_nao_pelo_hash_do_nome(self, memoria, alvo):
        pasta = self._pasta(memoria)
        velha = pasta / f"n.20250101-000000.{sha(b'PARCIAL')[:10]}.md"   # guardada a meio; entretanto ficou COMPLETO
        velha.write_bytes(b"COMPLETO")
        agora = time.time()
        os.utime(velha, (agora - 5, agora - 5))
        saiu = memoria.real / ".n.md.teste.tmp"
        saiu.write_bytes(b"PARCIAL")
        destino = cas._guardar_a_parte(cas.Troca(alvo, "e", "n", saiu))
        assert velha.exists() and velha.read_bytes() == b"COMPLETO" and destino.read_bytes() == b"PARCIAL"
        saiu2 = memoria.real / ".n.md.teste2.tmp"                # e o MESMO conteúdo, guardado há ≥ 1 s, fica uma vez só
        saiu2.write_bytes(b"COMPLETO")
        d2 = cas._guardar_a_parte(cas.Troca(alvo, "e", "n", saiu2))
        iguais = [p for p in salvos(memoria) if p.read_bytes() == b"COMPLETO"]
        assert iguais == [d2]

    def test_a_idade_conta_desde_que_se_guardou_nao_desde_o_mtime_da_nota(self, memoria, alvo):
        saiu = memoria.real / ".n.md.teste.tmp"
        saiu.write_bytes(b"VELHA MAS VALIOSA")
        os.utime(saiu, (time.time() - 90 * 86400,) * 2)
        destino = cas._guardar_a_parte(cas.Troca(alvo, "e", "n", saiu))
        assert destino is not None and destino.read_bytes() == b"VELHA MAS VALIOSA"
        assert time.time() - destino.stat().st_mtime < 60

    def test_a_poda_por_dias_nao_depende_do_limite_de_ficheiros(self, memoria, alvo, monkeypatch):
        monkeypatch.setattr(cas, "SALVOS_DIAS", 2)
        pasta = self._pasta(memoria)
        velho, recente = pasta / "n.20260101-000000.aaaaaaaaaa.md", pasta / "n.20261001-000000.bbbbbbbbbb.md"
        velho.write_bytes(b"velho")
        recente.write_bytes(b"recente")
        os.utime(velho, (time.time() - 3 * 86400,) * 2)
        os.utime(recente, (time.time() - 1 * 86400,) * 2)
        saiu = memoria.real / ".n.md.teste.tmp"
        saiu.write_bytes(b"NOVO")
        cas._guardar_a_parte(cas.Troca(alvo, "e", "n", saiu))
        assert not velho.exists() and recente.exists() and len(salvos(memoria)) == 2

    def test_o_mesmo_conteudo_acabado_de_guardar_ainda_nao_se_apaga(self, memoria, alvo):
        pasta = self._pasta(memoria)
        recente = pasta / f"n.20261001-000000.{sha(b'IGUAL')[:10]}.md"      # guardada agora mesmo (ainda pode estar a ser escrita)
        recente.write_bytes(b"IGUAL")
        saiu = memoria.real / ".n.md.teste.tmp"
        saiu.write_bytes(b"IGUAL")
        destino = cas._guardar_a_parte(cas.Troca(alvo, "e", "n", saiu))
        assert recente.exists() and destino.exists() and recente != destino

    def test_guardar_a_parte_sem_nada_que_guardar_devolve_none(self, memoria, alvo):
        assert cas._guardar_a_parte(cas.Troca(alvo, "e", "n", memoria.real / ".nao-existe.tmp")) is None
