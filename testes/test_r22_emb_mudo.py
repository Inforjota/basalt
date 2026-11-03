"""r22 — o que conta como fornecedor de embeddings «mudo» (achados m3/A1-a e m4/A1-b da auditoria eu-xhigh de 24/10).

EMB_MUDO_SO_SILENCIO_v1 (A1-a): o EMB_SEM_RESPOSTA_v1 contava como «sem resposta» QUALQUER IAIndisponivel sem código HTTP —
também «o Ollama não devolveu embedding» e «não é JSON», que são respostas. Um erro destes num só pedaço parava a corrida
inteira nesse pedaço (os seguintes ficavam sem vector). Agora só conta o silêncio: tempo esgotado ou ligação sem código HTTP
(`ia.IASemResposta`, lançado pelo `_post_json`).

EMB_ENCURTA_SE_LENTO_v1 (A1-b): antes do EMB_SEM_RESPOSTA_v1, um tempo esgotado por causa do TAMANHO (CPU lento, 6000
caracteres a passar do `tempo_limite`) acabava por tentar um texto mais curto, que passava. Desde então parava ao 2.º tempo
esgotado sem encurtar — e, como a ordem das fontes é sempre a mesma, todas as noites no mesmo pedaço. Agora, na indexação, antes
de dar o fornecedor por mudo tenta uma vez com o texto curto (600). Na pergunta (alguém à espera: `tempo` dado) o tecto continua
a ser 2 tentativas (EMB_PERGUNTA_CURTA_v1).

Contra um fornecedor HTTP real (nada de fakes na classificação do erro): os testes de comportamento falham no 0e88faf; os
controlos passam nos dois."""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from basalt import ia, sig
from basalt.config import IAConfig
from test_r22_arrumar_rc import pendurado  # noqa: F401  (fixture)
from test_r22_emb_sem_resposta import nota


class Fornecedor:
    """«Ollama» de teste: /api/embeddings responde {} (sem embedding) se o texto tem ESTRAGADO; demora `lento_s` se o texto
    passa de `lento_acima_de` caracteres; senão um vector. Guarda o comprimento de cada pedido."""

    def __init__(self):
        self.pedidos, self.lento_acima_de, self.lento_s = [], None, 0
        dono = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                texto = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}").get("prompt", "")
                dono.pedidos.append(len(texto))
                if dono.lento_acima_de is not None and len(texto) > dono.lento_acima_de:
                    time.sleep(dono.lento_s)
                corpo = {} if "ESTRAGADO" in texto else {"embedding": [1.0, 0.5, 0.25]}
                b = json.dumps(corpo).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(b)))
                    self.end_headers()
                    self.wfile.write(b)
                except OSError:
                    pass                     # o cliente já desistiu (tempo esgotado)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def parar(self):
        self.srv.shutdown(); self.srv.server_close()


def _ia(url, tempo_limite=1):
    return ia.IA(IAConfig(fornecedor="openai", url=url + "/v1", modelo_chat="m", tempo_limite=tempo_limite,
                          embeddings_fornecedor="ollama", embeddings_url=url, embeddings_modelo="nomic-embed-text"))


@pytest.fixture
def forn():
    f = Fornecedor()
    ia.definir(_ia(f.url))
    yield f
    ia.definir(None)
    f.parar()


# ───────────────────────── A1-a: só o silêncio conta como «mudo» ─────────────────────────
def test_resposta_sem_embedding_nao_e_silencio(forn):
    with pytest.raises(sig.EmbErro) as e:
        sig.embed("um texto ESTRAGADO")
    assert not isinstance(e.value, sig.EmbSemResposta), f"«{e.value}» contou como fornecedor mudo (respondeu, mas mal)"


def test_um_pedaco_que_responde_mal_nao_para_a_corrida(memoria, forn):
    nota(memoria, "a-estragada", "Esta nota faz o fornecedor responder ESTRAGADO.")
    nota(memoria, "b-boa", "Os barcos do porto precisam de manutenção.")
    mudo = sig.indexar()
    vecs = dict(sig.db().execute("SELECT ref, vec FROM pedaco"))
    assert vecs.get("b-boa#0"), f"a nota seguinte ficou sem vector: um erro de um só pedaço parou a corrida ({mudo!r})"
    assert mudo is None, f"a corrida acabou como «fornecedor mudo» ({mudo!r}) e o fornecedor respondeu"


def test_controlo_fornecedor_pendurado_continua_mudo(pendurado):  # noqa: F811
    """controlo: o silêncio real (aceita a ligação, não responde) continua a dar EmbSemResposta."""
    ia.definir(_ia(pendurado))
    try:
        with pytest.raises(sig.EmbSemResposta):
            sig.embed("um texto qualquer")
    finally:
        ia.definir(None)


def test_controlo_ligacao_recusada_e_silencio():
    """controlo: ninguém a escutar na porta (ligação recusada, sem código HTTP) também é silêncio."""
    s = socket.socket(); s.bind(("127.0.0.1", 0)); porta = s.getsockname()[1]; s.close()
    ia.definir(_ia(f"http://127.0.0.1:{porta}"))
    try:
        with pytest.raises(sig.EmbSemResposta):
            sig.embed("um texto qualquer")
    finally:
        ia.definir(None)


# ───────────────────────── A1-b: tempo esgotado por tamanho → tenta o texto curto antes de dar «mudo» ─────────────────────────
LONGO = "Os barcos do porto precisam de manutenção antes de Outubro. " * 50      # ~3000 caracteres


def test_tempo_esgotado_pelo_tamanho_tenta_o_texto_curto(forn):
    forn.lento_acima_de, forn.lento_s = 700, 2           # textos compridos passam do tempo_limite (1 s); curtos respondem já
    v = sig.embed(LONGO)
    assert v and len(v) == 3
    assert forn.pedidos[-1] <= 700 and len(forn.pedidos) == 3, f"pedidos (comprimentos): {forn.pedidos}"


def test_nota_comprida_lenta_nao_para_a_corrida_todas_as_noites(memoria, forn):
    forn.lento_acima_de, forn.lento_s = 700, 2
    nota(memoria, "a-comprida", "Os barcos do porto precisam de manutenção antes de Outubro, com velas e cordas novas. " * 10)
    nota(memoria, "b-curta", "As velas novas chegam amanhã.")
    mudo = sig.indexar()
    vecs = dict(sig.db().execute("SELECT ref, vec FROM pedaco"))
    assert vecs.get("a-comprida#0") and vecs.get("b-curta#0"), f"ficou sem vector ({mudo!r}): {sorted(k for k, v in vecs.items() if v)}"
    assert mudo is None


def test_controlo_pergunta_lenta_continua_a_2_tentativas(forn):
    """controlo (EMB_PERGUNTA_CURTA_v1): com alguém à espera, o texto curto extra não entra — 2 × tempo é o tecto."""
    forn.lento_acima_de, forn.lento_s = 700, 2
    with pytest.raises(sig.EmbSemResposta):
        sig.embed(LONGO, "query", tempo=1)
    assert len(forn.pedidos) == 2, f"pedidos (comprimentos): {forn.pedidos}"


def test_controlo_fornecedor_lento_em_tudo_continua_mudo(forn):
    """controlo: lento também no texto curto → mudo (3 tentativas, não 9)."""
    forn.lento_acima_de, forn.lento_s = 0, 2
    with pytest.raises(sig.EmbSemResposta):
        sig.embed(LONGO)
    assert len(forn.pedidos) <= 3, f"pedidos (comprimentos): {forn.pedidos}"
