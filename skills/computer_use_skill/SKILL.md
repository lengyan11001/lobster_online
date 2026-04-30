# Computer Use

This client skill is surfaced by the Lobster skill store for administrators.
The UI opens an isolated OpenClaw workspace chat and sends user messages to
`POST /api/openclaw/skill-chat` with `skill_id=computer_use_skill`.

It intentionally does not register Lobster MCP capabilities and does not route
through the main `/chat` or `/chat/stream` flow.
