"""Ronda 13 — servidor: refs com segredo, categorias das fontes externas, ambiente do Claude CLI, verificador, avisos e ficheiros de dados sem ligações.

Cada grupo diz o defeito que a revisão externa encontrou; os «controlo» são o comportamento que já estava certo e tem de ficar.
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from types import SimpleNamespace

from conftest import FM, RAIZ, toml_de, usar_ia


def _por_no_indice(esp, pedacos):
    """(fonte, ref, titulo, trecho) no índice do espaço, com o esquema que o basalt.sig cria."""
    from basalt import config, sig
    with config.dentro(esp):
        c = sig.db()
        c.executemany("INSERT OR REPLACE INTO pedaco (fonte, ref, titulo, trecho, hash, vec) VALUES (?, ?, ?, ?, 'h', x'')", pedacos)
        c.commit()
        c.close()


# ═════════════════════════ A — uma ref com segredo não abre pelos irmãos ═════════════════════════
REF_SECRETA = "doc#password: testeFicticio9876"


def test_ref_com_segredo_e_recusada_antes_de_juntar_os_irmaos(memoria):
    from basalt import sig
    _por_no_indice(memoria.esp, [("manual", "doc#0", "Manual", "parte limpa"), ("manual", REF_SECRETA, "Manual", "outra parte")])
    assert sig.ler_fonte("manual", REF_SECRETA) is None


def test_ref_com_segredo_o_comando_fonte_recusa_sem_a_repetir(memoria):
    _por_no_indice(memoria.esp, [("manual", "doc#0", "Manual", "parte limpa"), ("manual", REF_SECRETA, "Manual", "outra parte")])
    r = subprocess.run([sys.executable, "-m", "basalt.sig", "fonte", "manual", REF_SECRETA], cwd=RAIZ,
                       env={**os.environ, **memoria.ambiente()}, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "testeFicticio9876" not in r.stdout + r.stderr


def test_controlo_a_ref_limpa_continua_a_abrir_com_os_irmaos(memoria):
    from basalt import sig
    _por_no_indice(memoria.esp, [("manual", "doc#0", "Manual", "parte um"), ("manual", "doc#1", "Manual", "parte dois")])
    d = sig.ler_fonte("manual", "doc#0")
    assert d and [p["ref"] for p in d["pedacos"]] == ["doc#0", "doc#1"]


# ═════════════════════════ B — o `claude` do fornecedor claude-cli não herda as chaves do processo ═════════════════════════
CHAVE_FICTICIA = "sk-ant-api03-" + "x" * 40


def _claude_falso(tmp_path, monkeypatch):
    """um `claude` no PATH que responde OK e guarda o ambiente que recebeu."""
    pasta = tmp_path / "bin"
    pasta.mkdir()
    saida = tmp_path / "ambiente.json"
    p = pasta / "claude"
    p.write_text(f"#!{sys.executable}\nimport json, os, sys\nsys.stdin.read()\n"
                 f"open({str(saida)!r}, 'w').write(json.dumps(dict(os.environ)))\n"
                 "print(json.dumps({'result': 'OK', 'is_error': False}))\n", encoding="utf-8")
    p.chmod(0o755)
    monkeypatch.setenv("PATH", f"{pasta}{os.pathsep}{os.environ.get('PATH', '')}")
    return saida


def test_claude_cli_nao_recebe_as_chaves_do_processo(tmp_path, monkeypatch):
    from basalt import config, ia
    saida = _claude_falso(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", CHAVE_FICTICIA)
    monkeypatch.setenv("BASALT_IA_CHAVE", CHAVE_FICTICIA)
    monkeypatch.setenv("OPENAI_API_KEY", CHAVE_FICTICIA)
    geral = config.IAConfig(fornecedor="anthropic", url="http://127.0.0.1:9", chave_env="ANTHROPIC_API_KEY",
                            tarefas={"unir": {"fornecedor": "claude-cli", "modelo_chat": "sonnet"}})
    assert ia.IA(geral.para("unir")).chat("ola") == "OK"
    env = json.loads(saida.read_text())
    assert CHAVE_FICTICIA not in env.values(), sorted(k for k, v in env.items() if v == CHAVE_FICTICIA)
    assert env.get("PATH") and env.get("HOME")                       # o que o CLI precisa para correr continua lá


def test_claude_cli_recebe_so_a_chave_que_a_tarefa_declara(tmp_path, monkeypatch):
    from basalt import config, ia
    saida = _claude_falso(tmp_path, monkeypatch)
    monkeypatch.setenv("CHAVE_DA_TAREFA", "chave-ficticia-da-tarefa")
    monkeypatch.setenv("ANTHROPIC_API_KEY", CHAVE_FICTICIA)
    geral = config.IAConfig(fornecedor="nenhum", tarefas={"unir": {"fornecedor": "claude-cli", "chave_env": "CHAVE_DA_TAREFA"}})
    ia.IA(geral.para("unir")).chat("ola")
    env = json.loads(saida.read_text())
    assert env.get("ANTHROPIC_API_KEY") == "chave-ficticia-da-tarefa" and "CHAVE_DA_TAREFA" not in env


# ═════════════════════════ C — categorias das fontes externas: ref exacta, fonte vazia, itens novos ═════════════════════════
def _cats(memoria, d):
    memoria.esp.categorias.parent.mkdir(parents=True, exist_ok=True)
    memoria.esp.categorias.write_text(json.dumps({k: {"cats": c, "hash": "x"} for k, c in d.items()}), encoding="utf-8")


def _cats_lidas(memoria):
    return json.loads(memoria.esp.categorias.read_text(encoding="utf-8"))


@pytest.fixture
def com_notas(memoria):
    usar_ia(fornecedor="nenhum")                 # só as regras pelo nome: nada vai à IA
    memoria.nota("nota_viva", FM.format(nome="nota_viva") + "\nainda cá está\n")
    return memoria


def test_a_categoria_de_uma_ref_com_cardinal_fica(com_notas):
    from basalt import categorias
    _por_no_indice(com_notas.esp, [("pedidos", "repo#12", "Pedido 12", "texto"), ("pedidos", "repo#13", "Pedido 13", "texto")])
    _cats(com_notas, {"pedidos:repo#12": ["Trabalho"], "pedidos:repo#13": ["Finanças"]})
    categorias.main()
    d = _cats_lidas(com_notas)
    assert d.get("pedidos:repo#12", {}).get("cats") == ["Trabalho"], sorted(d)
    assert d.get("pedidos:repo#13", {}).get("cats") == ["Finanças"], sorted(d)


@pytest.fixture
def fonte_falsa(com_notas, tmp_path, monkeypatch):
    """uma fonte externa a sério para o `sig indexar`: um comando que imprime o JSON do ficheiro `itens.json` (ou falha)."""
    from basalt import sig
    itens = tmp_path / "itens.json"
    script = tmp_path / "fonte.py"
    script.write_text("import sys\nt = open(sys.argv[1]).read()\nsys.exit(3) if t == 'falha' else print(t)\n", encoding="utf-8")
    cmd = f'"{sys.executable}" "{script}" "{itens}"'
    monkeypatch.delenv("BASALT_SIG_ISOLADO", raising=False)
    monkeypatch.setattr(sig.config, "carregar", lambda *a, **k: SimpleNamespace(fontes_extra=[cmd]))

    def responder(lista):
        itens.write_text(lista if isinstance(lista, str) else
                         json.dumps([{"fonte": f, "ref": r, "titulo": t, "texto": x} for f, r, t, x in lista]), encoding="utf-8")
        sig.indexar()
    return responder


def test_fonte_que_respondeu_vazia_perde_as_categorias(fonte_falsa, com_notas):
    from basalt import categorias
    fonte_falsa([("agenda", "unica", "Reunião única", "a reunião de terça")])
    _cats(com_notas, {"agenda:unica": ["Trabalho"]})
    categorias.main()
    assert "agenda:unica" in _cats_lidas(com_notas)
    fonte_falsa([])                                  # a fonte respondeu: já não tem nada
    categorias.main()
    assert "agenda:unica" not in _cats_lidas(com_notas), "a fonte respondeu vazia: a categoria do item que saiu devia sair"


def test_controlo_fonte_que_falhou_guarda_as_categorias(fonte_falsa, com_notas):
    from basalt import categorias
    fonte_falsa([("agenda", "unica", "Reunião única", "a reunião de terça")])
    _cats(com_notas, {"agenda:unica": ["Trabalho"]})
    fonte_falsa("falha")                             # a fonte não respondeu: não se sabe o que tem
    categorias.main()
    assert _cats_lidas(com_notas).get("agenda:unica", {}).get("cats") == ["Trabalho"]


def test_itens_externos_novos_ganham_categoria_pelas_regras(com_notas):
    from basalt import categorias
    _por_no_indice(com_notas.esp, [("pedidos", "orcamento_cozinha", "Orçamento da cozinha", "valores"),
                                   ("pedidos", "repo#12", "Pedido 12", "a"), ("pedidos", "repo#13", "Pedido 13", "b"),
                                   ("manual", "caldeira#0", "Manual", "parte um"), ("manual", "caldeira#1", "Manual", "parte dois")])
    categorias.main()
    d = _cats_lidas(com_notas)
    assert d.get("pedidos:orcamento_cozinha", {}).get("cats") == ["Finanças"], sorted(d)
    assert {"pedidos:repo#12", "pedidos:repo#13", "manual:caldeira"} <= set(d), sorted(d)    # a ref exacta; a nota partida é uma só
    assert "manual:caldeira#0" not in d


def test_itens_externos_novos_ganham_categoria_pela_ia(com_notas):
    from basalt import categorias
    falsa = usar_ia(json.dumps({"categorias": ["Aprender"]}))
    _por_no_indice(com_notas.esp, [("leituras", "artigo-7", "Um artigo sobre solos", "texto do artigo")])
    _cats(com_notas, {"nota_viva": ["Trabalho"]})
    categorias.main()
    assert _cats_lidas(com_notas).get("leituras:artigo-7", {}).get("cats") == ["Aprender"]
    assert any("Um artigo sobre solos" in p for p in falsa.prompts)


def test_item_externo_com_segredo_nao_vai_a_ia(com_notas):
    from basalt import categorias
    falsa = usar_ia(json.dumps({"categorias": ["Aprender"]}))
    _por_no_indice(com_notas.esp, [("leituras", "artigo-8", "Artigo", "a senha: minhapalavrasecreta (router)")])
    _cats(com_notas, {"nota_viva": ["Trabalho"]})
    categorias.main()
    assert "leituras:artigo-8" not in _cats_lidas(com_notas) and not any("minhapalavrasecreta" in p for p in falsa.prompts)


def test_controlo_item_externo_ja_categorizado_nao_muda(com_notas):
    from basalt import categorias
    falsa = usar_ia(json.dumps({"categorias": ["Aprender"]}))
    _por_no_indice(com_notas.esp, [("leituras", "artigo-7#0", "Artigo", "a"), ("leituras", "artigo-7#1", "Artigo", "b")])
    _cats(com_notas, {"nota_viva": ["Trabalho"], "leituras:artigo-7": ["Pessoal"]})
    categorias.main()
    assert _cats_lidas(com_notas)["leituras:artigo-7"]["cats"] == ["Pessoal"]
    assert not any("Fonte: leituras" in p for p in falsa.prompts)            # (a nota do espaço vai, porque o hash é outro)


# ═════════════════════════ D — [avisos] comando: os ALERTAS também num comando teu, em fundo ═════════════════════════
def _esperar(p, segundos=15):
    import time
    fim = time.time() + segundos
    while time.time() < fim and not (p.exists() and p.stat().st_size):
        time.sleep(0.1)
    return p.read_text(encoding="utf-8") if p.exists() else None


@pytest.fixture
def avisos_falsos(memoria, tmp_path, monkeypatch):
    """um basalt.toml com [avisos] comando = um script que escreve a mensagem (e o ambiente) num ficheiro; `espera` o atrasa."""
    from basalt import duasvias
    saida, amb = tmp_path / "aviso.txt", tmp_path / "aviso_ambiente.json"
    script = tmp_path / "notificar.py"
    script.write_text("import json, os, sys, time\nif sys.argv[1] != '0': time.sleep(float(sys.argv[1]))\n"
                      f"open({str(amb)!r}, 'w').write(json.dumps(dict(os.environ)))\n"
                      f"open({str(saida)!r}, 'w', encoding='utf-8').write(sys.argv[2])\n", encoding="utf-8")

    def configurar(espera=0):
        toml = tmp_path / "basalt.toml"
        toml.write_text(toml_de(0, tmp_path / "raiz", extra=f"[avisos]\ncomando = {json.dumps([sys.executable, str(script), str(espera)])}\n"),
                        encoding="utf-8")
        monkeypatch.setenv("BASALT_CONFIG", str(toml))
    monkeypatch.setattr(duasvias, "ALERTAS", True)
    return SimpleNamespace(configurar=configurar, saida=saida, ambiente=amb)


def test_alerta_corre_o_comando_de_avisos(avisos_falsos, capfd):
    from basalt import duasvias
    avisos_falsos.configurar()
    duasvias.alerta("1 conflito(s) — versão da cópia guardada como *.CONFLITO-*.md na memória: compras")
    assert _esperar(avisos_falsos.saida) == "1 conflito(s) — versão da cópia guardada como *.CONFLITO-*.md na memória: compras"
    assert "ALERTA: 1 conflito(s)" in capfd.readouterr().err                # o stderr continua a ter o ALERTA


def test_a_mensagem_do_aviso_nao_leva_segredos(avisos_falsos, capfd):
    from basalt import duasvias
    avisos_falsos.configurar()
    duasvias.alerta("nota retida: senha: minhapalavrasecreta (router) e " + CHAVE_FICTICIA)
    m = _esperar(avisos_falsos.saida)
    assert m and "nota retida" in m and "minhapalavrasecreta" not in m and CHAVE_FICTICIA not in m, m
    err = capfd.readouterr().err
    assert "minhapalavrasecreta" not in err and CHAVE_FICTICIA not in err


def test_o_comando_de_avisos_nao_recebe_as_chaves_do_processo(avisos_falsos, monkeypatch):
    from basalt import duasvias
    monkeypatch.setenv("ANTHROPIC_API_KEY", CHAVE_FICTICIA)
    avisos_falsos.configurar()
    duasvias.alerta("teste de ambiente")
    assert _esperar(avisos_falsos.saida) == "teste de ambiente"
    env = json.loads(avisos_falsos.ambiente.read_text())
    assert CHAVE_FICTICIA not in env.values() and env.get("PATH")


def test_controlo_o_alerta_nao_espera_pelo_comando(avisos_falsos):
    import time
    from basalt import duasvias
    avisos_falsos.configurar(espera=30)
    t = time.time()
    duasvias.alerta("demora")
    assert time.time() - t < 5


@pytest.mark.parametrize("extra, erro", [
    ('[avisos]\ncomando = "notificar --titulo Basalt"\n', "[avisos] comando"),
    ('[avisos]\ncomando = ["notificar", 3]\n', "[avisos] comando"),
    ('[segredos]\nverificador = "verificar"\n', "[segredos] verificador"),
    ('[segredos]\nverificador = [""]\n', "[segredos] verificador"),
    ('[segredos]\nverificador = ["verificar"]\ntempo_limite = "30"\n', "[segredos] tempo_limite"),
    ('[segredos]\nverificador = ["verificar"]\ntempo_limite = 0\n', "[segredos] tempo_limite"),
])
def test_configuracao_dos_comandos_novos_validada(tmp_path, extra, erro):
    from basalt import config
    toml = tmp_path / "basalt.toml"
    toml.write_text(toml_de(0, tmp_path / "raiz", extra=extra), encoding="utf-8")
    with pytest.raises(config.ConfigInvalida, match=erro.replace("[", r"\[").replace("]", r"\]")):
        config.carregar(toml)


def test_controlo_sem_avisos_nem_verificador_a_configuracao_e_a_de_sempre(tmp_path):
    from basalt import config
    toml = tmp_path / "basalt.toml"
    toml.write_text(toml_de(0, tmp_path / "raiz"), encoding="utf-8")
    c = config.carregar(toml)
    assert not getattr(getattr(c, "avisos", None), "comando", ()) and not getattr(getattr(c, "segredos", None), "verificador", ())


# ═════════════════════════ E — [segredos] verificador: um comando teu decide também, em todos os caminhos ═════════════════════════
# o verificador falso: sai 1 se algum ficheiro tem MARCA-SECRETA, 2 se tem MARCA-FALHA, dorme com MARCA-DORME; regista o que viu
VERIFICADOR = '''import json, os, sys, time
registo, fich = sys.argv[1], sys.argv[2:]
textos = [open(f, encoding="utf-8").read() for f in fich]
with open(registo, "a", encoding="utf-8") as h:
    h.write(json.dumps({"argv": fich, "modos": [os.stat(f).st_mode & 0o777 for f in fich], "textos": textos}) + "\\n")
if any("MARCA-DORME" in t for t in textos):
    time.sleep(30)
if any("MARCA-FALHA" in t for t in textos):
    sys.exit(2)
sys.exit(1 if any("MARCA-SECRETA" in t for t in textos) else 0)
'''
LIMPA_V = FM.format(nome="limpa") + "\nO barco para o Pico sai às 8h.\n"
MARCADA = FM.format(nome="marcada") + "\nO código do cacifo é MARCA-SECRETA.\n"
PARTIDA = FM.format(nome="partida") + "\nEsta faz o verificador falhar: MARCA-FALHA.\n"


@pytest.fixture
def verificador(tmp_path, monkeypatch):
    """`extra` para o basalt.toml e `ligar(toml)` para o processo o ler; `chamadas()` = o que o verificador falso viu."""
    from basalt import segredos
    script, registo = tmp_path / "verificar.py", tmp_path / "verificador.jsonl"
    script.write_text(VERIFICADOR, encoding="utf-8")
    monkeypatch.setattr(segredos, "_VERIFICADOS", {}, raising=False)
    monkeypatch.setattr(segredos, "_ULTIMO_ALERTA", [0.0], raising=False)

    def extra(tempo=10):
        return f"[segredos]\nverificador = {json.dumps([sys.executable, str(script), str(registo)])}\ntempo_limite = {tempo}\n"

    def ligar(toml=None, tempo=10):
        if toml is None:
            toml = tmp_path / "basalt-verificador.toml"
            toml.write_text(toml_de(0, tmp_path / "raiz", extra=extra(tempo)), encoding="utf-8")
        monkeypatch.setenv("BASALT_CONFIG", str(toml))

    def chamadas():
        return [json.loads(l) for l in registo.read_text(encoding="utf-8").splitlines()] if registo.exists() else []
    return SimpleNamespace(extra=extra, ligar=ligar, chamadas=chamadas)


def test_verificador_recusa_ao_criar_pela_pagina(servidor_local, verificador):
    s = servidor_local(extra=verificador.extra())
    verificador.ligar(s.toml)
    r = s.pedido("POST", "/api/nota", {"nome": "v_secreta", "texto": "o código é MARCA-SECRETA"})
    assert r.codigo == 400, r.corpo
    r = s.pedido("POST", "/api/nota", {"nome": "v_partida", "texto": "isto faz falhar: MARCA-FALHA"})
    assert r.codigo == 503 and "[segredos] verificador" in r.json["erro"], r.corpo
    assert s.pedido("POST", "/api/nota", {"nome": "v_limpa", "texto": "o barco sai às 8h"}).codigo == 201
    copia = s.espaco().copia                              # a página cria na cópia editável
    assert not (copia / "v_secreta.md").exists() and not (copia / "v_partida.md").exists() and (copia / "v_limpa.md").exists()


def test_o_verificador_recebe_ficheiros_privados_que_depois_sao_apagados(memoria, verificador):
    from basalt import sig
    verificador.ligar()
    sig.guardar("lembrar de comprar velas novas", "terminal")
    chamadas = verificador.chamadas()
    assert len(chamadas) == 1, "o texto não foi ao verificador"
    ch = chamadas[0]
    assert ch["textos"] == ["lembrar de comprar velas novas"]
    assert all("velas" not in a for a in ch["argv"]), "o texto foi por argumento"
    assert ch["modos"] == [0o600] and not any(Path(a).exists() for a in ch["argv"])
    assert not Path(ch["argv"][0]).parent.exists(), "a pasta temporária ficou"


def test_verificador_que_falha_ou_demora_nao_deixa_gravar_na_entrada(memoria, verificador):
    import time
    from basalt import entrada, segredos, sig
    verificador.ligar(tempo=1)
    for texto in ("o código é MARCA-SECRETA", "faz falhar: MARCA-FALHA", "demora: MARCA-DORME"):
        t = time.time()
        with pytest.raises(segredos.SegredoEmClaro):
            sig.guardar(texto, "terminal")
        assert time.time() - t < 10
    assert entrada.ler() == []


def test_verificador_no_mcp(memoria, verificador):
    from basalt import mcp
    from basalt.config import AuthConfig, Config, Utilizador
    verificador.ligar()
    u = Utilizador(email="ana@exemplo.pt", espaco="t", nome="Ana")
    cfg = Config(dados=memoria.base / "raiz", escutar="127.0.0.1", auth=AuthConfig(modo="aberto"), utilizadores=[u])
    srv = mcp.ServidorMCP(cfg, web=__import__("basalt.web", fromlist=["web"]))
    with __import__("basalt.config", fromlist=["config"]).dentro(memoria.esp):
        r1 = srv._t_guardar_ideia({"texto": "o código é MARCA-SECRETA"}, u)
        r2 = srv._t_guardar_ideia({"texto": "faz falhar: MARCA-FALHA"}, u)
    assert r1[1] is True and "password" in r1[0]["erro"]
    assert r2[1] is True and "[segredos] verificador" in r2[0]["erro"]


def test_verificador_nas_notas_retidas_em_lote(memoria, verificador):
    from basalt import retidas
    verificador.ligar()
    for i in range(120):
        (memoria.real / f"n{i:03d}.md").write_text(LIMPA_V.replace("limpa", f"n{i:03d}"), encoding="utf-8")
    (memoria.real / "marcada.md").write_text(MARCADA, encoding="utf-8")
    assert retidas.nomes_retidos(memoria.real) == {"marcada"}
    ch = verificador.chamadas()
    # 121 notas → 3 lotes de até 50, mais a procura da marcada dentro do lote dela (partido ao meio) — não 121 chamadas
    assert [len(c["argv"]) for c in ch[:1]] == [50] and len(ch) <= 3 + 2 * 6, [len(c["argv"]) for c in ch]
    assert len({t for c in ch for t in c["textos"]}) == 121
    assert "marcada" not in {p.stem for p, _ in retidas.notas(memoria.real)}


def test_verificador_que_falha_retem_so_o_que_ainda_nao_viu(memoria, verificador):
    from basalt import retidas
    verificador.ligar()
    (memoria.real / "limpa.md").write_text(LIMPA_V, encoding="utf-8")
    assert retidas.nomes_retidos(memoria.real) == set()
    (memoria.real / "partida.md").write_text(PARTIDA, encoding="utf-8")
    assert retidas.nomes_retidos(memoria.real) == {"partida"}


def test_verificador_na_pergunta_com_indice_antigo(memoria, verificador):
    from basalt import sig
    verificador.ligar()
    (memoria.real / "marcada.md").write_text(MARCADA, encoding="utf-8")
    (memoria.real / "limpa.md").write_text(LIMPA_V, encoding="utf-8")
    _por_no_indice(memoria.esp, [("nota", "marcada#0", "marcada", "o cacifo"), ("nota", "limpa#0", "limpa", "o barco")])
    from basalt import config
    with config.dentro(memoria.esp):
        refs = [r for _, r, *_ in sig.carregar(sig.db())]
    assert refs == ["limpa#0"]


def test_verificador_na_lista_de_exclusao_da_pagina(memoria, verificador):
    from basalt import web
    verificador.ligar()
    for d in (memoria.real, memoria.copia):
        (d / "marcada.md").write_text(MARCADA, encoding="utf-8")
        (d / "limpa.md").write_text(LIMPA_V, encoding="utf-8")
    web.refazer_exclusao()
    assert web.excl() == {"marcada"}
    assert memoria.excluir.read_text(encoding="utf-8").split() == ["marcada"]


def test_verificador_na_sincronizacao_adia_ou_retem(memoria, verificador, monkeypatch):
    from basalt import duasvias
    monkeypatch.setattr(duasvias, "GIT", False)
    monkeypatch.setattr(duasvias, "ALERTAS", False)
    verificador.ligar()
    (memoria.copia / "partida.md").write_text(PARTIDA, encoding="utf-8")
    (memoria.copia / "marcada.md").write_text(MARCADA, encoding="utf-8")
    r = duasvias.ciclo()
    assert "partida.md" in r["adiados"] and "marcada.md" in r["retidos"], r
    assert not (memoria.real / "partida.md").exists() and not (memoria.real / "marcada.md").exists()
    assert "partida.md" not in json.loads(memoria.ledger.read_text() or "{}")


def test_verificador_na_importacao_e_nos_anexos_de_texto(memoria, verificador, tmp_path):
    from basalt import anexos, obsidian, segredos
    verificador.ligar()
    cofre = tmp_path / "cofre"
    cofre.mkdir()
    (cofre / "limpa.md").write_text("O barco sai às 8h.\n", encoding="utf-8")
    (cofre / "marcada.md").write_text("O código é MARCA-SECRETA.\n", encoding="utf-8")
    r = obsidian.importar(cofre, memoria.esp)
    assert r["importadas"] == ["limpa"] and [f for f, _ in r["saltadas"]] == ["marcada.md"], r
    with pytest.raises(segredos.SegredoEmClaro):
        anexos.guardar(memoria.esp, "lista.txt", "código: MARCA-SECRETA\n".encode())


def test_controlo_sem_verificador_nada_muda(memoria, monkeypatch, tmp_path):
    from basalt import entrada, retidas, sig, web
    toml = tmp_path / "basalt-sem.toml"
    toml.write_text(toml_de(0, tmp_path / "raiz"), encoding="utf-8")
    monkeypatch.setenv("BASALT_CONFIG", str(toml))
    for d in (memoria.real, memoria.copia):
        (d / "marcada.md").write_text(MARCADA, encoding="utf-8")
    sig.guardar("o código é MARCA-SECRETA", "terminal")
    assert [e["texto"] for e in entrada.ler()] == ["o código é MARCA-SECRETA"]
    assert retidas.nomes_retidos(memoria.real) == set()
    web.refazer_exclusao()
    assert web.excl() == set()


# ═════════════════════════ F — o registo do servidor e o relatório da importação não mostram chaves nem passwords ═════════════════════════
def test_erro_a_meio_de_um_pedido_nao_poe_a_chave_do_caminho_no_registo(servidor_local, monkeypatch, capfd):
    from basalt import web

    def corta(self, nome, user):
        raise BrokenPipeError(32, "Broken pipe")                # a ligação cortada a meio da resposta
    monkeypatch.setattr(web.H, "_anexo", corta)
    s = servidor_local()
    s.pedido("GET", f"/anexos/{CHAVE_FICTICIA}.png")
    import time
    time.sleep(0.2)
    saida = capfd.readouterr()
    assert CHAVE_FICTICIA not in saida.out + saida.err, saida
    assert "erro GET" in saida.out                              # o erro continua registado, só sem a chave


def test_o_traceback_do_servidor_nao_leva_a_chave_da_mensagem(capfd):
    from basalt import web
    classe = getattr(web, "Servidor", web.ThreadingHTTPServer)
    try:
        raise RuntimeError(f"falhou ao abrir /anexos/{CHAVE_FICTICIA}.png")
    except RuntimeError:
        classe.handle_error(None, None, ("127.0.0.1", 1))
    saida = capfd.readouterr()
    assert CHAVE_FICTICIA not in saida.out + saida.err and "falhou ao abrir" in saida.out + saida.err, saida


@pytest.mark.parametrize("nome", ["router senha=Tr0ub4dor&3.md", "senha: Xk9mPq2vLw.md"])
def test_o_relatorio_da_importacao_nao_mostra_a_password_do_nome(tmp_path, nome):
    cofre = tmp_path / "cofre"
    cofre.mkdir()
    (cofre / "limpa.md").write_text("O barco sai às 8h.\n", encoding="utf-8")
    (cofre / nome).symlink_to(cofre / "limpa.md")
    toml = tmp_path / "basalt.toml"
    toml.write_text(toml_de(0, tmp_path / "raiz"), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("BASALT_")}
    r = subprocess.run([sys.executable, "-m", "basalt", "importar", "--simular", str(cofre)], cwd=RAIZ, capture_output=True,
                       text=True, timeout=60, env=dict(env, BASALT_CONFIG=str(toml), HOME=str(tmp_path)))
    assert r.returncode == 0, r.stderr
    assert "1 saltadas" in r.stdout and "Tr0ub4dor" not in r.stdout + r.stderr and "Xk9mPq2vLw" not in r.stdout + r.stderr, r.stdout


def test_controlo_o_relatorio_da_importacao_continua_a_dizer_porque(tmp_path):
    cofre = tmp_path / "cofre"
    cofre.mkdir()
    (cofre / "limpa.md").write_text("O barco sai às 8h.\n", encoding="utf-8")
    (cofre / "atalho.md").symlink_to(cofre / "limpa.md")
    toml = tmp_path / "basalt.toml"
    toml.write_text(toml_de(0, tmp_path / "raiz"), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("BASALT_")}
    r = subprocess.run([sys.executable, "-m", "basalt", "importar", "--simular", str(cofre)], cwd=RAIZ, capture_output=True,
                       text=True, timeout=60, env=dict(env, BASALT_CONFIG=str(toml), HOME=str(tmp_path)))
    assert "atalho.md: é uma ligação simbólica" in r.stdout, r.stdout + r.stderr


# ═════════════════════════ G — os ficheiros de dados do espaço não seguem ligações simbólicas ═════════════════════════
# A Entrada, o trinco dela, os trabalhos do «Unir» e as categorias liam-se e escreviam-se por caminho: `A/dados/entrada.jsonl`
# → a Entrada de B fazia a página de A mostrar (e escrever) a Entrada de B.
ANA, BEA = "ana@exemplo.pt", "bea@exemplo.pt"
IDEIA_B, TRABALHO_B = "IDEIA_PRIVADA_DA_BEA", "TRABALHO_PRIVADO_DA_BEA"


@pytest.fixture
def dois(servidor_local):
    s = servidor_local(modo="cabecalho", utilizadores=[{"email": ANA, "espaco": "a"}, {"email": BEA, "espaco": "b"}])
    return s, s.espaco("a").criar(), s.espaco("b").criar()


def _ideia(esp, texto):
    from basalt import config, entrada
    with config.dentro(esp):
        return entrada.acrescentar(texto, "dono")


def _trabalho(esp, msg):
    import uuid
    jid = uuid.uuid4().hex
    esp.jobs_unir.mkdir(parents=True, exist_ok=True)
    (esp.jobs_unir / f"{jid}.json").write_text(json.dumps({"id": jid, "estado": "feita", "mensagem": msg}), encoding="utf-8")
    return jid


def _trocar_por_ligacao(caminho, alvo):
    import shutil
    if caminho.is_dir() and not caminho.is_symlink():
        shutil.rmtree(caminho)
    elif caminho.exists() or caminho.is_symlink():
        caminho.unlink()
    caminho.symlink_to(alvo, target_is_directory=alvo.is_dir())


@pytest.mark.parametrize("onde", ["ficheiro", "pasta-dados"])
def test_entrada_por_ligacao_nao_mostra_a_de_outra_conta(dois, onde):
    s, a, b = dois
    _ideia(b, IDEIA_B)
    if onde == "ficheiro":
        _trocar_por_ligacao(a.inbox, b.inbox)                    # A/dados/entrada.jsonl → a Entrada de B
    else:
        _trocar_por_ligacao(a.dados, b.dados)                    # A/dados → B/dados
    r = s.get("/api/entrada", email=ANA)
    assert IDEIA_B.encode() not in r.corpo, f"a página de A mostrou a Entrada de B ({r.codigo})"


def test_entrada_por_ligacao_nao_escreve_na_de_outra_conta(dois):
    from basalt import config, entrada
    s, a, b = dois
    _ideia(b, IDEIA_B)
    antes = b.inbox.read_bytes()
    _trocar_por_ligacao(a.inbox, b.inbox)
    with config.dentro(a):
        with pytest.raises(OSError):
            entrada.acrescentar("ideia escrita pela ana", "dono")
    assert b.inbox.read_bytes() == antes, "a ideia de A foi parar à Entrada de B"


def test_trinco_da_entrada_por_ligacao_nao_estraga_o_alvo(dois, tmp_path):
    from basalt import config, entrada
    s, a, b = dois
    alvo = tmp_path / "ficheiro-de-fora.txt"
    alvo.write_text("conteúdo que não é do Basalt\n", encoding="utf-8")
    a.dados.mkdir(parents=True, exist_ok=True)
    (a.dados / ".entrada.lock").symlink_to(alvo)
    with config.dentro(a):
        with pytest.raises(OSError):
            with entrada.trinco():
                pass
    assert alvo.read_text(encoding="utf-8") == "conteúdo que não é do Basalt\n", "o trinco esvaziou o ficheiro de fora"


def test_reescrita_da_entrada_nao_passa_pelo_temporario_plantado(dois):
    from basalt import config, entrada
    s, a, b = dois
    _ideia(b, IDEIA_B)
    _ideia(a, "ideia da ana para arrumar")
    antes = b.inbox.read_bytes()
    (a.dados / ".entrada.jsonl.tmp").symlink_to(b.inbox)          # o temporário fixo de antes, apontado à Entrada de B
    with config.dentro(a):
        entrada.arquivar_n(1)
    assert b.inbox.read_bytes() == antes, "a reescrita da Entrada de A escreveu na de B"
    with config.dentro(a):
        assert entrada.ler()[0]["arquivado"] is True


@pytest.mark.parametrize("onde", ["ficheiro", "pasta"])
def test_trabalhos_do_unir_por_ligacao_nao_mostram_os_de_outra_conta(dois, onde):
    s, a, b = dois
    jid = _trabalho(b, TRABALHO_B)
    if onde == "ficheiro":
        a.jobs_unir.mkdir(parents=True, exist_ok=True)
        (a.jobs_unir / f"{jid}.json").symlink_to(b.jobs_unir / f"{jid}.json")
    else:
        _trocar_por_ligacao(a.jobs_unir, b.jobs_unir)
    for caminho in ("/api/unir", f"/api/unir?job={jid}"):
        r = s.get(caminho, email=ANA)
        assert TRABALHO_B.encode() not in r.corpo, f"GET {caminho} de A mostrou o trabalho de B ({r.codigo})"


def test_categorias_por_ligacao_nao_mostram_as_de_outra_conta(dois):
    from basalt import config, web
    s, a, b = dois
    b.categorias.write_text(json.dumps({"nota-privada-da-bea": {"cats": ["Saúde"]}}), encoding="utf-8")
    _trocar_por_ligacao(a.categorias, b.categorias)
    with config.dentro(a):
        assert "nota-privada-da-bea" not in web.cats_de()


def test_controlo_a_entrada_e_os_trabalhos_da_propria_conta_continuam(dois):
    s, a, b = dois
    _ideia(a, "ideia normal da ana")
    jid = _trabalho(a, "trabalho normal da ana")
    r = s.get("/api/entrada", email=ANA)
    assert r.codigo == 200 and "ideia normal da ana" in r.corpo.decode()
    assert "trabalho normal da ana" in s.get("/api/unir", email=ANA).corpo.decode()
    assert s.get(f"/api/unir?job={jid}", email=ANA).json["mensagem"] == "trabalho normal da ana"
    assert IDEIA_B.encode() not in r.corpo


def test_sincronizacao_com_registo_por_ligacao_nao_usa_o_de_outra_conta(memoria, monkeypatch, tmp_path):
    """o registo da sincronização (ledger) de A apontado a outro diz «esta nota já esteve nos dois lados»: a nota nova que
    só está na cópia passava por apagada na memória real e saía da cópia."""
    import hashlib
    from basalt import duasvias
    monkeypatch.setattr(duasvias, "GIT", False)
    monkeypatch.setattr(duasvias, "ALERTAS", False)
    t = FM.format(nome="so-na-copia") + "\nideia escrita na página, ainda por sincronizar.\n"
    (memoria.copia / "so-na-copia.md").write_text(t, encoding="utf-8")
    alheio = tmp_path / "registo-de-outra-conta.json"
    alheio.write_text(json.dumps({"so-na-copia.md": hashlib.sha1(t.encode()).hexdigest()}), encoding="utf-8")
    if memoria.ledger.exists():
        memoria.ledger.unlink()
    memoria.ledger.symlink_to(alheio)
    try:
        duasvias.ciclo()
    except OSError:
        pass
    assert (memoria.copia / "so-na-copia.md").exists(), "o registo de fora fez apagar a nota da cópia"


def test_trinco_da_copia_por_ligacao_e_recusado(memoria, tmp_path):
    from basalt import duasvias, web
    alvo = tmp_path / "trinco-de-fora.lock"
    alvo.write_text("", encoding="utf-8")
    trinco = Path(duasvias.TRINCO)
    trinco.parent.mkdir(parents=True, exist_ok=True)
    if trinco.exists():
        trinco.unlink()
    trinco.symlink_to(alvo)
    for como in (duasvias.trinco, web.trinco_copia):
        with pytest.raises(OSError):
            with como(espera=1):
                pass


def test_indice_por_ligacao_nao_abre_o_de_outra_conta(dois):
    """o índice da pesquisa tem os trechos das notas: `A/dados/semantica.sqlite` → o de B punha a pesquisa de A a ler os de B."""
    from basalt import config, sig
    s, a, b = dois
    _por_no_indice(b, [("nota", "privada#0", "privada", "TRECHO_PRIVADO_DA_BEA")])
    _trocar_por_ligacao(a.semantica, b.semantica)
    with config.dentro(a):
        try:
            c = sig.db()
            trechos = [t for (t,) in c.execute("SELECT trecho FROM pedaco")]
            c.close()
        except OSError:
            trechos = []
    assert "TRECHO_PRIVADO_DA_BEA" not in trechos, "o índice de A abriu o de B"


# ═════════════════════════ H — a documentação das rotas sem login diz todas as que existem ═════════════════════════
def test_seguranca_md_lista_todas_as_rotas_da_api_sem_login(servidor_local):
    """o SEGURANCA.md dizia que /api/saude era a única rota sem login nem dono; /api/entrar e /api/sair também o são."""
    s = servidor_local(modo="senha", utilizadores=[{"email": "ana@exemplo.pt", "espaco": "a"}])
    x = {"X-Basalt": "1"}
    # controlo: as três são atendidas sem sessão nem dono (o próprio pedido responde, não o «entra primeiro»)
    assert s.pedido("GET", "/api/saude", email=None, dono=False).json == {"ok": True}
    r = s.pedido("POST", "/api/entrar", {"email": "ana@exemplo.pt", "senha": "errada"}, email=None, dono=False, cabecalhos=x)
    assert r.codigo == 401 and "senha errados" in r.json["erro"]
    assert s.pedido("POST", "/api/sair", {}, email=None, dono=False, cabecalhos=x).codigo == 200
    atendidas = {"/api/saude", "/api/entrar", "/api/sair"}
    doc = (RAIZ / "docs" / "SEGURANCA.md").read_text(encoding="utf-8")
    assert "única rota da API sem login" not in doc
    for c in sorted(atendidas):
        assert c in doc, f"{c} é atendida sem login e o SEGURANCA.md não a menciona"
