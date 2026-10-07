"""basalt.categorias — categoriza sozinho cada nota (1–2 categorias de uma lista FIXA).

Uma IA de chat (se houver, [ia]) + regras seguras pelo nome da nota. NÃO escreve nas notas: guarda à parte, em
<dados>/categorias.json  {"nota": {"cats": [...], "hash": "..."}} — incremental (só o que mudou).
A lista e as regras são do utilizador: [categorias] do basalt.toml (`lista` = nome → definição para a IA; `regras` = pares
[regex pelo nome da nota, categoria]). Sem isso usam-se as de origem (DEF/REGRAS, genéricas). Sem chat configurado só
valem as regras (e «Diversos» para o resto).
  python -m basalt.categorias            (re)categoriza o que mudou
  python -m basalt.categorias --tudo     refaz todas
  python -m basalt.categorias --teste    testa as regras e a validação (sem rede)
"""
import hashlib, json, re, sys
from pathlib import Path

from . import config
from .config import Via
from .ia import IAIndisponivel, actual as ia

PASTA = Via("real")        # PORTE: antes 3 memórias de projectos fixas (prefixo «tasca:», «root:»); agora só as notas do espaço
SAIDA = Via("categorias")
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


def main(tudo=False):
    saida = Path(SAIDA)
    antes = json.loads(saida.read_text()) if saida.exists() and not tudo else {}
    novo, feitos = {}, 0
    for p in sorted(Path(PASTA).glob("*.md")):
        if p.name in ("MEMORY.md", "index.md", "CONFIG.md") or ".bak" in p.name or ".CONFLITO-" in p.name:
            continue
        nome = p.stem
        t = p.read_text(encoding="utf-8", errors="replace"); h = hashlib.sha1(t.encode()).hexdigest()
        if nome in antes and antes[nome].get("hash") == h:
            novo[nome] = antes[nome]; continue
        corpo = re.sub(r"^---\n.*?\n---\n", "", t, count=1, flags=re.S)
        texto = f"Nome: {p.stem}\nDescrição: {campo(t, 'description')}\n\n{corpo[:700]}"
        m = pelo_modelo(texto); r = pelo_nome(p.stem, campo(t, "type"))
        cats = validar((m[:1] + r + m[1:]) if m else r) or ["Diversos"]    # principal do modelo + regras seguras
        novo[nome] = {"cats": cats, "hash": h}; feitos += 1
        if feitos % 20 == 0:
            print(f"  … {feitos}", file=sys.stderr, flush=True)
    saida.parent.mkdir(parents=True, exist_ok=True)
    tmp = saida.with_suffix(".tmp"); tmp.write_text(json.dumps(novo, ensure_ascii=False, indent=0)); tmp.replace(saida)
    conta = {}
    for v in novo.values():
        for c in v["cats"]:
            conta[c] = conta.get(c, 0) + 1
    print(f"{len(novo)} notas · {feitos} (re)categorizadas · " + ", ".join(f"{k} {v}" for k, v in sorted(conta.items(), key=lambda x: -x[1])))


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
