"""docs/GOOGLE.md — o bloco nginx do guia lido como um instalador o copiaria (auditoria de 21/10/2026 ao r14).

Testes ESTÁTICOS sobre o texto do guia (como os de test_instalacao.py), mais um `nginx -t` REAL ao bloco quando há nginx na
máquina. Cada um fixa um defeito encontrado ao montar o guia à letra num nginx de ensaio (oauth2-proxy falso por cookie à
frente de um Basalt real numa porta alta):
  - o include do segredo ia SEM a linha do e-mail: um `location` com o include e sem a substituição do e-mail deixava um
    visitante com sessão entrar como outra pessoa (reproduzido: `/api/eu` devolvia a conta forjada), e os dois curls do §7
    (sem sessão → 302 para o login) não o apanhavam;
  - o `/oauth2/auth` ia sem `proxy_pass_request_body off` / `Content-Length ""` (o subpedido levava o Content-Length do pedido
    original e nenhum corpo — medido no ensaio);
  - o §4 dizia que o histórico git não contava para o `espaco_max_mb` — conta (`Espaco.uso_bytes()` soma `memoria/` inteira).
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from basalt import config

RAIZ = Path(__file__).resolve().parent.parent
GUIA = "docs/GOOGLE.md"
BASALT = "http://127.0.0.1:8121"


def _texto():
    return (RAIZ / GUIA).read_text(encoding="utf-8")


def _blocos(texto, lingua="nginx"):
    return re.findall(r"```" + lingua + r"\n(.*?)```", texto, re.S)


def _server(texto):
    return next(b for b in _blocos(texto) if b.lstrip().startswith("server {"))


def _include(texto):
    return next(b for b in _blocos(texto) if b.lstrip().startswith("# /etc/nginx/basalt-segredo.conf"))


def _locations(server):
    """[(cabeçalho, corpo)] dos `location` do bloco server (um nível, como no guia)."""
    locs = re.findall(r"\n[ \t]+location ([^\n{]+?)\s*\{\n(.*?)\n[ \t]+\}", server, re.S)
    assert len(locs) >= 4, locs
    return locs


def _seccao(texto, titulo):
    ini = texto.index(titulo)
    fim = texto.find("\n## ", ini + 1)
    return texto[ini:fim if fim > 0 else None]


def test_include_traz_o_email_e_o_segredo_juntos():
    """o que abre a porta é um location com o segredo mas sem a substituição do e-mail — no mesmo ficheiro não dá para ter um sem o outro."""
    inc = _include(_texto())
    linhas = [l.strip() for l in inc.splitlines() if l.strip() and not l.strip().startswith("#")]
    assert any(re.fullmatch(r"proxy_set_header\s+X-Basalt-Email\s+\$email;", l) for l in linhas), inc
    assert any(re.match(r"proxy_set_header\s+X-Basalt-Proxy-Segredo\s+\"\S+\";", l) for l in linhas), inc
    assert len(linhas) == 2, linhas                       # só isso: nada de proxy_pass ou outros cabeçalhos escondidos no include


def test_cada_location_para_o_basalt_ou_tem_login_ou_apaga_o_email():
    server = _server(_texto())
    vistos = 0
    for cab, corpo in _locations(server):
        if BASALT not in corpo:
            continue
        vistos += 1
        com_include = "basalt-segredo.conf" in corpo
        apaga = re.search(r'proxy_set_header\s+X-Basalt-Email\s+"";', corpo) is not None
        poe = re.search(r"proxy_set_header\s+X-Basalt-Email\s+\$email;", corpo) is not None
        if com_include:
            assert "auth_request /oauth2/auth;" in corpo and "auth_request_set $email $upstream_http_x_auth_request_email;" in corpo, cab
            assert not apaga and not poe, cab               # o e-mail vem do include, não de uma 2.ª linha que alguém pode apagar
        else:
            assert apaga and not poe, cab                   # rota sem login (IA/MCP): o e-mail vai SEMPRE vazio
        # nunca um valor que não seja o do auth_request ou vazio
        for m in re.finditer(r"proxy_set_header\s+X-Basalt-Email\s+(\S+);", corpo):
            assert m.group(1) in ('""', "$email"), (cab, m.group(0))
        assert "X-Basalt-Proxy-Segredo" not in corpo, cab    # o segredo só pelo include (ficheiro 0600), nunca no ficheiro do site
    assert vistos == 2, vistos                              # location / e o das rotas da IA


def test_oauth2_auth_vai_sem_corpo():
    cab, corpo = next((c, b) for c, b in _locations(_server(_texto())) if c.strip() == "= /oauth2/auth")
    assert "internal;" in corpo
    assert "proxy_pass_request_body off;" in corpo, corpo
    assert re.search(r'proxy_set_header\s+Content-Length\s+"";', corpo), corpo


def test_prova_do_ataque_testa_o_basalt_directo_e_a_sessao_propria():
    s = _seccao(_texto(), "## 7. Prova do ataque")
    bash = "\n".join(_blocos(s, "bash"))
    # 1) o segredo prova-se directo à porta do Basalt (via https sem sessão dá 302 — não prova nada)
    assert bash.count(f"curl -i {BASALT}/api/eu") == 2, bash
    assert 'curl -i https://' not in bash, bash
    # 2) a substituição prova-se COM a sessão própria e o e-mail de outra pessoa
    assert '-b "_oauth2_proxy=' in bash and "X-Basalt-Email: outra-pessoa@" in bash, bash
    js = "\n".join(_blocos(s, "js"))
    assert 'fetch("/api/eu"' in js and '"X-Basalt-Email": "outra-pessoa@' in js, js
    assert "TEU e-mail" in s


def test_tecto_de_espaco_diz_que_o_git_conta(tmp_path):
    t = _seccao(_texto(), "## 4. `basalt.toml`")
    assert "não entra na conta" not in t
    assert "memoria/.git" in t and "copia/" in t
    # e é mesmo assim no código: o .git da memória soma para uso_bytes()
    esp = config.Espaco.de("x", tmp_path / "d")
    esp.criar()
    (esp.real / "a.md").write_bytes(b"a" * 1000)
    antes = esp.uso_bytes()
    assert antes >= 1000
    (esp.real / ".git" / "objects").mkdir(parents=True)
    (esp.real / ".git" / "objects" / "blob").write_bytes(b"g" * 5000)
    assert esp.uso_bytes() == antes + 5000


# ───────────────────────── nginx -t real ─────────────────────────
NGINX = shutil.which("nginx") or ("/usr/sbin/nginx" if Path("/usr/sbin/nginx").is_file() else None)


def _conf_de(pasta: Path, server: str, include: str) -> Path:
    inc = pasta / "basalt-segredo.conf"
    inc.write_text(include.replace("COLA_AQUI_O_MESMO_VALOR", "s" * 40), encoding="utf-8")
    server = server.replace("listen 443 ssl http2;", "listen 127.0.0.1:18999;").replace("/etc/nginx/basalt-segredo.conf", str(inc))
    for d in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi", "logs"):
        (pasta / d).mkdir(exist_ok=True)
    conf = pasta / "nginx.conf"
    conf.write_text(f"pid {pasta}/nginx.pid;\nerror_log {pasta}/logs/error.log warn;\nevents {{ worker_connections 16; }}\n"
                    f"http {{\naccess_log off;\nclient_body_temp_path {pasta}/client_body;\nproxy_temp_path {pasta}/proxy;\n"
                    f"fastcgi_temp_path {pasta}/fastcgi;\nuwsgi_temp_path {pasta}/uwsgi;\nscgi_temp_path {pasta}/scgi;\n{server}\n}}\n",
                    encoding="utf-8")
    return conf


def _nginx_t(conf: Path):
    r = subprocess.run([NGINX, "-t", "-p", str(conf.parent), "-c", str(conf)], capture_output=True, text=True, timeout=30)
    return r.returncode, r.stderr


@pytest.mark.skipif(NGINX is None, reason="sem nginx nesta máquina")
def test_o_bloco_do_guia_passa_no_nginx_t(tmp_path):
    rc, err = _nginx_t(_conf_de(tmp_path, _server(_texto()), _include(_texto())))
    assert rc == 0, err


@pytest.mark.skipif(NGINX is None, reason="sem nginx nesta máquina")
def test_include_sem_auth_request_em_lado_nenhum_nem_configura(tmp_path):
    """falha fechada já no `nginx -t`: o include usa $email, que só existe com o `auth_request_set` do location / — um site que
    copie o include para um location sem login e não tenha o location / não arranca («unknown "email" variable»)."""
    texto = _texto()
    server = _server(texto)
    sem_login = re.sub(r"\n[ \t]+location / \{\n.*?\n[ \t]+\}", "\n    location / {\n        proxy_pass http://127.0.0.1:8121;\n"
                       "        include /etc/nginx/basalt-segredo.conf;\n    }", server, count=1, flags=re.S)
    assert "auth_request" not in sem_login
    rc, err = _nginx_t(_conf_de(tmp_path, sem_login, _include(texto)))
    assert rc != 0 and "email" in err, err
