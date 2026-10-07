"""Ronda 8 (segredos em todas as entradas) e ronda 7 (contexto de validade das datas): UM guarda de passwords/chaves para TODAS as entradas (basalt.segredos).

Antes: a web tinha o guarda inteiro; a sincronização em duas vias só via formatos de chave; o MCP `guardar_ideia`, o
`basalt.sig guarda`, a importação Obsidian e os anexos de texto não verificavam nada. E o contexto de validade aceitava
qualquer «data»/«dia» nos 80 caracteres anteriores («data: 06/10 — senha: 2026-10-06» passava).
Cada teste falha na cópia anterior (5347bc6)."""
import os
import subprocess
import sys

import pytest

from basalt import anexos, duasvias, obsidian, sig, unir, web
from conftest import RAIZ
from test_mcp import amb, ferramenta          # noqa: F401  (fixture reutilizada)
from test_obsidian import esp                 # noqa: F401

PASSWORD = "senha do router: Tr0ub4dor&3"
CASOS = [
    # achado 4: a frase tem de falar de validade — não basta «data»/«dia» algures antes
    ("data: 06/10 — senha: 2026-10-06", True), ("dia de instalação: terça; password: 10:30:59", True),
    ("Validade: 2027; senha: 2026-10-06", True), ("A senha nova: 2026-10-06. A validade é de um ano.", True),
    ("Validade da senha: 2026-10-06", False), ("a senha expira: 06/10/2026", False), ("senha válida até: 2026-10-06", False),
    ("password expires: 2026-10-06", False), ("senha renovada em: 06/10/2026", False), ("password: 09h30 de amanhã", False),
    ("senha: 2026-10-06", True), ("password: 10:30:59", True), ("nota\nsenha: 06/10/2026\n", True),
    # a palavra tem de falar de PRAZO: «válida», «renovada», «valid» sozinhas não chegam
    ("a senha válida: 2026-10-06", True), ("senha renovada: 06/10/2026", True), ("valid password: 10:30:59", True),
    ("senha válida a partir de: 2026-10-06", False), ("password valid until: 2026-10-06", False),
    ("próxima renovação da senha: 2026-12-01", False), ("password rotation: 2026-12-01", False),
    # ronda 8 (segredos em todas as entradas): formatos de chave que faltavam
    ("chave: sk-proj-" + "a" * 30, True), ("token bslt_" + "b" * 35, True), ("github_pat_" + "c" * 35, True),
    # ronda 8 (segredos em todas as entradas): password só de letras
    ("senha: minhapalavrasecreta", True), ("password: correcthorsebatterystaple", True), ("senha: TopSecreto", True),
    ("senha: alterada", False), ("password: unchanged", False), ("senha: desconhecida", False),
    ("senha: alterada ontem pelo administrador", False), ("Mudei a password: agora é só por chave", False),
    # continua a poder falar-se de passwords sem as escrever
    ("senha do router: está no cofre", False), ("password: ver /root/cofre", False), ("password: ver_no_cofre", False),
    ("senha do NAS: https://cofre.exemplo.pt/cofre", False), ("mudar a password todos os meses", False),
    (PASSWORD, True), ("password = hunter2!", True), ("senha: 2026-10-06x7", True),
]


@pytest.mark.parametrize("linha, e_segredo", CASOS, ids=[c[0][:40] for c in CASOS])
def test_padroes(linha, e_segredo):
    assert web.tem_segredo_texto(linha) is e_segredo


def test_o_mesmo_padrao_em_todas_as_entradas():
    from basalt import segredos
    assert duasvias.SEGREDO_RX is web.SEGREDO_RX is unir.SEGREDO_RX is segredos.SEGREDO_RX
    assert web.tem_segredo_texto is segredos.tem_segredo_texto


def test_duas_vias_ve_passwords_escritas_a_mao(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("# nota\n" + PASSWORD + "\n", encoding="utf-8")
    assert duasvias.tem_segredo(p) is True
    assert duasvias.tem_segredo_bytes(b"senha: esta no cofre\n", "n.md") is False


def test_mcp_guardar_ideia_recusa_segredo_e_texto_em_branco(amb):          # noqa: F811
    srv, u, esp_ = amb["srv"], amb["u"], amb["esp"]
    d, erro = ferramenta(srv, u, "guardar_ideia", texto=PASSWORD)
    assert erro and "password ou chave" in d["erro"]
    d, erro = ferramenta(srv, u, "guardar_ideia", texto="   \n ")
    assert erro and "texto" in d["erro"]
    assert not esp_.inbox.exists() or "Tr0ub4dor" not in esp_.inbox.read_text(encoding="utf-8")
    d, erro = ferramenta(srv, u, "guardar_ideia", texto="senha: alterada ontem")
    assert not erro and d["ok"] and "alterada" in esp_.inbox.read_text(encoding="utf-8")


def test_terminal_guarda_recusa_segredo(memoria, capsys):
    with pytest.raises(ValueError, match="password ou chave"):
        sig.guardar(PASSWORD)
    assert not memoria.esp.inbox.exists()
    env = {**os.environ, **memoria.esp.ambiente()}
    r = subprocess.run([sys.executable, "-m", "basalt.sig", "guarda", PASSWORD], cwd=str(RAIZ), env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "password ou chave" in r.stderr and "guardado" not in r.stdout
    assert not memoria.esp.inbox.exists()


def test_importar_obsidian_salta_nota_com_segredo(esp, tmp_path):          # noqa: F811
    cofre = tmp_path / "cofre"
    cofre.mkdir()
    (cofre / "Limpa.md").write_text("# Limpa\n\nsenha: está no cofre\n", encoding="utf-8")
    (cofre / "Router.md").write_text("# Router\n\n" + PASSWORD + "\n", encoding="utf-8")
    r = obsidian.importar(cofre, esp)
    assert r["importadas"] == ["limpa"]
    assert ("Router.md", "tem o que parece uma password ou chave em claro") in r["saltadas"]
    assert not (esp.real / "router.md").exists()


def test_anexos_de_texto_passam_pelo_guarda(esp):                           # noqa: F811
    with pytest.raises(ValueError, match="password ou chave"):
        anexos.guardar(esp, "acessos.txt", ("# acessos\n" + PASSWORD + "\n").encode())
    with pytest.raises(ValueError, match="password ou chave"):
        anexos.guardar(esp, "dados.csv", b"nome,chave\nx,sk-ant-api03-" + b"a" * 40 + b"\n")
    assert not list(esp.anexos.glob("*")) if esp.anexos.exists() else True
    # controlo: um binário não é lido como texto corrido (sem PASSWORD_RX), mas um formato de chave conta em qualquer anexo
    assert anexos.guardar(esp, "foto.png", b"\x89PNG\r\n" + PASSWORD.encode()) == "foto.png"
    with pytest.raises(ValueError, match="password ou chave"):
        anexos.guardar(esp, "foto2.png", b"\x89PNG\r\nsk-ant-api03-" + b"a" * 40)


def test_api_guarda_recusa_password_so_de_letras(servidor_local):
    s = servidor_local()
    r = s.pedido("POST", "/api/guarda", {"texto": "senha: minhapalavrasecreta"})
    assert r.codigo == 400 and "password ou chave" in r.json["erro"]
    r = s.pedido("POST", "/api/guarda", {"texto": "senha: alterada"})
    assert r.codigo < 300
