# Skill Architecture

Skills are reusable capability templates, not subagents. Filesystem loaders discover `SKILL.md` files with bundled/user/project precedence. `SkillRegistry` supplies the stable runtime API: validate, register, list, search, and load. The existing `skill` tool either injects an inline attachment or explicitly delegates a fork-context skill to the existing child runner.

Skills describe prompt augmentation, optional tool scope, and checklist-like content. Tool scope is enforced through the child permission policy; a skill attachment alone cannot grant execution authority. Trace records the selected skill through the ordinary tool lifecycle.
