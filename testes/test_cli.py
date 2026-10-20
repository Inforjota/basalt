"""python3 -m basalt — iniciar, utilizador (novo/senha/mcp-token/listar), estado; o basalt.toml é editado no sítio."""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from basalt import __main__ as cli, auth, config   # noqa: E402


def correr(*args, entrada=""):
    r = subprocess.run([sys.executable, "-m", "basalt", *args], cwd=RAIZ, input=entrada, capture_output=True, text=True,
                       env={**os.environ, "BASALT_CONFIG": "", "BASALT_SENHA": ""})
    return r.returncode, r.stdout, r.stderr


def test_iniciar_cria_config_dados_e_utilizador(tmp_path):
    d = tmp_path / "dados"
    rc, out, err = correr("iniciar", "--dados", str(d), "--email", "Ana@Exemplo.pt", "--nome", "Ana", "--senha", "segredo-forte")
    assert rc == 0, err
    toml = d / "basalt.toml"
    assert toml.exists() and oct(toml.stat().st_mode & 0o777) == "0o600" and oct(d.stat().st_mode & 0o777) == "0o700"
    cfg = config.carregar(toml)
    assert cfg.dados == d and cfg.auth.modo == "senha" and [u.email for u in cfg.utilizadores] == ["ana@exemplo.pt"]
    u = cfg.utilizadores[0]
    assert u.espaco == "ana" and u.admin and u.senha_hash.startswith("scrypt$") and auth.verificar_senha(u.senha_hash, "segredo-forte")
    esp = cfg.espaco_de(u)
    assert esp.real.is_dir() and esp.copia.is_dir() and (esp.real / ".git").is_dir()
    assert "[[utilizadores]]" in toml.read_text() and 'eu@exemplo.pt' not in toml.read_text()   # o exemplo saiu
    rc, out, err = correr("iniciar", "--dados", str(d))
    assert rc != 0 and "já existe" in err                                                       # não reescreve sem --forcar
    rc, out, err = correr("--config", str(toml), "estado")
    assert rc == 0 and "espaço ana" in out and "auth senha" in out


def test_iniciar_aberto_sem_utilizador(tmp_path):
    d = tmp_path / "d"
    rc, out, err = correr("iniciar", "--dados", str(d), "--sem-ia")
    assert rc == 0, err
    cfg = config.carregar(d / "basalt.toml")
    assert cfg.auth.modo == "aberto" and cfg.utilizadores == [] and cfg.ia.fornecedor == "nenhum" and cfg.escutar == "127.0.0.1"
    rc, out, err = correr("iniciar", "--dados", str(d / "x"), "--modo", "senha")
    assert rc != 0                                                                              # senha sem utilizador: validar() recusa


def test_utilizador_novo_senha_token_listar(tmp_path):
    d = tmp_path / "dados"
    assert correr("iniciar", "--dados", str(d), "--email", "ana@exemplo.pt", "--senha", "segredo-forte")[0] == 0
    toml = d / "basalt.toml"
    antes = toml.read_text()
    rc, out, err = correr("--config", str(toml), "utilizador", "novo", "bea@exemplo.pt", "--nome", "Bea", "--senha", "outra-senha-9")
    assert rc == 0, err
    cfg = config.carregar(toml)
    assert [u.email for u in cfg.utilizadores] == ["ana@exemplo.pt", "bea@exemplo.pt"]
    assert not cfg.utilizador("bea@exemplo.pt").admin and cfg.espaco_de(cfg.utilizador("bea@exemplo.pt")).real.is_dir()
    assert correr("--config", str(toml), "utilizador", "novo", "bea@exemplo.pt", "--senha", "outra-senha-9")[0] != 0      # duplicado
    # senha: só muda o hash da Ana; o resto do ficheiro (comentários, IA…) fica como estava
    rc, out, err = correr("--config", str(toml), "utilizador", "senha", "ana@exemplo.pt", "--senha", "nova-senha-123")
    assert rc == 0, err
    cfg = config.carregar(toml)
    assert auth.verificar_senha(cfg.utilizador("ana@exemplo.pt").senha_hash, "nova-senha-123")
    assert auth.verificar_senha(cfg.utilizador("bea@exemplo.pt").senha_hash, "outra-senha-9")
    depois = toml.read_text()
    assert [l for l in antes.splitlines() if l.startswith("#") or l.startswith("[ia")] == [l for l in depois.splitlines() if l.startswith("#") or l.startswith("[ia")]
    # mcp-token: imprime o token uma vez, guarda só o hash
    rc, out, err = correr("--config", str(toml), "utilizador", "mcp-token", "bea@exemplo.pt")
    assert rc == 0, err
    token = re.search(r"(bslt_[A-Za-z0-9_-]+)", out).group(1)
    cfg = config.carregar(toml)
    assert cfg.utilizador("bea@exemplo.pt").mcp_token_hash == auth.hash_token(token) and token not in toml.read_text()
    assert cfg.utilizador("ana@exemplo.pt").mcp_token_hash == ""
    a = auth.Autenticador(cfg)
    assert a.utilizador_do_pedido({"Authorization": "Bearer " + token}).email == "bea@exemplo.pt"
    rc, out, err = correr("--config", str(toml), "utilizador", "mcp-token", "bea@exemplo.pt")      # gerar outro revoga o antigo
    assert rc == 0 and auth.Autenticador(config.carregar(toml)).utilizador_do_pedido({"Authorization": "Bearer " + token}) is None
    rc, out, err = correr("--config", str(toml), "utilizador", "listar")
    assert rc == 0 and "ana@exemplo.pt" in out and "bea@exemplo.pt" in out and "mcp" in out.splitlines()[1]
    assert correr("--config", str(toml), "utilizador", "senha", "zed@exemplo.pt", "--senha", "12345678x")[0] != 0  # desconhecido
    assert correr("--config", str(toml), "utilizador", "senha", "ana@exemplo.pt", "--senha", "curta")[0] != 0        # < 8


def test_definir_chave_no_bloco_certo():
    t = ('[[utilizadores]]\nemail = "a@x.pt"\nespaco = "a"\n# senha_hash = "scrypt$…"\nadmin = true\n\n'
         '[[utilizadores]]\nemail = "b@x.pt"\nespaco = "b"\n\n[ia]\nfornecedor = "nenhum"\n')
    t2 = cli.definir_chave_utilizador(t, "b@x.pt", "mcp_token_hash", "abc")
    bloco_b = t2.split("b@x.pt")[1].split("[ia]")[0]
    assert t2.count("mcp_token_hash") == 1 and '\nmcp_token_hash = "abc"\n' in bloco_b and "mcp_token_hash" not in t2.split("b@x.pt")[0]
    t3 = cli.definir_chave_utilizador(t, "a@x.pt", "senha_hash", "scrypt$1")
    assert '# senha_hash' not in t3 and 'senha_hash = "scrypt$1"' in t3.split("[[utilizadores]]")[1] and "b@x.pt" in t3
    with pytest.raises(KeyError):
        cli.definir_chave_utilizador(t, "c@x.pt", "x", "y")
    t4 = cli.acrescentar_utilizador(t, cli.bloco_utilizador("c@x.pt", "c", "C", False))
    assert t4.index("c@x.pt") > t4.index("b@x.pt") and t4.index("c@x.pt") < t4.index("[ia]")


def test_arranque_para_com_mensagem_clara_se_a_pasta_nao_tem_trocas_atomicas(tmp_path, monkeypatch):
    """19/10/2026: num portátil Windows (/mnt/c no WSL) ou Mac (pasta partilhada com o Docker) a falha era só na 1.ª gravação."""
    rc, out, err = correr("iniciar", "--dados", str(tmp_path / "d"), "--sem-ia")
    assert rc == 0, err                                            # caso bom: disco Linux
    from basalt import cas
    monkeypatch.setattr(cas, "troca_suportada", lambda pasta: "Invalid argument (EINVAL)")
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", str(tmp_path / "d" / "basalt.toml"), "serve"])
    msg = str(e.value)
    assert "não serve para os dados" in msg and "/mnt/c" in msg and "Só com um portátil" in msg


# ── ronda 13: a edição parte sempre do ficheiro ACTUAL (trinco tomado) ──
def _toml_ana_rui(tmp_path, h_ana, h_rui):
    toml = tmp_path / "basalt.toml"
    toml.write_text(f'[basalt]\ndados = "{tmp_path / "dados"}"\nescutar = "127.0.0.1"\ngit = false\n\n[auth]\nmodo = "senha"\n\n'
                    '[ia]\nfornecedor = "nenhum"\n\n'
                    f'[[utilizadores]]\nemail = "ana@x.pt"\nespaco = "ana"\nnome = "Ana"\nsenha_hash = "{h_ana}"\nadmin = true\n\n'
                    f'[[utilizadores]]\nemail = "rui@x.pt"\nespaco = "rui"\nnome = "Rui"\nsenha_hash = "{h_rui}"\nadmin = false\n',
                    encoding="utf-8")
    return toml


@pytest.mark.parametrize("sub", ["senha", "mcp-token"])
def test_utilizador_nao_desfaz_a_mudanca_feita_na_pagina_entretanto(tmp_path, monkeypatch, sub):
    """ronda 13: a CLI lia o basalt.toml, pedia a senha no terminal (podia demorar) e gravava o texto velho — uma senha mudada na
    página entretanto era desfeita sem ninguém dar por isso. Agora lê, edita e grava com o trinco tomado."""
    h_ana, h_rui = auth.hash_senha("senha da ana 1"), auth.hash_senha("senha do rui 1")
    toml = _toml_ana_rui(tmp_path, h_ana, h_rui)
    novo_rui = auth.hash_senha("senha do rui 2")

    def entretanto():                     # o que a página faz quando o Rui muda a senha: só a chave dele, com o trinco
        cli._escrever_toml(toml, cli.definir_chave_utilizador(toml.read_text(encoding="utf-8"), "rui@x.pt", "senha_hash", novo_rui))
    if sub == "senha":
        real = auth.hash_senha
        monkeypatch.setattr(auth, "hash_senha", lambda s: (entretanto(), real(s))[1])   # «enquanto o terminal espera pela senha»
    else:
        real = auth.gerar_token
        monkeypatch.setattr(auth, "gerar_token", lambda: (entretanto(), real())[1])
    cli.main(["--config", str(toml), "utilizador", sub, "ana@x.pt", "--senha", "nova da ana 1"])
    cfg = config.carregar(toml, ambiente={})
    assert cfg.utilizador("rui@x.pt").senha_hash == novo_rui                                  # a mudança da página ficou
    if sub == "senha":
        assert auth.verificar_senha(cfg.utilizador("ana@x.pt").senha_hash, "nova da ana 1")
    else:
        assert cfg.utilizador("ana@x.pt").mcp_token_hash and cfg.utilizador("ana@x.pt").senha_hash == h_ana
    assert not list(tmp_path.glob(".basalt.toml.*"))


def test_utilizador_desconhecido_tambem_dentro_do_trinco(tmp_path):
    """o utilizador que desaparece entre a conferência e a gravação dá o mesmo erro (e não um KeyError solto)."""
    toml = _toml_ana_rui(tmp_path, auth.hash_senha("senha da ana 1"), auth.hash_senha("senha do rui 1"))
    antes = toml.read_text(encoding="utf-8")
    with pytest.raises(SystemExit, match="desconhecido"):
        cli._editar_toml(toml, lambda t: cli.definir_chave_utilizador(t, "zed@x.pt", "senha_hash", "scrypt$1"))
    assert toml.read_text(encoding="utf-8") == antes
