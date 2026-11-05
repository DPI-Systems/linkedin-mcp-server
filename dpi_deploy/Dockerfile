# The upstream image plus the DPI tools.
#
# Upstream's Dockerfile builds the base unchanged (build.sh tags it
# dpi/linkedin-mcp-base), so pulling upstream never conflicts with this file.
# The base already runs headed Chromium on a virtual display and carries the
# noVNC login viewer; all this adds is dpi_salesnav and its entrypoint.
ARG BASE=dpi/linkedin-mcp-base:local
FROM ${BASE}

COPY --chown=pwuser:pwuser dpi_salesnav /app/dpi_salesnav

# Same supervisor as upstream (Xvfb, then the server); only the module differs.
# `python -m` puts the working directory /app on sys.path, so dpi_salesnav is
# importable without being part of the installed package.
ENTRYPOINT ["tini", "-e", "143", "--", "linkedin-mcp-entrypoint", "python", "-m", "dpi_salesnav"]
CMD ["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
