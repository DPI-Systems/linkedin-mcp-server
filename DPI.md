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
| `dpi_salesnav/celebrations.py` | Novedades de la red: cumpleaños, cambios de empleo, aniversarios |
| `dpi_salesnav/contact_info.py` | Email, teléfono y web de un contacto (ventana "Información de contacto") |
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
- **`linkedin_network_updates(categories?, max_results=30)`**: solo lectura. Lee "Ponte al día" de Mi red:
  `birthdays`, `job_changes`, `work_anniversaries` y `education` (por defecto las cuatro, una navegación cada una).
  Por persona devuelve `name`, `detail` (la línea de LinkedIn en el idioma de la cuenta: cuándo es o fue el
  cumpleaños, el puesto y la empresa nuevos), `linkedin_username`, `profile_url` y `post_url` (el anuncio del
  cambio de empleo o aniversario, para comentar). Para felicitar se pasa `linkedin_username` al `send_message` de
  upstream. LinkedIn solo enseña cumpleaños de los últimos días hasta hoy, y solo de quien los comparte.
- **`linkedin_contact_info(linkedin_username)`**: solo lectura. Emails, teléfonos y webs de un contacto de primer grado,
  más el texto entero de la ventana (cumpleaños, fecha de conexión). Sustituye a la sección `contact_info` de
  `get_person_profile`, que ya no funciona: LinkedIn no abre esa ventana desde `/overlay/contact-info/` (redirige al
  perfil), solo al pulsar el enlace del perfil. Se reconoce por estructura (enlaces `mailto:`, redirecciones
  `/safety/go/` y líneas con forma de teléfono), no por las etiquetas traducidas. Cada persona decide qué comparte, así
  que muchos contactos no tienen email ni teléfono.

No hay topes ni horarios en el servidor: cuándo y cuánto se envía lo decide quien llama. Lo que sí hay son
pausas aleatorias entre acciones dentro de cada herramienta (de 20 a 40 s entre páginas de resultados y de 0,5 a 2 s
entre los pasos de escribir y enviar un mensaje), para que el ritmo sea el de una persona.

Los selectores son atributos propios de Sales Nav (`data-anchor-send-inmail`,
`data-x-conversation-widget=compose-form`, `data-sales-action`, `overlay.close_overlay`), no texto
traducido. Si LinkedIn los cambia, la herramienta falla con un error claro en vez de enviar a ciegas.

## Aplicación DPI CRM (`dpi_crm/`)

Una sola aplicación con tres capas sobre la misma lógica de LinkedIn:

- **MCP** (este servidor, puerto 8001): dueño único del navegador y del perfil de LinkedIn. Lo usan el LLM y el backend.
- **API** (`dpi_crm/backend`, FastAPI en el 8100): las automatizaciones (`app/services/automations.py`), el CRM en
  Postgres y la cola de ejecuciones. Llama a LinkedIn como cliente del MCP, así que nunca abre el perfil por su cuenta
  y comparte la sesión con el LLM sin chocar.
- **Frontend** (`dpi_crm/frontend`, Next.js 15, React 19 y Ant Design 6 en el 3100, el mismo stack que Licita IA):
  Inicio, Novedades, Contactos, ficha de contacto, Empresas y Ejecuciones.

Automatizaciones (una a la vez, desde el botón o el cron `SYNC_CRON`, por defecto 08:00 de lunes a viernes):

- **`network_sync`**: lee "Ponte al día", guarda a cada persona y su novedad sin duplicar, y lee el perfil (experiencia)
  de los contactos nuevos o con la ficha de más de `PROFILE_REFRESH_DAYS` días, con pausas de 4 a 9 s entre perfiles.
- **`company_import`**: guarda las empresas elegidas en la pantalla Empresas y a las personas que Sales Nav lista en cada una,
  con los mismos filtros que `salesnav_company_people`.

Si LinkedIn devuelve `rate_limited`, `account_restricted` o `auth_required`, la ejecución se para, guarda lo leído y no
reintenta. La sincronización lee una vez la ficha de contacto de cada persona de la red (`linkedin_contact_info`) y
rellena email, teléfono y web **solo si están vacíos**: lo que escribe el equipo nunca se sobrescribe. Las notas son solo
del equipo.

Empresas y contactos son tablas relacionadas. Cada contacto apunta a la fila de su empresa actual. El id numérico de
LinkedIn de la empresa (`/company/<id>/` en la experiencia del perfil) es el mismo que usa Sales Navigator, así que un
contacto de la red y una importación de Sales Nav acaban en la misma empresa. Una empresa sin página en LinkedIn se
agrupa por nombre. El texto del perfil queda guardado (`profile_raw`): tras mejorar el parser,
`uv run python -m scripts.reparse_profiles` lo vuelve a interpretar sin entrar en LinkedIn.

```bash
cd dpi_crm && docker compose up -d                  # Postgres 17 en 127.0.0.1:5440
cd backend && cp .env.example .env && uv sync && uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8100
cd ../frontend && npm install && npm run dev         # http://localhost:3100
uv run pytest -q                                     # tests del backend (SQLite en memoria)
```

## Ejecutar el servidor MCP

```bash
uv run --directory ~/Repos/linkedin-mcp-server python -m dpi_salesnav \
  --no-auto-import --user-data-dir ~/.linkedin-mcp-cuenta1/profile \
  --transport streamable-http --host 127.0.0.1 --port 8001
```

- Cuenta 2: `~/.linkedin-mcp-cuenta2/profile` y `--port 8002`. Cada cuenta en su propio directorio padre.
- `--no-auto-import` es obligatorio con dos cuentas. Sin él, el servidor puede importar la sesión del navegador equivocado.
- `--no-headless` para ver el navegador.
- Tests offline: `uv run pytest dpi_salesnav/tests -q`.

## Despliegue en dpi-lab (cuenta 1)

El MCP de la cuenta 1 vive en `dpi-lab` (Ubuntu, NetBird `100.81.155.12`, misma IP pública de casa) y **solo se
alcanza por la mesh**: `https://linkedin-mcp.dev.dpi-systems.com/mcp`. Ese nombre resuelve a la IP de NetBird y el
certificado sale por DNS-01, así que no hay nada publicado en internet. Mientras corre allí, **no se arranca la cuenta 1
en otro sitio**: una cuenta, un navegador.

| Pieza | Dónde |
|---|---|
| Imagen | `dpi_deploy/Dockerfile`: la del proyecto original sin tocar (`dpi/linkedin-mcp-base:local`) más `dpi_salesnav` |
| Servicio | `dpi_deploy/docker-compose.yml` → contenedor `dpi-linkedin-mcp`, red `dpi-dev`, sin puertos publicados |
| Sesión de LinkedIn | `/opt/dpi/data/linkedin-mcp/cuenta1/` en dpi-lab (nunca en el repo) |
| Código en dpi-lab | `/opt/dpi/linkedin-mcp/src/` (copiado con `dpi_deploy/sync.sh`) |
| Proxy | bloque `linkedin-mcp.dev.dpi-systems.com` de `/opt/dpi/services/caddy/Caddyfile` (copia en `dpi_deploy/Caddyfile.snippet`) |
| Token | `/opt/dpi/services/.secrets/LINKEDIN_MCP_TOKEN` (0600), montado en `dpi-caddy` |

Dos barreras en Caddy: solo direcciones `100.81.0.0/16` (403 si no) y `Authorization: Bearer <token>` (401 si no). La
mesh tiene equipos de varias personas y quien llega al MCP actúa en LinkedIn como Fernando, por eso hacen falta las dos.
Se puede añadir una política de NetBird que limite el acceso a los equipos de Fernando.

**Conectar Claude Code** (cualquier equipo con NetBird):

```bash
claude mcp add --scope user --transport http linkedin-dpi https://linkedin-mcp.dev.dpi-systems.com/mcp --header "Authorization: Bearer $(ssh -n dpi@100.81.155.12 cat /opt/dpi/services/.secrets/LINKEDIN_MCP_TOKEN)"
```

**Conectar Claude Desktop** (`claude_desktop_config.json`, con el token en `AUTH`):

```json
{
  "mcpServers": {
    "linkedin-dpi": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://linkedin-mcp.dev.dpi-systems.com/mcp", "--header", "Authorization:${AUTH}"],
      "env": { "AUTH": "Bearer <token>" }
    }
  }
}
```

claude.ai web y móvil y ChatGPT no pueden usarlo: llaman al MCP desde sus servidores, que no ven la mesh.

**Login, o cuando LinkedIn pida verificación** (el servidor no tiene pantalla; Chrome corre en una pantalla virtual y se
ve con el visor noVNC por un túnel SSH):

```bash
ssh dpi@100.81.155.12 'cd /opt/dpi/linkedin-mcp/src/dpi_deploy && docker compose stop mcp && docker compose run -d --rm -p 127.0.0.1:6080:6080 --name dpi-linkedin-login login && sleep 20 && docker logs dpi-linkedin-login 2>&1 | grep login.html'
```

```bash
ssh -N -L 6080:127.0.0.1:6080 dpi@100.81.155.12
```

Se abre en el navegador la URL `http://127.0.0.1:6080/login.html#token=…` que imprime el primer comando, se entra en
LinkedIn a mano (Cmd+V pega la contraseña) y, cuando el visor se cierra, se arranca otra vez el servicio:

```bash
ssh dpi@100.81.155.12 'cd /opt/dpi/linkedin-mcp/src/dpi_deploy && docker compose up -d mcp'
```

**Actualizar** (desde el Mac, tras cambiar código o traer upstream):

```bash
dpi_deploy/sync.sh && ssh dpi@100.81.155.12 'cd /opt/dpi/linkedin-mcp/src && ./dpi_deploy/build.sh && cd dpi_deploy && docker compose up -d mcp'
```

**Logs:** `ssh dpi@100.81.155.12 docker logs -f dpi-linkedin-mcp`.

El CRM (`dpi_crm/backend`) usa este MCP con `MCP_URL=https://linkedin-mcp.dev.dpi-systems.com/mcp` y `MCP_TOKEN` en su
`.env`.

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
