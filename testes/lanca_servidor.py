"""Lançador mínimo do servidor para os testes: `python testes/lanca_servidor.py basalt.toml` (sem vigias)."""
import sys

from basalt import config, web

if __name__ == "__main__":
    web.servir(config.carregar(sys.argv[1]), vigias=False)
