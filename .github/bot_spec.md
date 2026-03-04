# BOT_SPEC

## Creating a new Telegram bot

I want you to develop this new Telegram bot with a consistent, modern UX/UI focused on operational clarity from the first version.

OBJECTIVE
Build a bot usable from day one, with coherent commands, buttons, and visual flows, while keeping clean architecture and minimal production-ready documentation.

SCOPE
- Python Telegram bot ready for Docker Compose deployment.
- Coherent conversational UX/UI with commands, persistent keyboard, and contextual inline buttons.
- Observability integration and operational responses focused on clarity for the end user.

OUT OF SCOPE (MVP)
- Additional web panel development.
- Advanced multi-tenant support.
- Automations not defined in this document.

UX/UI PRINCIPLES (MANDATORY)

1) Dual function access
- Every main function must be executable via:
  a) slash command,
  b) quick button on a persistent keyboard when applicable.

2) Clear commands
- Register `setMyCommands` with brief, concrete descriptions.
- Keep command names simple, predictable, and consistent.

3) Persistent keyboard
- Implement `ReplyKeyboardMarkup` with key actions.
- Compact design (2-4 main buttons), without clutter.
- Show the keyboard after important actions to keep navigation fluid.

4) Contextual inline buttons
- Use `InlineKeyboardMarkup` for:
  - option selection,
  - confirmations,
  - list refresh,
  - context-dependent actions.
- Do not use inline buttons for permanent global navigation (that belongs in the persistent keyboard).

5) Safe confirmations
- Every destructive or irreversible action must have inline confirmation:
  - Confirm / Cancel.

6) Homogeneous status messages
- Standardize all responses with iconography:
  - ℹ️ info
  - ✅ success
  - ⚠️ warning
  - ❌ error
- Keep messages short, direct, and actionable.
- Avoid ambiguous or overly long responses.

7) Conversational flow
- Show "typing..." for operations that may take time.
- If there is an error, explain what happened and what the user can do.
- Keep tone consistent and professional.

8) Limits and robustness
- Handle Telegram limits (long messages, callbacks, etc.).
- Manage global bot errors with a friendly response.
- Avoid breaking user flow when external services fail.

ARCHITECTURE AND CODE

- Separate responsibilities by layers (handlers, services, core/store, utils, etc.).
- Centralize message format helpers (info/success/warning/error).
- Keep configuration through environment variables.
- Design for Docker Compose deployment from the start.
- Write readable, typed code with low coupling.

MINIMUM REQUIRED DOCUMENTATION

- README with:
  - overview,
  - commands,
  - persistent buttons,
  - inline flows,
  - environment variables,
  - docker compose deployment.
- Initial CHANGELOG with what was implemented.
- Basic CONTRIBUTING.

UX/UI ACCEPTANCE CRITERIA

- Registered and functional commands.
- Persistent keyboard working.
- Inline buttons in contextual flows.
- Confirmation for destructive actions.
- Unified messages with ℹ️✅⚠️❌.
- Global error handler with friendly response.
- Updated README/CHANGELOG.

EXPECTED DELIVERY

1) Complete and functional code.
2) Summary of changes by file.
3) Final list of commands and buttons.
4) Deployment notes and quick validation.