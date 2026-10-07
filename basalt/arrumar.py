"""basalt.arrumar — a memória olha para si própria.

RELATÓRIO, não mexe em nada sozinho: (1) notas quase-duplicadas (mesmo assunto), (2) pendentes soltos (⏳/«por fazer»/«falta»)
em notas antigas, (3) resumo do que mudou hoje. Usa o índice de significado (<dados>/semantica.sqlite) quando ele tem vectores;
sem embeddings compara o texto das notas (TF-IDF) com um limiar mais exigente.

  python -m basalt.arrumar            relatório no terminal
  python -m basalt.arrumar --json     o mesmo em JSON (para a página)
  python -m basalt.arrumar --resumo   só o resumo do dia

Espaço: BASALT_ESPACO (+ basalt.toml); BASALT_REAL e BASALT_SIG_DB mandam campo a campo (ver basalt.config.Espaco).
"""
import datetime, json, math, os, re, sqlite3, subprocess, sys
from array import array
from collections import Counter, defaultdict
from operator import mul

try:                                    # PORTE: o numpy era obrigatório; agora só acelera (sem ele o cosseno faz-se em Python puro)
    import numpy as np
except ImportError:
    np = None

from . import config
from .config import Via
from .lexical import tokens

PASTA = Via("real")                     # PORTE: antes caminhos fixos; agora as pastas do espaço activo
DB = Via("semantica")
DUP = 0.955            # cosseno acima disto entre duas notas = provável duplicado/sobreposição (calibrado: distintas ficam < 0.93)
DUP_LEXICO = 0.6       # PORTE: sem embeddings, cosseno TF-IDF do texto das duas notas. Medido em ~240 notas: >= 0.40 só apanhou pares que os
                       # embeddings também apanham (1 par), a 0.35 já 2 em 5 eram falsos; 0.6 por prudência (quem une automático usa isto)
ANTIGA_DIAS = 21


# ───────────────────────────── vectores ─────────────────────────────
def _dot(a, b):
    if np is not None and not isinstance(a, array):
        return float(a @ b)
    return sum(map(mul, a, b))


def _normal(m):
    n = sum(x * x for x in m) ** 0.5
    return [x / n for x in m] if n else None


def vec_por_nota():
    """vector médio (normalizado) de cada NOTA, a partir dos pedaços do índice. Pedaços sem vector (índice só por palavras) ou
    de outro tamanho que o primeiro (mudou o modelo de embeddings) não contam."""
    if not DB.exists():
        return {}
    c = sqlite3.connect(DB)
    try:
        linhas = list(c.execute("SELECT ref,vec FROM pedaco WHERE fonte='nota'"))
    except sqlite3.DatabaseError:
        return {}
    finally:
        c.close()
    acc, dim = defaultdict(list), 0
    for ref, vec in linhas:
        if not vec or len(vec) % 4:
            continue
        dim = dim or len(vec)
        if len(vec) != dim:
            continue
        acc[ref.split("#")[0]].append(vec)
    out = {}
    for nota, vs in acc.items():
        if np is not None:
            m = np.mean([np.frombuffer(v, dtype=np.float32) for v in vs], axis=0)
            n = np.linalg.norm(m)
            if n:
                out[nota] = (m / n).astype(np.float32)
        else:
            tot = None
            for v in vs:
                a = array("f"); a.frombytes(v)
                tot = list(a) if tot is None else [x + y for x, y in zip(tot, a)]
            m = _normal([x / len(vs) for x in tot])
            if m:
                out[nota] = array("f", m)
    return out


def _tfidf_por_nota():
    """{nota: {termo: peso normalizado}} a partir do texto dos ficheiros (recurso quando o índice não tem vectores)."""
    docs = {}
    for p in sorted(PASTA.glob("*.md")):
        if p.name == "MEMORY.md":
            continue
        docs[p.stem] = Counter(tokens(p.read_text(encoding="utf-8", errors="replace")))
    df, N = Counter(), len(docs)
    for c in docs.values():
        df.update(c.keys())
    out = {}
    for nome, c in docs.items():
        w = {t: (1 + math.log(f)) * (math.log((1 + N) / (1 + df[t])) + 1) for t, f in c.items()}
        n = math.sqrt(sum(x * x for x in w.values()))
        if n:
            out[nome] = {t: x / n for t, x in w.items()}
    return out


def _cos_esparso(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


# ───────────────────────────── relatório ─────────────────────────────
_IGN = {"proj", "proje", "refer", "propo", "sessa", "incid"}   # PORTE: sem «lacer»/«2026» (eram do projecto de origem); anos saem por serem dígitos


def _raiz_nome(nome):
    return {w[:5] for w in re.split(r"[\W_:]+", nome.lower()) if len(w) > 3 and not w.isdigit() and w[:5] not in _IGN}


def duplicados():
    """pares de notas muito próximas E que partilham uma palavra distintiva no NOME — o só-cosseno junta temas diferentes,
    por isso pede-se também a palavra comum. [(semelhança, nota_a, nota_b)], da maior para a menor."""
    v = vec_por_nota()
    if v:
        limiar, sim = DUP, lambda a, b: _dot(v[a], v[b])
    else:
        v = _tfidf_por_nota()
        limiar, sim = DUP_LEXICO, lambda a, b: _cos_esparso(v[a], v[b])
    nomes = list(v)
    raizes = {n: _raiz_nome(n) for n in nomes}
    pares = []
    for i in range(len(nomes)):
        for j in range(i + 1, len(nomes)):
            if raizes[nomes[i]] & raizes[nomes[j]]:      # só se mede o cosseno de quem já partilha uma raiz (resultado igual, bem mais rápido)
                s = sim(nomes[i], nomes[j])
                if s >= limiar:
                    pares.append((round(float(s), 3), nomes[i], nomes[j]))
    return sorted(pares, reverse=True)


def pendentes_soltos():
    """notas antigas (não mexidas há muito) que ainda têm marcadores de pendente — candidatos a fechar ou a agir."""
    agora = datetime.datetime.now().timestamp()
    lim = agora - ANTIGA_DIAS * 86400
    marca = re.compile(r"(⏳|\[ \]|\bpor fazer\b|\bpor decidir\b|\bfalta\b|\bpendente\b)", re.I)
    out = []
    for p in sorted(PASTA.glob("*.md")):
        if p.name == "MEMORY.md" or p.stat().st_mtime > lim:
            continue
        linhas = [l.strip("-* ").strip() for l in p.read_text(encoding="utf-8", errors="replace").splitlines() if marca.search(l)]
        if linhas:
            out.append((int((agora - p.stat().st_mtime) / 86400), p.stem, linhas[:2]))
    return sorted(out, reverse=True)


def resumo_dia():
    """(commits de hoje na memória, notas mexidas hoje). PORTE: sem Farol, as «tarefas mexidas» passam a ser as notas mexidas;
    sem git (ou BASALT_GIT=0) não há commits — conta só os ficheiros."""
    commits = []
    if os.environ.get("BASALT_GIT") != "0":
        g = subprocess.run(["git", "-C", str(PASTA), "log", "--since=midnight", "--format=%s"], capture_output=True, text=True)
        commits = [l for l in g.stdout.splitlines() if l.strip()]
    meia_noite = datetime.datetime.combine(datetime.date.today(), datetime.time.min).timestamp()
    notas = [p.stem for p in sorted(PASTA.glob("*.md")) if p.name != "MEMORY.md" and p.stat().st_mtime >= meia_noite]
    return commits, notas


def relatorio(como_json=False, so_resumo=False):
    commits, notas = resumo_dia()
    hoje = datetime.date.today().isoformat()
    if so_resumo:
        print("\n".join([f"Basalt — resumo de {hoje}", f"  memória: {len(commits)} gravações hoje", f"  notas mexidas: {len(notas)}"]))
        return
    dup, pend = duplicados(), pendentes_soltos()
    if como_json:
        print(json.dumps({"duplicados": dup, "pendentes": [(i, n, l) for i, n, l in pend], "commits_hoje": commits,
                          "notas_hoje": notas}, ensure_ascii=False, indent=1))
        return
    print(f"Basalt — arrumação {hoje}\n")
    print(f"● Notas muito parecidas (rever se juntar): {len(dup)}")
    for s, a, b in dup[:8]:
        print(f"    [{s}] {a}  ≈  {b}")
    print(f"\n● Pendentes soltos em notas com ≥{ANTIGA_DIAS} dias: {len(pend)}")
    for idade, nome, linhas in pend[:8]:
        print(f"    {nome} ({idade}d): {linhas[0][:90]}")
    print(f"\n● Hoje: {len(commits)} gravações na memória · {len(notas)} notas mexidas")


def main(argv=None):
    a = sys.argv[1:] if argv is None else argv
    if "-h" in a or "--help" in a:
        return print(__doc__)
    try:
        config.definir_processo(config.espaco_de_processo())
        relatorio("--json" in a, "--resumo" in a)
    except config.ConfigInvalida as e:
        sys.exit(f"configuração inválida: {e}")


if __name__ == "__main__":
    main()
