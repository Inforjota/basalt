"""basalt.web — o servidor web do Basalt (HTTP, só biblioteca padrão). Contrato: docs/CONTRATO.md.

Segurança (camadas):
 1. quem entra: modo `senha` (formulário + sessão), `cabecalho` (proxy com SSO que põe o e-mail) ou `aberto` (só em 127.0.0.1);
    os agentes (MCP) entram por token Bearer em qualquer modo;
 2. cada utilizador só vê o seu espaço (`config.Espaco`): cada pedido corre «dentro» dele;
 3. escritas (PUT/POST) exigem `X-Basalt: 1` (CSRF) e vão para a CÓPIA editável — o vigia 2vias (`python -m basalt vigia`) leva-as
    à memória real com fotografia git, recusa segredos e trata conflitos;
 4. notas com segredo em claro (lista `excluidas.txt`, refeita de 5 em 5 min) nunca são servidas nem entram na pesquisa/pergunta.
Uso: python -m basalt serve   (basalt.toml; ver basalt.toml.exemplo)
"""
import contextlib, fcntl, hashlib, ipaddress, json, os, re, subprocess, sys, tempfile, threading, time, urllib.parse, uuid
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import arrumar as arr
from . import anexos, auth, caminhos, config, etiquetas, retidas, sig
from . import entrada as entrada_mod
from . import cas as _cas
from . import ia as ia_mod
from . import unir
from .config import Via
from .segredos import (MOTIVO as MOTIVO_SEGREDO, PASSWORD_RX, SEGREDO_RX, _DATA_HORA_RX, _VALIDADE_RX,  # noqa: F401
                       _password_em_claro, tem_segredo_ficheiro, tem_segredo_texto)

STATIC = Path(__file__).resolve().parent / "static"


def dono_de(user) -> str:
    """identificador estável e opaco de quem é a cópia local da página (conta + espaço). Não é segredo nem prova de nada —
    o servidor continua a decidir pela sessão; serve só para a página não misturar dados de duas contas no mesmo browser."""
    return hashlib.sha256(f"{(user.email or '').strip().lower()}\0{user.espaco}".encode("utf-8")).hexdigest()[:24]


def _erro(e, tipo=True) -> str:
    """um erro para o registo ou para a página: nunca leva os argumentos do motor (o texto da nota vai na linha de comandos,
    e o TimeoutExpired/CalledProcessError repetem-na) nem o que pareça uma chave ou password."""
    if isinstance(e, subprocess.TimeoutExpired):
        return f"{type(e).__name__}: o motor não respondeu em {e.timeout} s"
    if isinstance(e, subprocess.CalledProcessError):
        return f"{type(e).__name__}: o motor saiu com código {e.returncode}"
    m = SEGREDO_RX.sub("•••", (f"{type(e).__name__}: " if tipo else "") + str(e))
    return "(mensagem omitida: tinha o que parece uma password ou chave)" if tem_segredo_texto(m) else m
# PORTE: antes caminhos fixos (+ variáveis); agora as pastas do espaço do utilizador do pedido (config.Via segue o espaço activo do fio)
REAL = Via("real")
COPIA = Via("copia")
LEDGER = Via("ledger")
TRINCO = Via("trinco")
EXCL = Via("excluir")
SEMANTICA = Via("semantica")
INBOX = entrada_mod.INBOX                     # o mesmo objecto que basalt.entrada usa (quem ainda o importa daqui continua a funcionar)
TOQUES = Via("toques")
APAGADAS = Via("apagadas")
PERMISSOES_ENTRADA = Via("permissoes_entrada")
CATEGORIAS = Via("categorias")
JOBS = Via("jobs_unir")
VERSOES_OFFLINE = Via("versoes_offline")
IGNORA = {"index.md", "CONFIG.md", "MEMORY.md"}
NOME_RX = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".json": "application/json",
        ".webmanifest": "application/manifest+json", ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf"}   # fontes: KaTeX
TRINCO_ESPERA = 20          # segundos; o vigia segura-o só durante um ciclo (normalmente < 1 s)


# ── idioma da interface (config [basalt] idioma = pt | en | auto) ──
def idioma_do_pedido(configurado: str, accept_language: str | None) -> str:
    """«pt» e «en» mandam sempre; «auto» escolhe pelo Accept-Language: «en» se a 1.ª etiqueta en* (por ordem de q) tem q igual ou
    maior que a da 1.ª pt*; sem nenhuma das duas (ou cabeçalho vazio) fica «pt»."""
    if configurado in ("pt", "en"):
        return configurado
    if configurado != "auto":
        return "pt"
    etiquetas = []                                       # (q, posição, língua) — só pt e en interessam
    for i, parte in enumerate((accept_language or "").split(",")[:30]):
        tag, _, params = parte.strip().partition(";")
        lingua = tag.strip().lower().split("-")[0]
        if lingua not in ("pt", "en"):
            continue
        q = 1.0
        params = params.strip().replace(" ", "")
        if params.startswith("q="):
            try:
                q = float(params[2:])
            except ValueError:
                continue
        if q > 0:
            etiquetas.append((-q, i, lingua))
    etiquetas.sort()                                     # q maior primeiro; em empate, a que vem antes no cabeçalho
    q_en = next((-q for q, _, l in etiquetas if l == "en"), None)
    q_pt = next((-q for q, _, l in etiquetas if l == "pt"), None)
    return "en" if q_en is not None and (q_pt is None or q_en >= q_pt) else "pt"


def indice_no_idioma(html: bytes, idioma: str) -> bytes:
    """Em «en» troca o <html lang="pt-PT"> (index.html) ou <html lang="pt"> (entrar.html) da página; em «pt» devolve os mesmos
    bytes (o português fica byte a byte igual). A página lê `document.documentElement.lang` e traduz-se a si própria."""
    if idioma != "en":
        return html
    for antes in (b'<html lang="pt-PT">', b'<html lang="pt">'):
        if antes in html:
            return html.replace(antes, b'<html lang="en" data-idioma="en">', 1)
    return html

# A cópia editável tem VÁRIOS escritores em processos diferentes — esta web, o vigia 2vias (memória real ⇄ cópia) e quem
# sincronizar a cópia com o telemóvel. Todos passam por um trinco (flock no mesmo ficheiro); o vigia revalida os hashes
# dentro dele antes de escrever. O registo (ledger) diz se a memória real tem uma mudança que ainda não chegou à cópia —
# gravar por cima disso criava um conflito, por isso a web recusa (409) até o vigia a trazer.
# PORTE: o estado em memória é POR ESPAÇO (vários utilizadores no mesmo servidor): trinco de fio e lista de excluídas.
_estado_g = threading.Lock()
_trincos = {}
_excls = {}
_excl_lock = threading.Lock()       # PORTE: a 1.ª lista de excluídas de um espaço calcula-se uma vez só, mesmo com 3 pedidos em simultâneo
_job_lock = threading.Lock()        # PORTE: uma fusão de cada vez por servidor (não por espaço): a IA é um recurso único
_ia_lock = threading.Lock()          # protege as listas de uso por hora
_ia_editor_lock = threading.Lock()  # um pedido de cada vez à IA do editor
_ia_usos = []                       # PORTE: o limite horário de pedidos à IA é do servidor (a IA é partilhada), não de cada utilizador


class Ocupado(Exception):
    pass


def cas():
    """cada escrita da web na cópia é um CAS (basalt.cas: troca atómica + confirmação do que saiu): quem escreve na cópia sem trinco
    (um editor de notas, o telemóvel) e gravou nesse instante ganha, e a web responde 409."""
    return _cas


def _lock_fio():
    k = str(TRINCO)
    with _estado_g:
        return _trincos.setdefault(k, threading.Lock())


@contextlib.contextmanager
def trinco_copia(espera=None):
    espera = TRINCO_ESPERA if espera is None else espera
    lk = _lock_fio()
    if not lk.acquire(timeout=espera):
        raise Ocupado()
    f = None
    try:
        TRINCO.parent.mkdir(parents=True, exist_ok=True)
        f = open(TRINCO, "a+")
        fim = time.time() + espera
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > fim:
                    raise Ocupado()
                time.sleep(0.05)
        yield
    finally:
        if f:
            f.close()                      # fechar o ficheiro liberta o flock
        lk.release()


def _hash_no_ledger(nome_md):
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8")).get(nome_md)
    except (OSError, ValueError):
        return None


# ───────────────────────── segredos e lista de exclusão ─────────────────────────
# Os padrões (SEGREDO_RX, PASSWORD_RX) e tem_segredo_texto/tem_segredo_ficheiro vivem em basalt.segredos — o MESMO guarda para
# a web, o MCP, a entrada por terminal, a importação Obsidian, os anexos de texto e a sincronização (ronda 8, segredos em todas as entradas).


def refazer_exclusao():
    """nota da memória real que não está na cópia = retida pelo guarda de segredos do 2vias. Escreve excluidas.txt
    (usado também pelo basalt.sig). PORTE: sem outras memórias de projectos (só este espaço)."""
    novo = set()
    copia = {p.name for p in caminhos.notas(COPIA)}
    for p in caminhos.notas(REAL):
        if p.name not in IGNORA and ".bak" not in p.name and p.name not in copia:
            novo.add(p.stem)
    EXCL.parent.mkdir(parents=True, exist_ok=True)
    # temporário único por processo e fio: duas escritas simultâneas (pedido da página + vigia) nunca se pisam
    tmp = EXCL.with_name(f".{EXCL.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    tmp.write_text("".join(f"{n}\n" for n in sorted(novo)), encoding="utf-8")
    tmp.replace(EXCL)
    with _estado_g:
        _excls[config.actual().nome] = novo


def excl():
    """as notas excluídas do espaço activo (calculadas na 1.ª vez que se pedem; depois o vigia refaz de 5 em 5 min)."""
    nome = config.actual().nome
    if nome not in _excls:
        with _excl_lock:                  # a verificação e o cálculo no mesmo trinco: só o 1.º fio calcula, os outros esperam e reutilizam
            if nome not in _excls:
                refazer_exclusao()
    return _excls[nome]


def _espacos(cfg):
    """os espaços do servidor: um por espaço usado pelos utilizadores (ou o principal se não há utilizadores)."""
    nomes = sorted({u.espaco for u in cfg.utilizadores}) or [config.ESPACO_OMISSAO]
    return [cfg.espaco(n) for n in nomes]


def vigia_exclusao(cfg):
    while True:
        for esp in _espacos(cfg):
            try:
                with config.dentro(esp.criar()):
                    refazer_exclusao()
            except Exception as e:
                print("exclusão: erro", _erro(e)[:120], flush=True)
        time.sleep(300)


# ───────────────────────── notas ─────────────────────────
def campo(t, nome):
    m = re.search(rf'^\s*{nome}:\s*"?(.+?)"?\s*$', t, re.M)
    return m.group(1) if m else ""


def localizar(nome):
    """(caminho, editável) ou None. PORTE: sem prefixo `projecto:nota` (outras memórias só-leitura); todas as notas são editáveis."""
    if not NOME_RX.match(nome or "") or nome in excl() or f"{nome}.md" in IGNORA:
        return None
    q = COPIA / f"{nome}.md"
    if not caminhos.regular(q):                     # ronda 8 (ligações simbólicas): uma ligação simbólica com nome de nota não se abre (nem para dentro)
        return None
    p = q.resolve()
    return (p, True) if p.parent == COPIA.resolve() and p.exists() else None


def sha(b):
    return hashlib.sha1(b).hexdigest()


def todas_notas():
    out = []
    ex = excl()
    for p in caminhos.notas(COPIA):
        # só nomes que se conseguem abrir (ex.: as cópias «x.CONFLITO-<data>.md» ficam de fora; davam 404)
        if p.name in IGNORA or p.stem in ex or not NOME_RX.match(p.stem):
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        out.append({"nome": p.stem, "titulo": campo(t, "name") or p.stem, "desc": campo(t, "description"),
                    "tipo": (campo(t, "type") or "outros"), "mtime": int(p.stat().st_mtime), "editavel": True})
    cs = cats_de()
    for o in out:
        if o["tipo"] not in ("user", "feedback", "project", "reference"):
            o["tipo"] = "outros"
        o["categorias"] = cs.get(o["nome"], [])
    return sorted(out, key=lambda o: -o["mtime"])


LIG_RX = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


def ligacoes_de(texto):
    return {x.strip() for x in LIG_RX.findall(texto)}


def backlinks(nome):
    """[{nome, contexto}] — contexto = a linha onde está a ligação (como o painel de backlinks do Obsidian)."""
    rx = re.compile(r"\[\[" + re.escape(nome) + r"(?:[#|][^\]]*)?\]\]")
    out = []
    for o in todas_notas():
        loc = localizar(o["nome"])
        if not loc or o["nome"] == nome:
            continue
        t = loc[0].read_text(encoding="utf-8", errors="replace")
        linha = next((l.strip() for l in t.splitlines() if rx.search(l)), None)
        if linha is not None:
            out.append({"nome": o["nome"], "contexto": re.sub(r"\s+", " ", linha)[:220]})
    return out


def _vec(v):
    """um vector do arrumar (numpy ou array) no tipo certo para arr._dot."""
    return arr.np.asarray(v, dtype=arr.np.float32) if arr.np is not None else arr.array("f", v)


def parecidas(nome, k=5):
    """as notas mais próximas de uma nota: por significado (vectores do índice) ou, sem embeddings, por palavras (tf-idf)."""
    try:
        ex = excl()
        v = {n: w for n, w in arr.vec_por_nota().items() if n not in ex}
        if v and nome in v:
            sims = sorted(((float(arr._dot(v[n], v[nome])), n) for n in v if n != nome), reverse=True)[:k]
            return [{"nome": n, "sim": round(s, 3)} for s, n in sims]
        t = {n: w for n, w in arr._tfidf_por_nota().items() if n not in ex}
        if nome not in t:
            return []
        sims = sorted(((float(arr._cos_esparso(t[nome], t[n])), n) for n in t if n != nome), reverse=True)[:k]
        return [{"nome": n, "sim": round(s, 3)} for s, n in sims if s > 0]
    except Exception:
        return []


def _git_ligado():
    return os.environ.get("BASALT_GIT", "1") == "1"


def _git_log(*args, timeout=20):
    """saída do `git log` da memória real; vazia sem git (ou com BASALT_GIT=0, ou se a pasta não é um repositório)."""
    if not _git_ligado():
        return ""
    try:
        return subprocess.run(["git", "-C", str(REAL), "log", *args], capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def historico(nome, n=8):
    out = []
    for l in _git_log(f"-n{n}", "--format=%ci|%s", "--", f"{nome}.md").splitlines():
        d, _, m = l.partition("|")
        out.append({"data": d[:16], "msg": m})
    return out


def recentes(n=20):
    ex = excl()
    out = []
    for bloco in _git_log(f"-n{min(n, 100)}", "--format=@@%ci|%s", "--name-only").split("@@")[1:]:
        linhas = [l for l in bloco.splitlines() if l.strip()]
        d, _, m = linhas[0].partition("|")
        fich = [f for f in linhas[1:] if f.endswith(".md") and Path(f).stem not in ex]
        out.append({"data": d[:16], "msg": m, "ficheiros": fich})
    return out


# ───────────────────────── chamadas ao motor (basalt.sig / arrumar) ─────────────────────────
def motor(args, timeout=60, espaco=None):
    """corre `python -m basalt.sig …` no espaço activo (o filho recebe as pastas por variáveis de ambiente)."""
    return subprocess.run([sys.executable, "-m", "basalt.sig", *args], capture_output=True, text=True, timeout=timeout,
                          env=config.ambiente_filho(espaco), cwd=str(Path(__file__).resolve().parent.parent))


def nome_da_ref(fonte, ref):
    return ref.split("#")[0] if fonte == "nota" else None


def toque(acao, nomes):
    """regista que notas se usaram (só NOMES, sem conteúdo): alimenta visualizações «ao vivo» opcionais. Roda aos 2 MB."""
    try:
        ex = excl()
        nomes = [n for n in nomes if n and n not in ex][:6]
        if not nomes:
            return
        if TOQUES.exists() and TOQUES.stat().st_size > 2_000_000:
            TOQUES.replace(TOQUES.with_suffix(".1.jsonl"))
        with open(TOQUES, "a", encoding="utf-8") as fh:
            for n in nomes:
                fh.write(json.dumps({"ts": time.time(), "acao": acao, "no": n}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def procura(q, n):
    r = motor(["procura", q, "-n", str(n), "--json"])
    try:
        d = json.loads(r.stdout or "[]")
    except ValueError:
        return []
    ex = excl()
    return [{**x, "nome": nome_da_ref(x["fonte"], x["ref"])} for x in d if nome_da_ref(x["fonte"], x["ref"]) not in ex]


def pergunta(q):
    r = motor(["pergunta", q, "--json"], timeout=180)
    try:
        d = json.loads(r.stdout)
    except ValueError:
        return {"resposta": "Não consegui responder agora.", "fontes": []}
    for f in d.get("fontes", []):
        f["nome"] = f["ref"].split("#")[0] if not f["ref"].startswith(("decisao-", "memoria-", "tarefa-")) else None
    return d


def entrada():
    pend = entrada_mod.pendentes()
    if not pend:
        return []
    out = []
    textos = {p.stem: _norm(p.read_text(encoding="utf-8", errors="replace")) for p in caminhos.notas(COPIA)} if pend else {}
    pal = {n: _palavras(t) for n, t in textos.items()}
    df = {}
    for ws in pal.values():
        for w in ws:
            df[w] = df.get(w, 0) + 1
    for i, r in enumerate(pend, 1):
        sug, porque = None, []
        try:
            # a semelhança sozinha não separa (certas 62-76 %, erradas 65-78 % com frases curtas) → a nota tem de partilhar com a
            # ideia uma palavra pouco comum (< 8 % das notas). Calibrado com 8 frases: 7 certas (as sem nota ficam sem sugestão).
            raras = {w for w in _palavras(r["texto"]) if 0 < df.get(w, 0) < 0.08 * max(1, len(textos))}
            for x in procura(r["texto"], 5) if raras else []:
                n = x["nome"]
                comuns = raras & pal.get(n, set()) if n else set()
                if comuns:
                    sug, porque = n, sorted(comuns); break
        except Exception:
            pass
        out.append({"n": i, "t": r["t"], "de": r.get("de", "?"), "texto": r["texto"], "sugestao": sug, "porque": porque,
                    "auto": r.get("auto")})                                  # motivo da dúvida do automático (página: «Para decidires»)
    return out


_PARAR = set("para pelo pela pelos pelas como mais muito muita isto isso esta este estes estas aqui onde quando porque entao depois "
             "antes amanha hoje ontem ideia nota notas fazer feito teste testes primeiro primeira coisa coisas sobre entre ainda "
             "tambem sempre nunca todos todas tenho temos fica ficar".split())


def _norm(s):
    import unicodedata
    return unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode()


def _palavras(t):
    return {w[:6] for w in re.findall(r"[a-z0-9]{4,}", _norm(t)) if w not in _PARAR}


# ───────── Entrada: «Juntar à nota» e «Eliminar» ─────────
def _regs():
    return entrada_mod.ler()


def _entrada_mudar(t, fn):
    """aplica fn(registo) → registo|None (None = apagar) à ideia pendente com o carimbo t — pelo trinco comum da Entrada
    (basalt.entrada), o mesmo que o MCP e o CLI usam para acrescentar."""
    return entrada_mod.mudar(t, fn)


def entrada_arquivar(t):
    """marca a ideia como arrumada sem a juntar a nenhuma nota (a página identifica-a pelo t, não pela posição)."""
    alvo = _entrada_mudar(t, lambda x: dict(x, arquivado=True, arquivado_em=time.strftime("%Y-%m-%dT%H:%M:%S")))
    if not alvo:
        return 404, {"erro": "Essa ideia já não está na Entrada."}
    return 200, {"ok": True, "mensagem": "Arrumada."}


def entrada_juntar(t, nome, auto=None):
    """acrescenta a ideia no fim da nota (com a data e a origem) e tira-a da Entrada."""
    reg = next((r for r in _regs() if r.get("t") == t and not r.get("arquivado")), None)
    if not reg:
        return 404, {"erro": "Essa ideia já não está na Entrada."}
    loc = localizar(nome)
    if not loc or not loc[1]:
        return 400, {"erro": "Escolhe uma nota desta memória."}
    if tem_segredo_texto(reg["texto"]):
        return 400, {"erro": "A ideia tem o que parece uma password ou chave — guarda-a num gestor de palavras-passe, não numa nota."}
    b = loc[0].read_bytes(); atual = b.decode("utf-8", "replace")
    data = time.strftime("%d/%m/%Y", time.strptime(reg["t"][:10], "%Y-%m-%d")) if re.match(r"\d{4}-\d{2}-\d{2}", reg.get("t", "")) else time.strftime("%d/%m/%Y")
    linha = f"- {data}: {reg['texto'].strip()} _(da Entrada, via {reg.get('de', '?')})_"
    c, r = gravar(nome, atual.rstrip("\n") + "\n\n" + linha + "\n", sha(b))
    if c != 200:
        return c, r
    _entrada_mudar(t, lambda x: dict(x, arquivado=True, juntado_em=nome, linha=linha, arquivado_em=time.strftime("%Y-%m-%dT%H:%M:%S"),
                                     **({"auto": auto} if auto else {})))
    return 200, {"ok": True, "mensagem": f"Juntei à nota «{nome}»."}


def entrada_apagar(t):
    """elimina a ideia da Entrada (fica um registo em entrada-apagadas.jsonl, para o caso de ter sido engano)."""
    alvo = _entrada_mudar(t, lambda x: None)
    if not alvo:
        return 404, {"erro": "Essa ideia já não está na Entrada."}
    with open(APAGADAS, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(alvo, apagado_em=time.strftime("%Y-%m-%dT%H:%M:%S")), ensure_ascii=False) + "\n")
    return 200, {"ok": True, "mensagem": "Eliminada."}


# ───────── Entrada AUTOMÁTICA («Automático + Desfazer») ─────────
# De 3 em 3 min: cada ideia nova → candidatas (palavra rara em comum + significado) → a IA do [ia] decide juntar/lixo/dúvida.
# Travões: só junta a uma CANDIDATA com confiança ≥ 0.75; só arquiva como teste se for curta (≤ 60) e SEM palavras distintivas
# (uma ideia real nunca vai para o lixo); o resto fica na Entrada como sugestão para o utilizador aprovar. Tudo com Desfazer.
# PORTE: era um modelo local fixo; agora `ia.actual().chat_json`. Sem IA de chat a entrada automática não faz nada (vigia_entrada
# nem arranca). Os travões usam `sim` do motor: em pesquisa só por palavras a escala é outra e quase nada passa (fica «dúvida»).
def _candidatas(texto, n=3):
    textos = {p.stem: _norm(p.read_text(encoding="utf-8", errors="replace")) for p in caminhos.notas(COPIA)}
    pal = {k: _palavras(v) for k, v in textos.items()}
    df = {}
    for ws in pal.values():
        for w in ws:
            df[w] = df.get(w, 0) + 1
    raras = {w for w in _palavras(texto) if 0 < df.get(w, 0) < 0.08 * max(1, len(textos))}
    out = []
    for x in procura(texto, 6) if raras else []:
        k = x.get("nome")
        if k and k in pal and raras & pal[k] and k not in [c[0] for c in out]:
            out.append((k, sorted(raras & pal[k]), float(x.get("sim") or 0)))
    return out[:n], raras


def decidir_entrada(texto):
    """→ {acao: juntar|lixo|duvida, nota, confianca, motivo}. A decisão final é dos travões, não do modelo."""
    cands, raras = _candidatas(texto)
    desc = "\n".join(f"{i}) {k} — {campo((COPIA / (k + '.md')).read_text(encoding='utf-8', errors='replace'), 'description')[:200]}"
                     for i, (k, *_) in enumerate(cands, 1)) or "(nenhuma)"
    try:
        d = ia_mod.actual().chat_json(f"""Ideia guardada à pressa pelo utilizador: «{texto[:1500]}»
Notas candidatas da memória dele:
{desc}
Responde só JSON: {{"acao":"juntar|lixo|duvida","nota":"<nome exacto de uma candidata ou vazio>","confianca":0.0-1.0,"motivo":"frase curta em português de Portugal"}}
- juntar: a ideia pertence CLARAMENTE a uma das candidatas (mesmo assunto concreto).
- lixo: é só um teste, uma saudação ou não tem conteúdo nenhum.
- duvida: qualquer outro caso (na dúvida, duvida).""", tempo=90)
        if not isinstance(d, dict):
            raise ia_mod.IAIndisponivel("a IA não respondeu com um objecto JSON")
    except Exception as e:
        return {"acao": "duvida", "nota": cands[0][0] if cands else None, "confianca": 0, "motivo": f"IA indisponível ({_erro(e, tipo=False)[:60]})"}
    acao, nota = d.get("acao"), d.get("nota") or None
    try:
        conf = float(d.get("confianca") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    sim = next((c[2] for c in cands if c[0] == nota), 0)
    # 3.º travão: o modelo juntou «pintar a parede de azul» à nota da impressão 3D (0.8 de confiança). A semelhança com o
    # trecho da nota separa: certas 0.72-0.76, errada 0.655 → só junta sozinho com ≥ 0.68 (as mais fracas ficam como sugestão).
    if acao == "juntar" and not (nota in [c[0] for c in cands] and conf >= 0.75 and sim >= 0.68):
        acao = "duvida"
    if acao == "lixo" and not (len(texto.strip()) <= 60 and not raras and conf >= 0.8):
        acao = "duvida"
    if acao not in ("juntar", "lixo"):
        acao, nota = "duvida", (nota if nota in [c[0] for c in cands] else (cands[0][0] if cands else None))
    return {"acao": acao, "nota": nota, "confianca": round(conf, 2), "motivo": str(d.get("motivo", ""))[:200]}


def autor_de(r):
    """quem guardou a ideia. Registos sem «autor» vêm das fontes de confiança do basalt.toml ([entrada] fontes_de_confianca)."""
    fontes = set(config.carregar(validar=False).entrada.fontes_de_confianca)   # lido do toml de cada vez: mudar o toml vale já
    return r.get("autor") or ("dono" if r.get("de") in fontes else f"desconhecido:{r.get('de', '?')}")


def pode_auto(autor):
    """ideias de outras origens ficam SEMPRE na Entrada primeiro, a não ser que tenham permissão para entrarem sozinhas nas
    notas. Lista em entrada-permissoes.json {"auto": [...]} — por omissão só o «dono» (as fontes de confiança)."""
    try:
        return autor in json.loads(PERMISSOES_ENTRADA.read_text()).get("auto", [])
    except (OSError, ValueError):
        return autor == "dono"


def processar_entrada():
    feitos = 0
    for r in entrada_mod.pendentes():       # fotografia; cada decisão volta a ler sob o trinco em _entrada_mudar
        if r.get("arquivado") or r.get("auto") or r.get("auto_bloqueado"):
            continue
        if tem_segredo_texto(r.get("texto", "")):          # ronda 8 (notas retidas): uma ideia com segredo em claro fica na Entrada, sem ir à IA
            _entrada_mudar(r["t"], lambda x: dict(x, auto={"acao": "duvida", "nota": None, "confianca": 0, "autor": autor_de(x),
                                                            "quando": time.strftime("%Y-%m-%dT%H:%M:%S"), "motivo": MOTIVO_SEGREDO}))
            feitos += 1; continue
        d = decidir_entrada(r["texto"]); d["quando"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        d["autor"] = autor_de(r)
        if not pode_auto(d["autor"]) and d["acao"] in ("juntar", "lixo"):       # sem permissão → só sugestão, espera aprovação
            d.update(acao="duvida", motivo=f"de {d['autor']}, sem permissão para entrar sozinha — sugestão: {d.get('nota') or 'nenhuma'}")
        if d["acao"] == "juntar":
            c, _ = entrada_juntar(r["t"], d["nota"], auto=d)
            if c != 200:
                d.update(acao="duvida", motivo=f"não consegui juntar ({c})")
        if d["acao"] == "lixo":
            _entrada_mudar(r["t"], lambda x, d=d: dict(x, arquivado=True, arquivado_em=d["quando"], auto=d))
        if d["acao"] == "duvida":
            _entrada_mudar(r["t"], lambda x, d=d: dict(x, auto=d))
        feitos += 1
    return feitos


def vigia_entrada(cfg):
    if not (cfg.entrada.automatica and ia_mod.actual().chat_activo):
        return                                     # PORTE: sem IA de chat (ou desligada no toml) não há entrada automática
    while True:
        for esp in _espacos(cfg):
            try:
                with config.dentro(esp.criar()):
                    processar_entrada()
            except Exception as e:
                print("vigia_entrada:", _erro(e)[:200], flush=True)
        time.sleep(180)


def entrada_auto(n=20):
    """as últimas ideias arrumadas sozinhas (para a página mostrar com Desfazer)."""
    out = [{"t": r["t"], "texto": r["texto"], "de": r.get("de", "?"), "acao": r["auto"]["acao"], "nota": r.get("juntado_em"),
            "motivo": r["auto"].get("motivo", ""), "quando": r["auto"].get("quando", ""), "desfeito": bool(r.get("auto_bloqueado"))}
           for r in _regs() if r.get("auto") and r["auto"].get("acao") in ("juntar", "lixo")]
    return sorted(out, key=lambda x: x["quando"], reverse=True)[:n]


def entrada_desfazer(t):
    """desfaz o que o automático fez a uma ideia: tira a linha da nota (se ainda lá estiver) e devolve a ideia à Entrada."""
    reg = next((r for r in _regs() if r.get("t") == t and r.get("auto")), None)
    if not reg or reg.get("auto_bloqueado"):
        return 404, {"erro": "Nada para desfazer nesta ideia."}
    msg = "Voltou para a Entrada."
    if reg.get("juntado_em") and reg.get("linha"):
        loc = localizar(reg["juntado_em"])
        if loc and loc[1]:
            b = loc[0].read_bytes(); txt = b.decode("utf-8", "replace")
            if reg["linha"] in txt:
                novo = txt.replace("\n\n" + reg["linha"], "", 1) if ("\n\n" + reg["linha"]) in txt else txt.replace(reg["linha"], "", 1)
                c, g = gravar(reg["juntado_em"], novo, sha(b))
                if c != 200:
                    return c, g
                msg = f"Tirei a ideia da nota «{reg['juntado_em']}» e voltou para a Entrada."
    def _repor(r):
        if r.get("t") == t:
            r.update(arquivado=False, auto_bloqueado=True); r.pop("juntado_em", None); r.pop("linha", None); r.pop("arquivado_em", None)
        return r
    entrada_mod.mudar_todos(_repor)
    return 200, {"ok": True, "mensagem": msg}


# ───────────────────────── arrumar, grafo, categorias ─────────────────────────
def arrumar():
    """o resumo de arrumação (duplicados, pendentes, commits de hoje) — corre `python -m basalt.arrumar --json` no espaço activo."""
    r = subprocess.run([sys.executable, "-m", "basalt.arrumar", "--json"], capture_output=True, text=True, timeout=60,
                       env=config.ambiente_filho(), cwd=str(Path(__file__).resolve().parent.parent))
    try:
        d = json.loads(r.stdout)
    except ValueError:
        return {"duplicados": [], "pendentes": [], "commits_hoje": [], "notas_hoje": [], "auto": None}
    ex = excl()
    d["duplicados"] = [x for x in d.get("duplicados", []) if x[1] not in ex and x[2] not in ex]
    d["pendentes"] = [x for x in d.get("pendentes", []) if x[1] not in ex]
    # as «notas muito parecidas» não são escolha manual: `python -m basalt auto-arrumar` (cron/timer) une/liga/arquiva sozinho;
    # a página mostra só o resumo da última execução (Desfazer continua nas «últimas fusões»).
    try:
        d["auto"] = json.loads((config.actual().auto_arrumar / "ultimo.json").read_text())
    except (OSError, ValueError):
        d["auto"] = None
    return d


def grafo():
    notas = todas_notas()
    ids = {o["nome"] for o in notas}
    links, grau = [], {}
    for o in notas:
        loc = localizar(o["nome"])
        if not loc:
            continue
        for alvo in ligacoes_de(loc[0].read_text(encoding="utf-8", errors="replace")):
            if alvo in ids and alvo != o["nome"]:
                links.append({"source": o["nome"], "target": alvo})
                grau[o["nome"]] = grau.get(o["nome"], 0) + 1; grau[alvo] = grau.get(alvo, 0) + 1
    # PORTE: sem «projeto» nos nós (só havia uma memória)
    return {"nodes": [{"id": o["nome"], "tipo": o["tipo"], "grau": grau.get(o["nome"], 0)} for o in notas], "links": links}


def cats_de():
    try:
        d = json.loads(CATEGORIAS.read_text())
        ex = excl()
        return {n: v.get("cats", []) for n, v in d.items() if n not in ex}
    except Exception:
        return {}


def categorias():
    cs = cats_de(); conta = {}
    for n, cc in cs.items():
        for c in cc:
            conta[c] = conta.get(c, 0) + 1
    return {"categorias": [{"nome": k, "n": v} for k, v in sorted(conta.items(), key=lambda x: -x[1])], "notas": cs}


# ───────────────────────── fusão de notas (botão «Unir») ─────────────────────────
def _job_grava(j):
    JOBS.mkdir(parents=True, exist_ok=True)
    tmp = JOBS / f".{j['id']}.tmp"; tmp.write_text(json.dumps(j, ensure_ascii=False)); tmp.replace(JOBS / f"{j['id']}.json")


def _job_le(jid):
    p = JOBS / f"{jid}.json"
    return json.loads(p.read_text()) if re.match(r"^[0-9a-f-]{8,40}$", jid or "") and p.exists() else None


def jobs_recentes(n=10):
    fs = sorted(JOBS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:n] if JOBS.exists() else []
    return [json.loads(p.read_text()) for p in fs]


def _reindexar(esp=None):
    """tirar o par fundido das «parecidas»: reindexa e recalcula categorias, em segundo plano, neste espaço."""
    e = esp or config.actual()
    for modulo, args in (("basalt.sig", ["indexar"]), ("basalt.categorias", [])):
        subprocess.Popen([sys.executable, "-m", modulo, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=config.ambiente_filho(e), cwd=str(Path(__file__).resolve().parent.parent))


def _correr_fusao(j, esp):
    with config.dentro(esp):
        try:
            a = (REAL / f"{j['manter']}.md").read_text(encoding="utf-8"); b = (REAL / f"{j['fundir']}.md").read_text(encoding="utf-8")
            j.update(estado="a_fundir", passo="A IA está a juntar as duas notas — pode levar 1-3 minutos."); _job_grava(j)
            m = unir.fundir_llm(a, b, j["manter"], j["fundir"])
            j.update(passo="A verificar que não se perdeu nada…"); _job_grava(j)
            ok, motivo, est = unir.validar(a, b, m, j["manter"], j["fundir"])
            j["verificacao"] = est
            if not ok:
                j.update(estado="recusada", passo="", mensagem=f"Não apliquei: {motivo}. As notas ficaram como estavam."); _job_grava(j); return
            h, tocadas, feito = unir.aplicar(Path(str(REAL)), j["manter"], j["fundir"], m, a, b)   # recusa se uma mudou entretanto
            j.update(estado="feita" if feito else "falhou", commit=h, tocadas=tocadas, passo="",
                     mensagem=(f"Unidas. «{j['fundir']}» entrou em «{j['manter']}» ({est.get('factos', 0)} factos, {est.get('em_falta', 0)} perdidos); "
                               f"ligações actualizadas em {len(tocadas)} nota(s)." if feito else "O commit falhou; nada ficou a meio?"))
            _job_grava(j)
            _reindexar(esp)
        except Exception as e:
            j.update(estado="falhou", passo="", mensagem=f"Não apliquei: {_erro(e, tipo=False)[:200]}. (Se chegou a gravar, há fotografia git antes.)"); _job_grava(j)
        finally:
            if _job_lock.locked():
                _job_lock.release()


def iniciar_fusao(manter, fundir):
    if not ia_mod.actual().chat_activo:
        return 503, {"erro": "IA desligada neste servidor: sem ela não há fusão automática (liga-a em [ia] do basalt.toml).", "ia": False}
    for n in (manter, fundir):
        if not NOME_RX.match(n or "") or ":" in n or n in excl() or not (REAL / f"{n}.md").exists():
            return 400, {"erro": f"«{n}» não é uma nota desta memória que se possa unir."}
        if tem_segredo_ficheiro(REAL / f"{n}.md"):            # ronda 8 (notas retidas): retida pelo conteúdo (a lista pode ainda não a ter)
            return 400, {"erro": f"«{n}» tem o que parece uma password ou chave em claro — não a mando à IA."}
    if manter == fundir:
        return 400, {"erro": "Escolhe duas notas diferentes."}
    if not _job_lock.acquire(blocking=False):
        return 409, {"erro": "Já há uma fusão a correr; espera que acabe."}
    j = {"id": uuid.uuid4().hex[:12], "manter": manter, "fundir": fundir, "estado": "na_fila", "passo": "a começar…",
         "criada": time.strftime("%Y-%m-%d %H:%M:%S"), "mensagem": ""}
    _job_grava(j)
    threading.Thread(target=_correr_fusao, args=(j, config.actual()), daemon=True).start()
    return 202, {"ok": True, "job": j["id"]}


def desfazer_fusao(jid):
    j = _job_le(jid)
    if not j or j.get("estado") != "feita" or not j.get("commit"):
        return 400, {"erro": "Só se pode desfazer uma fusão que ficou feita."}
    ok, msg = unir.desfazer(Path(str(REAL)), j["commit"])
    if ok:
        j.update(estado="desfeita", mensagem=msg); _job_grava(j)
        _reindexar()
    return (200 if ok else 409), {"ok": ok, "mensagem": msg}


def diario(dia):
    """o dia da memória: gravações git, notas novas/alteradas, entradas — para a página 📅 Diário."""
    import datetime as _dt
    try:
        d = _dt.date.fromisoformat(dia) if dia else _dt.date.today()
    except ValueError:
        d = _dt.date.today()
    a, b = d.isoformat(), (d + _dt.timedelta(days=1)).isoformat()
    saida = _git_log(f"--since={a} 00:00", f"--until={b} 00:00", "--format=@@%ci|%s", "--name-status", timeout=30)
    commits, novas, mudadas = [], set(), set()
    ex = excl()
    for bloco in saida.split("@@")[1:]:
        linhas = [l for l in bloco.splitlines() if l.strip()]
        data, _, msg = linhas[0].partition("|"); fich = []
        for l in linhas[1:]:
            est, _, f = l.partition("\t")
            if not f.endswith(".md") or Path(f).stem in ex or f == "MEMORY.md":
                continue
            fich.append(f); (novas if est.startswith("A") else mudadas).add(Path(f).stem)
        commits.append({"hora": data[11:16], "msg": msg, "ficheiros": fich})
    ent = [e for e in _regs() if str(e.get("t", "")).startswith(a)]
    # PORTE: sem «tarefas» no diário (vinham de um sistema de tarefas externo)
    return {"dia": a, "commits": commits, "notas_novas": sorted(novas), "notas_mudadas": sorted(mudadas - novas),
            "entrada": [{"t": e["t"], "de": e.get("de"), "texto": e["texto"]} for e in ent]}


def indice():
    """O índice CURADO do Basalt (MEMORY.md da memória real) — só leitura. Recusado se tiver o que parece um segredo."""
    p = REAL / "MEMORY.md"
    if not p.exists():
        return {"markdown": "", "atualizado": 0, "notas": 0}
    if tem_segredo_ficheiro(p):
        return {"markdown": "_O índice tem neste momento algo que parece uma password — não o mostro aqui._",
                "atualizado": int(p.stat().st_mtime), "notas": 0}
    t = p.read_text(encoding="utf-8", errors="replace")
    return {"markdown": t, "atualizado": int(p.stat().st_mtime), "notas": len(re.findall(r"\]\([^)]+\.md\)", t))}


def estado():
    notas = todas_notas()
    lig = sum(len(ligacoes_de(localizar(o["nome"])[0].read_text(encoding="utf-8", errors="replace")))
              for o in notas if localizar(o["nome"]))
    ped = 0
    try:
        import sqlite3
        ped = sqlite3.connect(SEMANTICA).execute("SELECT count(*) FROM pedaco").fetchone()[0]
    except Exception:
        pass
    ult = recentes(1)
    ent = len(entrada_mod.pendentes())
    desc = ia_mod.actual().descricao()
    # PORTE: «modelo» era uma constante; agora o modelo de chat configurado ("" = IA desligada) e `ia` com o resto
    return {"notas": len(notas), "ligacoes": lig, "entrada": ent, "pedacos": ped,
            "ultima_foto": f"{ult[0]['data']} · {ult[0]['msg']}" if ult else "", "modelo": desc["modelo"], "ia": desc}


# ───────────────────────── escrita ─────────────────────────
OCUPADO = (503, {"erro": "A sincronização com a memória está ocupada; tenta daqui a uns segundos.", "estado": "ocupado"})


def gravar(nome, texto, base_hash):
    loc = localizar(nome)
    if not loc:
        return 404, {"erro": "Nota não encontrada."}
    p, editavel = loc
    if not editavel:
        return 403, {"erro": "Esta nota só se pode ler."}
    if tem_segredo_texto(texto):
        return 400, {"erro": "O texto tem o que parece uma password ou chave em claro — não gravei. Guarda-a num gestor de palavras-passe."}
    try:
        with trinco_copia():
            if not p.exists():
                return 404, {"erro": "Nota não encontrada."}
            b = p.read_bytes()
            actual = sha(b)
            if base_hash != actual:         # o 409 traz SEMPRE o texto actual e o hash dele (lidos juntos)
                return 409, {"estado": "conflito", "erro": "A nota mudou entretanto (noutro sítio). Recarrega para veres a versão nova.",
                             "actual": b.decode("utf-8", "replace"), "hash_actual": actual}
            pr = REAL / p.name              # a memória real já mudou e o vigia ainda não trouxe → não sobrepor
            if pr.exists():
                rh = sha(pr.read_bytes())
                if rh != actual and rh != _hash_no_ledger(p.name):
                    return 409, {"erro": "A nota acabou de mudar na memória (um agente); espera uns segundos e recarrega.",
                                 "hash_actual": actual, "actual": b.decode("utf-8", "replace"), "pendente": True, "estado": "ocupado"}
            dados = texto.encode("utf-8")
            motivo = cas().escrever(p, dados, actual, "web")
            if motivo:
                b = p.read_bytes() if p.exists() else b""
                return 409, {"estado": "conflito", "erro": f"Não gravei: {motivo}. Revê a versão que lá está.",
                             "actual": b.decode("utf-8", "replace"), "hash_actual": sha(b)}
    except Ocupado:
        return OCUPADO
    return 200, {"ok": True, "hash": sha(dados)}


def criar(nome, texto):
    if not re.match(r"^[a-z0-9_]{2,100}$", nome or ""):
        return 400, {"erro": "Nome inválido: usa só letras minúsculas, números e _ (ex.: ideia_barcos)."}
    if f"{nome}.md".lower() in {n.lower() for n in IGNORA}:     # «index» criava uma nota invisível
        return 400, {"erro": f"«{nome}» é um nome reservado; escolhe outro."}
    if tem_segredo_texto(texto or ""):
        return 400, {"erro": "O texto tem o que parece uma password ou chave em claro — não gravei."}
    p = COPIA / f"{nome}.md"
    try:
        with trinco_copia():
            if p.exists() or (REAL / f"{nome}.md").exists():
                return 409, {"erro": "Já existe uma nota com esse nome."}
            if cas().escrever(p, (texto or "").encode("utf-8"), None, "web"):    # nunca por cima de quem a criou agora
                return 409, {"erro": "Já existe uma nota com esse nome."}
    except Ocupado:
        return OCUPADO
    return 201, {"ok": True, "nome": nome}


# ─────────── sem internet · notas parecidas ao escrever · IA no editor ───────────
def pacote():
    """tudo o que a página guarda no aparelho para funcionar SEM internet: as notas com texto e hash (o hash é o que a
    sincronização manda como base_hash — se a nota mudou no servidor entretanto, o gravar() dá 409 e nada se sobrepõe)."""
    out = []
    for o in todas_notas():
        loc = localizar(o["nome"])
        if loc:
            b = loc[0].read_bytes()
            out.append(dict(o, texto=b.decode("utf-8", "replace"), hash=sha(b)))
    return {"gerado": int(time.time()), "notas": out}


def parecidas_texto(nome, texto):
    """notas existentes muito parecidas com o texto que se está a escrever (o aviso «já tens uma nota assim»).
    PORTE: com embeddings usa o mesmo limiar do arrumar (arr.DUP); sem eles, TF-IDF com arr.DUP_LEXICO."""
    try:
        ex = excl()
        nome = nome or "nota_nova"
        if sig._com_embeddings():
            desc = (re.search(r"^description:\s*(.*)$", texto, re.M) or [None, ""])[1]
            corpo = re.sub(r"^---\n.*?\n---\n", "", texto[:8000], count=1, flags=re.S)
            vs = [sig.embed((f"[{nome}] {desc}\n" if i == 0 else "") + b) for i, b in enumerate(sig._partir(corpo)[:4])]
            v = arr._normal([sum(x) / len(vs) for x in zip(*vs)]) if vs else None
            if v is None:
                return {"parecidas": []}
            v = _vec(v)
            todas = {k: w for k, w in arr.vec_por_nota().items() if k != nome and k not in ex}
            sims = sorted(((float(arr._dot(v, w)), k) for k, w in todas.items()), reverse=True)[:3]
            return {"parecidas": [{"nome": k, "semelhanca": round(s, 3)} for s, k in sims if s >= arr.DUP]}
        extra = "__texto_novo__"
        t = {k: w for k, w in arr._tfidf_por_nota(extra={extra: texto[:8000]}).items() if k == extra or k not in ex}
        sims = sorted(((float(arr._cos_esparso(t[extra], w)), k) for k, w in t.items() if k not in (extra, nome)), reverse=True)[:3]
        return {"parecidas": [{"nome": k, "semelhanca": round(s, 3)} for s, k in sims if s >= arr.DUP_LEXICO]}
    except Exception:
        return {"parecidas": []}


IA_ACOES = {
    "resumir": "Resume o texto em poucas linhas. Mantém todos os números, datas, nomes, caminhos e [[ligações]].",
    "reescrever": "Reescreve o texto mais claro e mais curto, sem perder nenhum facto (números, datas, nomes, caminhos, `código`, [[ligações]]).",
    "corrigir": "Corrige só a ortografia e a gramática (português de Portugal). Não mudes o sentido, a ordem nem o estilo.",
    "continuar": "Escreve só a continuação natural do texto (uma frase ou item de lista), no mesmo estilo. Não inventes factos: se não souberes, escreve menos.",
}


def _limite_horario(usos, maximo):
    """True se ainda há quota na última hora (e regista o uso)."""
    agora = time.time()
    with _ia_lock:
        usos[:] = [t for t in usos if agora - t < 3600]
        if len(usos) >= maximo:
            return False
        usos.append(agora)
        return True


def ia(acao, texto, contexto=""):
    """o editor pede à IA para resumir/reescrever/corrigir/continuar o texto seleccionado. Um pedido de cada vez, com limite
    por hora ([ia] pedidos_por_hora); nunca envia nem devolve o que pareça segredo. IA desligada → 503 {"ia": false}."""
    cfg_ia = ia_mod.actual()
    if not cfg_ia.chat_activo:
        return 503, {"erro": "IA desligada neste servidor.", "ia": False}
    if acao not in IA_ACOES:
        return 400, {"erro": "Acção desconhecida."}
    maximo = cfg_ia.cfg.texto_maximo
    texto, contexto = (texto or "").strip(), (contexto or "")[:4000]
    if not texto:
        return 400, {"erro": "Selecciona primeiro o texto."}
    if len(texto) > maximo:          # cortar em silêncio fazia o «Substituir» apagar o fim da selecção
        return 400, {"erro": f"A selecção tem {len(texto)} caracteres; a IA só trabalha com até {maximo} de cada vez. Selecciona menos."}
    if tem_segredo_texto(texto + "\n" + contexto):
        return 400, {"erro": "O texto tem o que parece uma password ou chave — não o mando à IA."}
    if not _limite_horario(_ia_usos, cfg_ia.cfg.pedidos_por_hora):
        return 429, {"erro": f"Já foram {cfg_ia.cfg.pedidos_por_hora} pedidos à IA na última hora; espera um bocado."}
    if not _ia_editor_lock.acquire(blocking=False):
        return 409, {"erro": "Já há um pedido à IA a correr; espera uns segundos."}
    try:
        prompt = (f"{IA_ACOES[acao]}\nResponde APENAS com o texto final em markdown, sem explicações e sem ``` à volta.\n"
                  + (f"\nContexto (a nota onde o texto está — só para perceberes o assunto, não o repitas):\n{contexto}\n" if contexto else "")
                  + f"\n=== TEXTO ===\n{texto}")
        try:
            out = re.sub(r"^```(?:markdown|md)?\n|\n```$", "", cfg_ia.chat(prompt, tempo=180).strip())
        except ia_mod.IAIndisponivel:
            return 502, {"erro": "A IA demorou demasiado ou respondeu mal; tenta outra vez."}
        if not out:
            return 502, {"erro": "A IA não respondeu; tenta outra vez."}
        if tem_segredo_texto(out):
            return 502, {"erro": "A resposta tinha o que parece um segredo — não a mostro."}
        return 200, {"texto": out, "modelo": cfg_ia.modelo}
    finally:
        _ia_editor_lock.release()


REC_ESPERA = 2              # segundos à espera de outra sincronização antes de responder «ocupado»
_rec_lock = threading.Lock()
_rec_usos = []
# PORTE: a marca deixou de nomear uma pessoa; o texto é parte do contrato com a página (que a mostra como «a confirmar»)
MARCA_CONFIRMAR = "⚠️ a confirmar (versão offline vs agentes)"


def _texto_hash(p):
    b = p.read_bytes()
    return b.decode("utf-8", "replace"), sha(b)


def _guardar_versao_offline(nome, texto):
    """a versão escrita sem internet nunca se perde — uma cópia por CONTEÚDO (o mesmo pedido repetido não acumula ficheiros)."""
    VERSOES_OFFLINE.mkdir(parents=True, exist_ok=True)
    h = sha(texto.encode("utf-8"))[:10]
    ja = sorted(VERSOES_OFFLINE.glob(f"{nome}.*.{h}.md"))
    if ja:
        return ja[-1].name
    copia = VERSOES_OFFLINE / f"{nome}.{time.strftime('%Y%m%d-%H%M%S')}.{h}.md"
    copia.write_text(texto, encoding="utf-8")
    return copia.name


def _partes(t):
    """(frontmatter, corpo) em linhas TAL E QUAL (só sem os espaços do fim — a indentação conta) e sem linhas vazias.
    frontmatter = None se o texto não o tem."""
    ls, fm = t.splitlines(), None
    if ls and ls[0].rstrip() == "---":
        for i in range(1, len(ls)):
            if ls[i].rstrip() == "---":
                fm, ls = ls[1:i], ls[i + 1:]
                break
    limpo = lambda xs: [x.rstrip() for x in xs if x.strip()]
    return (None if fm is None else limpo(fm)), limpo(ls)


def _sem_marca(x):
    """«linha ⚠️ a confirmar (…)» → «linha» (a marca só no FIM e depois de um espaço); senão None."""
    if x.endswith(MARCA_CONFIRMAR):
        y = x[:-len(MARCA_CONFIRMAR)]
        if y.strip() and y != y.rstrip():
            return y.rstrip()
    return None


def _subseq_falta(req, F):
    """devolve a 1.ª linha de req que não aparece em F pela mesma ordem (None se estão todas)."""
    j = 0
    for x in req:
        while j < len(F) and F[j] != x:
            j += 1
        if j >= len(F):
            return x
        j += 1
    return None


def _validar_bloco(B, T, A, F, onde):
    cB, cT, cA = Counter(B), Counter(T), Counter(A)
    novas_t = {x for x in cT if cT[x] > cB[x]}
    G = []                                  # o resultado com a marca «⚠️ a confirmar» tirada (conta como a linha do utilizador)
    for x in F:
        if x in cA or x in cT:
            G.append(x)
            continue
        y = _sem_marca(x)
        if y is not None and y in novas_t:
            G.append(y)
            continue
        return f"{onde}tem uma linha que não está tal e qual em nenhuma das versões: «{x[:80]}»"
    cF = Counter(G)
    for x in set(cB) | set(cT) | set(cA) | set(cF):
        b, dT, dA = cB[x], cT[x] - cB[x], cA[x] - cB[x]
        mais_min = max(dT, dA) if dT > 0 and dA > 0 else max(dT, 0) + max(dA, 0)   # os dois juntaram a mesma = pode ser 1
        mais_max = max(dT, 0) + max(dA, 0)
        menos_max = max(-dT, 0) + max(-dA, 0)
        menos_min = max(-dT, -dA, 0)
        lo, hi = max(0, b + mais_min - menos_max), b + mais_max - menos_min
        if not lo <= cF[x] <= hi:
            quantas = f"{lo}×" if lo == hi else f"entre {lo} e {hi}×"
            return f"{onde}a linha «{x[:80]}» aparece {cF[x]}× — devia aparecer {quantas}"
    apagadas = cB - cT
    precisa_a = []
    for x in A:
        if apagadas[x] > 0:
            apagadas[x] -= 1
        else:
            precisa_a.append(x)
    novas = (cT - cB) - cA
    precisa_t = []
    for x in T:
        if novas[x] > 0:
            novas[x] -= 1
            precisa_t.append(x)
    x = _subseq_falta(precisa_a, G)
    if x is not None:
        return f"{onde}perdia ou mudava de sítio uma linha da versão do servidor: «{x[:80]}»"
    x = _subseq_falta(precisa_t, G)
    if x is not None:
        return f"{onde}perdia ou mudava de sítio uma linha tua: «{x[:80]}»"
    return ""


def _validar_juncao(base, teu, actual, final):
    """A verificação decide por LINHAS EXACTAS (a indentação conta; só se
    ignoram os espaços do fim e as linhas vazias), separando o frontmatter do corpo. Regras, em cada parte:
      1) cada linha do resultado é, tal e qual, uma linha da ACTUAL ou da do utilizador — a IA só pode juntar « ⚠️ a confirmar…»
         no FIM de uma linha NOVA do utilizador;
      2) cada linha aparece o número de vezes certo (o que estava na BASE ± o que o utilizador e os agentes juntaram/tiraram) —
         nem duplicadas, nem uma ocorrência nova do utilizador «engolida» por uma antiga igual;
      3) as linhas da ACTUAL (menos as que o utilizador apagou) e as linhas novas do utilizador ficam pela mesma ordem.
    Garante que não se perde nem inventa uma LINHA; não garante o SENTIDO (um título mudado por cima de uma lista que os
    agentes aumentaram) — por isso nenhuma junção se grava sozinha: o utilizador confirma sempre.
    Devolve '' se está bem, senão o motivo."""
    if len(final.strip()) < 20:
        return "a junção veio vazia"
    if tem_segredo_texto(final):
        return "o resultado tem o que parece um segredo"
    (fB, cB), (fT, cT), (fA, cA), (fF, cF) = (_partes(t) for t in (base, teu, actual, final))
    if fF is None and (fA is not None or fT is not None):
        return "perdia o frontmatter"
    if fF is not None and fA is None and fT is None:
        return "inventava um frontmatter"
    if fF is not None:
        m = _validar_bloco(fB or [], fT or [], fA or [], fF, "frontmatter: ")
        if m:
            return m
    return _validar_bloco(cB, cT, cA, cF, "")


def _juntar_linhas(base, teu, actual):
    """junção clássica a 3 versões (git merge-file): (texto, nº de sobreposições). Determinística: nunca reescreve uma linha."""
    with tempfile.TemporaryDirectory(prefix="basalt-juntar-") as d:
        fs = []
        for n, t in (("teu", teu), ("base", base), ("actual", actual)):
            q = Path(d) / n
            q.write_text(t, encoding="utf-8")
            fs.append(str(q))
        r = subprocess.run(["git", "merge-file", "-p", "-L", "teu", "-L", "base", "-L", "actual", *fs], capture_output=True, timeout=30)
    if not 0 <= r.returncode < 128:
        raise RuntimeError("git merge-file falhou")
    return r.stdout.decode("utf-8", "replace"), r.returncode




def _proposta_ia(base_texto, texto, actual):
    prompt = f"""Há três versões da mesma nota da memória de trabalho do utilizador:
- BASE: a que o utilizador tinha no aparelho quando ficou sem internet.
- DO UTILIZADOR: o que ele escreveu sem internet, a partir da BASE.
- ACTUAL: a que está agora no servidor — os agentes mudaram-na entretanto.
Escreve a nota FINAL, que o utilizador vai rever antes de gravar:
1. Parte da ACTUAL e não percas nenhuma linha dela, pela mesma ordem — excepto as linhas da BASE que o utilizador apagou ou corrigiu (e os agentes não mexeram): aí fica a versão dele.
2. Acrescenta as linhas novas do utilizador no sítio certo. Não omitas nenhuma: se achares que já está dito na ACTUAL, mantém-na na mesma e diz no resumo qual é.
3. Usa as linhas TAL E QUAL estão nas versões: não reescrevas, não resumas, não mudes números, palavras nem ligações [[…]]. Podes só escolher e ordenar linhas.
4. Se o que o utilizador escreveu contradiz a ACTUAL, mantém as duas linhas e junta no fim da linha dele « {MARCA_CONFIRMAR}».
5. Mantém o frontmatter (--- … ---). Não inventes nada.
Responde com a nota final completa, depois uma linha só com ===RESUMO=== e depois 1-3 frases: o que acrescentaste, o que te parece repetido e o que ficou a confirmar.

=== BASE ===
{base_texto}

=== DO UTILIZADOR ===
{texto}

=== ACTUAL ===
{actual}
"""
    res = ia_mod.actual().chat(prompt, tempo=180).strip()
    final, _, resumo = res.partition("===RESUMO===")
    return re.sub(r"^```(?:markdown|md)?\n|\n```$", "", final.strip()) + "\n", resumo.strip()[:600]


def _conflito(p, erro, teu, copia=None, **extra):
    """TODAS as respostas de conflito trazem o texto actual e o hash desse mesmo texto (lidos juntos)."""
    try:
        actual, h = _texto_hash(p)
    except OSError:
        actual, h = "", ""
    r = {"estado": "conflito", "erro": erro, "actual": actual, "hash_actual": h, "teu": teu}
    if copia:
        r["copia"] = copia
    r.update(extra)
    return 409, r


def reconciliar(nome, base_texto, texto):
    """sincronização de uma edição feita SEM internet (pedido do utilizador 25/09: «se os agentes mudaram entretanto, um agente
    verifica se não é algo já dito ou tratado»). BASE = o que o aparelho tinha, TEU = o que se escreveu offline, ACTUAL = o
    servidor agora. Ninguém mexeu → grava. Mexeram em sítios diferentes → junção linha a linha (git merge-file), verificada,
    como PROPOSTA para o utilizador confirmar. Mexeram nas MESMAS linhas → conflito: a IA pode deixar uma
    PROPOSTA (verificada linha a linha), mas quem escolhe é o utilizador — nenhuma reescrita da IA se grava sozinha, nem
    nenhuma junção.
    A versão offline fica sempre guardada na pasta offline-versoes do espaço."""
    loc = localizar(nome)
    if not loc:
        return 404, {"erro": "Nota não encontrada."}
    if not loc[1]:
        return 403, {"erro": "Esta nota só se pode ler."}
    p = loc[0]
    if tem_segredo_texto(texto):
        return 400, {"erro": "O texto tem o que parece uma password ou chave em claro — não gravei. Guarda-a num gestor de palavras-passe."}
    actual, h_actual = _texto_hash(p)
    if actual == base_texto or actual == texto:                # ninguém mexeu (ou já está igual) → gravação normal
        c, r = gravar(nome, texto, h_actual)
        if c != 409:
            return c, dict(r, estado="gravado" if c == 200 else r.get("estado", "erro"))
        if r.get("pendente"):                                  # o vigia está a trazer uma mudança → tenta já a seguir
            return 503, dict(r, estado="ocupado")
        # mudou entre a leitura e a gravação → segue o caminho completo (cópia da versão offline + junção)
    # limites ANTES de guardar cópias ou de esperar — uma sincronização de cada vez, sem 5 min de espera por pedido
    if not _rec_lock.acquire(timeout=REC_ESPERA):
        return 503, {"estado": "ocupado", "erro": "Há outra sincronização a correr; tento outra vez daqui a pouco."}
    try:
        copia = _guardar_versao_offline(nome, texto)          # a versão offline nunca se perde, aconteça o que acontecer
        actual, h_actual = _texto_hash(p)
        if actual == base_texto or actual == texto:
            c, r = gravar(nome, texto, h_actual)
            if c != 409:
                return c, dict(r, estado="gravado" if c == 200 else r.get("estado", "erro"), copia=copia)
            if r.get("pendente"):
                return 503, dict(r, estado="ocupado", copia=copia)
            return _conflito(p, r.get("erro", "A nota mudou outra vez."), texto, copia)      # com texto + hash
        try:
            junta, n = _juntar_linhas(base_texto, texto, actual)
        except (subprocess.TimeoutExpired, RuntimeError, OSError):
            junta, n = "", -1
        if n == 0 and not _validar_juncao(base_texto, texto, actual, junta):
            # a junção linha a linha também NÃO se grava sozinha — juntar linhas não preserva o
            # sentido (ex.: o utilizador muda «# Serviços permitidos» para «proibidos» e os agentes juntam um serviço à lista). Fica
            # como proposta pronta: o utilizador confirma com um toque. Sem IA.
            return _conflito(p, "Os agentes mudaram outras partes desta nota — confirma a junção.", texto, copia,
                             proposta=junta, proposta_hash=h_actual, proposta_tipo="linhas",
                             resumo="Juntei linha a linha, sem IA: as tuas mudanças e as dos agentes estão em sítios diferentes. "
                                    "Confirma que o sentido continua certo (ex.: um título que mudaste e o que está por baixo).")
        extra = {}
        # ronda 9 (achado G6 da ronda 2): o prompt leva as TRÊS versões — só se validava a do utilizador; uma password na BASE (já
        # apagada por ele) ou na ACTUAL, ou uma nota retida, iam para a IA. Assim fica só o conflito, para o utilizador escolher.
        a_ia_pode = not retidas.retida(nome, "\n".join((base_texto, texto, actual)))
        # PORTE: sem IA de chat não há proposta (fica só o conflito para o utilizador escolher); o limite horário é [ia] propostas_por_hora
        if a_ia_pode and ia_mod.actual().chat_activo and _limite_horario(_rec_usos, ia_mod.actual().cfg.propostas_por_hora):
            try:
                prop, resumo = _proposta_ia(base_texto, texto, actual)
                motivo = _validar_juncao(base_texto, texto, actual, prop)
                if not motivo:
                    extra = {"proposta": prop, "proposta_hash": h_actual, "resumo": resumo, "proposta_tipo": "ia"}
                else:                                          # ronda 9 (achado G7 da ronda 2): o motivo cita linhas da nota — não vai para o registo
                    print("sincronizar: proposta da IA recusada (não passou a verificação linha a linha)", flush=True)
            except (ia_mod.IAIndisponivel, ValueError, OSError):
                pass
        return _conflito(p, "Mudaste as mesmas linhas que os agentes — escolhe tu" +
                         (" (a IA deixou uma proposta para reveres)." if extra else "."), texto, copia, **extra)
    finally:
        _rec_lock.release()




# ───────────────────────── HTTP ─────────────────────────
# Rotas registáveis por outros módulos (ex.: o MCP) sem tocar no handler: ROTAS_EXTRA[("POST", "/mcp")] = função(handler).
# São consultadas ANTES de tudo o resto (antes da sessão e do cabeçalho X-Basalt): quem as regista trata da sua própria
# autenticação (o handler dá `_quem()`, `aut`, `cfg`, `_corpo()`, `_json()`) e, se precisar do espaço do utilizador, `_dentro(u)`.
ROTAS_EXTRA: dict = {}


def proxy_de_confianca(ip: str, proxies=()) -> bool:
    """ronda 8 (X-Forwarded-For): loopback conta sempre; outro proxy (Docker, outra máquina) só se estiver em [auth] proxies_de_confianca."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if a.is_loopback:
        return True
    for p in proxies:
        try:
            if a in ipaddress.ip_network(p, strict=False):
                return True
        except ValueError:
            continue
    return False


def ip_do_pedido(ip: str, xff: str | None, proxies=()) -> str:
    """o IP de quem pediu. Atrás de um proxy de confiança vale o X-Forwarded-For, lido da DIREITA (o último é o que o nosso proxy
    acrescentou; o 1.º podia vir inventado pelo visitante para fugir ao limite de tentativas), saltando os próprios proxies. De
    qualquer outro sítio o cabeçalho ignora-se; um valor que não é IP fica no IP da ligação."""
    if not proxy_de_confianca(ip, proxies):
        return ip
    cadeia = [x.strip() for x in (xff or "").split(",") if x.strip()]
    for x in reversed(cadeia):
        try:
            x = str(ipaddress.ip_address(x))
        except ValueError:
            return ip
        if not proxy_de_confianca(x, proxies):
            return x
    return ip


class H(BaseHTTPRequestHandler):
    server_version = "basalt"
    sys_version = ""
    cfg = None                      # postos por criar_servidor() numa subclasse
    aut = None
    PWA_PUBLICOS = {"/manifest.webmanifest", "/icon-192.png", "/icon-512.png", "/favicon.ico"}   # sem dados: nome + ícone
    # PORTE: caminhos que não pedem sessão: o formulário de entrada e os dois POST que o servem (a sessão ainda nem existe)
    ENTRADA = {"/entrar", "/api/entrar", "/api/sair"}
    # ronda 9: o único pedido da API que pode ir sem X-Basalt-Dono — é por ele que a página fica a saber o dono
    SEM_DONO = {"/api/eu"}

    def parse_request(self):
        self._dono_eco = ""                          # ronda 9: por pedido (a ligação pode servir vários)
        return super().parse_request()

    def end_headers(self):
        # ronda 9 (achado G1 da ronda 2): a resposta diz de que dono é — o service worker só a guarda (na cache desse dono)
        # se o eco for igual ao dono do pedido
        if getattr(self, "_dono_eco", ""):
            self.send_header("X-Basalt-Dono", self._dono_eco)
        super().end_headers()

    def log_message(self, fmt, *a):
        if not self.path.startswith("/static/"):
            print(time.strftime("%d/%m %H:%M:%S"), self.command, self.path.split("?")[0], flush=True)

    # ── quem é ──
    def _ip(self):
        """o IP de quem pediu (ver ip_do_pedido): atrás de um proxy de confiança vale o X-Forwarded-For lido da DIREITA."""
        cfg = getattr(self, "cfg", None)
        proxies = cfg.auth.proxies_de_confianca if cfg is not None else ()
        return ip_do_pedido(self.client_address[0], self.headers.get("X-Forwarded-For"), proxies)

    def _anfitriao_ok(self):
        """ronda 8 (Host/Origin): em modo aberto (sem senha) — ou quando [basalt] anfitrioes está definido — o Host do pedido tem de ser um nome nosso.
        É o que separa a página de um sítio malicioso que aponte um nome DNS seu para 127.0.0.1 (DNS rebinding): o browser da
        vítima chama o Basalt local com Host = esse nome e, sem isto, lia e escrevia as notas."""
        if self.cfg.auth.modo != "aberto" and not self.cfg.anfitrioes:
            return True
        return config.anfitriao_de(self.headers.get("Host")) in config.anfitrioes(self.cfg)

    def _recusar_anfitriao(self):
        h = self.headers.get("Host") or ""
        return self._json(421, {"erro": f"Este servidor não responde pelo nome «{h[:80]}». "
                                        "Em modo aberto só por localhost/127.0.0.1; para outro nome, põe-no em [basalt] anfitrioes."})

    def _outra_origem(self):
        """ronda 8 (Host/Origin): numa escrita, um Origin que não é nosso (nos mesmos modos em que se confere o Host) → motivo; senão ""."""
        if self.cfg.auth.modo != "aberto" and not self.cfg.anfitrioes:
            return ""
        origem = self.headers.get("Origin")
        if origem and config.anfitriao_de(origem) not in config.anfitrioes(self.cfg):
            return "Pedido de outro sítio (Origin)."
        if (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
            return "Pedido de outro sítio."
        return ""

    def _quem(self):
        return self.aut.utilizador_do_pedido(self.headers, self._ip())

    def _por_token(self):
        """agentes (MCP, scripts) com Authorization: Bearer — não têm cópia no browser, logo não têm dono a conferir."""
        return (self.headers.get("Authorization") or "").strip().lower().startswith("bearer ")

    def _dono_errado(self, user, d=None, exigir=True):
        """ronda 8 (contas no mesmo browser): a página manda X-Basalt-Dono = o dono da cópia local com que está a trabalhar.
        Se outra conta entrou entretanto neste browser, o cookie já é dela — o pedido seria feito (e a edição sem internet
        enviada) em nome de quem não a escreveu. Recusa-se SEM trancar a cópia (não é falta de acesso) e diz-se porquê.
        ronda 9 (achado G2 da ronda 2): SEM dono também se recusa (428) — antes passava, e um envio de anexo ou um pedido depois
        de uma verificação falhada ia em nome de quem estivesse no browser. `d` = o dono vindo de outro sítio (?d= de uma <img>);
        `exigir=False` só para /api/eu. Com o dono certo, a resposta leva-o de volta (eco, para o service worker)."""
        if self._por_token():
            return False
        d = (self.headers.get("X-Basalt-Dono") if d is None else d) or ""
        d = d.strip()
        if not d:
            if not exigir:
                return False
            self._json(428, {"erro": "Pedido sem o dono da cópia (X-Basalt-Dono): recarrega a página.", "semDono": True},
                       [("X-Basalt-Sem-Dono", "1")])
            return True
        if d != dono_de(user):
            self._json(403, {"erro": "Entrou outra conta neste browser: recarrega a página antes de continuar.", "outraConta": True},
                       [("X-Basalt-Outra-Conta", "1")])
            return True
        self._dono_eco = d
        return False

    def _sem_acesso(self, caminho):
        modo = self.cfg.auth.modo
        if modo == "cabecalho" and (self.headers.get(self.cfg.auth.cabecalho) or "").strip():
            return self._json(403, {"erro": "Este utilizador não tem acesso a este Basalt."})
        if modo == "senha" and (caminho in ("/", "") or not caminho.startswith(("/api/", "/static/"))):
            self.send_response(302); self.send_header("Location", "/entrar"); self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0"); self.end_headers(); return
        return self._json(401, {"erro": "Precisas de entrar.", "entrar": "/entrar" if modo == "senha" else ""})

    @contextlib.contextmanager
    def _dentro(self, u):
        with config.dentro(self.cfg.espaco_de(u).criar()):
            yield

    # ── respostas ──
    def _json(self, code, obj, cabecalhos=()):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in cabecalhos:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def _corpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n < 0 or n > 2_000_000:                   # negativo fazia rfile.read(-1) e prendia o fio
            raise ValueError("demasiado grande")
        return json.loads(self.rfile.read(n) or b"{}")

    def _static(self, caminho):
        if caminho in ("/", "") and not (STATIC / "index.html").is_file():
            b = ("<!doctype html><html lang='pt-PT'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                 "<meta http-equiv='refresh' content='30'><title>Basalt</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;"
                 "background:#0b1419;color:#dfe8ec;font:16px/1.5 system-ui,sans-serif;padding:16px}div{max-width:420px;text-align:center}"
                 "b{font-size:1.4em;display:block;margin-bottom:8px}</style></head><body><div><b>Basalt</b>A interface está a ser montada."
                 "<br>Esta página volta a tentar sozinha de 30 em 30 segundos.</div></body></html>").encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(b))); self.end_headers()
            self.wfile.write(b); return
        if caminho in ("/manifest.webmanifest", "/sw.js", "/icon-192.png", "/icon-512.png", "/favicon.ico"):
            caminho = "/static/" + caminho[1:]
        if caminho == "/entrar":
            caminho = "/static/entrar.html"
        rel = "index.html" if caminho in ("/", "") else caminho[len("/static/"):] if caminho.startswith("/static/") else None
        if rel is None:
            return self._json(404, {"erro": "não existe"})
        p = (STATIC / rel).resolve()
        if STATIC.resolve() not in p.parents and p != STATIC.resolve() / "index.html" or not p.is_file():
            return self._json(404, {"erro": "não existe"})
        b = p.read_bytes()
        auto = False
        if rel in ("index.html", "entrar.html"):        # as duas páginas com texto: a interface e o ecrã de entrada
            auto = self.cfg.idioma == "auto"
            b = indice_no_idioma(b, idioma_do_pedido(self.cfg.idioma, self.headers.get("Accept-Language")))
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(p.suffix, "application/octet-stream"))
        self.send_header("Cache-Control", "no-cache")
        if auto:
            self.send_header("Vary", "Accept-Language")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; "
                         "base-uri 'none'; form-action 'self'")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    # ── GET ──
    def _extra(self, metodo):
        f = ROTAS_EXTRA.get((metodo, urllib.parse.urlparse(self.path).path))
        if f is None:
            return False
        try:
            f(self)
        except Exception as e:
            print("erro", metodo, self.path.split("?")[0], _erro(e)[:200], flush=True)
            try:
                self._json(500, {"erro": "Erro interno."})
            except OSError:
                pass
        return True

    def do_GET(self):
        if not self._anfitriao_ok():
            return self._recusar_anfitriao()
        if self._extra("GET"):
            return
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
        if u.path in self.PWA_PUBLICOS:             # o Chrome pede o manifesto/ícones SEM cookies
            return self._static(u.path)
        if u.path == "/api/saude":                   # ronda 8 (instalação): healthcheck (Docker/monitorização) sem sessão — só diz que está vivo
            return self._json(200, {"ok": True})
        if u.path == "/entrar":
            if self.cfg.auth.modo != "senha":
                self.send_response(302); self.send_header("Location", "/"); self.send_header("Content-Length", "0"); self.end_headers(); return
            return self._static(u.path)
        user = self._quem()
        if user is None:
            return self._sem_acesso(u.path)
        g = lambda k, d="": (q.get(k) or [d])[0]
        if u.path.startswith("/anexos/"):           # ronda 9 (achado G2 da ronda 2): uma <img> não manda cabeçalhos → ?d=
            if self._dono_errado(user, g("d") or None):
                return
        elif u.path.startswith("/api/") and self._dono_errado(user, exigir=u.path not in self.SEM_DONO):
            return
        try:
            with self._dentro(user):
                if u.path.startswith("/anexos/"):                                           # ficheiros das notas (![[foto.png]])
                    return self._anexo(urllib.parse.unquote(u.path[len("/anexos/"):]), user)
                if not u.path.startswith("/api/"):
                    return self._static(u.path)
                if u.path == "/api/etiquetas":                                              # autocompletar #etiquetas no editor
                    return self._json(200, {"etiquetas": etiquetas.do_espaco(config.actual())})
                if u.path == "/api/eu":
                    return self._json(200, {"email": user.email, "nome": user.nome or user.email.split("@")[0], "admin": user.admin,
                                            "modo": self.cfg.auth.modo, "espaco": user.espaco, "lingua": self.cfg.lingua,
                                            "ia": ia_mod.actual().descricao(), "dono": dono_de(user)})
                if u.path == "/api/estado": return self._json(200, estado())
                if u.path == "/api/notas": return self._json(200, todas_notas())
                if u.path == "/api/pacote": return self._json(200, pacote())                      # para funcionar sem internet
                if u.path == "/api/nota":
                    nome = g("nome"); loc = localizar(nome)
                    if not loc:
                        return self._json(404, {"erro": "Nota não encontrada (ou reservada por ter dados sensíveis)."})
                    p, ed = loc; b = p.read_bytes(); t = b.decode("utf-8", errors="replace")
                    toque("ler", [nome])
                    return self._json(200, {"nome": nome, "editavel": ed, "texto": t, "hash": sha(b),
                                            "meta": {"name": campo(t, "name"), "description": campo(t, "description"), "type": campo(t, "type")},
                                            "backlinks": backlinks(nome), "parecidas": parecidas(nome),
                                            "historico": historico(nome), "categorias": cats_de().get(nome, [])})
                if u.path == "/api/procura":
                    if not g("q").strip():
                        return self._json(200, [])
                    res = procura(g("q")[:300], max(1, min(int(g("n", "10") or 10), 30)))
                    toque("procurar", [r.get("nome") for r in res[:3]])
                    return self._json(200, res)
                if u.path == "/api/grafo": return self._json(200, grafo())
                if u.path == "/api/indice": return self._json(200, indice())
                if u.path == "/api/categorias": return self._json(200, categorias())
                if u.path == "/api/diario": return self._json(200, diario(g("dia")))
                if u.path == "/api/unir":
                    if g("job"):
                        j = _job_le(g("job"))
                        return self._json(200, j) if j else self._json(404, {"erro": "Fusão não encontrada."})
                    return self._json(200, jobs_recentes())
                if u.path == "/api/entrada": return self._json(200, entrada())
                if u.path == "/api/entrada/auto": return self._json(200, entrada_auto())          # arrumadas sozinhas (Desfazer)
                if u.path == "/api/arrumar": return self._json(200, arrumar())
                if u.path == "/api/recentes": return self._json(200, recentes(int(g("n", "20") or 20)))
                return self._json(404, {"erro": "Rota desconhecida."})
        except Exception as e:
            print("erro GET", u.path, _erro(e)[:200], flush=True)
            return self._json(500, {"erro": "Erro interno."})

    # ── escritas ──
    def _escrita(self, user):
        if self.headers.get("X-Basalt") != "1":
            self._json(403, {"erro": "Pedido sem o cabeçalho X-Basalt."}); return False
        motivo = self._outra_origem()
        if motivo:
            self._json(403, {"erro": motivo}); return False
        if self._dono_errado(user):
            return False
        return True

    # ── anexos: imagens, PDFs, áudio… guardados ao lado das notas (basalt/anexos.py) e etiquetas ──
    def _anexo(self, nome, user):
        estado, cab, corpo = anexos.ler(config.actual(), nome)
        if estado != 200:
            return self._json(estado, {"erro": "Nome de anexo inválido." if estado == 400 else "Não existe."})
        # ronda 8: o anexo guardava-se 1 h na cache do browser («private, max-age=3600») — depois de sair e entrar outra conta,
        # o browser mostrava o anexo da anterior sem perguntar. Agora revalida sempre, e a etiqueta muda com o dono.
        etag = '"' + hashlib.sha256(dono_de(user).encode("utf-8") + b"\0" + corpo).hexdigest()[:32] + '"'
        if etag in [e.strip() for e in (self.headers.get("If-None-Match") or "").split(",")]:
            self.send_response(304); self.send_header("ETag", etag); self.send_header("Cache-Control", anexos.CACHE)
            self.send_header("Vary", "Cookie"); self.end_headers(); return
        self.send_response(200)
        for k, v in (cab.items() if hasattr(cab, "items") else cab):
            self.send_header(k, v)
        self.send_header("ETag", etag); self.send_header("Vary", "Cookie")
        self.end_headers(); self.wfile.write(corpo)

    def _anexo_enviar(self, user, u):
        """POST /api/anexos?nome=foto.png com o ficheiro no corpo. Verifica tamanho e nome ANTES de ler o corpo."""
        nome = (urllib.parse.parse_qs(u.query).get("nome") or [""])[0]
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n <= 0:
            return self._json(400, {"erro": "Ficheiro vazio."})
        if n > anexos.TAMANHO_MAX:
            return self._json(413, {"erro": "Ficheiro demasiado grande (máximo %d MiB)." % (anexos.TAMANHO_MAX // (1024 * 1024))})
        if not anexos.nome_ok(nome):
            return self._json(400, {"erro": "Nome de ficheiro inválido (letras, dígitos, espaços, . _ - e uma extensão conhecida)."})
        dados = self.rfile.read(n)
        try:
            with self._dentro(user):
                final = anexos.guardar(config.actual(), nome, dados)
        except ValueError as e:
            return self._json(400, {"erro": _erro(e, tipo=False)})
        return self._json(201, {"nome": final, "bytes": len(dados)})

    def do_PUT(self):
        if not self._anfitriao_ok():
            return self._recusar_anfitriao()
        if self._extra("PUT"):
            return
        user = self._quem()
        if user is None:
            return self._sem_acesso(urllib.parse.urlparse(self.path).path)
        if not self._escrita(user):
            return
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
        try:
            with self._dentro(user):
                if u.path == "/api/nota":
                    c = self._corpo()
                    if not isinstance(c.get("texto"), str):     # sem «texto» gravava a nota vazia (ou «None»)
                        return self._json(400, {"erro": "Falta o texto da nota."})
                    cod, r = gravar((q.get("nome") or [""])[0], str(c.get("texto", "")), str(c.get("base_hash", "")))
                    if cod == 200:
                        toque("escrever", [(q.get("nome") or [""])[0]])
                    return self._json(cod, r)
                return self._json(404, {"erro": "Rota desconhecida."})
        except Exception as e:
            print("erro PUT", _erro(e)[:200], flush=True); return self._json(400, {"erro": "Pedido inválido."})

    def _entrar(self):
        """POST /api/entrar {email, senha} (modo senha): cookie de sessão HttpOnly/SameSite=Strict."""
        if self.cfg.auth.modo != "senha":
            return self._json(400, {"erro": "O início de sessão por senha não está activo neste servidor."})
        if self.headers.get("X-Basalt") != "1":
            return self._json(403, {"erro": "Pedido sem o cabeçalho X-Basalt."})
        try:
            c = self._corpo()
        except Exception:
            return self._json(400, {"erro": "Pedido inválido."})
        if not isinstance(c, dict):                   # PORTE: um corpo JSON que não é objecto ([1,2]) dava erro e cortava a ligação
            return self._json(400, {"erro": "Pedido inválido."})
        ok, res = self.aut.entrar(str(c.get("email", "")), str(c.get("senha", "")), self._ip())
        if not ok:
            return self._json(429 if res.startswith("Demasiadas") else 500 if res.startswith("Não consegui") else 401, {"erro": res})
        seguro = (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"
        return self._json(200, {"ok": True}, [("Set-Cookie", self.aut.cabecalho_cookie(res, seguro))])

    def _sair(self):
        if self.headers.get("X-Basalt") != "1":
            return self._json(403, {"erro": "Pedido sem o cabeçalho X-Basalt."})
        ok, motivo = self.aut.sair(self.aut.token_do_pedido(self.headers))
        return self._json(200 if ok else 500, {"ok": ok, **({} if ok else {"erro": motivo})},   # ronda 8 (configuração relida em execução): «saí» só quando ficou gravado
                          [("Set-Cookie", self.aut.cabecalho_apagar_cookie())])

    def do_POST(self):
        if not self._anfitriao_ok():
            return self._recusar_anfitriao()
        if self._extra("POST"):
            return
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/entrar":
            return self._entrar()
        if u.path == "/api/sair":
            return self._sair()
        user = self._quem()
        if user is None:
            return self._sem_acesso(u.path)
        if not self._escrita(user):
            return
        if u.path == "/api/anexos":                                                          # corpo = o ficheiro, não JSON
            return self._anexo_enviar(user, u)
        try:
            c = self._corpo()
            with self._dentro(user):
                if u.path == "/api/nota":
                    if not isinstance(c.get("texto"), str):
                        return self._json(400, {"erro": "Falta o texto da nota."})
                    cod, r = criar(str(c.get("nome", "")), str(c.get("texto", "")))
                    if cod == 201:
                        toque("escrever", [str(c.get("nome", ""))])
                    return self._json(cod, r)
                if u.path == "/api/parecidas":                                                   # aviso ao escrever
                    return self._json(200, parecidas_texto(str(c.get("nome", ""))[:120], str(c.get("texto", ""))))
                if u.path == "/api/sincronizar":                                                 # edição feita sem internet
                    return self._json(*reconciliar(str(c.get("nome", "")), str(c.get("base_texto", "")), str(c.get("texto", ""))))
                if u.path == "/api/ia":                                                          # IA no editor
                    return self._json(*ia(str(c.get("acao", "")), str(c.get("texto", "")), str(c.get("contexto", ""))))
                if u.path == "/api/pergunta":
                    qq = str(c.get("q", "")).strip()[:500]
                    if not qq:
                        return self._json(200, {"resposta": "Escreve uma pergunta.", "fontes": []})
                    if not ia_mod.actual().chat_activo:
                        return self._json(503, {"erro": "IA desligada neste servidor: a pesquisa funciona, mas não há respostas escritas.", "ia": False})
                    rp = pergunta(qq); toque("procurar", [f.get("nome") for f in rp.get("fontes", [])])
                    return self._json(200, rp)
                if u.path == "/api/guarda":
                    t = str(c.get("texto", "")).strip()[:4000]
                    if not t:
                        return self._json(400, {"erro": "Nada para guardar."})
                    if tem_segredo_texto(t):
                        return self._json(400, {"erro": "Parece uma password ou chave — não guardei. Usa um gestor de palavras-passe."})
                    r = motor(["guarda", t, "--de", "web"])
                    return self._json(200, {"ok": r.returncode == 0, "mensagem": r.stdout.strip() or "guardado"})
                if u.path == "/api/unir":
                    return self._json(*iniciar_fusao(str(c.get("manter", "")), str(c.get("fundir", ""))))
                if u.path == "/api/unir/desfazer":
                    return self._json(*desfazer_fusao(str(c.get("job", ""))))
                if u.path == "/api/entrada/juntar":
                    return self._json(*entrada_juntar(str(c.get("t", "")), str(c.get("nota", ""))))
                if u.path == "/api/entrada/desfazer":
                    return self._json(*entrada_desfazer(str(c.get("t", ""))))
                if u.path == "/api/entrada/apagar":
                    return self._json(*entrada_apagar(str(c.get("t", ""))))
                if u.path == "/api/entrada/arquivar":
                    if c.get("t"):                                  # a página manda o t (único); o n é só para páginas antigas
                        return self._json(*entrada_arquivar(str(c.get("t"))))
                    r = motor(["arquivar", str(int(c.get("n", 0)))])
                    return self._json(200 if r.returncode == 0 else 400, {"ok": r.returncode == 0, "mensagem": (r.stdout or r.stderr).strip()})
                return self._json(404, {"erro": "Rota desconhecida."})
        except Exception as e:
            print("erro POST", _erro(e)[:200], flush=True); return self._json(400, {"erro": "Pedido inválido."})


# ───────────────────────── arranque ─────────────────────────
def git_iniciar(esp):
    """PORTE: a memória real precisa de ser um repositório git para o histórico, o diário e o «Desfazer» da fusão. Num espaço novo
    cria-o (com uma fotografia inicial); se já é um repositório (o cofre de quem já usa git) não lhe toca."""
    if os.environ.get("BASALT_GIT", "1") != "1" or (esp.real / ".git").exists():
        return
    try:
        subprocess.run(["git", "-C", str(esp.real), "init", "-q"], capture_output=True, timeout=30)
        subprocess.run(["git", "-C", str(esp.real), "add", "-A"], capture_output=True, timeout=30)
        subprocess.run(["git", "-C", str(esp.real), *config.git_c(), "commit", "-q", "--allow-empty", "-m", "basalt: início"],
                       capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        print("aviso: não consegui iniciar o git da memória:", _erro(e)[:100], flush=True)


def vigia_copia(cfg, segundos):
    """a ponte memória real ⇄ cópia editável (basalt.duasvias), de `segundos` em `segundos`, em cada espaço.
    PORTE: antes era um serviço à parte; no pacote vai dentro do servidor (desliga-se com `serve --sem-vigias`)."""
    from . import duasvias
    while True:
        for esp in _espacos(cfg):
            try:
                with config.dentro(esp):
                    duasvias.ciclo()
            except Exception as e:
                print("vigia da cópia:", _erro(e)[:200], flush=True)
        time.sleep(segundos)


def indexar_se_vazio(cfg):
    """PORTE: em produção o índice de pesquisa é reconstruído por um agendador (de noite); numa instalação nova ninguém o fez ainda e
    a pesquisa dava sempre vazio. No arranque, e só se o índice do espaço está vazio e há notas, corre-se `basalt.sig indexar` uma vez.
    O resto do tempo continua a ser o agendador do sistema (ver `python -m basalt.sig indexar`)."""
    for esp in _espacos(cfg):
        try:
            with config.dentro(esp):
                vazio = int(estado().get("pedacos") or 0) == 0 and any(caminhos.notas(COPIA))
            if vazio:
                subprocess.run([sys.executable, "-m", "basalt.sig", "indexar"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env=config.ambiente_filho(esp), cwd=str(Path(__file__).resolve().parent.parent), timeout=3600)
        except Exception as e:
            print("indexação inicial:", _erro(e)[:200], flush=True)


def criar_servidor(cfg, host=None, porta=None):
    """o servidor HTTP pronto a servir (não arranca fios nem vigias). porta=0 → o sistema escolhe uma livre (testes)."""
    config.activar(cfg)                       # ambiente para os filhos + IA de [ia] (ronda 8, --config para tudo: o mesmo que a linha de comandos faz)
    for esp in _espacos(cfg):
        esp.criar()
        git_iniciar(esp)
    classe = type("Handler", (H,), {"cfg": cfg, "aut": auth.Autenticador(cfg)})
    try:
        from . import mcp
        mcp.registar(ROTAS_EXTRA, cfg, classe.aut)   # POST /mcp (só Bearer) + GET /mcp → 405; o MCP importa basalt.web sozinho
    except ImportError:
        pass
    srv = ThreadingHTTPServer((host if host is not None else cfg.escutar, cfg.porta if porta is None else porta), classe)
    srv.daemon_threads = True
    return srv


def servir(cfg, vigias=True, host=None, porta=None):
    srv = criar_servidor(cfg, host, porta)
    if vigias:
        # PORTE: um ciclo de sincronização ANTES de calcular as notas retidas — numa instalação nova a cópia está vazia e,
        # sem isto, todas as notas apareciam como «retidas pelo guarda de segredos» até ao refresco dos 5 minutos.
        from . import duasvias
        for esp in _espacos(cfg):
            try:
                with config.dentro(esp):
                    duasvias.ciclo()
            except Exception as e:
                print("sincronização inicial:", _erro(e)[:200], flush=True)
    for esp in _espacos(cfg):
        with config.dentro(esp):
            refazer_exclusao()
    if vigias:
        segundos = int(os.environ.get("BASALT_VIGIA_SEGUNDOS", "10"))
        threading.Thread(target=vigia_copia, args=(cfg, segundos), daemon=True).start()
    threading.Thread(target=vigia_exclusao, args=(cfg,), daemon=True).start()
    threading.Thread(target=indexar_se_vazio, args=(cfg,), daemon=True).start()
    threading.Thread(target=vigia_entrada, args=(cfg,), daemon=True).start()          # entrada automática (só com IA de chat)
    h, p = srv.server_address[:2]
    print(f"basalt em http://{h}:{p} (auth={cfg.auth.modo}, ia={cfg.ia.fornecedor}, vigias={'sim' if vigias else 'não'})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
