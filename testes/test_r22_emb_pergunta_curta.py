"""r22 (EMB_PERGUNTA_CURTA_v1, adenda A2): o vector da PERGUNTA tem alguém à espera. O `_pontuar` promete cair para as palavras
quando o fornecedor de embeddings não responde, mas cada tentativa esperava o `tempo_limite` de [ia] (120 s na produção) e
desiste à 2.ª: 240 s. A procura da página é morta aos 60 s (web.motor) e a pergunta aos 180 s — nunca chegavam às palavras; o
aviso «já tens uma nota assim» prendia um fio do servidor 240 s. Agora a pergunta espera no máximo `sig.TEMPO_PERGUNTA` por
tentativa; a indexação nocturna continua com o `tempo_limite`.

Os testes de comportamento falham no b07d078 (tempo None / 2 × tempo_limite); o controlo passa nos dois.
"""
import time

import pytest

from basalt import ia, sig, web
from basalt.config import IAConfig
from test_r22_arrumar_rc import pendurado  # noqa: F401  (fixture)
from test_r22_emb_sem_resposta import IABoa, nota


class IAGrava(IABoa):
    """responde e guarda o `tempo` de cada pedido."""
    def __init__(self):
        super().__init__(); self.tempos = []

    def embeddings(self, textos, tempo=None):
        self.tempos.append(tempo)
        return super().embeddings(textos, tempo)


@pytest.fixture(autouse=True)
def _repor_ia():
    yield
    ia.definir(None)


@pytest.fixture
def indexado(memoria):
    for n in ("barcos", "velas"):
        nota(memoria, n, f"Os {n} do porto precisam de manutenção.")
    ia.definir(IABoa()); sig.indexar()
    return memoria


def test_a_pergunta_tem_tempo_curto(indexado):
    f = IAGrava(); ia.definir(f)
    _, semantico = sig._pontuar("barcos", sig.carregar(sig.db()))
    assert semantico and len(f.tempos) == 1
    assert f.tempos[0] is not None and f.tempos[0] <= 20, f"a pergunta esperou {f.tempos[0]!r} (None = tempo_limite, 120 s)"


def test_o_aviso_de_nota_parecida_tem_tempo_curto(indexado):
    f = IAGrava(); ia.definir(f)
    web.parecidas_texto("nova", "---\nname: nova\ndescription: d\n---\nOs barcos do porto.\n")
    assert f.tempos and all(t is not None and t <= 20 for t in f.tempos), f.tempos


def test_controlo_a_indexacao_continua_com_o_tempo_limite(memoria):
    nota(memoria, "barcos", "Os barcos do porto precisam de manutenção.")
    f = IAGrava(); ia.definir(f); sig.indexar()
    assert f.tempos == [None]


def test_fornecedor_pendurado_a_procura_cai_para_as_palavras_a_tempo(indexado, pendurado, monkeypatch):
    """com o fornecedor real a não responder: tempo_limite 3 s → antes 2 × 3 s; com TEMPO_PERGUNTA = 1 → ~2 s."""
    ia.definir(ia.IA(IAConfig(fornecedor="openai", url=pendurado + "/v1", modelo_chat="m", tempo_limite=3,
                              embeddings_fornecedor="ollama", embeddings_url=pendurado, embeddings_modelo="nomic-embed-text")))
    monkeypatch.setattr(sig, "TEMPO_PERGUNTA", 1, raising=False)
    t0 = time.monotonic()
    sims, semantico = sig._pontuar("barcos", sig.carregar(sig.db()))
    dt = time.monotonic() - t0
    assert not semantico and max(sims) > 0, "não caiu para as palavras"
    assert dt < 4.5, f"levou {dt:.1f} s (2 × tempo_limite = 6 s): a pergunta não usa o tempo curto"
