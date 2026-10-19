# Basalt

**Your notes, with memory.** A Markdown vault (Obsidian-compatible) that searches by meaning, answers with sources, tidies
loose ideas on its own, and lets your AI agents (Claude Code, Codex, Cursor, claude.ai…) read and write **without ever
overwriting** what you wrote. Works offline, in plain Python, on your own machine.

[Português](README.md)

## Why

- **Bring your own AI.** Connect **Claude** (web, Desktop, phone), **ChatGPT**, **Claude Code** and **Codex** to your notes in two
  minutes: the 🔌 **Connect your AI** button → pick the AI → copy the address → **Allow**. No headers to paste: Basalt ships its
  own OAuth 2.1 (PKCE, your account only, revoke any time). Needs a public https address (`url_publico`,
  [docs/INSTALAR.md](docs/INSTALAR.md)); details in [docs/MCP.md](docs/MCP.md).
- **Your files, in Markdown.** `memoria/` is a regular Obsidian vault: open it with Obsidian, `vim`, anything. No proprietary
  database; history is `git`. Already have a vault? `basalt importar` brings notes and attachments in without overwriting
  anything ([docs/OBSIDIAN.md](docs/OBSIDIAN.md)).
- **Semantic search, offline.** With a local embeddings model (Ollama) you can find "that note about the 8 o'clock ferry"
  without matching the words. Without embeddings, search is lexical (BM25) and still works.
- **AI agents that break nothing.** The MCP server gives AIs seven tools; every write requires the `hash` of the version the
  agent read. If the note changed meanwhile, the agent gets the current text and merges — it never overwrites. There is no
  delete tool.
- **Nothing is lost in a race.** Atomic compare-and-swap writes (`renameat2`), a cross-process lock, safety copies of whatever
  leaves in a swap. Adversarially audited by an AI of another family during development.
- **Secrets never get in.** A plaintext password or API key in a note is refused on save.
- **Really offline.** The UI keeps a local copy (service worker + IndexedDB) with per-session locks: edit on your phone with no
  network, sync when it is back; if the session expires, the local copy is locked.
- **Diagrams and formulas** as in Obsidian (Mermaid, KaTeX); a CodeMirror 6 editor with AI (summarise, rewrite, continue —
  and cancel midway). Paste or drop an image or a PDF into the editor and it is stored in `memoria/anexos/` as `![[name]]`;
  `[[links]]` and `#tags` autocomplete from your own notes.
- **Python standard library only.** No required dependencies; `numpy` optional for the semantic index.

## Install in 2 minutes

```bash
git clone https://github.com/REPO_POR_DEFINIR/basalt && cd basalt
python3 -m basalt iniciar --dados ~/basalt-data --email you@example.com   # asks for a password; creates basalt.toml and folders
export BASALT_CONFIG=~/basalt-data/basalt.toml                            # the next commands find the file through this
python3 -m basalt serve                                                   # http://127.0.0.1:8121
```

Docker: run `iniciar` first, then `docker compose up -d` (the container needs the `basalt.toml`). As a systemd service:
`sistema/basalt.service`. The exact steps, the HTTPS proxy and local AI are in [docs/INSTALAR.md](docs/INSTALAR.md) (Portuguese).

Requirements: Python ≥ 3.11, `git` (history), Linux (CAS uses `renameat2`). Optional: Ollama for local embeddings and AI.

## Configure

Everything in one `basalt.toml` ([commented example](basalt/basalt.toml.exemplo)): data folder, port, auth mode (`senha` = password
form · `cabecalho` = header from an SSO proxy you already have · `aberto` = open, localhost only), users (each with its own
space), AI (`openai`-compatible such as Ollama/OpenRouter/Mistral, `anthropic`, or the `claude` CLI), embeddings, categories,
Inbox. API keys **never** go in the file: environment variable or a `0600` file only.

```bash
python3 -m basalt utilizador novo ana@example.com --nome Ana       # + password
python3 -m basalt utilizador mcp-token ana@example.com             # token for agents (shown once)
python3 -m basalt indexar                                          # (re)build the semantic index
python3 -m basalt importar ~/My-vault --simular                    # bring an Obsidian vault in (dry run first)
python3 -m basalt exportar ~/exported-vault                        # take the memory out to a folder (opens in Obsidian)
python3 -m basalt arrumar --auto                                   # Inbox → notes, links, categories (what the nightly timer runs; --simular only shows)
```

The CLI and the config keys are in Portuguese (the author's language), but **the web UI is available in English**. Turn it on in
`basalt.toml`:

```toml
[basalt]
idioma = "en"      # "pt" (default) · "en" · "auto" (follows the browser's Accept-Language)
```

or with the environment variable `BASALT_IDIOMA=en` (it wins over the file). After changing it, **reload the page**: the UI is
cached by a service worker, so the new language only shows up after a reload. Limits: error messages coming from the server
API stay in Portuguese, and the quick-save prefix in the command bar is still `guarda:`.

## Your AI agents

```bash
claude mcp add basalt -- python3 -m basalt --config ~/basalt-data/basalt.toml mcp --utilizador ana@example.com
```

Then, in Claude Code: "search my notes for what I decided about the server", "append to note `project_x` that …".
Claude (web/phone) and ChatGPT over OAuth, Codex and Cursor over HTTP with a token, and the conflict protocol: [docs/MCP.md](docs/MCP.md).

## Layout

```
basalt/        config · auth · cas · duasvias (two-way sync) · sig (search) · ia · lexical · unir · arrumar · web · mcp
static/        the UI (one HTML file, service worker)
testes/        pytest (+ Chromium for the UI)
docs/          INSTALAR · MCP · SEGURANCA
docker/  sistema/
```

Per user space: `memoria/` (the vault, git), `copia/` (the editable copy the UI uses), `dados/` (index, Inbox, ledger, CAS
safety copies). `memoria ↔ copia` sync runs both ways with CAS; conflicts are marked, never resolved blindly.

## Security, in two lines

Listens on `127.0.0.1` by default; for the network, put an HTTPS proxy in front. HttpOnly/SameSite=Strict sessions, scrypt,
per-IP attempt limit. MCP over HTTP accepts `Authorization: Bearer` only (never the cookie — a third-party site in your browser
cannot write to your notes). More in [docs/SEGURANCA.md](docs/SEGURANCA.md); giving others access? [docs/PRIVACIDADE.md](docs/PRIVACIDADE.md) (GDPR, notice template, PT). Found something? Open a private issue.

## Licence

MIT — see [LICENCA](LICENCA). Made in the Azores by [Inforjota](https://inforjota.com).
