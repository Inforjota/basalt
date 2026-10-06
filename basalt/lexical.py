"""basalt.lexical — pesquisa por palavras (BM25) em Python puro, para o Basalt funcionar SEM embeddings nem IA.

É o motor de recurso do índice: quando não há fornecedor de embeddings, `procurar` usa isto. Sem acentos, sem maiúsculas,
tokens de 2+ letras/dígitos; um documento = uma nota (ou um pedaço dela). Pequeno e previsível: ~1 ms por procura em
algumas centenas de notas.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict

_TOKEN = re.compile(r"[a-z0-9]{2,}")
_STOP = frozenset("a o as os um uma uns umas de do da dos das em no na nos nas e ou que se por para com sem ao à às aos "
                  "the of and to in is it for on with as at by an be this that from or".split())


def normalizar(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")


def tokens(s: str) -> list[str]:
    return [t for t in _TOKEN.findall(normalizar(s)) if t not in _STOP]


class IndiceLexical:
    """BM25 (k1=1.5, b=0.75). `construir([(id, texto), …])` e depois `procurar(q, n) → [(id, pontuação)]`."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs: dict = {}            # id → Counter de termos
        self.tamanho: dict = {}         # id → nº de tokens
        self.df: Counter = Counter()    # termo → nº de docs
        self.media = 0.0

    def construir(self, docs) -> "IndiceLexical":
        self.docs, self.tamanho, self.df = {}, {}, Counter()
        for id_, texto in docs:
            ts = tokens(texto)
            self.docs[id_] = Counter(ts)
            self.tamanho[id_] = len(ts)
            self.df.update(set(ts))
        self.media = (sum(self.tamanho.values()) / len(self.tamanho)) if self.tamanho else 0.0
        return self

    def _idf(self, t: str) -> float:
        n, df = len(self.docs), self.df.get(t, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def procurar(self, consulta: str, n: int = 8):
        q = tokens(consulta)
        if not q or not self.docs:
            return []
        pont = defaultdict(float)
        for t in set(q):
            idf = self._idf(t)
            if not self.df.get(t):
                continue
            for id_, c in self.docs.items():
                f = c.get(t, 0)
                if not f:
                    continue
                norm = self.k1 * (1 - self.b + self.b * self.tamanho[id_] / (self.media or 1))
                pont[id_] += idf * (f * (self.k1 + 1)) / (f + norm)
        melhores = sorted(pont.items(), key=lambda x: -x[1])[:n]
        topo = melhores[0][1] if melhores else 1.0
        # devolve também uma «semelhança» 0..1 para a interface tratar igual ao cosseno dos embeddings
        return [(id_, p, (p / topo) if topo else 0.0) for id_, p in melhores]
