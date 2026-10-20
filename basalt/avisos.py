"""basalt.avisos — os ALERTAS (duas vias, verificador de segredos…) no stderr e, se quiseres, num comando teu.

Com `[avisos] comando = ["comando", "arg"]` no basalt.toml, cada ALERTA corre também `comando arg "mensagem"` (uma notificação
no telemóvel, um email…). Corre em fundo — quem dá o alerta não espera —, com um ambiente mínimo (sem as chaves do processo) e
um tempo limite curto; se falhar, fica só uma linha no stderr. A mensagem é a mesma do ALERTA, com o que pareça uma chave ou
password tapado. Sem `[avisos]`, fica tudo como antes: só o `ALERTA:` no stderr (o journal do systemd ou o cron guardam-no).

    python -m basalt.avisos SEGUNDOS COMANDO… MENSAGEM     # (uso interno) corre o comando com tempo limite e regista a falha
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from . import config
from .segredos import mascarar

TEMPO_LIMITE = 10


def _comando() -> tuple:
    """o [avisos] comando da configuração (vazio = desligado). Ler a configuração nunca impede o ALERTA de sair no stderr."""
    try:
        c = getattr(config.carregar(validar=False), "avisos", None)
    except Exception as e:
        print(f"aviso: não li [avisos] da configuração ({type(e).__name__})", file=sys.stderr)
        return ()
    cmd = getattr(c, "comando", ())
    if cmd and not config.comando_ok(cmd):
        print("aviso: [avisos] comando tem de ser uma lista de textos — não o corri", file=sys.stderr)
        return ()
    return tuple(cmd or ())


def alerta(msg) -> None:
    """o ALERTA no stderr e, se houver [avisos] comando, também nele (em fundo)."""
    m = mascarar(str(msg))
    print("ALERTA:", m, file=sys.stderr, flush=True)
    cmd = _comando()
    if cmd:
        enviar(cmd, m)


def enviar(cmd: tuple, mensagem: str) -> None:
    """lança `cmd mensagem` noutro processo e volta logo. O processo intermédio aplica o tempo limite e regista a falha —
    assim o aviso sai mesmo quando quem o deu (o `2vias` do cron) acaba antes dele."""
    raiz = str(Path(__file__).resolve().parent.parent)
    pp = os.environ.get("PYTHONPATH", "")
    env = config.ambiente_minimo({"PYTHONPATH": raiz + (os.pathsep + pp if pp else "")})
    try:
        p = subprocess.Popen([sys.executable, "-m", "basalt.avisos", str(TEMPO_LIMITE), *cmd, mensagem], env=env, cwd=raiz,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        print(f"aviso: o comando de avisos não arrancou ({type(e).__name__})", file=sys.stderr)
        return
    threading.Thread(target=p.wait, daemon=True).start()        # num processo longo (o servidor) não fica um zombie


def _correr(argv: list) -> int:
    segundos, *cmd = argv
    try:
        r = subprocess.run(cmd, env=config.ambiente_minimo(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=int(segundos))
    except subprocess.TimeoutExpired:
        print(f"aviso: o comando de avisos não acabou em {segundos} s", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"aviso: o comando de avisos não correu ({type(e).__name__})", file=sys.stderr)
        return 1
    if r.returncode != 0:
        print(f"aviso: o comando de avisos saiu com código {r.returncode}", file=sys.stderr)
    return r.returncode


if __name__ == "__main__":
    sys.exit(_correr(sys.argv[1:]) if len(sys.argv) >= 4 else 2)
