# Tool Hook Architecture

Hooks surround, but do not replace, the executor lifecycle: before tool call, guard/permission preflight, handler, after tool result, and tool error. `BEFORE_TOOL_CALL`, `AFTER_TOOL_RESULT`, and `ON_TOOL_ERROR` are public aliases for compatible legacy events.

Registrations have priority and enablement state. Callback exceptions are isolated, traced, and do not crash the runtime. A hook may request a blocking error or input update, but updated input is revalidated and rechecked by guard and permission policy; a guard denial is never overridable.
