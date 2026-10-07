# Basalt — imagem mínima: Python 3.12 + git (histórico das notas). Sem dependências pip: só stdlib.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 basalt
WORKDIR /app
COPY basalt/ /app/basalt/
# Dentro do contentor o servidor escuta em 0.0.0.0 (senão a porta publicada não chega lá); o compose só a publica em
# 127.0.0.1. Por isso a imagem exige login ([auth] modo = "senha", criado por `iniciar`) — arranque do zero em docker/compose.yml.
ENV BASALT_CONFIG=/dados/basalt.toml \
    BASALT_DADOS=/dados \
    BASALT_ESCUTAR=0.0.0.0 \
    BASALT_PORTA=8121 \
    PYTHONUNBUFFERED=1
USER basalt
VOLUME ["/dados"]
EXPOSE 8121
# /api/saude é pública (sem login) e só diz {"ok": true}; o /api/estado exige sessão e daria sempre «unhealthy»
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8121/api/saude', timeout=5).status==200 else 1)"
ENTRYPOINT ["python3", "-m", "basalt"]
CMD ["serve"]
