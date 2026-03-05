# MVP Task List — ServerWatch AI Bot

## Dependencias y configuración
- [x] Añadir dependencias en `pyproject.toml`: `python-telegram-bot`, `httpx`, `aiosqlite`
- [x] Actualizar `Dockerfile` para instalar dependencias

## Arquitectura base
- [x] Crear estructura de carpetas: `app/handlers/`, `app/services/`, `app/core/`, `app/utils/`
- [x] `app/core/config.py` — carga y validación de variables de entorno
- [x] `app/utils/formatting.py` — helper de mensajes con ℹ️ ✅ ⚠️ ❌
- [x] `app/core/auth.py` — middleware que bloquea mensajes de usuarios distintos a `TELEGRAM_CHAT_ID`
- [x] `locale/en.json` — centralizar todos los textos del bot
- [x] `app/utils/i18n.py` — helper de carga y acceso a textos por clave (soporte multi-idioma)

## Capa de arranque del bot — primer despliegue validable
- [ ] `app/main.py` — refactor: inicializar `Application` de `python-telegram-bot`
- [ ] Registrar `setMyCommands`
- [ ] `ReplyKeyboardMarkup` persistente (botones: Estado, Alertas, Modelos, Ayuda)
- [ ] Handler global de errores con respuesta amigable
- [ ] `app/handlers/start.py` — `/start` → saludo personalizado con el nombre de usuario de Telegram + teclado persistente

## Capa de servicios
- [ ] `app/services/glances.py` — cliente async para Glances API (CPU, RAM, disco, red, procesos, Docker)
- [ ] `app/services/ollama.py` — cliente async para Ollama (listar modelos, chat/completions con contexto inyectado)
- [ ] `app/core/store.py` — SQLite con `aiosqlite` (historial de conversación, configuración de umbrales)

## Handlers
- [ ] `app/handlers/chat.py` — mensaje de texto libre → recoge métricas → prompt con contexto → respuesta LLM
- [ ] `app/handlers/status.py` — `/status` / botón Estado → resumen visual de métricas
- [ ] `app/handlers/alerts.py` — `/alerts` / botón Alertas → ver/editar umbrales con inline buttons + confirmación
- [ ] `app/handlers/models.py` — `/models` / botón Modelos → flujo de selección de modelo activo

## Gestión de modelo Ollama
- [ ] `app/core/store.py` — persistir en SQLite el modelo activo seleccionado por el usuario
- [ ] Al arrancar, inicializar el modelo activo con el valor de `OLLAMA_MODEL` si no hay ninguno guardado en BD
- [ ] `/models` muestra la lista de modelos instalados con el modelo activo marcado (✅)
- [ ] Inline buttons para seleccionar cualquier modelo de la lista
- [ ] Confirmación antes de cambiar de modelo activo
- [ ] Todos los prompts a Ollama usan el modelo activo almacenado en BD

## Motor de alertas
- [ ] `app/services/scheduler.py` — loop async que comprueba métricas cada `ALERT_CHECK_INTERVAL_SECONDS`
- [ ] Lógica de cooldown (`ALERT_COOLDOWN_SECONDS`) para no spamear alertas repetidas
- [ ] Envío proactivo de alerta al chat cuando se supera un umbral

## Documentación (Siempre hay que ir actualizando)
- [ ] `CHANGELOG.md`
- [ ] `CONTRIBUTING.md`
- [ ] `README.md`