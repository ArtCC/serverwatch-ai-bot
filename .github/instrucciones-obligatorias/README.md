# Instrucciones de obligado cumplimiento

Esta carpeta centraliza las normas **obligatorias** del proyecto `serverwatch-ai-bot`.

## Objetivo
Definir cómo diseñar, implementar y operar el bot de Telegram en Python de forma:
- estandarizada,
- mantenible,
- segura,
- y preparada para despliegue en Docker/Portainer.

## Estructura recomendada
Añadiremos documentos numerados para mantener orden y trazabilidad:

- `00-principios.md` → Principios base y alcance.
- `01-arquitectura.md` → Arquitectura del sistema y decisiones técnicas.
- `02-estandares-codigo.md` → Convenciones de código Python, tipado, logging y testing.
- `03-seguridad.md` → Gestión de secretos, hardening, permisos y control de acceso.
- `04-operacion.md` → Despliegue Docker/Portainer, observabilidad y runbooks.
- `05-alertas-ia.md` → Reglas de alertado + uso de Ollama para interpretación.

## Reglas de uso de esta carpeta
1. Todo documento debe indicar: **fecha**, **autor** y **versión**.
2. Cualquier cambio relevante en arquitectura o seguridad debe reflejarse aquí antes de implementarse.
3. Las decisiones importantes deben quedar registradas de forma explícita y revisable.
4. Evitar ambigüedades: usar criterios verificables y ejemplos concretos.

## Plantilla mínima por documento
```md
# Título

- Fecha: YYYY-MM-DD
- Autor: Nombre
- Versión: 0.1

## Contexto

## Decisión / Regla

## Justificación

## Criterios de cumplimiento

## Ejemplos
```
