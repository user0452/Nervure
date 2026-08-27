# Project Instruction Architecture

`InstructionMemoryLoader` is the project instruction boundary. It loads user guidance, workspace guidance, `.nervure/instructions.md`, `.nervure/rules/*.md`, and local overrides in deterministic order. Instructions are rendered as a dedicated system-prompt section, before user messages and long-term memory; they never enter the chat transcript as a user turn.

The loader applies frontmatter/path matching, include containment, UTF-8 reading, an approximate configurable token budget, and trace metadata (`project_instructions_loaded`). Prompt text is guidance only: the static system/safety sections and guard/permission execution checks remain higher-authority boundaries.

Future work may replace the deterministic token estimate with a provider tokenizer without changing the loading contract.
