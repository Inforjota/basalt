"""r23 (auditoria Fable 12/11/2026): o fornecedor `claude-cli` também passa à [ia.reserva] quando NÃO RESPONDE.

O CHANGELOG r23 e o INSTALAR.md prometem a reserva «quando o de [ia] ou de uma tarefa não responde (tempo esgotado, sem
ligação, HTTP 5xx ou 429)». No 104, [ia.unir] e [ia.arrumar] são `claude-cli`: um `claude` pendurado até ao tempo limite
tem de ir à reserva como vai a ponte. Um erro de CONFIGURAÇÃO do `claude` (não instalado, sem sessão) continua a não ir,
como um 4xx. Nenhum processo real é lançado: o `subprocess.run` é substituído.
"""
import subprocess

import pytest

from basalt import ia
from basalt.config import IAConfig
from basalt.ia import IA, IAIndisponivel, IASemResposta

OLLAMA_OK = {"choices": [{"message": {"content": "resposta do ollama"}}]}
RESERVA = {"fornecedor": "openai", "url": "http://ollama-falso:11434/v1", "modelo_chat": "qwen-falso"}


class Rede:
    """substitui o ia._post_json: regista (url, corpo, cabeçalhos, tempo) e responde pela lista (excepção ou dict)."""

    def __init__(self, *respostas):
        self.respostas, self.pedidos = list(respostas), []

    def __call__(self, url, corpo, cab, tempo):
        self.pedidos.append((url, corpo, dict(cab), tempo))
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def claude_pendurado(monkeypatch):
    """o `claude -p` nunca responde: o subprocess.run esgota o tempo. Devolve a lista de chamadas (cmd, timeout)."""
    chamadas = []

    def run(cmd, **kw):
        chamadas.append((list(cmd), kw.get("timeout")))
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(ia.subprocess, "run", run)
    return chamadas


def test_claude_cli_sem_resposta_e_iasemresposta(monkeypatch):
    claude_pendurado(monkeypatch)
    with pytest.raises(IASemResposta, match="não respondeu em 30 s"):
        IA(IAConfig(fornecedor="claude-cli", modelo_chat="sonnet", tempo_limite=30)).chat("olá")


def test_claude_cli_sem_resposta_vai_a_reserva(monkeypatch):
    chamadas = claude_pendurado(monkeypatch)
    rede = Rede(OLLAMA_OK)
    monkeypatch.setattr(ia, "_post_json", rede)
    x = IA(IAConfig(fornecedor="claude-cli", modelo_chat="sonnet", tempo_limite=30, reserva=dict(RESERVA)))
    assert x.chat("olá", sistema="sê breve") == "resposta do ollama"
    assert len(chamadas) == 1 and chamadas[0][1] == 30, "o claude foi chamado uma vez, com o tempo do principal"
    url, corpo, cab, tempo = rede.pedidos[0]
    assert url.startswith("http://ollama-falso:11434/v1") and corpo["model"] == "qwen-falso"
    assert tempo == 30, "a reserva sem tempo próprio usa o do chamador"
    assert "Authorization" not in cab, "a reserva não leva chave nenhuma do principal"


def test_tarefa_claude_cli_sem_resposta_vai_a_reserva(monkeypatch):
    """espelha o basalt.toml do 104: [ia] anthropic pela ponte, [ia.unir] claude-cli, [ia.reserva] qwen no Ollama."""
    claude_pendurado(monkeypatch)
    rede = Rede(OLLAMA_OK)
    monkeypatch.setattr(ia, "_post_json", rede)
    monkeypatch.setenv("CHAVE_PONTE_TESTE", "ponte-teste-nao-e-segredo")
    cfg = IAConfig(fornecedor="anthropic", url="http://ponte-falsa:8140", chave_env="CHAVE_PONTE_TESTE",
                   modelo_chat="claude-sonnet-5-5", tempo_limite=30,
                   tarefas={"unir": {"fornecedor": "claude-cli", "modelo_chat": "sonnet"}}, reserva=dict(RESERVA))
    assert IA(cfg).para("unir").chat("une estas notas", tempo=600) == "resposta do ollama"
    assert rede.pedidos[0][3] == 600, "o unir pede 600 s; a reserva herda esse tempo"


def test_claude_cli_sem_reserva_continua_a_dizer_o_de_sempre(monkeypatch):
    claude_pendurado(monkeypatch)
    with pytest.raises(IAIndisponivel, match="não respondeu em 30 s"):
        IA(IAConfig(fornecedor="claude-cli", tempo_limite=30)).chat("olá")


@pytest.mark.parametrize("falha,texto", [
    (FileNotFoundError("claude"), "não está instalado"),
    (subprocess.CompletedProcess([], 1, stdout='{"is_error": true, "result": "Not logged in"}', stderr=""), "não tem sessão iniciada"),
    (subprocess.CompletedProcess([], 1, stdout='{"is_error": true, "result": "Credit balance is too low"}', stderr=""), "sem saldo"),
])
def test_erro_de_configuracao_do_claude_nao_vai_a_reserva(monkeypatch, falha, texto):
    def run(cmd, **kw):
        if isinstance(falha, Exception):
            raise falha
        return falha

    monkeypatch.setattr(ia.subprocess, "run", run)
    rede = Rede(OLLAMA_OK)
    monkeypatch.setattr(ia, "_post_json", rede)
    with pytest.raises(IAIndisponivel, match=texto) as x:
        IA(IAConfig(fornecedor="claude-cli", tempo_limite=30, reserva=dict(RESERVA))).chat("olá")
    assert not isinstance(x.value, IASemResposta)
    assert rede.pedidos == [], "um erro de configuração não se esconde na reserva (como um 4xx)"
