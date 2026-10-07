"""basalt.sig — pesquisa por significado. Não há fornecedor de embeddings verdadeiro: usa-se o `ollama_falso.py`, que dá
vectores por «tema» (frases do mesmo tema ficam perto sem palavras em comum). Sem embeddings o mesmo índice responde por
palavras (BM25) — também testado aqui."""
import json
import os
import subprocess
import sys

import pytest

from conftest import FM, RAIZ, ia_de_teste
from ollama_falso import OllamaFalso

BARCOS = FM.format(nome="barcos") + "\nOs barcos do porto precisam de velas novas antes da maré de Outubro.\n"
IMPRESSORA = FM.format(nome="impressora") + "\nA impressora entope o bico com filamento PLA húmido.\n"
BOLO = FM.format(nome="receitas") + "\nBolo de laranja: farinha, açúcar, ovos, forno a 180.\n"


@pytest.fixture
def ollama():
    o = OllamaFalso()
    yield o
    o.parar()


def _notas(memoria):
    for nome, texto in (("barcos", BARCOS), ("impressora", IMPRESSORA), ("receitas", BOLO)):
        (memoria.real / f"{nome}.md").write_text(texto, encoding="utf-8")


@pytest.fixture
def sig(memoria, ollama):
    """o módulo `basalt.sig` sobre o espaço de teste, com IA (chat + embeddings) simulada."""
    from basalt import ia, sig as modulo
    _notas(memoria)
    ia.definir(ia_de_teste(ollama))
    return modulo


@pytest.fixture
def sig_sem_embeddings(memoria, ollama):
    """o mesmo, mas SEM fornecedor de embeddings: o índice guarda só o texto e a procura é por palavras."""
    from basalt import ia, sig as modulo
    _notas(memoria)
    ia.definir(ia_de_teste(ollama, embeddings=False))
    return modulo


def procura_json(sig, capsys, termo, n=8):
    capsys.readouterr()
    sig.procura(termo, n, como_json=True)
    return json.loads(capsys.readouterr().out)


def refs(sig):
    return sorted(r for (r,) in sig.db().execute("SELECT ref FROM pedaco"))


class TestIndexar:
    def test_indexar_guarda_um_pedaco_por_nota_com_vector_de_768(self, sig, ollama):
        sig.indexar()
        assert refs(sig) == ["barcos#0", "impressora#0", "receitas#0"]
        vec = sig.db().execute("SELECT vec FROM pedaco LIMIT 1").fetchone()[0]
        assert len(vec) == 768 * 4
        assert all(p.startswith("search_document: ") for p in ollama.pedidos_emb)

    def test_so_volta_a_embeber_o_que_mudou(self, sig, ollama, memoria):
        sig.indexar()
        ollama.pedidos_emb.clear()
        sig.indexar()
        assert ollama.pedidos_emb == []
        (memoria.real / "barcos.md").write_text(BARCOS + "\nO veleiro grande fica no porto.\n", encoding="utf-8")
        sig.indexar()
        assert len(ollama.pedidos_emb) == 1 and "veleiro grande" in ollama.pedidos_emb[0]

    def test_nota_apagada_sai_do_indice(self, sig, memoria):
        sig.indexar()
        (memoria.real / "receitas.md").unlink()
        sig.indexar()
        assert refs(sig) == ["barcos#0", "impressora#0"]

    def test_o_indice_de_memoria_do_claude_code_nao_e_uma_nota(self, sig, memoria):
        (memoria.real / "MEMORY.md").write_text("- [barcos](barcos.md) — índice\n", encoding="utf-8")
        sig.indexar()
        assert not any(r.startswith("MEMORY") for r in refs(sig))

    def test_texto_comprido_recusado_pelo_fornecedor_tenta_se_mais_curto(self, sig, ollama, memoria):
        (memoria.real / "longa.md").write_text(FM.format(nome="longa") + "\n" + "barco " * 900 + "\n", encoding="utf-8")
        ollama.recusar_acima_de = 2100
        sig.indexar()
        assert "longa#0" in refs(sig)
        tamanhos = [len(p) for p in ollama.pedidos_emb if "barco barco" in p]
        assert tamanhos[0] > 2100 and tamanhos[-1] <= 2100

    def test_fornecedor_em_baixo_nao_estraga_o_indice_que_ja_havia(self, sig, ollama, memoria, capsys):
        sig.indexar()
        (memoria.real / "barcos.md").write_text(BARCOS + "mudança\n", encoding="utf-8")
        ollama.em_baixo = True
        sig.indexar()
        assert "3 saltados" not in capsys.readouterr().out
        assert refs(sig) == ["barcos#0", "impressora#0", "receitas#0"]
        trecho = sig.db().execute("SELECT trecho FROM pedaco WHERE ref='barcos#0'").fetchone()[0]
        assert "mudança" not in trecho

    def test_sem_embeddings_indexa_so_o_texto(self, sig_sem_embeddings, ollama, capsys):
        sig_sem_embeddings.indexar()
        assert refs(sig_sem_embeddings) == ["barcos#0", "impressora#0", "receitas#0"]
        assert ollama.pedidos_emb == []
        assert all(len(v or b"") == 0 for (v,) in sig_sem_embeddings.db().execute("SELECT vec FROM pedaco"))
        assert "só por palavras" in capsys.readouterr().out

    def test_sem_embeddings_nao_repete_o_trabalho_se_nada_mudou(self, sig_sem_embeddings, capsys):
        sig_sem_embeddings.indexar()
        capsys.readouterr()
        sig_sem_embeddings.indexar()
        assert "0 (re)indexados" in capsys.readouterr().out


class TestProcurar:
    def test_encontra_pelo_significado_sem_palavras_em_comum(self, sig, capsys, ollama):
        sig.indexar()
        r = procura_json(sig, capsys, "navio no mar")
        assert [x["ref"] for x in r][:1] == ["barcos#0"]
        assert set(r[0]) == {"fonte", "ref", "titulo", "sim", "trecho"} and r[0]["sim"] > 0.8
        assert any(p.startswith("search_query: navio no mar") for p in ollama.pedidos_emb)

    def test_outro_tema_encontra_outra_nota(self, sig, capsys):
        sig.indexar()
        assert procura_json(sig, capsys, "camadas da extrusora")[0]["ref"] == "impressora#0"

    def test_sem_nada_de_confianca_devolve_vazio(self, sig, capsys):
        sig.indexar()
        assert procura_json(sig, capsys, "zebra quantica xpto") == []

    def test_notas_excluidas_nunca_aparecem(self, sig, capsys, memoria):
        sig.indexar()
        memoria.excluir.write_text("barcos\n")
        assert all(not x["ref"].startswith("barcos") for x in procura_json(sig, capsys, "navio no mar veleiro"))

    def test_indice_vazio_pede_para_indexar(self, sig):
        with pytest.raises(SystemExit, match="índice vazio"):
            sig.procura("barcos", 5, como_json=True)

    def test_sem_embeddings_procura_por_palavras(self, sig_sem_embeddings, capsys):
        sig_sem_embeddings.indexar()
        r = procura_json(sig_sem_embeddings, capsys, "velas do porto")
        assert r and r[0]["ref"] == "barcos#0"
        assert procura_json(sig_sem_embeddings, capsys, "farinha e ovos")[0]["ref"] == "receitas#0"

    def test_sem_embeddings_nao_acha_o_que_nao_tem_palavras_em_comum(self, sig_sem_embeddings, capsys):
        # o que o significado apanha («navio no mar» → barcos) as palavras sozinhas não apanham: é o preço de não ter IA
        sig_sem_embeddings.indexar()
        assert procura_json(sig_sem_embeddings, capsys, "zebra quantica xpto") == []

    def test_sem_embeddings_uma_so_palavra_em_comum_numa_pergunta_longa_nao_chega(self, sig_sem_embeddings, capsys):
        # «velas» existe na nota, mas a pergunta tem outras 3 palavras que não: por palavras isto não é de confiança
        sig_sem_embeddings.indexar()
        assert procura_json(sig_sem_embeddings, capsys, "velas azuis metalicas brilhantes") == []
        assert procura_json(sig_sem_embeddings, capsys, "velas")[0]["ref"] == "barcos#0"

    def test_sem_embeddings_as_excluidas_tambem_nao_aparecem(self, sig_sem_embeddings, capsys, memoria):
        sig_sem_embeddings.indexar()
        memoria.excluir.write_text("barcos\n")
        assert procura_json(sig_sem_embeddings, capsys, "velas do porto") == []

    def test_se_o_fornecedor_cai_na_pergunta_procura_por_palavras(self, sig, ollama, capsys):
        # PORTE: antes era erro; agora a pesquisa cai para as palavras em vez de falhar
        sig.indexar()
        ollama.em_baixo = True
        r = procura_json(sig, capsys, "velas do porto")
        assert r and r[0]["ref"] == "barcos#0"

    def test_sem_numpy_o_cosseno_em_python_puro_da_o_mesmo(self, sig, capsys, monkeypatch):
        sig.indexar()
        com = [x["ref"] for x in procura_json(sig, capsys, "navio no mar")]
        monkeypatch.setattr(sig, "np", None)
        sem = [x["ref"] for x in procura_json(sig, capsys, "navio no mar")]
        assert sem == com and sem[:1] == ["barcos#0"]


class TestPergunta:
    def test_pergunta_responde_so_com_os_excertos_encontrados(self, sig, ollama, capsys):
        sig.indexar()
        capsys.readouterr()
        sig.pergunta("o que falta aos navios?", como_json=True)
        d = json.loads(capsys.readouterr().out)
        assert d["resposta"] == ollama.resposta_chat
        assert d["fontes"][0] == {"n": 1, "titulo": "barcos", "ref": "barcos#0"}
        msgs = ollama.pedidos_chat[0]["messages"]
        assert "velas novas" in msgs[1]["content"] and "Não está na memória" in msgs[0]["content"]
        assert "farinha" not in msgs[1]["content"]

    def test_sem_excertos_de_confianca_nao_chama_o_modelo(self, sig, ollama, capsys):
        sig.indexar()
        capsys.readouterr()
        sig.pergunta("zebra quantica xpto", como_json=True)
        assert json.loads(capsys.readouterr().out) == {"resposta": "Não está na memória.", "fontes": []}
        assert ollama.pedidos_chat == []

    def test_o_prompt_do_sistema_nao_tem_nomes_proprios(self, sig):
        nomes = ("Jos\u00e9", "Quadro" + "s")      # escritos assim para este ficheiro não conter o que verifica
        assert not any(n in sig.SISTEMA for n in nomes)


class TestGuardar:
    def test_guardar_ideia_escreve_na_entrada_e_sugere_nota(self, sig, memoria, capsys):
        sig.indexar()
        capsys.readouterr()
        sig.guardar("  comprar velas para o veleiro  ", de="web")
        linha = json.loads((memoria.partilhada / "entrada.jsonl").read_text().splitlines()[-1])
        assert linha["texto"] == "comprar velas para o veleiro" and linha["de"] == "web" and linha["arquivado"] is False
        assert "parece ligar-se a: barcos" in capsys.readouterr().out

    def test_guardar_com_indice_vazio_da_sucesso(self, memoria):
        env = dict(os.environ, **memoria.ambiente(), HOME=str(memoria.home), PYTHONPATH=str(RAIZ), BASALT_SIG_ISOLADO="1")
        env.pop("BASALT_CONFIG", None)
        r = subprocess.run([sys.executable, "-m", "basalt.sig", "guarda", "ideia", "--de", "web"],
                           env=env, capture_output=True, text=True, timeout=30, cwd=str(memoria.base))
        assert r.returncode == 0, r.stderr
        assert "ideia" in (memoria.partilhada / "entrada.jsonl").read_text()
