# BOT_SPEC

## Creación de nuevo bot para Telegram

Quiero que desarrolles este nuevo bot de Telegram con una UX/UI consistente, moderna y orientada a claridad operativa desde la primera versión.

OBJETIVO
Construir un bot usable desde el día 1, con comandos, botones y flujos visuales coherentes, manteniendo arquitectura limpia y documentación mínima de producción.

ALCANCE
- Bot de Telegram en Python listo para despliegue en Docker Compose.
- UX/UI conversacional coherente con comandos, teclado persistente y botones inline contextuales.
- Integración de observabilidad y respuestas operativas orientadas a claridad para el usuario final.

NO ALCANCE (MVP)
- Desarrollo de panel web adicional.
- Soporte multi-tenant avanzado.
- Automatizaciones no definidas en este documento.

PRINCIPIOS UX/UI (OBLIGATORIOS)

1) Doble acceso a funciones
- Toda función principal debe poder ejecutarse por:
  a) comando slash,
  b) botón rápido en teclado persistente cuando aplique.

2) Comandos claros
- Registrar `setMyCommands` con descripciones breves y concretas.
- Mantener nombres de comandos simples, previsibles y consistentes.

3) Teclado persistente
- Implementar `ReplyKeyboardMarkup` con acciones clave.
- Diseño compacto (2-4 botones principales), sin saturar.
- Mostrar teclado tras acciones importantes para mantener navegación fluida.

4) Botones inline contextuales
- Usar `InlineKeyboardMarkup` para:
  - selección de opciones,
  - confirmaciones,
  - refresco de listados,
  - acciones dependientes del contexto.
- No usar inline para navegación global permanente (eso va en teclado persistente).

5) Confirmaciones seguras
- Toda acción destructiva o irreversible debe tener confirmación inline:
  - Confirmar / Cancelar.

6) Mensajes de estado homogéneos
- Estandarizar todas las respuestas con iconografía:
  - ℹ️ info
  - ✅ success
  - ⚠️ warning
  - ❌ error
- Mensajes cortos, directos y accionables.
- Evitar respuestas ambiguas o demasiado largas.

7) Flujo conversacional
- Mostrar "typing..." en operaciones que puedan tardar.
- Si hay error, explicar qué pasó y qué puede hacer el usuario.
- Mantener tono consistente y profesional.

8) Límites y robustez
- Manejar límites de Telegram (mensajes largos, callbacks, etc.).
- Gestionar errores globales del bot con respuesta amigable.
- Evitar romper el flujo del usuario ante fallos de servicios externos.

ARQUITECTURA Y CÓDIGO

- Separar responsabilidades por capas (handlers, services, core/store, utils, etc.).
- Centralizar helpers de formato de mensajes (info/success/warning/error).
- Mantener configuración por variables de entorno.
- Diseñar para despliegue con Docker Compose desde el inicio.
- Escribir código legible, tipado y con bajo acoplamiento.

DOCUMENTACIÓN MÍNIMA REQUERIDA

- README con:
  - overview,
  - comandos,
  - botones persistentes,
  - flujos inline,
  - variables de entorno,
  - despliegue docker compose.
- CHANGELOG inicial con lo implementado.
- CONTRIBUTING básico.

CRITERIOS DE ACEPTACIÓN UX/UI

- Comandos registrados y funcionales.
- Teclado persistente funcionando.
- Inline buttons en flujos contextuales.
- Confirmación en acciones destructivas.
- Mensajes unificados con ℹ️✅⚠️❌.
- Error handler global con respuesta amigable.
- README/CHANGELOG actualizados.

ENTREGA ESPERADA

1) Código completo y funcional.
2) Resumen de cambios por archivo.
3) Lista final de comandos y botones.
4) Notas de despliegue y validación rápida.