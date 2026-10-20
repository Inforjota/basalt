"""basalt.sig — pesquisa por SIGNIFICADO na memória (e por palavras quando não há embeddings).

Indexa as notas do espaço (mais o que `[fontes_extra]` do basalt.toml trouxer) e procura por proximidade de significado —
encontra a nota certa mesmo sem as palavras exactas. Sem fornecedor de embeddings ([ia.embeddings]) o MESMO índice responde por
palavras (BM25, basalt.lexical): menos esperto, mas funciona sem IA nenhuma. Nada sai para fora dos endereços configurados.

  python -m basalt.sig indexar                 (re)constrói o índice; só re-embede o que mudou (por hash)
  python -m basalt.sig procura "texto" [-n N] [--json]
  python -m basalt.sig pergunta "texto" [--json]      responde só com excertos da memória (precisa do chat em [ia])
  python -m basalt.sig guarda "texto" [--de ORIGEM]   ideia rápida → caixa de entrada
  python -m basalt.sig fonte FONTE REF [--json]       o texto que o índice tem de um resultado de [fontes_extra] (só leitura)
  python -m basalt.sig entrada [--json] | arquivar N | estado | --autoteste

Índice: <dados>/semantica.sqlite. Espaço: BASALT_ESPACO (+ basalt.toml); BASALT_REAL, BASALT_SIG_DB, BASALT_INBOX,
BASALT_EXCLUIR mandam campo a campo (ver basalt.config.Espaco).
"""
import hashlib, json, os, re, shlex, sqlite3, subprocess, sys, unicodedata
from array import array
from operator import mul
from pathlib import Path

try:                                    # PORTE: o numpy era obrigatório; agora só acelera (sem ele o cosseno faz-se em Python puro)
    import numpy as np
except ImportError:
    np = None

from . import caminhos, config, retidas
from . import entrada as entrada_mod      # «entrada» aqui é o comando do CLI
from .config import Via
from .ia import IA, IAIndisponivel, actual as ia, definir as definir_ia   # noqa: F401  (IA, definir_ia: para quem importa o sig)
from .lexical import IndiceLexical
from .segredos import SegredoEmClaro, retidos, tem_segredo_nota, tem_segredo_texto

PASTA = Via("real")                     # PORTE: antes BASALT_DIR/caminhos fixos; agora as pastas do espaço activo
DB = Via("semantica")
INBOX = Via("inbox")
EXCLUIR = Via("excluir")
LIMIAR = 0.52        # piso baixo: só decide quem é CANDIDATO; o filtro forte (raízes) trata do disparate
LIMIAR_FORTE = 0.80  # tão perto que conta mesmo sem partilhar palavras (calibrado com casos bons e maus, nomic-embed-text)


# ───────────────────────────── IA (embeddings e chat): basalt.ia.actual() ─────────────────────────────
class EmbErro(Exception):
    pass


def _prefixo(papel):
    # PORTE: o prefixo `search_query:`/`search_document:` é do nomic-embed-text (a omissão); outros modelos não o querem
    if "nomic" not in (ia().cfg.embeddings_modelo or "").lower():
        return ""
    return "search_query: " if papel == "query" else "search_document: "


def _normalizar(v):
    n = sum(x * x for x in v) ** 0.5
    return array("f", [x / n for x in v] if n else v)


def embed(texto, papel="document"):
    """Vector normalizado (array 'f'). O nomic-embed-text pede prefixo — sem ele os valores ficam todos altos e o disparate
    «parece» tudo (ver --autoteste do limiar). Tenta mais curto se o fornecedor recusar (HTTP 5xx); 3 falhas transitórias →
    EmbErro (o indexador salta esse pedaço)."""
    px, ult = _prefixo(papel), ""
    for limite in (6000, 2000, 600):
        for _ in range(3):
            try:
                return _normalizar(ia().embeddings([px + texto[:limite]])[0])
            except IAIndisponivel as e:
                ult = str(e)[:80]
                m = re.search(r"HTTP (\d{3})", str(e))
                if m and m.group(1)[0] == "5":
                    break            # pedaço recusado por este tamanho → tenta mais curto
                if m:
                    raise EmbErro(ult)
    raise EmbErro(ult)


def _com_embeddings():
    return ia().embeddings_activos


def _sims(blobs, q):
    """cosseno (vectores já normalizados) de cada blob com o vector da pergunta."""
    if np is not None:
        M = np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(len(blobs), len(q))
        return (M @ np.asarray(q, dtype=np.float32)).tolist()
    out = []
    for b in blobs:
        v = array("f")
        v.frombytes(b)
        out.append(sum(map(mul, v, q)))
    return out


# ───────────────────────────── índice ─────────────────────────────
def sem_acentos(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS pedaco (fonte TEXT, ref TEXT, titulo TEXT, trecho TEXT, hash TEXT, "
              "vec BLOB, atualizado TEXT DEFAULT (datetime('now')), PRIMARY KEY (fonte, ref))")
    return c


def _partir(texto, limite=1000):
    """Parte por parágrafos e junta até ~limite caracteres (não corta a meio de um parágrafo curto)."""
    blocos, atual = [], ""
    for par in re.split(r"\n\s*\n", texto):
        par = par.strip()
        if not par:
            continue
        if len(atual) + len(par) + 1 > limite and atual:
            blocos.append(atual); atual = par
        else:
            atual = (atual + "\n" + par).strip()
    if atual:
        blocos.append(atual)
    return blocos or [texto[:limite]]


def campo(t, nome):
    m = re.search(rf'^{nome}:\s*"?(.+?)"?\s*$', t, re.M)
    return m.group(1) if m else ""


def _extras():
    """[(fonte, ref, titulo, trecho)] dos comandos de `[fontes_extra]` (cada um imprime JSON [{fonte, ref, titulo, texto}]).
    PORTE: aqui entravam as notas de um sistema externo, com comandos fixos no código; agora é só configuração.
    BASALT_SIG_ISOLADO=1 (testes) não corre nenhum, para a suite não ler o basalt.toml de quem a corre."""
    _CONSULTAS.clear()
    if os.environ.get("BASALT_SIG_ISOLADO") == "1":
        return []
    itens, retidos = [], {}
    for cmd in config.carregar(validar=False).fontes_extra:
        _CONSULTAS[cmd] = None                      # falhou (ou ainda não acabou): as fontes dele ficam como estavam
        try:
            r = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=60, check=True)
            vistas = set()
            for d in json.loads(r.stdout or "[]"):
                f, ref, texto = str(d.get("fonte") or ""), str(d.get("ref") or ""), str(d.get("texto") or "")
                if f and f != "nota":
                    vistas.add(f)
                if f and f != "nota" and ref and texto.strip():
                    titulo = str(d.get("titulo") or ref)
                    if tem_segredo_texto(titulo) or tem_segredo_texto(ref):     # o título e a ref também vão para a procura e para as citações
                        retidos[f] = retidos.get(f, 0) + 1
                        continue
                    itens.append((f, ref, titulo, texto))
            _CONSULTAS[cmd] = vistas                  # respondeu (mesmo que com a lista vazia)
        except Exception as e:
            print(f"  aviso: a fonte extra {cmd!r} falhou ({str(e)[:80]}) — indexo o resto", file=sys.stderr)
    for f, n in retidos.items():                # só a fonte e a contagem, nunca o conteúdo
        print(f"  aviso: {n} {'item' if n == 1 else 'itens'} da fonte {f!r} não {'entra' if n == 1 else 'entram'} (segredo no título/ref)",
              file=sys.stderr)
    return itens


# ronda 13: o que cada comando de [fontes_extra] respondeu nesta corrida — comando → fontes que trouxe (respondeu, mesmo
# vazio) ou None (falhou). O `indexar` guarda-o na tabela `consulta` do índice, e as categorias distinguem «a fonte respondeu e
# já não tem nada» (as categorias dela saem) de «a fonte não respondeu» (ficam).
_CONSULTAS: dict = {}


def _registar_consultas(c):
    """tabela `consulta` (fonte, comando, ok): ok=1 se o comando que a trouxe respondeu NESTA corrida. Uma fonte que o comando
    deixou de trazer continua dele (respondeu sem ela = vazia); um comando que falhou ou saiu da configuração põe ok=0."""
    c.execute("CREATE TABLE IF NOT EXISTS consulta (fonte TEXT PRIMARY KEY, comando TEXT, ok INTEGER, quando TEXT)")
    c.execute("UPDATE consulta SET ok=0")
    for cmd, vistas in _CONSULTAS.items():
        if vistas is None:
            continue
        cmd = hashlib.sha256(cmd.encode("utf-8")).hexdigest()       # o comando pode levar argumentos que não interessa guardar
        for (f,) in c.execute("SELECT fonte FROM consulta WHERE comando=?", (cmd,)).fetchall():
            vistas = vistas | {f}
        for f in vistas:
            c.execute("INSERT OR REPLACE INTO consulta (fonte, comando, ok, quando) VALUES (?, ?, 1, datetime('now'))", (f, cmd))


def fontes():
    """Devolve [(fonte, ref, titulo, trecho)] de tudo o que se indexa."""
    itens = []
    for p, t in retidas.notas(PASTA):          # ronda 8 (notas retidas): uma nota retida (lista ou segredo em claro) nunca chega ao índice nem aos embeddings
        nome, desc = campo(t, "name") or p.stem, campo(t, "description")
        corpo = re.sub(r"^---\n.*?\n---\n", "", t, count=1, flags=re.S)
        # o nome+descrição viajam com cada pedaço, para o contexto não se perder
        cab = f"[{nome}] {desc}".strip()
        for i, bloco in enumerate(_partir(corpo)):
            itens.append(("nota", f"{p.stem}#{i}", nome, f"{cab}\n{bloco}" if i == 0 else bloco))
    itens += _extras()
    return [(f, r, tit, tr) for (f, r, tit, tr) in itens if tr and tr.strip()]


def _hash(s):
    return "v2-" + hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()


def indexar():
    c = db(); itens = fontes()
    usar = _com_embeddings()
    tinha = {(a, b): (h, n or 0) for a, b, h, n in c.execute("SELECT fonte,ref,hash,length(vec) FROM pedaco")}
    vistos, novos, falhas = set(), 0, []
    for fonte, ref, titulo, trecho in itens:
        vistos.add((fonte, ref)); h = _hash(trecho)
        h0, n0 = tinha.get((fonte, ref), (None, 0))
        if h0 == h and (n0 or not usar):          # igual e (já tem vector, ou nem se usam vectores)
            continue
        vec = b""
        if usar:
            try:
                vec = embed(trecho).tobytes()
            except EmbErro as e:
                falhas.append((ref, str(e))); continue     # salta este pedaço; volta na próxima corrida
        c.execute("INSERT OR REPLACE INTO pedaco (fonte,ref,titulo,trecho,hash,vec,atualizado) "
                  "VALUES (?,?,?,?,?,?,datetime('now'))", (fonte, ref, titulo, trecho, h, vec))
        novos += 1
        if novos % 25 == 0:
            c.commit(); print(f"  … {novos} embebidos", file=sys.stderr)
    # removo só os pedaços que já não existem; um que HOJE falhou está em `vistos`, logo mantém a versão anterior
    apagar = [k for k in tinha if k not in vistos]
    for fonte, ref in apagar:
        c.execute("DELETE FROM pedaco WHERE fonte=? AND ref=?", (fonte, ref))
    _registar_consultas(c)
    c.commit()
    tot = c.execute("SELECT count(*) FROM pedaco").fetchone()[0]
    print(f"índice: {tot} pedaços · {novos} (re){'embebidos' if usar else 'indexados'} · {len(apagar)} removidos" +
          ("" if usar else " · só por palavras (sem fornecedor de embeddings)") +
          (f" · {len(falhas)} saltados: {', '.join(r for r,_ in falhas[:5])}" if falhas else ""))


def _excluidas():
    """notas que NÃO podem sair (ex.: têm segredo em claro) — o ficheiro `excluir` do espaço, um nome por linha. O que está
    aqui nunca aparece na pesquisa nem chega ao modelo da pergunta."""
    try:
        txt = caminhos.ler_texto(EXCLUIR)              # ronda 13: uma ligação simbólica é recusada, não seguida
    except FileNotFoundError:
        return set()
    return {l.strip() for l in txt.splitlines() if l.strip()}


_RETIDA_AGORA: dict = {}        # sha256 do conteúdo → retida? — a decisão segue o CONTEÚDO, não a marca do ficheiro


def _retidas_agora(nomes, excl) -> dict:
    """ronda 9 (achado G6 da ronda 2): a nota TAL COMO ESTÁ AGORA. Um índice feito antes de ela ganhar um segredo ainda tem os
    pedaços antigos — limpos um a um — e a lista `excluir` pode não estar em dia; olhar só para cada pedaço deixava-os ir para a
    pergunta. Retida = na lista, segredo no nome, segredo no texto actual, ou a nota já não existe (ou não é um ficheiro normal).
    ronda 10 (achado G6 da r3): a decisão guardada era por (mtime, tamanho) — trocar a nota por outra do mesmo tamanho e com o
    mesmo mtime (ou por outro inode) reutilizava a decisão antiga. Agora lê-se SEMPRE o conteúdo actual (pelo descritor verificado,
    sem ligações) e só a detecção de segredos fica guardada, pelo sha256 desse conteúdo.
    ronda 13: as que o guarda deixa passar vão ao verificador configurado ([segredos] verificador) — todas de uma vez, em lotes;
    se ele falhar, ficam retidas (e não se guarda essa decisão: na próxima tentativa volta a perguntar-se)."""
    out, falta = {}, []
    for nome in nomes:
        if nome in excl or tem_segredo_texto(nome):
            out[nome] = True
            continue
        try:
            b = caminhos.ler(Path(PASTA) / f"{nome}.md")
        except (OSError, ValueError):
            out[nome] = True                           # não existe, é uma ligação, não é um ficheiro normal → retida
            continue
        chave = hashlib.sha256(b).hexdigest()
        r = _RETIDA_AGORA.get(chave)
        texto = b.decode("utf-8", errors="replace")
        if r is None:
            r = tem_segredo_texto(texto)
            if len(_RETIDA_AGORA) > 20000:
                _RETIDA_AGORA.clear()
            _RETIDA_AGORA[chave] = r
        out[nome] = r
        if not r:
            falta.append((nome, texto))
    for (nome, _), r in zip(falta, retidos([t for _, t in falta]) if falta else []):
        out[nome] = r
    return out


def _retida_agora(nome, excl):
    return _retidas_agora([nome], excl)[nome]


def carregar(c, onde="", args=()):
    """[(fonte, ref, titulo, trecho, vec_bytes)] — sem as notas retidas TAL COMO ESTÃO AGORA (lista, nome, texto actual), nem
    pedaços com segredo em claro (um índice feito antes de a nota ficar retida ainda os pode ter: saem na próxima indexação, mas
    entretanto também não chegam ao modelo). `onde`/`args`: um filtro SQL a mais (ronda 12: `ler_fonte` lê só uma ref) — o
    filtro das exclusões é este, o MESMO da procura e da pergunta."""
    linhas = c.execute("SELECT fonte,ref,titulo,trecho,vec FROM pedaco" + onde, args).fetchall()
    retida = _retidas_agora({r.split("#")[0] for f, r, *_ in linhas if f == "nota"}, _excluidas())   # ronda 13: em lote
    out = []
    for f, r, t, tr, v in linhas:
        if f == "nota" and retida[r.split("#")[0]]:
            continue
        # ronda 12: um índice feito antes de o título/ref serem verificados ainda pode tê-los (as notas já são tratadas acima)
        if f != "nota" and (tem_segredo_texto(t or "") or tem_segredo_texto(r or "")):
            continue
        if not tem_segredo_texto(tr or ""):
            out.append((f, r, t, tr, v or b""))
    return out


def _ordem_pedaco(ref):
    """«doc» antes de «doc#0», «doc#2» antes de «doc#10»; um sufixo que não é número vai para o fim, por ordem alfabética."""
    _, cardinal, suf = ref.partition("#")
    if not cardinal:
        return (0, 0, "")
    return (1, int(suf), "") if suf.isascii() and suf.isdigit() else (2, 0, suf)


def ler_fonte(fonte, ref):
    """ronda 12: o texto que o índice tem de um resultado de uma fonte externa (`[fontes_extra]`), para se ler sem sair do Basalt.
    Todos os pedaços dessa ref (a ref tal e qual e as «ref#N» com o mesmo título — como a procura, que junta por «ref#»), pela
    ordem. As exclusões são as da procura (`carregar`): um pedaço com um segredo em claro não sai (`omitidos` conta-os, sem
    nada do texto); se nada sobra, None. As notas não se lêem aqui (`fonte="nota"` → None): abrem-se na página, com as regras delas."""
    fonte, ref = str(fonte or ""), str(ref or "")
    base = ref.split("#")[0]
    if not fonte or fonte == "nota" or not base:
        return None
    if tem_segredo_texto(fonte) or tem_segredo_texto(ref):   # ronda 13: recusa ANTES de juntar — senão os irmãos limpos abriam
        return None                                         # pela ref proibida (e a resposta repetia-a)
    c = db()
    try:
        onde, args = " WHERE fonte=? AND (ref=? OR substr(ref, 1, ?)=?)", (fonte, base, len(base) + 1, base + "#")
        todas = c.execute("SELECT ref, titulo FROM pedaco" + onde, args).fetchall()
        linhas = carregar(c, onde, args)
    finally:
        c.close()
    # «repo#12» e «repo#13» podem ser coisas diferentes (dois pedidos do mesmo repositório): com a ref exacta no índice, só se
    # juntam os pedaços com o título dela (os de uma nota partida têm todos o mesmo título)
    exactas = [t for r, t in todas if r == ref]
    if exactas:
        todas = [(r, t) for r, t in todas if t == exactas[0]]
        linhas = [l for l in linhas if l[2] == exactas[0]]
    if not linhas:
        return None
    linhas.sort(key=lambda l: _ordem_pedaco(l[1]))
    return {"fonte": fonte, "ref": ref, "titulo": (exactas[0] if exactas else linhas[0][2]) or base,
            "pedacos": [{"ref": l[1], "texto": l[3]} for l in linhas], "omitidos": max(0, len(todas) - len(linhas))}


BANAIS = {"como", "para", "pelo", "pela", "esta", "este", "isso", "fazer", "feito", "sobre", "mais", "menos", "quando",
          "onde", "cada", "porque", "que", "qual", "quais", "com", "sem", "dos", "das", "numa", "num", "uma", "uns",
          "tem", "ter", "foi", "sao", "ser", "está", "estao", "nao", "sim", "muito", "pouco", "todo", "toda", "coisa",
          "resultados", "resultado", "receita", "receitas", "melhor", "melhores", "pior", "grande", "grandes", "comprar"}   # comuns ou ambíguas → não contam como coincidência


def _raizes(s):
    """conjunto de raízes (5 primeiras letras, sem acentos, sem banais/números). Parte também nos «_» dos nomes de notas
    (project_barcos → project, barcos). Casa cancelados~cancelamentos, voo~voos por prefixo."""
    return {w[:5] for w in re.findall(r"[a-z0-9]+", sem_acentos(s).replace("_", " "))
            if len(w) > 3 and w not in BANAIS and not w.isdigit()}


def _pontuar(termo, linhas):
    """(pontuações 0..1 por linha, semantico). Por significado (cosseno) quando há embeddings e o índice tem vectores do
    mesmo tamanho; senão por palavras (BM25 normalizado). PORTE: se o fornecedor de embeddings não responde à pergunta, cai
    para as palavras em vez de falhar (antes era erro)."""
    if _com_embeddings():
        try:
            q = embed(termo, "query")
        except EmbErro as e:
            print(f"  aviso: sem embeddings agora ({e}) — procuro por palavras", file=sys.stderr)
            q = None
        if q is not None:
            idx = [i for i, l in enumerate(linhas) if len(l[4]) == 4 * len(q)]
            if idx:
                sims = [0.0] * len(linhas)
                for i, s in zip(idx, _sims([linhas[i][4] for i in idx], q)):
                    sims[i] = float(s)
                return sims, True
    ix = IndiceLexical().construir((i, l[2] + "\n" + l[3]) for i, l in enumerate(linhas))
    sims = [0.0] * len(linhas)
    for i, _p, rel in ix.procurar(termo, n=len(linhas)):
        sims[i] = float(rel)
    return sims, False


def _procurar(termo, n, dedup=True):
    c = db(); linhas = carregar(c)
    if not linhas:
        sys.exit("índice vazio — corre: python -m basalt.sig indexar")
    fontesl, refs, titulos, trechos = ([l[k] for l in linhas] for k in range(4))
    sims, semantico = _pontuar(termo, linhas)
    piso = LIMIAR if semantico else 1e-9
    rq = _raizes(termo)
    # IDENTIFICADORES da pergunta: siglas em maiúsculas (NAS, SSO, API) e termos com número (volume2, 104) — muito
    # distintivos e que as raízes perdem (ex.: «NAS» colide com a preposição «nas»). Procuram-se tal e qual no texto.
    ids = set(re.findall(r"\b[A-Z]{2,}\d*\b|\b\w*\d\w*\b", termo)) - {"2026", "2027"}
    ids = {x for x in ids if len(x) >= 2}
    out = []
    for i in sorted(range(len(linhas)), key=lambda k: -sims[k])[:80]:
        s = sims[i]
        if s < piso:
            break
        id_hit = any(re.search(rf"\b{re.escape(x)}\b", titulos[i] + " " + trechos[i]) for x in ids)
        hits = len(rq & _raizes(titulos[i] + " " + trechos[i]))
        # FORTE = de confiança. O nomic dá valores comprimidos, por isso não basta a proximidade:
        #  · muito perto (LIMIAR_FORTE), OU
        #  · pergunta de várias palavras que partilha 2+ raízes com a nota, OU
        #  · pergunta reduzida a UMA palavra cujo termo nomeia a nota (raiz no TÍTULO) — separa «Barcos» (título) de
        #    «melhor telemóvel 2027» (fica só «telemóvel», que não é título de nada).
        no_titulo = bool(rq & _raizes(titulos[i]))
        if semantico:
            # um identificador exacto (NAS, volume2…) num pedaço já topicamente perto = forte
            forte = s >= LIMIAR_FORTE or hits >= 2 or (len(rq) == 1 and no_titulo) or (id_hit and s >= 0.60)
        else:
            # PORTE: por palavras não há «proximidade»: forte = partilha 2+ raízes (ou todas, se a pergunta tem menos de 2) ou tem o identificador
            forte = hits >= min(2, max(1, len(rq))) or id_hit
        out.append({"fonte": fontesl[i], "ref": refs[i], "titulo": titulos[i], "sim": round(s, 3),
                    "hits": hits, "forte": forte, "ordem": s + 0.02 * min(hits, 3) + (0.06 if id_hit else 0),
                    "trecho": trechos[i]})
    # Passagem por TÍTULO: uma palavra que dá nome a uma nota (ex.: «Barcos») pode embeber mal sozinha e ficar abaixo
    # do piso; aqui garante-se que aparece na mesma. Só conta o que o nome/título contém a raiz — não abre porta ao disparate.
    ja = {r["ref"] for r in out}
    if rq:
        for i in range(len(titulos)):
            if refs[i] in ja:
                continue
            if rq & _raizes(titulos[i]):
                s = sims[i]
                out.append({"fonte": fontesl[i], "ref": refs[i], "titulo": titulos[i], "sim": round(s, 3),
                            "hits": 1, "forte": True, "ordem": s + 0.05, "trecho": trechos[i]})
                ja.add(refs[i])
    # Passagem por IDENTIFICADOR em TODO o índice: siglas/termos-com-número (NAS, volume2) são tão distintivos que uma
    # nota que os contenha interessa mesmo que o embedding desta frase fique longe (o nomic falha frases assim). Junta-se
    # o melhor por nota, ordenado pela proximidade, para não encher com dezenas de pedaços que só mencionam «104».
    if ids:
        idl = {x.lower() for x in ids}

        def bate_id(i):
            # 2 = o identificador dá NOME à nota (sinal forte: o utilizador nomeou a nota); 1 = aparece no texto; 0 = não
            nome = set(re.split(r"[\W_]+", sem_acentos(refs[i].split("#")[0])))
            if idl & nome:
                return 2
            return 1 if any(re.search(rf"\b{re.escape(x)}\b", titulos[i] + " " + trechos[i]) for x in ids) else 0
        marcados = [(bate_id(i), i) for i in range(len(titulos)) if refs[i] not in ja]
        marcados = [(g, i) for g, i in marcados if g]
        marcados.sort(key=lambda gi: (-gi[0], -sims[gi[1]]))
        for g, i in marcados[:12]:
            s = sims[i]
            # nome da nota → sobe para o topo; menção no texto → empurrão modesto
            ordem = (0.90 + 0.05 * min(len(rq & _raizes(trechos[i])), 3)) if g == 2 else s + 0.04
            out.append({"fonte": fontesl[i], "ref": refs[i], "titulo": titulos[i], "sim": round(s, 3),
                        "hits": 1, "forte": True, "ordem": ordem, "trecho": trechos[i]}); ja.add(refs[i])
    # um pedaço por nota (o melhor), para não repetir a mesma nota — a `pergunta` desliga isto (quer vários pedaços da nota)
    if dedup:
        vistos_nota, unico = set(), []
        for r in sorted(out, key=lambda r: -r["ordem"]):
            chave = (r["fonte"], r["ref"].split("#")[0])
            if chave in vistos_nota:
                continue
            vistos_nota.add(chave); unico.append(r)
    else:
        unico = sorted(out, key=lambda r: -r["ordem"])
    fortes = [r for r in unico if r["forte"]][:n]
    return fortes, unico[:n]


def procura(termo, n=8, como_json=False):
    fortes, todos = _procurar(termo, n)
    if como_json:
        # a pergunta só deve usar o que é de confiança
        print(json.dumps([{k: r[k] for k in ("fonte", "ref", "titulo", "sim", "trecho")} for r in fortes],
                         ensure_ascii=False, indent=1)); return
    marca = {"nota": "📝", "tarefa": "✔"}

    def linha(r):
        corpo = re.sub(r"\s+", " ", r["trecho"]).strip()
        print(f'{marca.get(r["fonte"],"·")} [{r["sim"]:.2f}] {r["titulo"]}  ({r["ref"]})\n    {corpo[:200]}')
    if fortes:
        for r in fortes:
            linha(r)
        return
    if todos:
        print("sem correspondência clara — talvez (parecido, mas pode não ser o que procuras):")
        for r in todos[:3]:
            linha(r)
        return
    print(f'nada parecido (nenhum acima de {LIMIAR:.2f}). Tenta outras palavras.')


# PORTE: o prompt era do dono da memória (nome próprio); agora genérico. Só em português (a língua da interface inglesa é a fase C).
SISTEMA = ("És a memória de trabalho do utilizador (as notas do Basalt). Respondes SÓ com base nos EXCERTOS numerados que te dou. "
           "Regras: (1) responde em 2 a 4 frases, texto corrido, SEM títulos nem listas; (2) se o excerto tiver números ou "
           "percentagens que respondem à pergunta, usa-os tal como estão; (3) cita a fonte de cada facto com o número entre "
           "parêntesis rectos: [1], [2] (só o número); (4) se a resposta não estiver nos excertos, responde exactamente "
           "«Não está na memória.» e nada mais — NUNCA inventes nem uses conhecimento geral. Português de Portugal.")


def _llm(termo, fontes):
    ctx = "\n\n".join(f"[{i}] {re.sub(chr(10)+'+', ' ', r['trecho']).strip()[:700]}" for i, r in enumerate(fontes, 1))
    return ia().chat(f"EXCERTOS:\n{ctx}\n\nPERGUNTA: {termo}", SISTEMA, tempo=120).strip()


def _para_pergunta(termo, n=5, por_nota=2):
    """pedaços para responder: os mais fortes, no máximo `por_nota` por nota (variedade sem ruído a mais)."""
    fortes, _ = _procurar(termo, 40, dedup=False)
    conta, escolhidos = {}, []
    for r in fortes:
        nota = r["ref"].split("#")[0]
        if conta.get(nota, 0) >= por_nota:
            continue
        conta[nota] = conta.get(nota, 0) + 1; escolhidos.append(r)
        if len(escolhidos) >= n:
            break
    return escolhidos


def pergunta(termo, como_json=False):
    """sem chat configurado → IAIndisponivel (quem chama decide; o `main` imprime o motivo e sai com 2)."""
    fortes = _para_pergunta(termo)
    if not fortes:
        r = {"resposta": "Não está na memória.", "fontes": []}
        print(json.dumps(r, ensure_ascii=False) if como_json else
              'Não encontrei nada de confiança na memória sobre isso.\n'
              f'Tenta reformular, ou: python -m basalt.sig procura "{termo}"')
        return
    resp = _llm(termo, fortes)
    if como_json:
        print(json.dumps({"resposta": resp, "fontes": [{"n": i, "titulo": r["titulo"], "ref": r["ref"], "fonte": r["fonte"]}
                                                        for i, r in enumerate(fortes, 1)]}, ensure_ascii=False)); return
    print(resp + "\n")
    recusa = resp.strip().lower().startswith("não está na memória")
    citadas = {int(x) for x in re.findall(r"\[n?(\d+)\]", resp)}   # aceita [1] e [n1]
    if not recusa and citadas:
        print("Fontes:")
        for i, r in enumerate(fortes, 1):
            if i in citadas:
                print(f"  [{i}] {r['titulo']} · {r['ref']}")


# ───────────────────────────── caixa de entrada ─────────────────────────────
def guardar(texto, de="terminal"):
    """Captura rápida de qualquer lado → caixa de entrada (uma linha JSON). Arruma-se depois (basalt.auto_arrumar ou à mão)."""
    if tem_segredo_nota(texto):           # ronda 13: e o verificador configurado (se falhar, VerificacaoFalhou: também não fica)
        raise SegredoEmClaro()            # o mesmo guarda da web/MCP: a ideia NÃO fica na entrada
    entrada_mod.acrescentar(texto, de)           # pelo trinco comum da Entrada (a web/MCP/arrumação reescrevem o mesmo ficheiro)
    # sugere a nota mais parecida (não decide nada), se houver índice
    try:
        fortes, _ = _procurar(texto, 1)
        dica = f" · parece ligar-se a: {fortes[0]['titulo']}" if fortes else ""
    except (Exception, SystemExit):     # o índice vazio faz sys.exit; a ideia já ficou guardada, não é erro
        dica = ""
    print(f"guardado na entrada do Basalt{dica}")


def _ler_inbox():
    return entrada_mod.ler()


def entrada(como_json=False):
    regs = [r for r in _ler_inbox() if not r.get("arquivado")]
    if como_json:
        print(json.dumps(regs, ensure_ascii=False, indent=1)); return
    if not regs:
        print("caixa de entrada vazia."); return
    print(f"{len(regs)} por arrumar:")
    for i, r in enumerate(regs, 1):
        print(f"  {i}. [{r['t'][:16]} · {r.get('de','?')}] {r['texto'][:160]}")
    print("(arruma à mão nas notas; marca como feito com: python -m basalt.sig arquivar N)")


def arquivar(n):
    try:
        alvo = entrada_mod.arquivar_n(n)
    except IndexError as e:
        sys.exit(f"nº fora do intervalo (há {e.args[0]} por arrumar)")
    print(f"marcado como arrumado: {alvo['texto'][:80]}")


def estado():
    c = db()
    por = c.execute("SELECT fonte,count(*) FROM pedaco GROUP BY fonte ORDER BY 2 DESC").fetchall()
    tot = sum(n for _, n in por)
    print(f"basalt.sig — {DB}\n  {tot} pedaços: " + ", ".join(f"{f} {n}" for f, n in por) if por else
          "índice vazio — corre: python -m basalt.sig indexar")


def autoteste():
    """Não precisa de fornecedor: verifica só a mecânica (partir, hash, cosseno, BM25) com vectores à mão."""
    ok = True
    a = _partir("um\n\ndois\n\n" + "x" * 1200, limite=100)
    if not (len(a) >= 2):
        ok = False; print("FALHA partir")
    v1, v2 = array("f", [1, 0, 0]), array("f", [0, 1, 0])
    s = _sims([v1.tobytes(), v2.tobytes()], v1)
    if not (abs(s[0] - 1) < 1e-6 and abs(s[1]) < 1e-6):
        ok = False; print("FALHA cosseno")
    if _hash("a") == _hash("b"):
        ok = False; print("FALHA hash")
    ix = IndiceLexical().construir([(0, "barcos e velas no porto"), (1, "bolo de laranja com farinha")])
    if not ix.procurar("velas do barco", 1) or ix.procurar("velas do barco", 1)[0][0] != 0:
        ok = False; print("FALHA lexical")
    print("basalt.sig autoteste:", "OK" if ok else "FALHOU"); return ok


def main(argv=None):
    a = sys.argv[1:] if argv is None else argv
    if not a or a[0] in ("-h", "--help"):
        return print(__doc__)
    if a[0] == "--autoteste":
        return sys.exit(0 if autoteste() else 1)
    try:
        config.definir_processo(config.espaco_de_processo())
        return _comando(a)
    except config.ConfigInvalida as e:
        sys.exit(f"configuração inválida: {e}")
    except IAIndisponivel as e:
        print(f"IA indisponível: {e}", file=sys.stderr)
        sys.exit(2)
    except SegredoEmClaro as e:
        print(e, file=sys.stderr)
        sys.exit(2)


def _comando(a):
    if a[0] == "indexar":
        return indexar()
    if a[0] == "estado":
        return estado()
    if a[0] == "procura":
        resto = a[1:]; n = 8; como_json = "--json" in resto
        resto = [x for x in resto if x != "--json"]
        if "-n" in resto:
            i = resto.index("-n"); n = int(resto[i + 1]); resto = resto[:i] + resto[i + 2:]
        if not resto:
            sys.exit('python -m basalt.sig procura "texto"')
        return procura(" ".join(resto), n, como_json)
    if a[0] == "pergunta":
        resto = [x for x in a[1:] if x != "--json"]
        if not resto:
            sys.exit('python -m basalt.sig pergunta "texto"')
        return pergunta(" ".join(resto), "--json" in a[1:])
    if a[0] == "guarda":
        de = "terminal"
        resto = a[1:]
        if "--de" in resto:
            i = resto.index("--de"); de = resto[i + 1]; resto = resto[:i] + resto[i + 2:]
        if not resto:
            sys.exit('python -m basalt.sig guarda "texto"')
        return guardar(" ".join(resto), de)
    if a[0] == "fonte":                  # ronda 12: um resultado de [fontes_extra], só para ler (a página abre-o sem o poder mudar)
        if len(a) < 3:
            sys.exit("python -m basalt.sig fonte FONTE REF [--json]")
        d = ler_fonte(a[1], a[2])
        if "--json" in a[3:]:
            print(json.dumps(d, ensure_ascii=False))
        elif d is None:
            print("não está no índice (ou não se mostra: parece ter uma password ou chave em claro).")
        else:
            print(f"{d['titulo']}  ({d['fonte']} · {d['ref']})\n")
            print("\n\n".join(x["texto"] for x in d["pedacos"]))
        return None if d else sys.exit(1)
    if a[0] == "entrada":
        return entrada("--json" in a[1:])
    if a[0] == "arquivar":
        return arquivar(int(a[1]))
    sys.exit(__doc__)


if __name__ == "__main__":
    main()
