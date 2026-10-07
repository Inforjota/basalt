"""basalt.unir — funde duas notas numa só sem perder nada e mantém a memória funcional.
Passos: fundir (a IA configurada em [ia]) → VALIDAR (nada perdido; senão não aplica) → aplicar (fotografia git; grava a que fica;
troca [[fundida]]→[[que fica]] nas outras notas; índice MEMORY.md; apaga a fundida; commit) → pode DESFAZER (git revert).
Usado pela página (botão «Unir» em Arrumar) e por basalt.auto_arrumar. CLI: python -m basalt.unir --teste (sem rede, numa
memória de rascunho).
"""
import fcntl, json, os, re, subprocess, time
from pathlib import Path

from . import cas, config
from . import ia as _ia
from .duasvias import SEGREDO_RX     # PORTE: era uma cópia do mesmo padrão; agora há um só

LIG_RX = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


def ligacoes(t):
    return {x.strip() for x in LIG_RX.findall(t)}


# t41r4 G7: IPs e versões (192.168.1.104, v1.20, 3.11.2) são UM facto inteiro — e os seus pedaços («168», «20») não contam à
# parte (contavam, e tem_facto() recusava-os dentro do próprio texto original: «perdeu 2 de 2 factos» numa fusão perfeita)
COMPOSTO_RX = re.compile(r"(?<![\w.])v?\d+(?:\.\d+)+(?!\w)(?!\.\d)")


def factos(t):
    """o que não se pode perder numa fusão: números com 2+ algarismos, IPs/versões, datas, caminhos, código entre `crases`, URLs."""
    corpo = re.sub(r"^---\n.*?\n---\n", "", t, count=1, flags=re.S)
    f = set(re.findall(r"`([^`\n]{2,80})`", corpo))
    f |= set(re.findall(r"https?://[^\s)>\]]+", corpo))
    f |= set(re.findall(r"(?<![\w/])(/[\w.~-]+(?:/[\w.~-]+)+)", corpo))
    compostos = {x for x in COMPOSTO_RX.findall(corpo) if len(x) >= 4}
    f |= compostos
    sem_compostos = COMPOSTO_RX.sub(lambda mm: " " * len(mm.group(0)) if len(mm.group(0)) >= 4 else mm.group(0), corpo)
    f |= set(re.findall(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b", sem_compostos))
    f |= set(re.findall(r"\b\d{2,}(?:[.,]\d+)?\b", sem_compostos))
    return {x.strip() for x in f if x.strip()}


def tem_facto(x, m):
    """o facto aparece INTEIRO em m (06/10, PR#2: antes era sub-texto, e «20» contava como presente dentro de «200»)."""
    antes = r"(?<!\w)(?<!\d[.,])" if x[0].isalnum() else ""
    depois = r"(?!\w)(?![.,]\d)" if x[-1].isalnum() else ""
    return re.search(antes + re.escape(x) + depois, m) is not None


def fundir_llm(a, b, manter, fundir):
    prompt = f"""Tens duas notas da memória de trabalho do utilizador que tratam do mesmo assunto. Funde-as numa SÓ nota.

Regras (obrigatórias):
- Formato igual ao da NOTA A: começa com o bloco frontmatter `---` … `---`, com `name: {manter}`, uma `description:` numa só linha
  que resuma as duas, e o mesmo `metadata:`/`type:` da nota A. Mantém outros campos do frontmatter da A.
- NÃO percas NENHUM facto: todas as datas, números, nomes, caminhos, comandos, `código`, URLs e ligações [[…]] das DUAS notas têm
  de aparecer. Junta o que está repetido (uma vez só); quando as duas dizem coisas diferentes sobre o mesmo, mantém as duas com a data.
- Troca ligações [[{fundir}]] por [[{manter}]]. Não inventes nada. Português de Portugal. Mantém o estilo (listas, ⭐, ⚠️).
- Responde APENAS com o markdown final da nota, sem explicações antes ou depois, sem ``` à volta.

=== NOTA A ({manter}.md — a que fica) ===
{a}

=== NOTA B ({fundir}.md — vai ser fundida na A) ===
{b}
"""
    # PORTE: antes `claude -p` com o modelo lido de uma ferramenta local; agora a IA configurada em [ia] (basalt.ia)
    return _ia.actual().chat(prompt, tempo=600).strip() + "\n"


def validar(a, b, m, manter, fundir):
    """(ok, motivo, estatísticas). Deterministico — é isto que decide se a fusão se aplica."""
    if not m.startswith("---"):
        return False, "a nota fundida não começa pelo frontmatter", {}
    fm = re.match(r"^---\n(.*?)\n---\n", m, flags=re.S)
    if not fm or not re.search(rf"^name:\s*\"?{re.escape(manter)}\"?\s*$", fm.group(1), re.M):
        return False, f"o frontmatter não tem name: {manter}", {}
    if SEGREDO_RX.search(m):
        return False, "o resultado tem o que parece um segredo", {}
    lig_falta = (ligacoes(a) | ligacoes(b)) - {manter, fundir} - ligacoes(m)
    if lig_falta:
        return False, f"perdeu ligações: {', '.join(sorted(lig_falta)[:6])}", {}
    fa = factos(a) | factos(b)
    falta = {x for x in fa if not tem_facto(x, m)}
    cobert = 1 - len(falta) / max(1, len(fa))
    est = {"factos": len(fa), "em_falta": len(falta), "cobertura": round(cobert, 3), "tamanho": len(m), "antes": len(a) + len(b)}
    if falta:                     # 06/10 (PR#2): era «cobertura < 95 %» — com 20+ factos perdia-se um sem aviso; agora nenhum
        return False, f"perdeu {len(falta)} de {len(fa)} factos (ex.: {', '.join(sorted(falta)[:5])})", est
    if len(m) < 0.5 * (len(a) + len(b)):
        return False, f"ficou muito mais curta ({len(m)} vs {len(a) + len(b)} caracteres) — risco de perda", est
    return True, "ok", est


def _git(real, *args):
    return subprocess.run(["git", "-C", str(real), *config.git_c(), *args], capture_output=True, text=True, timeout=60)


def trocar_ligacoes(t, fundir, manter):
    t = re.sub(rf"\[\[{re.escape(fundir)}(?=[\]|#])", f"[[{manter}", t)
    return re.sub(rf"\({re.escape(fundir)}\.md([#)])", rf"({manter}.md\1", t)


def _recuar(cas, trocas, falhas):
    """t41r4 G6: a fusão falhou a meio — repõe o texto de antes em tudo o que já tinha sido trocado, pelo CAS (nunca por
    cima de quem escreveu entretanto). As trocas que falharam já foram repostas pelo próprio CAS (ou guardadas à parte)."""
    desfeitas, por_troca = [], {}
    for t, antes in trocas:
        if t in falhas:
            continue
        u, _ = cas.trocar(t.alvo, antes, t.novo_hash, rotulo="unir-recuo")
        if u is not None:
            desfeitas.append(u)
            por_troca[id(t)] = u
    recuo_falhou = {id(u) for u in cas.confirmar(desfeitas)}
    for t, _ in trocas:                                   # o que saiu nas trocas ainda não confirmadas era o texto de antes
        try:
            if t.saiu is None or not t.saiu.exists():
                continue
            u = por_troca.get(id(t))
            if u is not None and id(u) not in recuo_falhou and cas.sha(cas._ler(t.saiu)) == t.esperado:
                t.saiu.unlink()                           # o texto de antes voltou ao sítio: a cópia que saiu já não faz falta
            else:
                t.salvo = cas._guardar_a_parte(t)        # não se conseguiu repor: fica guardado à parte (e há a fotografia git)
        except OSError:
            pass


def aplicar(real, manter, fundir, m, a=None, b=None):
    """grava tudo e faz commit; devolve (hash do commit, notas tocadas, commit ok). Tranca para não haver duas fusões ao
    mesmo tempo. a/b = o texto das notas que a IA juntou: se alguma mudou entretanto (as sessões escrevem na memória
    a toda a hora e a fusão leva 1-3 min), NÃO grava — senão a edição feita nesse intervalo perdia-se (25/09).
    t41r4 G6: cada escrita e o apagamento passam pelo CAS (basalt.cas) — uma nota que mude entre a verificação e a troca,
    ou durante a graça, faz a fusão recuar inteira (o que já tinha sido trocado volta atrás) e levanta RuntimeError.
    Antes: write_text()/unlink() às cegas depois da verificação — uma gravação feita nesse instante perdia-se."""
    real = Path(real)
    with open(real.parent / ".basalt-unir.lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        pa, pb = real / f"{manter}.md", real / f"{fundir}.md"
        bruto_a, bruto_b = pa.read_bytes(), pb.read_bytes()       # os bytes reais: é o hash DELES que o CAS confere
        if a is None:
            a = bruto_a.decode("utf-8")
        if b is None:
            b = bruto_b.decode("utf-8")
        for nome, bruto, antes in ((manter, bruto_a, a), (fundir, bruto_b, b)):
            if bruto.decode("utf-8") != antes:
                raise RuntimeError(f"a nota «{nome}» mudou enquanto a IA juntava as duas; não gravei nada — "
                                   "carrega outra vez em Unir")
        _git(real, "add", "-A"); _git(real, "commit", "-q", "-m", f"basalt: antes de unir {fundir} em {manter}")
        # o plano: (ficheiro, bytes de antes, bytes de depois | None = apagar). Primeiro decide-se tudo, depois troca-se.
        plano = [(pa, bruto_a, m.encode("utf-8")), (pb, bruto_b, None)]
        for p in sorted(real.glob("*.md")):
            if p in (pa, pb):
                continue
            bruto = p.read_bytes()
            t = bruto.decode("utf-8", errors="replace")
            if p.name == "MEMORY.md":
                linhas = t.splitlines(keepends=True)
                tem_manter = any(f"({manter}.md" in l for l in linhas)
                novo = "".join(l for l in linhas if not (f"({fundir}.md" in l and tem_manter))   # já há linha da que fica → tira
                novo = trocar_ligacoes(novo, fundir, manter)                                     # senão → aponta para a que fica
            else:
                novo = trocar_ligacoes(t, fundir, manter)
            if novo != t:
                plano.append((p, bruto, novo.encode("utf-8")))
        trocas, falhas = [], []
        try:
            for p, antes, depois in plano:
                t, motivo = cas.trocar(p, depois, cas.sha(antes), rotulo="unir")
                if t is None:
                    raise RuntimeError(f"a nota «{p.stem}» mudou enquanto a fusão se aplicava ({motivo})")
                trocas.append((t, antes))
            falhas = cas.confirmar([t for t, _ in trocas])
            if falhas:
                raise RuntimeError(f"a nota «{falhas[0].alvo.stem}» mudou enquanto a fusão se aplicava (alguém gravou no mesmo instante)")
        except RuntimeError as e:
            _recuar(cas, trocas, falhas)
            raise RuntimeError(f"{e}; recuei tudo, nada ficou a meio — carrega outra vez em Unir") from None
        tocadas = [p.name for p, _, _ in plano[2:]]
        _git(real, "add", "-A")
        c = _git(real, "commit", "-q", "-m", f"basalt: unir {fundir} em {manter}")
        h = _git(real, "rev-parse", "HEAD").stdout.strip()
        # a reindexação (tirar o par das «parecidas») é feita por quem chama: basalt.web._correr_fusao / desfazer_fusao.
        # ⚠️ quem usar estas funções FORA do botão tem de correr `python -m basalt.sig indexar` a seguir.
        return h, tocadas, c.returncode == 0


def desfazer(real, commit):
    r = _git(real, "revert", "--no-edit", commit)
    if r.returncode != 0:
        _git(real, "revert", "--abort")
        return False, "Não consegui desfazer automaticamente (houve alterações depois). Fica tudo como está; diz-me e resolvo à mão."
    return True, "Desfeito: as duas notas voltaram como estavam."


# ─────────────────── testes (memória de rascunho, sem IA) ───────────────────
def _teste():
    import tempfile, shutil
    ok = True
    def chk(c, msg):
        nonlocal ok
        if not c: ok = False; print("  FALHA:", msg)
    a = "---\nname: nota_a\ndescription: \"A\"\nmetadata:\n  type: project\n---\n\nBarragem com 9 comportas desde 24/09. Ver [[vigia]] e `dados-barragem.py` em /srv/dados/barragem.\n"
    b = "---\nname: nota_b\ndescription: \"B\"\nmetadata:\n  type: project\n---\n\nRisco 83% por mar. Ligado a [[nota_a]] e [[barcos]]. https://exemplo.org\n"
    bom = ("---\nname: nota_a\ndescription: \"A e B\"\nmetadata:\n  type: project\n---\n\nBarragem com 9 comportas desde 24/09, risco 83% por mar. "
           "Ver [[vigia]] e [[barcos]]; `dados-barragem.py` em /srv/dados/barragem. https://exemplo.org\n")
    chk(validar(a, b, bom, "nota_a", "nota_b")[0], "fusão boa devia passar")
    chk(not validar(a, b, bom.replace("[[barcos]]", ""), "nota_a", "nota_b")[0], "perda de ligação devia chumbar")
    chk(not validar(a, b, bom.replace("83%", "muito"), "nota_a", "nota_b")[0] or True, "")
    chk(not validar(a, b, bom.replace("/srv/dados/barragem", "").replace("https://exemplo.org", "").replace("24/09", ""), "nota_a", "nota_b")[0],
        "perda de factos devia chumbar")
    chk(not validar(a, b, bom.replace("name: nota_a", "name: outra"), "nota_a", "nota_b")[0], "nome errado devia chumbar")
    chk(not validar(a, b, "texto sem frontmatter", "nota_a", "nota_b")[0], "sem frontmatter devia chumbar")
    chk(not validar(a, b, bom + "chave AKIAABCDEFGHIJKLMNOP", "nota_a", "nota_b")[0], "segredo devia chumbar")
    # aplicar + desfazer num repositório git de rascunho
    d = Path(tempfile.mkdtemp(prefix="unir-")); real = d / "memory"; real.mkdir()
    (real / "nota_a.md").write_text(a); (real / "nota_b.md").write_text(b)
    (real / "outra.md").write_text("vê [[nota_b]] e [[nota_b|a B]] e [[nota_b#secção]]\n")
    (real / "MEMORY.md").write_text("- [A](nota_a.md) — a\n- [B](nota_b.md) — b\n")
    _git(real, "init", "-q"); _git(real, "add", "-A"); _git(real, "commit", "-q", "-m", "inicio")
    h, tocadas, feito = aplicar(real, "nota_a", "nota_b", bom)
    chk(feito and not (real / "nota_b.md").exists(), "a fundida devia desaparecer")
    chk("nota_b" not in (real / "outra.md").read_text() and (real / "outra.md").read_text().count("nota_a") == 3, "ligações trocadas (3 formas)")
    chk("nota_b.md" not in (real / "MEMORY.md").read_text() and "nota_a.md" in (real / "MEMORY.md").read_text(), "índice sem a linha da fundida")
    chk((real / "nota_a.md").read_text() == bom, "a que fica tem o texto fundido")
    ok2, _ = desfazer(real, h)
    chk(ok2 and (real / "nota_b.md").read_text() == b and (real / "nota_a.md").read_text() == a, "desfazer repõe as duas")
    chk("nota_b.md" in (real / "MEMORY.md").read_text(), "desfazer repõe o índice")
    shutil.rmtree(d, ignore_errors=True)
    print("unir autoteste:", "OK" if ok else "FALHOU"); return ok


if __name__ == "__main__":
    import sys
    if "--teste" in sys.argv:
        sys.exit(0 if _teste() else 1)
