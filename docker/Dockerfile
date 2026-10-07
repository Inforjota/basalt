# Basalt — imagem mínima: Python 3.12 + git (histórico das notas). Sem dependências pip: só stdlib.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 basalt
WORKDIR /app
COPY basalt/ /app/basalt/
COPY basalt.toml.exemplo /app/
ENV BASALT_CONFIG=/dados/basalt.toml \
    BASALT_DADOS=/dados \
    BASALT_ESCUTAR=0.0.0.0 \
    BASALT_PORTA=8121 \
    PYTHONUNBUFFERED=1
USER basalt
VOLUME ["/dados"]
EXPOSE 8121
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8121/api/estado', timeout=4).status == 200 else 1)"
ENTRYPOINT ["python3", "-m", "basalt"]
CMD ["serve"]
