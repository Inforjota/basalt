"""basalt.arrumar — o relatório da memória: notas quase-duplicadas (por significado e, sem embeddings, por texto), pendentes soltos
em notas antigas e o resumo do dia. Só lê; nada aqui grava notas."""
import array
import json
import os
import subprocess
import time

import pytest

from basalt import arrumar, config, sig


def nota(memoria, nome, texto="", dias=0):
    p = memoria.real / f"{nome}.md"
    p.write_text(texto, encoding="utf-8")
    if dias:
        t = time.time() - dias * 86400
        os.utime(p, (t, t))
    return p


TEXTO_BARRAGEM = ("A barragem tem nove comportas e foi inspeccionada em setembro. O nível da albufeira sobe com a chuva forte "
                  "e as comportas abrem de forma automática quando o caudal passa o limite combinado com a protecção civil.")
TEXTO_OUTRO = ("Receita do bolo de laranja: quatro ovos, uma chávena de açúcar, farinha, sumo de laranja e raspa. "
               "Vai ao forno a cento e oitenta graus durante quarenta minutos.")


def vec(*xs):
    a = array.array("f", xs)
    return a.tobytes()


def indexar_vectores(memoria, por_ref):
    """põe vectores no índice de significado, com o esquema verdadeiro (sig.db)."""
    c = sig.db()
    for ref, v in por_ref.items():
        c.execute("INSERT INTO pedaco (fonte, ref, titulo, trecho, hash, vec) VALUES ('nota', ?, '', '', '', ?)", (ref, v))
    c.commit()
    c.close()


@pytest.fixture(params=[True, False], ids=["com-numpy", "sem-numpy"])
def numpy_ou_nao(request, monkeypatch):
    """as contas de vectores dão o mesmo com numpy e em Python puro."""
    if not request.param:
        monkeypatch.setattr(arrumar, "np", None)
    elif arrumar.np is None:
        pytest.skip("sem numpy neste ambiente")


# ───────────────────────── notas parecidas, sem vectores (texto) ─────────────────────────
class TestDuplicadosPeloTexto:
    def test_o_mesmo_assunto_com_uma_palavra_distintiva_no_nome_e_um_par(self, memoria):
        nota(memoria, "barragem_comportas", TEXTO_BARRAGEM)
        nota(memoria, "barragem_inspeccao", TEXTO_BARRAGEM + " Segunda frase.")
        nota(memoria, "bolo_laranja", TEXTO_OUTRO)
        pares = arrumar.duplicados()
        assert [(a, b) for _, a, b in pares] == [("barragem_comportas", "barragem_inspeccao")]
        s = pares[0][0]
        assert 0.6 <= s <= 1.0 and s == round(s, 3)

    def test_texto_igual_mas_sem_palavra_comum_no_nome_nao_e_par(self, memoria):
        nota(memoria, "barragem_comportas", TEXTO_BARRAGEM)
        nota(memoria, "relatorio_final", TEXTO_BARRAGEM)
        assert arrumar.duplicados() == []

    def test_nome_parecido_mas_texto_diferente_nao_e_par(self, memoria):
        nota(memoria, "barragem_comportas", TEXTO_BARRAGEM)
        nota(memoria, "barragem_receita", TEXTO_OUTRO)
        assert arrumar.duplicados() == []

    def test_um_cosseno_de_texto_acima_de_0_6_e_par(self, memoria):
        nota(memoria, "barragem_a", TEXTO_BARRAGEM)
        nota(memoria, "barragem_b", TEXTO_BARRAGEM + " " + TEXTO_OUTRO)         # cosseno ≈ 0,62
        v = arrumar._tfidf_por_nota()
        assert 0.6 < arrumar._cos_esparso(v["barragem_a"], v["barragem_b"]) < 0.7
        assert [p[1:] for p in arrumar.duplicados()] == [("barragem_a", "barragem_b")]

    def test_um_cosseno_de_texto_abaixo_de_0_6_nao_e_par_mas_o_limiar_e_que_decide(self, memoria, monkeypatch):
        nota(memoria, "barragem_a", TEXTO_BARRAGEM)
        nota(memoria, "barragem_b", TEXTO_OUTRO + " " + TEXTO_OUTRO + " " + TEXTO_BARRAGEM)   # cosseno ≈ 0,43
        v = arrumar._tfidf_por_nota()
        assert 0.3 < arrumar._cos_esparso(v["barragem_a"], v["barragem_b"]) < 0.6
        assert arrumar.duplicados() == []
        monkeypatch.setattr(arrumar, "DUP_LEXICO", 0.3)
        assert [p[1:] for p in arrumar.duplicados()] == [("barragem_a", "barragem_b")]

    @pytest.mark.parametrize("a,b", [("projecto_a", "projecto_b"), ("sessao_1", "sessao_2"), ("referencia_x", "referencia_y"),
                                     ("relatorio_2026", "contrato_2026"), ("a_b", "a_c")])
    def test_palavras_genericas_curtas_ou_numeros_no_nome_nao_contam(self, memoria, a, b):
        nota(memoria, a, TEXTO_BARRAGEM)
        nota(memoria, b, TEXTO_BARRAGEM)
        assert arrumar.duplicados() == []

    def test_a_raiz_e_as_5_primeiras_letras_da_palavra(self, memoria):
        nota(memoria, "barragens_x", TEXTO_BARRAGEM)
        nota(memoria, "barraca_y", TEXTO_BARRAGEM)         # «barra»: as 5 primeiras letras coincidem, a 6.ª não
        nota(memoria, "barrel_z", TEXTO_BARRAGEM)          # «barre»: só 4 coincidem
        assert [p[1:] for p in arrumar.duplicados()] == [("barraca_y", "barragens_x")]

    def test_a_palavra_comum_pode_estar_em_qualquer_posicao_do_nome(self, memoria):
        nota(memoria, "comportas_barragem", TEXTO_BARRAGEM)
        nota(memoria, "inspeccao_barragem", TEXTO_BARRAGEM)
        nota(memoria, "nivel:barragem", TEXTO_BARRAGEM)    # dois pontos também separam palavras
        assert len(arrumar.duplicados()) == 3

    def test_o_indice_memory_md_nunca_entra(self, memoria):
        nota(memoria, "MEMORY", TEXTO_BARRAGEM)
        nota(memoria, "memory_extra", TEXTO_BARRAGEM)       # partilharia a raiz «memor» com o índice, se ele contasse
        nota(memoria, "barragem_x", TEXTO_BARRAGEM)
        assert arrumar.duplicados() == []

    def test_ordenados_do_mais_parecido_para_o_menos(self, memoria):
        nota(memoria, "barragem_a", TEXTO_BARRAGEM)
        nota(memoria, "barragem_b", TEXTO_BARRAGEM)                              # igual: 1.0
        nota(memoria, "barragem_c", TEXTO_BARRAGEM + " Acrescento umas palavras sobre inspecção e manutenção das comportas.")
        s = [p[0] for p in arrumar.duplicados()]
        assert s == sorted(s, reverse=True) and s[0] == 1.0 and len(s) == 3

    def test_sem_notas_nao_ha_pares(self, memoria):
        assert arrumar.duplicados() == []

    def test_uma_nota_sem_texto_aproveitavel_nao_rebenta(self, memoria):
        nota(memoria, "barragem_vazia", "")
        nota(memoria, "barragem_x", TEXTO_BARRAGEM)
        assert arrumar.duplicados() == []

    def test_as_notas_a_escrever_entram_pelo_extra(self, memoria):
        nota(memoria, "barragem_comportas", TEXTO_BARRAGEM)
        v = arrumar._tfidf_por_nota({"barragem_rascunho": TEXTO_BARRAGEM})
        assert set(v) == {"barragem_comportas", "barragem_rascunho"}
        assert arrumar._cos_esparso(v["barragem_comportas"], v["barragem_rascunho"]) == pytest.approx(1.0)
        assert "barragem_rascunho" not in arrumar._tfidf_por_nota()

    def test_cosseno_esparso_nao_depende_da_ordem(self):
        a, b = {"x": 0.6, "y": 0.8}, {"y": 1.0}
        assert arrumar._cos_esparso(a, b) == arrumar._cos_esparso(b, a) == pytest.approx(0.8)
        assert arrumar._cos_esparso(a, {"z": 1.0}) == 0.0


# ───────────────────────── notas parecidas, por significado (vectores) ─────────────────────────
class TestDuplicadosPeloSignificado:
    def test_vectores_iguais_com_raiz_comum_no_nome_sao_par(self, memoria, numpy_ou_nao):
        indexar_vectores(memoria, {"barragem_a": vec(1, 0, 0, 0), "barragem_b": vec(1, 0, 0, 0), "bolo_x": vec(0, 1, 0, 0)})
        assert arrumar.duplicados() == [(1.0, "barragem_a", "barragem_b")]

    def test_o_limiar_dos_vectores_e_0_955(self, memoria, numpy_ou_nao):
        # cosseno entre (1,0) e (c, sqrt(1-c²))
        def par(c):
            return {"barragem_a": vec(1, 0, 0, 0), "barragem_b": vec(c, (1 - c * c) ** 0.5, 0, 0)}
        indexar_vectores(memoria, par(0.96))
        assert [p[1:] for p in arrumar.duplicados()] == [("barragem_a", "barragem_b")]

    def test_abaixo_do_limiar_nao_e_par(self, memoria, numpy_ou_nao):
        indexar_vectores(memoria, {"barragem_a": vec(1, 0, 0, 0), "barragem_b": vec(0.94, (1 - 0.94 ** 2) ** 0.5, 0, 0)})
        assert arrumar.duplicados() == []

    def test_so_o_cosseno_alto_sem_palavra_comum_no_nome_nao_chega(self, memoria, numpy_ou_nao):
        indexar_vectores(memoria, {"barragem_a": vec(1, 0, 0, 0), "relatorio_b": vec(1, 0, 0, 0)})
        assert arrumar.duplicados() == []

    def test_os_pedacos_de_uma_nota_fazem_a_media_e_a_ref_perde_o_cardinal(self, memoria, numpy_ou_nao):
        indexar_vectores(memoria, {"barragem_a#0": vec(1, 0, 0, 0), "barragem_a#1": vec(0, 1, 0, 0), "barragem_b": vec(1, 1, 0, 0)})
        v = arrumar.vec_por_nota()
        assert set(v) == {"barragem_a", "barragem_b"}
        assert arrumar.duplicados()[0][1:] == ("barragem_a", "barragem_b") and arrumar.duplicados()[0][0] == pytest.approx(1.0, abs=1e-3)

    def test_o_vector_de_cada_nota_fica_normalizado(self, memoria, numpy_ou_nao):
        indexar_vectores(memoria, {"barragem_a": vec(3, 4, 0, 0)})
        v = arrumar.vec_por_nota()["barragem_a"]
        assert sum(float(x) * float(x) for x in v) == pytest.approx(1.0)

    def test_pedacos_sem_vector_ou_de_outro_tamanho_nao_contam(self, memoria, numpy_ou_nao):
        indexar_vectores(memoria, {"barragem_a": vec(1, 0, 0, 0), "barragem_b": None, "barragem_c": b"", "barragem_d": vec(1, 0),
                                   "barragem_e": b"123", "barragem_f": vec(1, 0, 0, 0)})
        assert set(arrumar.vec_por_nota()) == {"barragem_a", "barragem_f"}

    def test_um_vector_que_nao_e_de_floats_de_4_bytes_nao_decide_o_tamanho(self, memoria, numpy_ou_nao):
        indexar_vectores(memoria, {"0_estragado": b"x" * 18, "barragem_a": vec(1, 0, 0, 0), "barragem_b": vec(1, 0, 0, 0)})
        assert set(arrumar.vec_por_nota()) == {"barragem_a", "barragem_b"}

    def test_o_primeiro_vector_decide_o_tamanho(self, memoria, numpy_ou_nao):
        c = sig.db()
        c.execute("INSERT INTO pedaco (fonte, ref, vec) VALUES ('nota','a_um', ?)", (vec(1, 0),))
        c.execute("INSERT INTO pedaco (fonte, ref, vec) VALUES ('nota','b_dois', ?)", (vec(1, 0, 0, 0),))
        c.commit(); c.close()
        assert set(arrumar.vec_por_nota()) == {"a_um"}

    def test_so_as_notas_contam_nao_as_outras_fontes(self, memoria, numpy_ou_nao):
        c = sig.db()
        c.execute("INSERT INTO pedaco (fonte, ref, vec) VALUES ('web','x#1', ?)", (vec(1, 0, 0, 0),))
        c.execute("INSERT INTO pedaco (fonte, ref, vec) VALUES ('nota','barragem_a', ?)", (vec(1, 0, 0, 0),))
        c.commit(); c.close()
        assert set(arrumar.vec_por_nota()) == {"barragem_a"}

    def test_vector_nulo_nao_entra(self, memoria, numpy_ou_nao):
        indexar_vectores(memoria, {"barragem_a": vec(0, 0, 0, 0), "barragem_b": vec(1, 0, 0, 0)})
        assert set(arrumar.vec_por_nota()) == {"barragem_b"}

    def test_sem_indice_ou_com_indice_estragado_cai_para_o_texto(self, memoria):
        assert arrumar.vec_por_nota() == {}
        (memoria.dados / "semantica.sqlite").write_text("isto não é uma base de dados sqlite" * 50)
        assert arrumar.vec_por_nota() == {}
        nota(memoria, "barragem_a", TEXTO_BARRAGEM)
        nota(memoria, "barragem_b", TEXTO_BARRAGEM)
        assert len(arrumar.duplicados()) == 1

    def test_indice_so_por_palavras_sem_vectores_tambem_cai_para_o_texto(self, memoria):
        indexar_vectores(memoria, {"barragem_a": None, "barragem_b": None})
        nota(memoria, "barragem_a", TEXTO_BARRAGEM)
        nota(memoria, "barragem_b", TEXTO_BARRAGEM)
        assert len(arrumar.duplicados()) == 1

    def test_produto_interno_com_e_sem_numpy(self, numpy_ou_nao):
        if arrumar.np is not None:
            a, b = arrumar.np.array([1, 2, 3], dtype="float32"), arrumar.np.array([4, 5, 6], dtype="float32")
        else:
            a, b = array.array("f", [1, 2, 3]), array.array("f", [4, 5, 6])
        assert arrumar._dot(a, b) == pytest.approx(32.0)

    def test_normalizar(self):
        assert arrumar._normal([3, 4]) == pytest.approx([0.6, 0.8])
        assert arrumar._normal([0, 0]) is None


# ───────────────────────── pendentes soltos ─────────────────────────
class TestPendentesSoltos:
    def test_so_notas_antigas_com_marcador(self, memoria):
        nota(memoria, "velha_com", "- ⏳ ligar ao banco\n- outra coisa\nfalta pagar a licença\n", dias=40)
        nota(memoria, "velha_sem", "tudo feito\n", dias=40)
        nota(memoria, "nova_com", "⏳ amanhã\n", dias=1)
        r = arrumar.pendentes_soltos()
        assert [(i, n) for i, n, _ in r] == [(40, "velha_com")]
        assert r[0][2] == ["⏳ ligar ao banco", "falta pagar a licença"]

    @pytest.mark.parametrize("linha", ["⏳ x", "- [ ] tarefa", "isto fica por fazer", "POR DECIDIR: o preço", "falta o cabo",
                                       "assunto Pendente", "* [ ] item"])
    def test_cada_marcador_conta(self, memoria, linha):
        nota(memoria, "velha", linha + "\n", dias=30)
        assert [n for _, n, _ in arrumar.pendentes_soltos()] == ["velha"]

    @pytest.mark.parametrize("linha", ["faltam dois", "[x] feito", "o pendentes_2 já fechou", "nada por aqui", "porfazer"])
    def test_o_que_parece_mas_nao_e_marcador(self, memoria, linha):
        nota(memoria, "velha", linha + "\n", dias=30)
        assert arrumar.pendentes_soltos() == []

    def test_a_fronteira_e_de_21_dias(self, memoria):
        nota(memoria, "vinte", "⏳ x\n", dias=20)
        nota(memoria, "vinte_e_dois", "⏳ x\n", dias=22)
        assert [n for _, n, _ in arrumar.pendentes_soltos()] == ["vinte_e_dois"]

    def test_mostra_as_duas_primeiras_linhas_e_ordena_pela_mais_antiga(self, memoria):
        nota(memoria, "a", "⏳ 1\n⏳ 2\n⏳ 3\n", dias=30)
        nota(memoria, "b", "⏳ x\n", dias=90)
        r = arrumar.pendentes_soltos()
        assert [(n, i) for i, n, _ in r] == [("b", 90), ("a", 30)]
        assert r[1][2] == ["⏳ 1", "⏳ 2"]

    def test_o_indice_memory_md_nao_conta(self, memoria):
        nota(memoria, "MEMORY", "⏳ x\n", dias=60)
        assert arrumar.pendentes_soltos() == []

    def test_texto_com_bytes_invalidos_nao_rebenta(self, memoria):
        (memoria.real / "velha.md").write_bytes(b"\xff\xfe \xe2\x8f\xb3 \xff x\n")
        t = time.time() - 40 * 86400
        os.utime(memoria.real / "velha.md", (t, t))
        assert [n for _, n, _ in arrumar.pendentes_soltos()] == ["velha"]


# ───────────────────────── resumo do dia ─────────────────────────
class TestResumoDoDia:
    def test_sem_git_so_conta_os_ficheiros_de_hoje(self, memoria, monkeypatch):
        monkeypatch.setenv("BASALT_GIT", "0")
        nota(memoria, "de_hoje", "x")
        nota(memoria, "de_ontem", "x", dias=2)
        nota(memoria, "MEMORY", "x")
        assert arrumar.resumo_dia() == ([], ["de_hoje"])

    def test_com_git_vem_o_assunto_dos_commits_de_hoje(self, memoria, monkeypatch):
        monkeypatch.delenv("BASALT_GIT", raising=False)
        r = memoria.real
        nota(memoria, "x", "um")
        g = ["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(g + ["init", "-q"], check=True)
        subprocess.run(g + ["add", "-A"], check=True)
        subprocess.run(g + ["commit", "-q", "-m", "primeira gravação"], check=True)
        nota(memoria, "y", "dois")
        subprocess.run(g + ["add", "-A"], check=True)
        subprocess.run(g + ["commit", "-q", "-m", "segunda gravação"], check=True)
        commits, notas = arrumar.resumo_dia()
        assert commits == ["segunda gravação", "primeira gravação"] and notas == ["x", "y"]

    def test_pasta_sem_repositorio_nao_rebenta(self, memoria, monkeypatch):
        monkeypatch.delenv("BASALT_GIT", raising=False)
        nota(memoria, "x", "um")
        assert arrumar.resumo_dia() == ([], ["x"])


# ───────────────────────── relatório e linha de comandos ─────────────────────────
@pytest.fixture
def no_ambiente(memoria, monkeypatch):
    """`main()` lê o espaço do ambiente, como um subprocesso."""
    monkeypatch.setenv("BASALT_GIT", "0")
    for k, v in memoria.ambiente().items():
        monkeypatch.setenv(k, v)
    return memoria


class TestRelatorio:
    def montar(self, memoria):
        nota(memoria, "barragem_a", TEXTO_BARRAGEM + "\n⏳ rever o limite\n", dias=30)
        nota(memoria, "barragem_b", TEXTO_BARRAGEM + "\n")
        nota(memoria, "bolo", TEXTO_OUTRO)

    def test_json(self, no_ambiente, capsys):
        self.montar(no_ambiente)
        arrumar.main(["--json"])
        d = json.loads(capsys.readouterr().out)
        assert set(d) == {"duplicados", "pendentes", "commits_hoje", "notas_hoje"}
        assert [p[1:] for p in d["duplicados"]] == [["barragem_a", "barragem_b"]]
        assert d["pendentes"] == [[30, "barragem_a", ["⏳ rever o limite"]]]
        assert d["commits_hoje"] == [] and d["notas_hoje"] == ["barragem_b", "bolo"]

    def test_texto(self, no_ambiente, capsys):
        self.montar(no_ambiente)
        arrumar.main([])
        out = capsys.readouterr().out
        assert "Basalt — arrumação" in out
        assert "● Notas muito parecidas (rever se juntar): 1" in out and "barragem_a  ≈  barragem_b" in out
        assert "● Pendentes soltos em notas com ≥21 dias: 1" in out and "barragem_a (30d): ⏳ rever o limite" in out
        assert "● Hoje: 0 gravações na memória · 2 notas mexidas" in out

    def test_o_texto_mostra_no_maximo_8_de_cada(self, no_ambiente, capsys):
        for i in range(10):
            nota(no_ambiente, f"pendente_{i}", f"⏳ x{i}\n", dias=30 + i)
        arrumar.main([])
        out = capsys.readouterr().out
        assert "Pendentes soltos em notas com ≥21 dias: 10" in out
        assert sum(1 for l in out.splitlines() if l.startswith("    pendente_")) == 8

    def test_o_texto_mostra_no_maximo_8_pares(self, no_ambiente, capsys):
        for i in range(10):
            nota(no_ambiente, f"barragem_{i}", TEXTO_BARRAGEM)       # 45 pares
        arrumar.main([])
        out = capsys.readouterr().out
        assert "Notas muito parecidas (rever se juntar): 45" in out
        assert sum(1 for l in out.splitlines() if "≈" in l) == 8

    def test_so_o_resumo(self, no_ambiente, capsys):
        self.montar(no_ambiente)
        arrumar.main(["--resumo"])
        out = capsys.readouterr().out
        assert "resumo de" in out and "memória: 0 gravações hoje" in out and "notas mexidas: 2" in out
        assert "parecidas" not in out and "Pendentes" not in out

    @pytest.mark.parametrize("flag", ["-h", "--help"])
    def test_ajuda(self, capsys, flag):
        arrumar.main([flag])
        assert "RELATÓRIO, não mexe em nada sozinho" in capsys.readouterr().out

    def test_configuracao_invalida_sai_com_mensagem(self, no_ambiente, monkeypatch):
        monkeypatch.setattr(config, "espaco_de_processo", lambda *a: (_ for _ in ()).throw(config.ConfigInvalida("modo errado")))
        with pytest.raises(SystemExit) as e:
            arrumar.main([])
        assert "configuração inválida: modo errado" in str(e.value)

    def test_nao_escreve_nada_nas_notas(self, no_ambiente, capsys):
        self.montar(no_ambiente)
        antes = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in no_ambiente.real.glob("*.md")}
        arrumar.main(["--json"])
        assert antes == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in no_ambiente.real.glob("*.md")}
