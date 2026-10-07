"""Ronda 8 (notas retidas): «retida» quer dizer «não sai para a IA» em TODOS os caminhos — índice/embeddings, pergunta (índice antigo),
categorias, arrumar (pares), auto_arrumar, unir (botão e fusão) e Entrada automática. Uma nota está retida se o nome está em
excluidas.txt OU se o texto tem um segredo em claro (basalt.retidas). Antes, só a pesquisa consultava a lista — o indexador
embebia a nota retida, a categorização e o auto_arrumar mandavam-na ao modelo e a Entrada decidia a ideia antes de olhar ao
segredo. Cada alvo falha no código anterior (git archive 5347bc6); os controlos passam nos dois."""
import json

import pytest

from basalt import arrumar, auto_arrumar, categorias, ia, sig, unir, web
from conftest import FM, ia_de_teste, usar_ia
from ollama_falso import OllamaFalso

PASSWORD = "Tr0ub4dor&3"
LIMPA = FM.format(nome="barcos") + "\nOs barcos do porto precisam de velas novas antes da maré de Outubro.\n"
ROUTER = FM.format(nome="router") + f"\nO router do escritório reinicia às segundas. senha do router: {PASSWORD}\n"
TEXTO_ROUTER = ("O router do escritório tem nove portas e foi inspeccionado em setembro. O sinal da antena sobe com a chuva forte "
                "e as portas abrem de forma automática quando o tráfego passa o limite combinado com a protecção civil.")


@pytest.fixture
def ollama():
    o = OllamaFalso()
    yield o
    o.parar()


def nota(memoria, nome, texto):
    (memoria.real / f"{nome}.md").write_text(texto, encoding="utf-8")


def refs(nome_prefixo):
    return [r for (r,) in sig.db().execute("SELECT ref FROM pedaco") if r.startswith(nome_prefixo)]


class TestIndice:
    def test_nota_com_segredo_nao_e_embebida_nem_guardada_no_indice(self, memoria, ollama):
        nota(memoria, "barcos", LIMPA); nota(memoria, "router", ROUTER)
        ia.definir(ia_de_teste(ollama))
        sig.indexar()
        assert refs("barcos") and not refs("router")
        assert not any(PASSWORD in t for t in ollama.pedidos_emb), "o segredo foi ao fornecedor de embeddings"
        assert all(PASSWORD not in (tr or "") for (tr,) in sig.db().execute("SELECT trecho FROM pedaco"))

    def test_nota_na_lista_de_excluidas_nao_entra_no_indice(self, memoria, ollama):
        nota(memoria, "barcos", LIMPA); nota(memoria, "velas", LIMPA.replace("barcos", "velas"))
        memoria.excluir.write_text("velas\n", encoding="utf-8")
        ia.definir(ia_de_teste(ollama))
        sig.indexar()
        assert refs("barcos") and not refs("velas")
        assert not any("velas novas" in t and "[velas]" in t for t in ollama.pedidos_emb)

    def test_indice_antigo_com_segredo_nao_chega_a_pergunta_e_sai_na_reindexacao(self, memoria, ollama):
        """um índice feito antes de a nota ficar retida ainda tem os pedaços dela: não podem ir ao modelo, e a próxima
        indexação tira-os."""
        nota(memoria, "barcos", LIMPA); nota(memoria, "router", ROUTER)
        ia.definir(ia_de_teste(ollama))
        c = sig.db()
        c.execute("INSERT INTO pedaco (fonte, ref, titulo, trecho, hash, vec) VALUES ('nota', 'router#0', 'router', ?, 'h', x'')",
                  (f"[router]\nsenha do router: {PASSWORD}",))
        c.commit()
        assert all(PASSWORD not in tr for _, _, _, tr, _ in sig.carregar(c)), "o pedaço com segredo ia para a pergunta"
        c.close()
        sig.indexar()
        assert not refs("router")


class TestCategorias:
    def test_nota_retida_nao_vai_ao_modelo_nem_fica_categorizada(self, memoria):
        nota(memoria, "barcos", LIMPA); nota(memoria, "router", ROUTER)
        f = usar_ia('{"categorias": ["Tecnologia"]}')
        categorias.main()
        assert f.chamadas == 1 and all(PASSWORD not in p for p in f.prompts)
        saida = json.loads((memoria.dados / "categorias.json").read_text(encoding="utf-8"))
        assert "barcos" in saida and "router" not in saida


class TestArrumar:
    def test_pares_pelo_texto_nunca_incluem_uma_nota_retida(self, memoria):
        nota(memoria, "router_casa", TEXTO_ROUTER + f"\nsenha do router: {PASSWORD}\n")
        nota(memoria, "router_escritorio", TEXTO_ROUTER + " Segunda frase.")
        assert arrumar.duplicados() == []
        nota(memoria, "router_casa", TEXTO_ROUTER)                          # controlo: sem o segredo, é um par
        assert [p[1:] for p in arrumar.duplicados()] == [("router_casa", "router_escritorio")]

    def test_pares_pelo_indice_nunca_incluem_uma_nota_retida(self, memoria):
        from test_arrumar import indexar_vectores, vec
        nota(memoria, "router_casa", TEXTO_ROUTER + f"\nsenha do router: {PASSWORD}\n")
        nota(memoria, "router_escritorio", TEXTO_ROUTER)
        indexar_vectores(memoria, {"router_casa#0": vec(1.0, 0.0), "router_escritorio#0": vec(1.0, 0.0)})
        assert arrumar.duplicados() == []
        nota(memoria, "router_casa", TEXTO_ROUTER)                          # controlo
        assert [p[1:] for p in arrumar.duplicados()] == [("router_casa", "router_escritorio")]

    def test_auto_arrumar_nao_manda_um_par_retido_a_ia(self, memoria, monkeypatch):
        nota(memoria, "router_casa", TEXTO_ROUTER + f"\nsenha do router: {PASSWORD}\n")
        nota(memoria, "router_escritorio", TEXTO_ROUTER + " Segunda frase.")
        f = usar_ia('{"acao": "ligar", "manter": "router_escritorio", "confianca": 0.9, "motivo": "x"}')
        monkeypatch.setattr(auto_arrumar, "SIMULAR", True)
        d = auto_arrumar.decidir("router_casa", "router_escritorio")
        assert d["acao"] == "deixar" and "retida" in d["motivo"]
        auto_arrumar._correr()
        assert f.chamadas == 0, "o texto da nota retida foi à IA"


class TestUnir:
    def test_fundir_recusa_mandar_nota_com_segredo_a_ia(self, memoria):
        f = usar_ia(LIMPA)
        with pytest.raises(ValueError, match="password ou chave"):
            unir.fundir_llm(ROUTER, LIMPA, "router", "barcos")
        assert f.chamadas == 0

    def test_o_botao_unir_recusa_uma_nota_com_segredo_mesmo_fora_da_lista(self, memoria):
        memoria.nota("barcos", LIMPA); memoria.nota("router", ROUTER)         # nas duas vias: a lista de excluídas não a tem
        f = usar_ia(LIMPA)
        codigo, r = web.iniciar_fusao("barcos", "router")
        assert codigo == 400 and "password ou chave" in r["erro"]
        assert f.chamadas == 0


class TestEntradaAutomatica:
    def test_ideia_com_segredo_fica_na_entrada_sem_ir_a_ia(self, memoria):
        memoria.nota("barcos", LIMPA)
        inbox = memoria.esp.inbox
        inbox.parent.mkdir(parents=True, exist_ok=True)
        inbox.write_text(json.dumps({"t": "2026-10-07T10:00:00", "texto": f"senha do router: {PASSWORD}", "de": "web"}) + "\n"
                         + json.dumps({"t": "2026-10-07T10:00:01", "texto": "comprar velas para os barcos", "de": "web"}) + "\n",
                         encoding="utf-8")
        f = usar_ia('{"acao": "duvida", "nota": "", "confianca": 0.5, "motivo": "x"}')
        assert web.processar_entrada() == 2
        assert f.chamadas == 1 and all(PASSWORD not in p for p in f.prompts), "a ideia com segredo foi à IA"
        regs = [json.loads(l) for l in inbox.read_text(encoding="utf-8").splitlines()]
        com_segredo = next(r for r in regs if PASSWORD in r["texto"])
        assert not com_segredo.get("arquivado") and com_segredo["auto"]["acao"] == "duvida"
        assert "password ou chave" in com_segredo["auto"]["motivo"]
