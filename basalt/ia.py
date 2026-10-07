"""basalt.ia — um só sítio para falar com a IA: chat (resumir, fundir notas, arrumar) e embeddings (pesquisa por significado).

Fornecedores ([ia] fornecedor): nenhum · openai (qualquer API compatível: Ollama, OpenRouter, Mistral, LM Studio, vLLM…)
· anthropic (Messages API) · claude-cli (o `claude -p` instalado na máquina, isolado: sem ferramentas nem definições).
Embeddings ([ia.embeddings] fornecedor): ollama (/api/embeddings) · openai (/embeddings) · nenhum → pesquisa lexical (BM25).
A chave só entra por variável de ambiente (`chave_env`) ou ficheiro 0600 (`chave_ficheiro`); nunca aparece em erros nem registos.
Só stdlib; sem rede fora dos endereços configurados.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from .config import IAConfig
from .segredos import SEGREDO_RX


class IAIndisponivel(RuntimeError):
    """não há fornecedor configurado, ou ele não respondeu — quem chama decide o que fazer sem IA."""


def sem_cerca(txt: str) -> str:
    return re.sub(r"^```(?:markdown|md|json)?\n|\n```$", "", (txt or "").strip())


def _chave(nome_env: str, ficheiro: str) -> str:
    if ficheiro:
        p = Path(ficheiro).expanduser()
        try:
            if p.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise IAIndisponivel(f"a chave em {p} pode ser lida por outros utilizadores (chmod 600)")
            return p.read_text(encoding="utf-8").strip()
        except OSError as e:
            raise IAIndisponivel(f"não consigo ler a chave em {p}: {e.strerror}")
    return (os.environ.get(nome_env) or "").strip() if nome_env else ""


class _SemRedirect(urllib.request.HTTPRedirectHandler):
    """nunca seguimos um redirect: o urllib de série repete o Authorization/x-api-key no endereço novo, e a chave iria parar
    a outro servidor. Devolver None faz o 3xx chegar como HTTPError, tratado em _post_json."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_abrir = urllib.request.build_opener(_SemRedirect()).open
_REDIRECTS = {301, 302, 303, 307, 308}
_CREDENCIAL = ("auth", "key", "token", "secret")


def _mascarar(texto: str, cabecalhos: dict) -> str:
    """o texto de um erro sem credenciais: os valores dos cabeçalhos de autenticação (inteiros ou só o token a seguir a
    «Bearer») e tudo o que pareça uma chave pela forma."""
    for k, v in cabecalhos.items():
        if not any(x in k.lower() for x in _CREDENCIAL):
            continue
        for parte in {str(v), str(v).split(" ", 1)[-1]}:
            if len(parte) >= 8:
                texto = texto.replace(parte, "•••")
    return SEGREDO_RX.sub("•••", texto)


def _mensagem_http(bruto: bytes) -> str:
    """a parte útil de um corpo de erro: `error.message` / `error` / `message` se for JSON; senão a 1.ª linha de texto sem
    marcação. Nunca o corpo inteiro — um proxy mal configurado pode ecoar os cabeçalhos do pedido."""
    texto = bruto.decode("utf-8", "replace")
    try:
        j = json.loads(texto)
        e = j.get("error", j) if isinstance(j, dict) else None
        m = e.get("message", e.get("error")) if isinstance(e, dict) else e
        texto = m if isinstance(m, str) else ""
    except ValueError:
        texto = re.sub(r"<[^>]*>", " ", texto)
    return " ".join(texto.split())[:120]


def _post_json(url: str, corpo: dict, cabecalhos: dict, tempo: int) -> dict:
    dados = json.dumps(corpo).encode("utf-8")
    req = urllib.request.Request(url, data=dados, headers={"Content-Type": "application/json", **cabecalhos}, method="POST")
    origem = url.split("/v1")[0]
    try:
        with _abrir(req, timeout=tempo) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in _REDIRECTS:
            raise IAIndisponivel(_mascarar(f"{origem}: HTTP {e.code} — redireccionou para outro endereço e não o sigo (a chave iria "
                                           f"com o pedido). Põe em [ia] url o endereço final.", cabecalhos))
        raise IAIndisponivel(_mascarar(f"{origem}: HTTP {e.code} {_mensagem_http(e.read(2000))}".rstrip(), cabecalhos))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise IAIndisponivel(_mascarar(f"sem resposta de {url}: {getattr(e, 'reason', e)}", cabecalhos))
    except ValueError:
        raise IAIndisponivel(f"{url}: resposta não é JSON")


_global = None


def definir(ia_) -> None:
    """a IA do processo (o servidor e os testes põem-na aqui; None = voltar a ler o basalt.toml na próxima vez)."""
    global _global
    _global = ia_


def actual() -> "IA":
    """a IA do processo, construída do basalt.toml à primeira chamada. Usada pelos módulos de linha de comandos (sig,
    categorias, unir, arrumar…) para não ser cada um a ler a configuração."""
    global _global
    if _global is None:
        from .config import carregar
        _global = IA(carregar(validar=False).ia)
    return _global


class IA:
    def __init__(self, cfg: IAConfig):
        self.cfg = cfg

    # ── estado ──
    @property
    def chat_activo(self) -> bool:
        return self.cfg.fornecedor != "nenhum"

    @property
    def embeddings_activos(self) -> bool:
        return self.cfg.embeddings_activos()

    @property
    def modelo(self) -> str:
        return self.cfg.modelo_chat or {"claude-cli": "claude (omissão do CLI)", "anthropic": "claude-sonnet-5-5"}.get(self.cfg.fornecedor, "")

    def descricao(self) -> dict:
        return {"chat": self.cfg.fornecedor, "modelo": self.modelo if self.chat_activo else "",
                "embeddings": (self.cfg.embeddings_fornecedor or ("openai" if self.cfg.fornecedor == "openai" else "nenhum")),
                "modelo_embeddings": self.cfg.embeddings_modelo if self.embeddings_activos else ""}

    # ── chat ──
    def chat(self, prompt: str, sistema: str = "", json_: bool = False, tempo: int | None = None) -> str:
        """uma pergunta, uma resposta em texto (já sem ``` à volta). json_=True pede JSON ao fornecedor que o suporte."""
        if not self.chat_activo:
            raise IAIndisponivel("IA desligada ([ia] fornecedor = \"nenhum\")")
        tempo = tempo or self.cfg.tempo_limite
        f = self.cfg.fornecedor
        if f == "openai":
            return self._openai(prompt, sistema, json_, tempo)
        if f == "anthropic":
            return self._anthropic(prompt, sistema, json_, tempo)
        if f == "claude-cli":
            return self._claude_cli(prompt, sistema, json_, tempo)
        raise IAIndisponivel(f"fornecedor desconhecido: {f}")

    def chat_json(self, prompt: str, sistema: str = "", tempo: int | None = None):
        txt = self.chat(prompt, sistema, json_=True, tempo=tempo)
        try:
            return json.loads(sem_cerca(txt))
        except ValueError:
            m = re.search(r"\{.*\}|\[.*\]", txt, flags=re.S)
            if not m:
                raise IAIndisponivel("a IA não respondeu em JSON")
            try:
                return json.loads(m.group(0))
            except ValueError:
                raise IAIndisponivel("a IA não respondeu em JSON")

    def _openai(self, prompt, sistema, json_, tempo):
        if not self.cfg.modelo_chat:
            raise IAIndisponivel("[ia] modelo_chat em falta")
        msgs = ([{"role": "system", "content": sistema}] if sistema else []) + [{"role": "user", "content": prompt}]
        corpo = {"model": self.cfg.modelo_chat, "messages": msgs, "temperature": 0}
        if json_:
            corpo["response_format"] = {"type": "json_object"}
        cab = {}
        k = _chave(self.cfg.chave_env, self.cfg.chave_ficheiro)
        if k:
            cab["Authorization"] = "Bearer " + k
        r = _post_json(self.cfg.url.rstrip("/") + "/chat/completions", corpo, cab, tempo)
        try:
            return sem_cerca(r["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError):
            raise IAIndisponivel("resposta inesperada do fornecedor (sem choices[0].message.content)")

    def _anthropic(self, prompt, sistema, json_, tempo):
        k = _chave(self.cfg.chave_env, self.cfg.chave_ficheiro)
        if not k:
            raise IAIndisponivel(f"sem chave: define a variável {self.cfg.chave_env} (ou [ia] chave_ficheiro)")
        corpo = {"model": self.cfg.modelo_chat or "claude-sonnet-5-5", "max_tokens": 8192, "temperature": 0,
                 "messages": [{"role": "user", "content": prompt + ("\nResponde APENAS com JSON válido." if json_ else "")}]}
        if sistema:
            corpo["system"] = sistema
        r = _post_json((self.cfg.url or "https://api.anthropic.com").rstrip("/") + "/v1/messages", corpo,
                       {"x-api-key": k, "anthropic-version": "2023-06-01"}, tempo)
        try:
            return sem_cerca("".join(b.get("text", "") for b in r["content"] if b.get("type") == "text"))
        except (KeyError, TypeError):
            raise IAIndisponivel("resposta inesperada da Anthropic")

    def _claude_cli(self, prompt, sistema, json_, tempo):
        cmd = ["claude", "-p", "--setting-sources", "", "--tools", "", "--strict-mcp-config", "--output-format", "json"]
        if self.cfg.modelo_chat:
            cmd += ["--model", self.cfg.modelo_chat]
        if sistema:
            cmd += ["--append-system-prompt", sistema]
        if json_:
            prompt += "\nResponde APENAS com JSON válido, sem ``` à volta."
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=tempo, cwd="/tmp")
        except FileNotFoundError:
            raise IAIndisponivel("o comando `claude` não está instalado nesta máquina")
        except subprocess.TimeoutExpired:
            raise IAIndisponivel(f"o `claude` não respondeu em {tempo} s")
        try:
            d = json.loads(r.stdout or "{}")
        except ValueError:
            raise IAIndisponivel("o `claude` respondeu algo que não é JSON")
        if d.get("is_error") or r.returncode != 0:
            raise IAIndisponivel("o `claude` devolveu erro: " + str(d.get("result") or r.stderr)[:200])
        return sem_cerca(d.get("result") or "")

    # ── embeddings ──
    def embeddings(self, textos: list[str], tempo: int | None = None) -> list[list[float]]:
        """um vector por texto, pela mesma ordem. Sem fornecedor → IAIndisponivel (o índice passa ao lexical)."""
        if not self.embeddings_activos:
            raise IAIndisponivel("sem fornecedor de embeddings")
        tempo = tempo or self.cfg.tempo_limite
        f = self.cfg.embeddings_fornecedor or "openai"
        url = (self.cfg.embeddings_url or self.cfg.url).rstrip("/")
        k = _chave(self.cfg.embeddings_chave_env or self.cfg.chave_env, self.cfg.chave_ficheiro)
        cab = {"Authorization": "Bearer " + k} if k else {}
        if f == "ollama":
            base = re.sub(r"/api(/embeddings|/embed)?$|/v1$", "", url)
            out = []
            for t in textos:
                r = _post_json(base + "/api/embeddings", {"model": self.cfg.embeddings_modelo, "prompt": t}, cab, tempo)
                v = r.get("embedding")
                if not v:
                    raise IAIndisponivel("o Ollama não devolveu embedding (modelo %r instalado? `ollama pull`)" % self.cfg.embeddings_modelo)
                out.append(v)
            return out
        if f == "openai":
            r = _post_json(url + "/embeddings", {"model": self.cfg.embeddings_modelo, "input": textos}, cab, tempo)
            try:
                dados = sorted(r["data"], key=lambda d: d.get("index", 0))
                return [d["embedding"] for d in dados]
            except (KeyError, TypeError):
                raise IAIndisponivel("resposta inesperada do fornecedor de embeddings")
        raise IAIndisponivel(f"fornecedor de embeddings desconhecido: {f}")
