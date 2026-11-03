# DPI Systems: extensión Sales Navigator

Fork de [stickerdaniel/linkedin-mcp-server](https://github.com/stickerdaniel/linkedin-mcp-server).
Todo lo nuestro vive en `dpi_salesnav/` y en este fichero. **No se edita ningún fichero de upstream**,
así que traer cambios del original no produce conflictos.

## Cómo encaja

`python -m dpi_salesnav` arranca el CLI de upstream (`cli_main.main()`, mismos flags) después de
envolver `create_mcp_server`. Cada servidor que construye recibe las herramientas de upstream más las
nuestras. El modo daemon está apagado por defecto. Si alguien lo activa (`--daemon`), el proceso
owner se crea con el entrypoint de upstream y no tendrá las herramientas de Sales Nav.

| Fichero | Qué hace |
|---|---|
| `dpi_salesnav/__main__.py` | Entrypoint; registra las herramientas sobre el servidor de upstream |
| `dpi_salesnav/capture.py` | Escucha pasiva de las respuestas `/sales-api/` que la propia página pide (mismo patrón que `linkedin/conversations.py`; nunca hacemos peticiones propias a la API) |
| `dpi_salesnav/people.py` | Empresa → personas (búsqueda de leads de Sales Nav) |
| `dpi_salesnav/inmail.py` | Envío de InMail desde la ficha del lead |
| `dpi_salesnav/tools.py` | Registro MCP |

## Herramientas

- **`salesnav_company_people(company, keywords?, max_results=25)`**: solo lectura. `company` acepta un slug
  (`sorigue`), una URL o el id numérico (`1205762`). Con slug cuesta una carga de página más para obtener el id.
  Cada página de 25 resultados es una navegación, con 4-9 s de pausa entre páginas. Por lead devuelve:
  `full_name`, `title`, `company`, `tenure_at_company`, `tenure_at_position`, `position_started_on`,
  `location`, `seniority`, `degree`, `recent_activity`, `badges`, `summary`, `open_link` y `sales_nav_url`.
- **`salesnav_send_inmail(lead_url, subject, body, confirm_send=false)`**: escritura.
  - Sin `confirm_send` es una vista previa: escribe el texto, comprueba el botón de enviar, lo borra y cierra el formulario.
  - Con `confirm_send=true` envía y lo confirma con la respuesta POST de Sales Nav y el formulario vacío.
  - Tope diario por cuenta en `DPI_SALESNAV_INMAIL_DAILY_CAP` (por defecto 15), con registro en `~/.dpi-salesnav/`.
  - `status: "unconfirmed"` significa que se pulsó enviar pero no hubo confirmación: revisa el buzón antes de reintentar.

Los selectores son atributos propios de Sales Nav (`data-anchor-send-inmail`,
`data-x-conversation-widget=compose-form`, `data-sales-action`, `overlay.close_overlay`), no texto
traducido. Si LinkedIn los cambia, la herramienta falla con un error claro en vez de enviar a ciegas.

## Ejecutar

```bash
uv run --directory ~/Repos/linkedin-mcp-server python -m dpi_salesnav \
  --no-auto-import --user-data-dir ~/.linkedin-mcp-cuenta1/profile \
  --transport streamable-http --host 127.0.0.1 --port 8001
```

- Cuenta 2: `~/.linkedin-mcp-cuenta2/profile` y `--port 8002`. Cada cuenta en su propio directorio padre.
- `--no-auto-import` es obligatorio con dos cuentas. Sin él, el servidor puede importar la sesión del navegador equivocado.
- `--no-headless` para ver el navegador.
- Tests offline: `uv run pytest dpi_salesnav/tests -q`.

## Sincronizar con upstream

Modelo recomendado: `main` es un espejo de `upstream/main` y nuestro trabajo va en `dpi/main`
(rama por defecto del fork). Las ramas de trabajo entran a `dpi/main` por PR.

```bash
git fetch upstream
git checkout main && git merge --ff-only upstream/main && git push origin main
git checkout dpi/main && git merge main && uv sync && uv run pytest dpi_salesnav/tests -q
```

Tras sincronizar, prueba una llamada real a cada herramienta. Lo que puede romperse no son los
ficheros sino las APIs internas que importamos: `get_ready_extractor`, `tool_errors`,
`get_or_create_browser`, `read_company`, `core.throttle` y `create_mcp_server(role=...)`.

## Datos

Ni perfiles, ni cookies, ni JSON capturados van al repo: todo vive en `~/.linkedin-mcp-cuenta*/`,
y `~/.dpi-salesnav/`, fuera del árbol del proyecto.
