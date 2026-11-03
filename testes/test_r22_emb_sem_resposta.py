"""Ronda 22 (EMB_SEM_RESPOSTA_v1): o fornecedor de embeddings que NÃO RESPONDE (tempo esgotado) não pode prender o indexar.
24/10 04:10: o Ollama ocupado com outro lote demorava a responder; cada pedaço fazia 9 tentativas (3 tamanhos × 3) de 120 s e o
indexar do auto_arrumar passou os 1800 s. Agora: 2 «sem resposta» → desiste do pedaço E da corrida (os restantes ficam com a
versão anterior, nada é apagado). Um HTTP 5xx continua a encurtar o texto (é o tamanho), e o fornecedor que responde não muda."""
import pytest

from basalt import ia, sig
from basalt.ia import IAIndisponivel, IASemResposta
from conftest import FM


class IAMuda:
    """responde «sem resposta» (como o _post_json com TimeoutError) a todos os pedidos; conta-os."""
    embeddings_activos = True
    cfg = type("Cfg", (), {"embeddings_modelo": "nomic-embed-text"})()

    def __init__(self, erro="sem resposta de http://ollama-falso:11434: timed out"):
        self.erro, self.pedidos = erro, 0

    def embeddings(self, textos, tempo=None):
        self.pedidos += 1
        # «sem resposta» é o que o _post_json lança com tempo esgotado / sem ligação (IASemResposta); o resto é IAIndisponivel
        raise (IASemResposta if self.erro.startswith("sem resposta") else IAIndisponivel)(self.erro)


class IABoa(IAMuda):
    def embeddings(self, textos, tempo=None):
        self.pedidos += 1
        return [[1.0, 0.0, 0.0] for _ in textos]


def nota(memoria, nome, texto):
    (memoria.real / f"{nome}.md").write_text(FM.format(nome=nome) + "\n" + texto + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _repor_ia():
    yield
    ia.definir(None)


def test_embed_sem_resposta_desiste_a_2_tentativas_em_vez_de_9():
    f = IAMuda(); ia.definir(f)
    with pytest.raises(sig.EmbErro):
        sig.embed("um texto qualquer")
    assert f.pedidos == 2, f"{f.pedidos} pedidos a um fornecedor que não responde (eram 9 × 120 s)"


def test_http_5xx_continua_a_encurtar_o_texto():
    """controlo: a recusa por tamanho não é «sem resposta» — continua a tentar os 3 tamanhos."""
    f = IAMuda("http://ollama-falso:11434: HTTP 500 contexto excedido"); ia.definir(f)
    with pytest.raises(sig.EmbErro):
        sig.embed("x" * 7000)
    assert f.pedidos == 3


def test_indexar_com_fornecedor_mudo_para_logo_e_guarda_os_vectores_antigos(memoria, capsys):
    for n in ("barcos", "velas", "redes", "remos", "boias"):
        nota(memoria, n, f"As {n} do porto precisam de manutenção antes de Outubro.")
    ia.definir(IABoa()); sig.indexar()
    antes = dict(sig.db().execute("SELECT ref, vec FROM pedaco"))
    assert len(antes) == 5 and all(antes.values())
    for n in ("barcos", "velas", "redes", "remos", "boias"):            # todas mudam → todas precisam de vector novo
        nota(memoria, n, f"As {n} do porto foram arranjadas em Setembro.")
    f = IAMuda(); ia.definir(f)
    sig.indexar()
    assert f.pedidos == 2, f"{f.pedidos} pedidos: devia desistir da corrida ao 1.º pedaço mudo (antes 9 por pedaço = 45)"
    depois = dict(sig.db().execute("SELECT ref, vec FROM pedaco"))
    assert depois == antes, "um pedaço perdeu o vector (ou foi apagado) porque o fornecedor não respondeu"
    assert "sem resposta" in capsys.readouterr().out


def test_fornecedor_que_responde_indexa_tudo(memoria):
    """controlo: com resposta, nada muda."""
    for n in ("barcos", "velas"):
        nota(memoria, n, f"As {n} do porto precisam de manutenção.")
    f = IABoa(); ia.definir(f); sig.indexar()
    assert f.pedidos == 2 and len(dict(sig.db().execute("SELECT ref, vec FROM pedaco"))) == 2
