# MVP Task List — ServerWatch AI Bot

## Dependencias y configuración
- [x] Añadir dependencias en `pyproject.toml`: `python-telegram-bot`, `httpx`, `aiosqlite`
- [x] Actualizar `Dockerfile` para instalar dependencias

## Arquitectura base
- [ ] Crear estructura de carpetas: `app/handlers/`, `app/services/`, `app/core/`, `app/utils/`
- [ ] `app/core/config.py` — carga y validación de variables de entorno
- [ ] `app/utils/formatting.py` — helper de mensajes con ℹ️ ✅ ⚠️ ❌
- [ ] `app/core/auth.py` — middleware que bloquea mensajes de usuarios distintos a `TELEGRAM_CHAT_ID`
- [ ] `locale/en.json` — centralizar todos los textos del bot
- [ ] `app/utils/i18n.py` — helper de carga y acceso a textos por clave (soporte multi-idioma)

## Capa de servicios
- [ ] `app/services/glances.py` — cliente async para Glances API (CPU, RAM, disco, red, procesos, Docker)
- [ ] `app/services/ollama.py` — cliente async para Ollama (listar modelos, chat/completions con contexto inyectado)
- [ ] `app/core/store.py` — SQLite con `aiosqlite` (historial de conversación, configuración de umbrales)

## Bot Telegram
- [ ] `app/main.py` — refactor: inicializar `Application` de `python-telegram-bot`
- [ ] Registrar `setMyCommands`
- [ ] `ReplyKeyboardMarkup` persistente (botones: Estado, Alertas, Modelos, Ayuda)
- [ ] Handler global de errores con respuesta amigable

## Handlers
- [ ] `app/handlers/start.py` — `/start` → bienvenida + mostrar teclado persistente
- [ ] `app/handlers/chat.py` — mensaje de texto libre → recoge métricas → prompt con contexto → respuesta LLM
- [ ] `app/handlers/status.py` — `/status` / botón Estado → resumen visual de métricas
- [ ] `app/handlers/alerts.py` — `/alerts` / botón Alertas → ver/editar umbrales con inline buttons + confirmación
- [ ] `app/handlers/models.py` — `/models` / botón Modelos → lista de modelos Ollama disponibles

## Motor de alertas
- [ ] `app/services/scheduler.py` — loop async que comprueba métricas cada `ALERT_CHECK_INTERVAL_SECONDS`
- [ ] Lógica de cooldown (`ALERT_COOLDOWN_SECONDS`) para no spamear alertas repetidas
- [ ] Envío proactivo de alerta al chat cuando se supera un umbral

## Documentación
- [ ] `CHANGELOG.md` con lo implementado
- [ ] `CONTRIBUTING.md` básico
- [ ] Actualizar `README.md` con comandos, botones y flujos
