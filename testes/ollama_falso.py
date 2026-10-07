"""Fornecedor de IA simulado (nenhum servidor de embeddings ou de chat real é chamado nos testes).

Fala os dois dialectos que o `basalt.ia` usa:
  /api/embeddings        (Ollama)  {"prompt"}         → {"embedding": [...]}
  /v1/embeddings         (OpenAI)  {"input": [...]}   → {"data": [{"index", "embedding"}]}
  /v1/chat/completions   (OpenAI)  {"messages": ...}  → {"choices": [{"message": {"content"}}]}
  /api/chat              (Ollama)  {"messages": ...}  → {"message": {"content"}}
Os vectores têm 768 dimensões e são DETERMINÍSTICOS, feitos por «temas»: cada palavra de um tema soma 1 na dimensão desse
tema; as outras palavras somam pouco numa dimensão dada pelo seu hash. Assim duas frases do mesmo tema ficam muito perto
mesmo SEM palavras em comum (é o que se quer testar numa pesquisa por significado) e frases de temas diferentes ficam longe.
"""
import json
import re
import threading
import unicodedata
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIM = 768
TEMAS = {
    0: "barco barcos vela velas mar maré mares navio navios ilha ilhas barragem porto veleiro",
    1: "impressora impressoras filamento filamentos bico camada camadas pla extrusora",
    2: "bolo bolos farinha forno receita açúcar ovos massa",
}
TEMA_DE = {}
for t, palavras_ in TEMAS.items():
    for p in palavras_.split():
        TEMA_DE[unicodedata.normalize("NFD", p).encode("ascii", "ignore").decode()] = t


def palavras(texto):
    s = unicodedata.normalize("NFD", texto.lower()).encode("ascii", "ignore").decode()
    s = re.sub(r"^search_(query|document):\s*", "", s)
    return re.findall(r"[a-z0-9]{2,}", s)


def vector(texto):
    v = [0.0] * DIM
    for w in palavras(texto):
        if w in TEMA_DE:
            v[TEMA_DE[w]] += 1.0
        else:
            v[10 + zlib.crc32(w.encode()) % (DIM - 10)] += 0.15
    return v


class OllamaFalso:
    def __init__(self):
        self.pedidos_emb = []          # textos pedidos (com o prefixo search_…, se o motor o puser)
        self.pedidos_chat = []         # corpos dos pedidos de chat
        self.resposta_chat = "Os barcos têm velas novas [1]."
        self.recusar_acima_de = None   # simula o «HTTP 500» do fornecedor para textos compridos
        self.em_baixo = False          # simula o fornecedor desligado (500 em tudo)
        dono = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _responde(self, codigo, obj):
                b = json.dumps(obj).encode()
                self.send_response(codigo)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_POST(self):
                corpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if dono.em_baixo:
                    return self._responde(500, {"error": "em baixo"})
                if self.path in ("/api/embeddings", "/v1/embeddings"):
                    textos = [corpo.get("prompt", "")] if self.path == "/api/embeddings" else (
                        [corpo["input"]] if isinstance(corpo.get("input"), str) else list(corpo.get("input", [])))
                    dono.pedidos_emb.extend(textos)
                    if dono.recusar_acima_de is not None and any(len(t) > dono.recusar_acima_de for t in textos):
                        return self._responde(500, {"error": "input too long"})
                    if self.path == "/api/embeddings":
                        return self._responde(200, {"embedding": vector(textos[0])})
                    return self._responde(200, {"data": [{"index": i, "embedding": vector(t)} for i, t in enumerate(textos)]})
                if self.path in ("/api/chat", "/v1/chat/completions"):
                    dono.pedidos_chat.append(corpo)
                    if self.path == "/api/chat":
                        return self._responde(200, {"message": {"role": "assistant", "content": dono.resposta_chat}})
                    return self._responde(200, {"choices": [{"message": {"role": "assistant", "content": dono.resposta_chat}}]})
                return self._responde(404, {"error": "?"})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.porta = self.srv.server_address[1]
        self.base = f"http://127.0.0.1:{self.porta}"
        self.url_emb = self.base                 # fornecedor de embeddings «ollama»: a raiz (o motor acrescenta /api/embeddings)
        self.url_chat = self.base + "/v1"        # fornecedor de chat «openai»: …/v1 (o motor acrescenta /chat/completions)
        self.fio = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.fio.start()

    def parar(self):
        self.srv.shutdown()
        self.srv.server_close()
