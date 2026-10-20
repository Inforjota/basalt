"""basalt.categorias — categoriza sozinho cada nota (1–2 categorias de uma lista FIXA).

Uma IA de chat (se houver, [ia]) + regras seguras pelo nome da nota. NÃO escreve nas notas: guarda à parte, em
<dados>/categorias.json  {"nota": {"cats": [...], "hash": "..."}} — incremental (só o que mudou).
A lista e as regras são do utilizador: [categorias] do basalt.toml (`lista` = nome → definição para a IA; `regras` = pares
[regex pelo nome da nota, categoria]). Sem isso usam-se as de origem (DEF/REGRAS, genéricas). Sem chat configurado só
valem as regras (e «Diversos» para o resto).
As categorias das fontes externas («fonte:ref», de [fontes_extra] ou de uma instalação anterior) não se recalculam aqui mas
também não se perdem: ficam enquanto a fonte as tiver no índice (sem índice, ou de uma fonte que não respondeu ou ele não
conhece, ficam sempre; de uma fonte que respondeu vazia, saem). Os itens externos novos, sem categoria, categorizam-se como
as notas (ronda 13).
  python -m basalt.categorias            (re)categoriza o que mudou
  python -m basalt.categorias --tudo     refaz todas as notas
  python -m basalt.categorias --teste    testa as regras e a validação (sem rede)
"""
import hashlib, json, re, sys
from pathlib import Path

from . import caminhos, config, retidas
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


def _ler_indice():
    """(pedaços das fontes externas [(fonte, ref, titulo, trecho)], fontes que responderam na última indexação) — None sem
    índice, com ele vazio (sem pedaços nem consultas) ou ilegível: aí não há como saber o que uma fonte externa ainda tem."""
    p = Path(SEMANTICA)
    if not p.exists():
        return None
    try:
        import sqlite3
        c = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            n = c.execute("SELECT count(*) FROM pedaco").fetchone()[0]
            linhas = c.execute("SELECT fonte, ref, titulo, trecho FROM pedaco WHERE fonte != 'nota' ORDER BY fonte, ref").fetchall()
            try:                    # ronda 13: a tabela das consultas (o basalt.sig faz-la; um índice antigo não a tem)
                ok = {str(f) for (f,) in c.execute("SELECT fonte FROM consulta WHERE ok=1")}
            except sqlite3.Error:
                ok = set()
        finally:
            c.close()
    except Exception:
        return None
    if not n and not ok:
        return None
    return [(str(f), str(r), str(t or ""), str(x or "")) for f, r, t, x in linhas], ok


def _chave(fonte, ref):
    """«fonte:ref» — uma ref que já traz a fonte («diario:2026-10-01») fica como está."""
    return ref if ref.startswith(f"{fonte}:") else f"{fonte}:{ref}"


def _itens_externos(pedacos):
    """{chave: (titulo, [trechos])} — um item por coisa da fonte. Os pedaços «ref#N» de uma mesma ref com o MESMO título são
    uma nota partida (como no `basalt.sig fonte`) e contam como um item só, com a chave da ref sem «#»; «repo#12» e «repo#13»
    com títulos diferentes são coisas diferentes e ficam com a ref exacta (ronda 13)."""
    titulos = {}
    for f, r, t, _ in pedacos:
        titulos.setdefault((f, r.split("#")[0]), set()).add(t)
    itens = {}
    for f, r, t, x in pedacos:
        base, cardinal, suf = r.partition("#")
        partida = cardinal and suf.isdigit() and len(titulos[(f, base)]) == 1
        k = _chave(f, base if partida else r)
        itens.setdefault(k, (t, []))[1].append(x)
    return itens


def _externas_no_indice(ind=None):
    """(chaves «fonte:ref» que o índice tem de fontes que não são notas, fontes que se sabe como estão) — None se não se sabe.
    Cada pedaço dá a chave da ref exacta («pedidos:repo#12») E a da ref sem «#» («projecto:x#0» → «projecto:x»): antes só ficava
    a segunda, e a categoria de «pedidos:repo#12» perdia-se com o item ainda no índice (ronda 13). As fontes que se sabe como
    estão são as que têm pedaços e as que responderam na última indexação, mesmo vazias (as categorias delas saem)."""
    ind = _ler_indice() if ind is None else ind
    if ind is None:
        return None
    pedacos, ok = ind
    chaves, fontes = set(), set(ok)
    for f, ref, _, _ in pedacos:
        chaves.add(_chave(f, ref))
        chaves.add(_chave(f, ref.split("#")[0]))
        fontes.add(f)
    return chaves, fontes


def _externas_que_ficam(antes: dict, ind=None) -> dict:
    """ronda 12: as categorias das fontes externas que se mantêm — as que a fonte ainda tem no índice; sem índice, ou de uma fonte
    que não se sabe como está (uma instalação anterior, uma fonte desligada por agora, um comando que falhou), ficam todas. Só uma
    nota do espaço que já não existe perde a categoria (essas não têm «:» no nome). Ronda 13: uma fonte que respondeu VAZIA perde-as."""
    ext = {k: v for k, v in antes.items() if ":" in k}
    ind = _externas_no_indice(ind)
    if ind is None:
        return ext
    chaves, fontes = ind
    return {k: v for k, v in ext.items() if k in chaves or k.split(":", 1)[0] not in fontes}


# ronda 13: os itens novos das fontes externas também se categorizam (antes ficavam sem categoria para sempre). Com IA, no
# máximo estes por corrida (uma fonte grande não prende a corrida; o resto fica para a seguinte).
EXTERNAS_POR_CORRIDA = 200


def _categorizar_externas(pedacos, ja: dict) -> dict:
    """{chave: {"cats", "hash"}} dos itens externos do índice que ainda não têm categoria (nem pela chave exacta nem pela
    da ref sem «#»), pelo mesmo método das notas: IA (se houver) + regras pelo nome. Um item com segredo no título, na ref
    ou no texto não vai ao modelo nem ganha categoria."""
    from .segredos import tem_segredo_texto
    novo, pedidos = {}, 0
    for k, (titulo, trechos) in _itens_externos(pedacos).items():
        if k in ja or k.split("#")[0] in ja:
            continue
        corpo = "\n".join(trechos)
        if tem_segredo_texto(k) or tem_segredo_texto(titulo) or tem_segredo_texto(corpo):
            continue
        if ia().chat_activo:
            if pedidos >= EXTERNAS_POR_CORRIDA:
                continue
            pedidos += 1
        nome = k.split(":", 1)[1]
        m = pelo_modelo(f"Nome: {titulo or nome}\nFonte: {k.split(':', 1)[0]}\n\n{corpo[:700]}")
        r = pelo_nome(nome, "")
        cats = validar((m[:1] + r + m[1:]) if m else r) or ["Diversos"]
        novo[k] = {"cats": cats, "hash": hashlib.sha1(corpo.encode()).hexdigest()}
    return novo


def main(tudo=False):
    saida = Path(SAIDA)
    try:
        antes_todo = json.loads(caminhos.ler_texto(saida))          # ronda 13: sem seguir ligações
    except FileNotFoundError:
        antes_todo = {}
    except ValueError:
        antes_todo = {}
    antes_todo = antes_todo if isinstance(antes_todo, dict) else {}
    antes = antes_todo if not tudo else {}            # --tudo recategoriza as notas; as fontes externas ficam na mesma
    ind = _ler_indice()
    externas = _externas_que_ficam(antes_todo, ind)
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
    novas_ext = _categorizar_externas(ind[0], novo) if ind else {}
    novo.update(novas_ext)
    caminhos.escrever_atomico(saida, json.dumps(novo, ensure_ascii=False, indent=0).encode("utf-8"))
    conta = {}
    for v in novo.values():
        for c in v.get("cats", []):
            conta[c] = conta.get(c, 0) + 1
    print(f"{notas} notas · {feitos} (re)categorizadas · "
          + (f"{len(externas)} de fontes externas mantidas · " if externas else "")
          + (f"{len(novas_ext)} de fontes externas novas · " if novas_ext else "")
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
