"""basalt.categorias — categoriza sozinho cada nota (1–2 categorias de uma lista FIXA).

Uma IA de chat (se houver, [ia]) + regras seguras pelo nome da nota. NÃO escreve nas notas: guarda à parte, em
<dados>/categorias.json  {"nota": {"cats": [...], "hash": "..."}} — incremental (só o que mudou).
A lista e as regras são do utilizador: [categorias] do basalt.toml (`lista` = nome → definição para a IA; `regras` = pares
[regex pelo nome da nota, categoria]). Sem isso usam-se as de origem (DEF/REGRAS, genéricas). Sem chat configurado só
valem as regras (e «Diversos» para o resto).
As categorias das fontes externas («fonte:ref», de [fontes_extra] ou de uma instalação anterior) não se recalculam aqui mas
também não se perdem: ficam enquanto a fonte as tiver no índice (sem índice, ou de uma fonte que ele não conhece, ficam sempre).
  python -m basalt.categorias            (re)categoriza o que mudou
  python -m basalt.categorias --tudo     refaz todas as notas
  python -m basalt.categorias --teste    testa as regras e a validação (sem rede)
"""
import hashlib, json, re, sys
from pathlib import Path

from . import config, retidas
from .config import Via
from .ia import IAIndisponivel, actual as ia

PASTA = Via("real")        # PORTE: antes 3 memórias de projectos fixas (com prefixo de projecto); agora só as notas do espaço
SAIDA = Via("categorias")
SEMANTICA = Via("semantica")
# PORTE: a lista e as regras de origem eram do dono da memória; estas são genéricas (e substituíveis em [categorias])
DEF = {
    "Trabalho": "projectos, clientes, propostas, reuniões, tarefas, planos, contratos",
    "Tecnologia": "software, programação, servidores, redes, ferramentas, configurações, automatização",
    "Finanças": "dinheiro, contas, facturas, orçamentos, impostos, investimentos",
    "Pessoal": "vida pessoal, família, saúde, casa, lazer, viagens",
    "Aprender": "estudo, cursos, livros, artigos, resumos, ideias para explorar",
    "Regras de trabalho": "SÓ regras e lições sobre COMO o assistente deve trabalhar (comportamento, erros a evitar, preferências)",
    "Diversos": "nada acima encaixa",
}
REGRAS = [(r"projec|client|propost|reuni|contrat", "Trabalho"),
          (r"servidor|rede_|backup|config|linux|docker|python|codigo|api_", "Tecnologia"),
          (r"financ|orcament|factura|fatura|imposto|conta_", "Finanças"),
          (r"familia|saude|viagem|casa_|ferias", "Pessoal"),
          (r"curso|estudo|livro|aprender|resumo", "Aprender")]
_CONF = None               # (lista, regras) já resolvidos (omissão ← basalt.toml)


def definir(lista=None, regras=None):
    """põe a lista/regras em uso (servidor, testes). Sem argumentos: volta a ler o basalt.toml na próxima vez."""
    global _CONF
    _CONF = None if lista is None and regras is None else (dict(lista or DEF), [tuple(r) for r in (regras if regras is not None else REGRAS)])


def _conf():
    global _CONF
    if _CONF is None:
        c = config.carregar(validar=False).categorias
        _CONF = (dict(c.lista) or dict(DEF), [tuple(r) for r in c.regras] or list(REGRAS))
    return _CONF


def lista():
    """{categoria: definição} em uso."""
    return _conf()[0]


def pelo_nome(nome, tipo):
    n = nome.lower(); out = []
    for rx, cat in _conf()[1]:
        if re.search(rx, n) and cat not in out:
            out.append(cat)
    if tipo == "feedback" and "Regras de trabalho" in lista() and "Regras de trabalho" not in out:
        out.append("Regras de trabalho")
    return out


def validar(cats):
    """só nomes da lista, sem repetidos, no máximo 2; «Diversos» só se for a única."""
    out = []
    for c in cats if isinstance(cats, list) else []:
        c = str(c).strip()
        if c in lista() and c not in out:
            out.append(c)
    if len(out) > 1 and "Diversos" in out:
        out.remove("Diversos")
    return out[:2]


def pelo_modelo(texto):
    if not ia().chat_activo:
        return []
    sis = ("Classificas notas de trabalho. Escolhe 1 categoria principal e, só se for "
           "claramente também sobre outra, uma 2.ª. Responde APENAS JSON: {\"categorias\": [\"...\"]}. Categorias (nome EXACTO: "
           "definição):\n" + "\n".join(f"- {k}: {v}" for k, v in lista().items()))
    try:
        return validar(ia().chat_json(texto, sis, tempo=90).get("categorias"))
    except (IAIndisponivel, AttributeError):
        return []


def campo(t, nome):
    m = re.search(rf'^\s*{nome}:\s*"?(.+?)"?\s*$', t, re.M)
    return m.group(1) if m else ""


def _externas_no_indice():
    """(chaves «fonte:ref» que o índice tem de fontes que não são notas, fontes que ele conhece) — None sem índice (ou vazio,
    ou ilegível): aí não há como saber o que uma fonte externa ainda tem."""
    p = Path(SEMANTICA)
    if not p.exists():
        return None
    try:
        import sqlite3
        c = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            if not c.execute("SELECT count(*) FROM pedaco").fetchone()[0]:
                return None
            linhas = c.execute("SELECT DISTINCT fonte, ref FROM pedaco WHERE fonte != 'nota'").fetchall()
        finally:
            c.close()
    except Exception:
        return None
    chaves, fontes = set(), set()
    for f, ref in linhas:
        r = str(ref).split("#")[0]                  # «projecto:x#0» (pedaços) e «decisao-1» (um pedaço só) dão «projecto:x» e «agenda:decisao-1»
        chaves.add(r if r.startswith(f"{f}:") else f"{f}:{r}")
        fontes.add(str(f))
    return chaves, fontes


def _externas_que_ficam(antes: dict) -> dict:
    """ronda 12: as categorias das fontes externas que se mantêm — as que a fonte ainda tem no índice; sem índice, ou de uma fonte
    que o índice não conhece (uma instalação anterior, uma fonte desligada por agora), ficam todas. Só uma nota do espaço que já
    não existe perde a categoria (essas não têm «:» no nome)."""
    ext = {k: v for k, v in antes.items() if ":" in k}
    ind = _externas_no_indice()
    if ind is None:
        return ext
    chaves, fontes = ind
    return {k: v for k, v in ext.items() if k in chaves or k.split(":", 1)[0] not in fontes}


def main(tudo=False):
    saida = Path(SAIDA)
    try:
        antes_todo = json.loads(saida.read_text()) if saida.exists() else {}
    except ValueError:
        antes_todo = {}
    antes_todo = antes_todo if isinstance(antes_todo, dict) else {}
    antes = antes_todo if not tudo else {}            # --tudo recategoriza as notas; as fontes externas ficam na mesma
    externas = _externas_que_ficam(antes_todo)
    novo, feitos = {}, 0
    for p, t in retidas.notas(PASTA, ignora=("MEMORY.md", "index.md", "CONFIG.md")):   # ronda 8 (notas retidas): uma nota retida não vai ao modelo
        nome = p.stem
        h = hashlib.sha1(t.encode()).hexdigest()
        if nome in antes and antes[nome].get("hash") == h:
            novo[nome] = antes[nome]; continue
        corpo = re.sub(r"^---\n.*?\n---\n", "", t, count=1, flags=re.S)
        texto = f"Nome: {p.stem}\nDescrição: {campo(t, 'description')}\n\n{corpo[:700]}"
        m = pelo_modelo(texto); r = pelo_nome(p.stem, campo(t, "type"))
        cats = validar((m[:1] + r + m[1:]) if m else r) or ["Diversos"]    # principal do modelo + regras seguras
        novo[nome] = {"cats": cats, "hash": h}; feitos += 1
        if feitos % 20 == 0:
            print(f"  … {feitos}", file=sys.stderr, flush=True)
    notas = len(novo)
    for k, v in externas.items():
        novo.setdefault(k, v)
    saida.parent.mkdir(parents=True, exist_ok=True)
    tmp = saida.with_suffix(".tmp"); tmp.write_text(json.dumps(novo, ensure_ascii=False, indent=0)); tmp.replace(saida)
    conta = {}
    for v in novo.values():
        for c in v.get("cats", []):
            conta[c] = conta.get(c, 0) + 1
    print(f"{notas} notas · {feitos} (re)categorizadas · "
          + (f"{len(externas)} de fontes externas mantidas · " if externas else "")
          + ", ".join(f"{k} {v}" for k, v in sorted(conta.items(), key=lambda x: -x[1])))


def _teste():
    ok = True

    def chk(c, m):
        nonlocal ok
        if not c:
            ok = False; print("FALHA:", m)
    definir({"Alfa": "a", "Beta": "b", "Gama": "c", "Regras de trabalho": "r", "Diversos": "nada"},
            [(r"alfa|aa_", "Alfa"), (r"^beta", "Beta")])
    try:
        chk(validar(["Alfa", "inventada", "Alfa", "Gama", "Beta"]) == ["Alfa", "Gama"], "validar filtra/limita")
        chk(validar(["Diversos", "Gama"]) == ["Gama"], "Diversos sai se houver outra")
        chk(validar("lixo") == [], "validar aguenta lixo")
        chk(pelo_nome("nota_alfa_x", "project") == ["Alfa"], "regra pelo nome")
        chk(pelo_nome("beta_alfa", "") == ["Alfa", "Beta"] or pelo_nome("beta_alfa", "") == ["Beta", "Alfa"], "duas regras")
        chk("Regras de trabalho" in pelo_nome("feedback_x", "feedback"), "feedback → regras de trabalho")
        definir({"Alfa": "a", "Diversos": "nada"}, [])
        chk("Regras de trabalho" not in pelo_nome("feedback_x", "feedback"), "sem essa categoria na lista não se acrescenta")
        chk(validar(["Alfa", "Gama"]) == ["Alfa"], "a lista em uso é a configurada")
    finally:
        definir()
    print("basalt.categorias autoteste:", "OK" if ok else "FALHOU"); return ok


def cli(argv=None):
    a = sys.argv[1:] if argv is None else argv
    if "--teste" in a:
        return sys.exit(0 if _teste() else 1)
    try:
        config.definir_processo(config.espaco_de_processo())
        main("--tudo" in a)
    except config.ConfigInvalida as e:
        sys.exit(f"configuração inválida: {e}")


if __name__ == "__main__":
    cli()
