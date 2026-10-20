"""Ronda 13 — servidor: refs com segredo, categorias das fontes externas, ambiente do Claude CLI, verificador e avisos.

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
