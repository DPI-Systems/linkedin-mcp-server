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
| `dpi_salesnav/article_markdown.py`, `dpi_salesnav/article.py` | Artículos de LinkedIn desde Markdown |
| `dpi_salesnav/inbox.py` | Buzón de Sales Nav: lista de hilos y mensajes de un hilo |
| `dpi_salesnav/tools.py` | Registro MCP |

## Herramientas

- **`salesnav_search_companies(name)`**: solo lectura. Devuelve todas las empresas que da el buscador de cuentas de
  Sales Nav (hasta 25) con `company_id`, `name`, `industry`, `employees`, `location`, `description` y enlaces. No elige:
  quien llama compara y escoge (cuidado con UTE, filiales y nombres parecidos).
- **`salesnav_company_people(company_id, keywords?, titles?, seniority?, functions?, regions?, posted_recently?, changed_jobs_recently?, max_results=25)`**: solo lectura.
  - `company_id`: el id numérico que devuelve `salesnav_search_companies`.
  - Filtros: `titles` (texto libre), `seniority` (`director`, `vp`, `cxo`, `owner`, ...), `functions` (`sales`,
    `business_development`, `operations`, ...), `regions` (ids de zona; España = `105646813`), `posted_recently`
    (ha publicado en los últimos 30 días) y `changed_jobs_recently` (ha cambiado de empleo en los últimos 90 días).
  - Cada página de 25 resultados es una navegación. Por lead devuelve: `full_name`, `title`, `company`,
    `tenure_at_company`, `tenure_at_position`, `position_started_on`, `location`, `seniority`, `degree`,
    `recent_activity`, `changed_jobs_recently`, `promoted_recently`, `badges`, `summary`, `open_link` y
    `sales_nav_url`. Sales Nav no siempre incluye la insignia de actividad, así que `recent_activity` puede venir
    vacío aunque la persona haya publicado.
- **`salesnav_search_people(keywords?, titles?, seniority?, functions?, regions?, ...)`**: como la anterior pero en
  todas las empresas. Ambas devuelven `region_breakdown` (zonas de los resultados con su id): para filtrar por una
  ciudad sin conocer su id, se busca con la ciudad en `keywords`, se lee su id ahí y se repite con `regions`.
- **`salesnav_send_inmail(lead_url, subject, body, confirm_send=false)`**: escritura.
  - Sin `confirm_send` es una vista previa: escribe el texto, comprueba el botón de enviar, lo borra y cierra el formulario.
  - Con `confirm_send=true` envía. Solo devuelve `sent` si el texto aparece en la conversación abierta.
  - `status: "unconfirmed"` significa que se pulsó enviar pero no hubo confirmación: revisa el buzón antes de reintentar.
- **`salesnav_inbox(limit=20)`**: los 20 hilos más recientes del buzón de Sales Nav, con el contacto, el último
  mensaje, `awaiting_their_reply` (el último mensaje es nuestro) y `they_replied`. Abrir el buzón abre su hilo más reciente y lo marca como leído.
- **`salesnav_get_thread(thread_id)`**: todos los mensajes de un hilo, del más antiguo al más reciente. Lo marca como leído.

- **`salesnav_create_article(title, markdown, images_dir?, cover_image?, author?, publish_in?, publish=false, commentary?)`**:
  escribe un artículo de LinkedIn (`/pulse/...`) desde Markdown y **por defecto solo deja un borrador** y devuelve
  `draft_url`. Admite `#`/`##` (titular), `###` (subtitular), negrita, cursiva, enlaces, listas, citas, código,
  separadores e imágenes locales en su propia línea (`![pie de foto](imagen.png)`), además de la portada. Tablas,
  HTML y tachado vuelven en `warnings`. `author` es "publicar como" (tú o una página que administres) y `publish_in`
  una newsletter: **por defecto es artículo individual**, porque una edición de newsletter se envía a sus suscriptores.
  Tras guardar, reabre el borrador y devuelve en `saved` lo que LinkedIn guardó de verdad (imágenes subidas,
  titulares, listas...).
- **`salesnav_publish_article(draft_id, commentary?, confirm_publish=false)`**: publica un borrador ya revisado.
  Sin `confirm_publish` solo dice título, autor y destino.

No hay topes ni horarios en el servidor: cuándo y cuánto se envía lo decide quien llama. Lo que sí hay son
pausas aleatorias entre acciones dentro de cada herramienta (de 20 a 40 s entre páginas de resultados y de 0,5 a 2 s
entre los pasos de escribir y enviar un mensaje), para que el ritmo sea el de una persona.

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
fuera del árbol del proyecto.
