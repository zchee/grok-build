# Grok Build configuration schemas

JSON Schemas (draft-07) for every user-configurable file format consumed by the
`grok` CLI/TUI in this repository. One schema per on-disk format, hand-authored
from the serde model with `file:line` provenance in every property description
(`[src: <path>:<line>]` suffixes). TOML formats get JSON Schemas too: Taplo,
tombi, and eglot consume JSON Schema for TOML validation.

These schemas are **documentation-grade truth, not compiler-enforced**. No
config struct in this tree derives `schemars::JsonSchema`, and this tree is
periodically overwritten by "Synced from monorepo" commits, so the schemas live
outside `crates/` and are validated by a standalone harness with a citation
drift check. Each schema records the commit it was authored against in its
`x-source-rev` field (currently `0f4d7c91b8b2b408333f6de1e8a76cb8eaa71899`).

## Running the validation harness

```console
$ cd schemas && ./validate.py
```

Requires only `uv` (the PEP-723 shebang pulls `jsonschema` and `referencing`;
TOML parsing uses stdlib `tomllib`, Python >= 3.11). The script:

1. Meta-validates all 18 schemas against draft-07.
2. Validates every fixture: `fixtures/<name>/valid/*` must pass,
   `fixtures/<name>/invalid/*` must fail.
3. Validates the real in-repo corpus: the 5 hook examples under
   `crates/codegen/xai-grok-hooks/examples/hooks/*.json` and
   `crates/codegen/xai-grok-models/default_models.json`.
4. Drift-checks every `[src: path:line]` citation (path exists, cited line
   within file length). This is a staleness signal only; it never judges
   semantic strictness.
5. Enforces the "no `additionalProperties: false`" gate (see Corrections #0).

Human-readable log lines go to stderr; a machine-readable JSON summary goes to
stdout; exit status is 0 iff everything passes.

**Run `./validate.py` after every "Synced from monorepo" commit.** Stale
citations mean the upstream types moved and the affected schema needs a manual
re-check against its cited sources. There is no regeneration step: schemas are
hand-authored (see the plan ADR: schemars derives in `crates/**` would be wiped
by the next sync).

## Master index

| Schema | Config file(s) / surface | Format | Tier / precedence notes |
|---|---|---|---|
| `grok-config.schema.json` | `~/.grok/config.toml`, `<cwd..git-root>/.grok/config.toml`, `~/.grok/managed_config.toml`, `/etc/grok/managed_config.toml` | TOML | 6-layer deep merge, see "config.toml layers" below |
| `requirements-config.schema.json` | `~/.grok/requirements.toml`, `/etc/grok/requirements.toml`, macOS MDM `ai.x.grok`/`requirements_toml_base64` | TOML | Thin wrapper over `grok-config.schema.json` + `fail_closed` + `[[version_overrides]]`; highest 3 layers of the config merge |
| `grok-hooks.schema.json` | `~/.grok/hooks/*.json`, `<git-root>/.grok/hooks/*.json`, `~/.grok/hooks/imported-from-claude.json`, `.cursor/hooks.json`, `hooks` key of Claude settings | JSON | Additive; global sources load before project |
| `mcp-json.schema.json` | `<cwd..git-root>/.mcp.json`, plugin `.mcp.json`, `mcpServers` of `~/.claude.json` and `.cursor/mcp.json` | JSON | Merged below `config.toml` `[mcp_servers]`, see "MCP server merge" below |
| `plugin-manifest.schema.json` | `plugin.json`, `.grok-plugin/plugin.json`, `.claude-plugin/plugin.json` | JSON | Per-plugin |
| `marketplace-index.schema.json` | `.grok-plugin/marketplace.json`, `.claude-plugin/marketplace.json`, `.grok/marketplace.json`, `.claude/marketplace.json` | JSON | Per-marketplace |
| `plugin-catalog.schema.json` | `plugin-index.json` | JSON | Per-marketplace catalog |
| `known-marketplaces.schema.json` | `~/.claude/plugins/known_marketplaces.json`, `extraKnownMarketplaces` value shape | JSON | Claude-compat, user tier |
| `installed-plugins.schema.json` | `~/.claude/plugins/installed_plugins.json` | JSON | Claude-Code-owned state, read tolerantly |
| `lsp-config.schema.json` | `~/.grok/lsp.json`, `<cwd>/.grok/lsp.json` | JSON | Project file gated by folder trust |
| `campaigns-state.schema.json` | `~/.grok/campaigns_state.json` | JSON | Machine-written dismissal state |
| `default-models.schema.json` | `crates/codegen/xai-grok-models/default_models.json` (embedded via `include_str!`) | JSON | Compiled in; not user-editable at runtime |
| `remote-settings.schema.json` | Server settings payload (`GET /v1/settings`) | JSON | Server-controlled; informational |
| `sandbox-config.schema.json` | `~/.grok/sandbox.toml`, `<project>/.grok/sandbox.toml` | TOML | Project file is additive-only over user file |
| `pager-config.schema.json` | `~/.grok/pager.toml` | TOML | Hot-reloadable appearance only |
| `trusted-folders.schema.json` | `~/.grok/trusted_folders.toml` | TOML | User tier only |
| `claude-settings-compat.schema.json` | Consumed subset of `.claude/settings.json`, `.claude/settings.local.json`, `~/.claude/settings.json`, `~/.claude/settings.local.json` | JSON | local overrides plain; cwd > repo root > global |
| `managed-settings-compat.schema.json` | `/Library/Application Support/ClaudeCode/managed-settings.json` (macOS), `/etc/claude-code/managed-settings.json` (Linux) | JSON | Admin-enforced; deny wins |

Fixtures live in `fixtures/<schema-basename>/{valid,invalid}/`. The root
directory is `$GROK_HOME` if set, else `~/.grok`
[src: crates/codegen/xai-grok-config/src/paths.rs:35-38]; the system tier is
`/etc/grok/` [src: crates/codegen/xai-grok-config/src/paths.rs:72].

## Precedence and merge order

### config.toml layers

`effective_config_base` starts from the system-managed layer and deep-merges
each subsequent layer on top (later wins on key conflict)
[src: crates/codegen/xai-grok-config/src/loader.rs:237-251]:

```text
system_managed  (/etc/grok/managed_config.toml)
  < managed             (~/.grok/managed_config.toml)
  < user                (~/.grok/config.toml, then <cwd..git-root>/.grok/config.toml)
  < user_requirements   (~/.grok/requirements.toml)
  < system_requirements (/etc/grok/requirements.toml)
  < mdm_requirements    (macOS MDM ai.x.grok requirements_toml_base64)
```

Campaign sources use their own priority order (first id wins):
requirements > remote > user > managed > system_managed
[src: crates/codegen/xai-grok-config/src/loader.rs:253-256].

### MCP server merge

Layers are applied in order; later `insert()` beats earlier `or_insert()`
[src: crates/codegen/xai-grok-shell/src/session/managed_mcp.rs:5-12]:

```text
1. config.toml [mcp_servers]  seeds the map; enabled = false blocks lower layers
2. Plugins                    or_insert (never overrides config.toml)
3. ~/.claude.json             or_insert (imported user/local MCP servers)
4. .mcp.json                  or_insert (team baseline)
5. Client                     insert (always wins)
6. Managed                    header injection + auto-create missing connectors
```

### Hooks

Hook sources are additive; global hooks run before project hooks. A source is
either a settings file (only its `hooks` key is used) or a directory of
`*.json` hook files [src: crates/codegen/xai-grok-hooks/src/discovery.rs:126-141].

### Skills

Priority order (same-name skill from a higher-priority source wins)
[src: crates/codegen/xai-grok-agent/src/prompt/skills.rs:49-58]:

```text
Local (cwd/.grok/skills, cwd/.agents/skills, cwd/.claude/skills)
  > intermediate dirs (cwd..repo-root)
  > Repo (repo_root/.grok|.agents|.claude/skills)
  > User (~/.grok/skills, ~/.agents/skills, ~/.claude/skills)
  > additional config.paths
  > Server (injected server_skill_dirs)
  > Bundled (bundled_skill_dirs + ~/.grok/bundled)
```

### Claude-compat settings

Files are discovered in priority order, most-specific first: project
`<dir>/.claude/settings.local.json` then `<dir>/.claude/settings.json` walking
from cwd up to the repo root (cwd entries first), then global
`~/.claude/settings.local.json` then `~/.claude/settings.json`. Permission
rules from all files are merged; `defaultMode` uses scope precedence (the most
specific file that sets it wins)
[src: crates/codegen/xai-grok-workspace/src/permission/claude_settings.rs:337-461].

## Corrections discovered during authoring

Findings where the code differs from the plan brief or from what a naive schema
would encode. Each is deliberately reflected in the shipped schemas.

0. **No schema sets `additionalProperties: false`, ever.** No config struct in
   this tree uses `#[serde(deny_unknown_fields)]`; every format tolerates
   unknown keys, so `false` would reject valid documents. `validate.py`
   enforces this as a hard gate.
1. **`known_marketplaces.json` has two independent consumers requiring
   different per-entry keys**: `SettingsEntry { source }` (internally tagged,
   tag = `source`, lowercase `git`/`github`/`local`)
   [src: crates/codegen/xai-grok-plugin-marketplace/src/config.rs:184-220] and
   `KnownMarketplaceEntry { installLocation }` (camelCase)
   [src: crates/codegen/xai-grok-agent/src/plugins/marketplace.rs:152-176].
   The schema therefore requires the union `["source", "installLocation"]`,
   with optional `lastUpdated`/`autoUpdate`
   [src: crates/codegen/xai-grok-plugin-marketplace/src/config.rs:387-390].
2. **`managed-settings.json` `hooks` is NOT consumed by grok-build.** The
   plan's S18 row listed hooks; no code path reads a `hooks` key from managed
   settings (`resolution.rs:1027` is `AllowedMcpServer::Name`, unrelated). The
   schema omits it. Conversely `deniedMcpServers` and `strictKnownMarketplaces`
   ARE consumed and are modeled
   [src: crates/codegen/xai-grok-workspace/src/permission/resolution.rs:718-914].
3. **S18 permissions parsing is strict; S17 is tolerant.** In
   `managed-settings.json` one malformed permission entry drops ALL rules
   (strict serde); in Claude `settings.json` entries are parsed per-entry and
   bad ones are skipped. `disableBypassPermissionsMode` is a STRING compared
   against `"disable"`, not a boolean. `allowedMcpServers`/`deniedMcpServers`
   entries are `{serverUrl}` | `{command}` | `{serverName}`, deny wins.
4. **MCP transport union is `anyOf`, not `oneOf` (S1 and S3).** Serde's
   untagged enum picks the first matching variant, so a server object with
   BOTH `command` and `url` is valid (Stdio wins), an EMPTY object is valid
   (StreamableHttp; `url` defaults to `""`), and `command = 123` is also valid
   (falls through to StreamableHttp, unknown key ignored). A `oneOf`
   discrimination would wrongly reject the both-fields document. The transport
   union cannot fail structurally; invalid fixtures target typed non-flattened
   fields instead.
5. **S12 clamps are resolver-side and deliberately stricter than raw serde.**
   `DoomLoopRecoverySettings` bounds (`max_threshold` 2..=64, `max_retries`
   0..=5), `goal_verifier_count`, `goal_classifier_max_runs`,
   `goal_strategist_every`, and `goalRoleModel`'s
   `required: ["model", "agent_type"]` encode the post-parse clamping/dropping
   the resolver applies, not what serde would accept. This is a documented
   plan choice; the drift check never flags it. `auto_compact_threshold_percent`
   is 0..=255 (exact serde u8), 0-100 only semantically.
6. **Hook event names: 51 distinct spellings, not "3 per event".**
   `UserPromptSubmit` has NO camelCase spelling (only PascalCase, snake_case,
   and the `beforeSubmitPrompt` alias)
   [src: crates/codegen/xai-grok-hooks/src/event.rs:37-91]. Unrecognized event
   keys are skipped with a warning, never rejected.
7. **Sandbox `extends` accepts both `"read-only"` and `"readonly"`, never
   `"off"`.** `ProfileName::from_str` accepts both spellings
   [src: crates/codegen/xai-grok-sandbox/src/profiles.rs:95]; extending is
   depth-1 onto built-ins only, and `"off"` is not extendable. The project
   `sandbox.toml` is additive-only.
8. **`requirements.toml` is not a distinct format.** It is `config.toml`'s
   namespace (everything optional) plus exactly two extension keys stripped
   before the merge: `fail_closed` (defined cross-crate in
   `prod/mc/cli-chat-proxy-types/src/deployment_config_types.rs:110`) and
   `[[version_overrides]]`. The schema is a thin `allOf` wrapper with a
   relative `$ref: "./grok-config.schema.json"`; `validate.py` resolves the
   ref via a local registry, and same-directory refs work in editors.
9. **Two `[worktree_pool]` types parse the same key.** The wired, strict shape
   is `agent::config`'s own `WorktreePoolConfig` (all-`Option`, gates config
   load); config-types' `PoolConfig` also parses the key tolerantly
   (`unwrap_or_default`) but only test code calls it. Both are documented in
   S1 [src: crates/codegen/xai-grok-shell/src/util/config/mcp.rs:171-185].
10. **Plugin `name` kebab-case is post-parse validation, not serde.**
    `PluginManifest::validate()` enforces it after deserialization
    [src: crates/codegen/xai-grok-agent/src/plugins/manifest.rs:172-185]. The
    schema encodes the pattern anyway (documented as post-parse); note the
    regex `^[a-z0-9]([a-z0-9-]*[a-z0-9])?$` accepts consecutive hyphens
    (`a--b` is legal). `PathOrInline` accepts ANY JSON value
    (`anyOf: [string, {}]`).
11. **`plugin-index.json` `version` is `const 1` in the schema, but serde
    accepts any u64** -- the version gate lives in `load_catalog`
    [src: crates/codegen/xai-grok-plugin-marketplace/src/catalog.rs:92-99],
    the application layer.
12. **Marketplace `IndexSource` object form has zero required fields** (the
    hand-written visitor tolerates any subset)
    [src: crates/codegen/xai-grok-plugin-marketplace/src/index.rs:88].
13. **S12 citations corrected during authoring**: `CampaignOverride` lives in
    `crates/codegen/xai-grok-config-types/src/lib.rs:22-28` (not
    `campaigns.rs`); `WorktreeAutoGcSettings` has 8 fields (the brief missed
    `include_rebuild` and `rebuild_min_interval_secs`). S12 models 147
    top-level fields (plan estimated ~140).
14. **`trusted_folders.toml` soft-fails as a whole**: one malformed entry
    makes the entire document parse to empty (trust everything is forgotten,
    nothing granted) [src: crates/codegen/xai-grok-workspace/src/trust.rs:41-55].
15. **`installed_plugins.json` is Claude-Code-owned**: real files carry extra
    `version` keys (container integer, per-entry string) beyond what grok-build
    reads; the schema stays permissive and documents the known extras
    [src: crates/codegen/xai-grok-agent/src/plugins/discovery.rs:806-812].
16. **`lsp.json` has MIXED aliasing**: 7 fields carry camelCase aliases while
    `command`/`args`/`env`/`transport`/`settings` are canonical-only (there is
    no `rename_all` on the struct)
    [src: crates/codegen/xai-grok-tools/src/implementations/grok_build/lsp/config.rs:229-258].
17. **`[dashboard]` persists to `config.toml`, not `pager.toml`**, despite
    living in pager-crate code. `pager.toml` owns ONLY appearance
    (`RawAppearanceConfig`, 7 top-level keys); 43 of 46 registry-driven
    settings persist into `config.toml`'s `[ui]`/`[cli]`/
    `[toolset.ask_user_question]`; `multiline_mode`/`plan_mode` are
    session-only; `coding_data_sharing` goes to auth metadata.
18. **`[features].telemetry` is `boolean | string`**; recognized string values
    are documented but NOT enum-restricted (unknown strings are tolerated).
19. **`[[version_overrides]]` semver is a genuine hard-fail**
    [src: crates/codegen/xai-grok-config/src/loader.rs:405-408] -- one of the
    few places serde-level strictness really exists; the invalid fixture is a
    true rejection.
20. **Pager `execute.header_style` effective default is `"label"`**: the
    struct-level `Default` impl wins over the enum's `#[default]` variant
    (`Shell`) [src: crates/codegen/xai-grok-pager/src/settings/config.rs:1322].
21. **`default_models.json`: Rust parses only `default` and `models[].model`**
    [src: crates/codegen/xai-grok-models/src/lib.rs:15,27]; the schema
    documents the full on-disk key set for editors, requiring only
    `[default, models]` at top level and `[model]` per entry.

## Not file-configurable

| Surface | Why there is no schema |
|---|---|
| Themes | Compiled in via `include_bytes!` [src: crates/codegen/xai-grok-markdown/src/syntax.rs:35,171]; no user theme file is read |
| Keybindings | No keybindings-file reader exists anywhere in the tree |

## Excluded from schemas

Integrity-checked machine state; writing to these files breaks signatures or
corrupts caches, so schemas would only invite harm:

- `managed_config.sig.json`, `managed_identity.sig.json`
  [src: crates/codegen/xai-grok-config/src/signed_policy.rs:64,67,279,316]
- `managed_config_cache.json`
  [src: crates/codegen/xai-grok-config/src/managed_cache.rs:15,172,182]
- `~/.grok/logs/` (log output), `~/.grok/marketplace-cache/`
  [src: crates/codegen/xai-grok-plugin-marketplace/src/git.rs:4,155]

## Editor integration

VS Code (JSON files), in `.vscode/settings.json`:

```jsonc
{
  "json.schemas": [
    { "fileMatch": ["**/.grok/hooks/*.json"], "url": "./schemas/grok-hooks.schema.json" },
    { "fileMatch": ["**/.mcp.json"], "url": "./schemas/mcp-json.schema.json" },
    { "fileMatch": ["**/plugin.json", "**/.grok-plugin/plugin.json", "**/.claude-plugin/plugin.json"], "url": "./schemas/plugin-manifest.schema.json" },
    { "fileMatch": ["**/marketplace.json"], "url": "./schemas/marketplace-index.schema.json" },
    { "fileMatch": ["**/.claude/settings.json", "**/.claude/settings.local.json"], "url": "./schemas/claude-settings-compat.schema.json" },
    { "fileMatch": ["**/managed-settings.json"], "url": "./schemas/managed-settings-compat.schema.json" },
    { "fileMatch": ["**/.grok/lsp.json"], "url": "./schemas/lsp-config.schema.json" }
  ]
}
```

TOML files: Taplo (`evenBetterToml` in VS Code) and tombi accept JSON Schema
per file pattern. Taplo example (`.taplo.toml`):

```toml
[[rule]]
include = ["**/.grok/config.toml", "**/managed_config.toml"]

[rule.schema]
path = "schemas/grok-config.schema.json"

[[rule]]
include = ["**/.grok/sandbox.toml"]

[rule.schema]
path = "schemas/sandbox-config.schema.json"
```

The same pattern works for `pager.toml` -> `pager-config.schema.json`,
`trusted_folders.toml` -> `trusted-folders.schema.json`, and
`requirements.toml` -> `requirements-config.schema.json` (its relative `$ref`
to `grok-config.schema.json` resolves as long as both files stay in the same
directory).

## Appendix A -- Configuration path inventory

Root resolution: `$GROK_HOME` else `~/.grok`
[src: crates/codegen/xai-grok-config/src/paths.rs:35-38]; system tier
`/etc/grok/` [src: crates/codegen/xai-grok-config/src/paths.rs:72].

| # | Path / surface | Format | Schema | Source |
|---|---|---|---|---|
| 1 | `$GROK_HOME` (else `~/.grok`) root dir | dir | - | paths.rs:35-38 |
| 2 | `/etc/grok/` system config dir | dir | - | paths.rs:72 |
| 3 | `~/.grok/config.toml` | TOML | grok-config | loader.rs:83-95 |
| 4 | `<cwd..git-root>/.grok/config.toml` | TOML | grok-config | loader.rs:108-114 |
| 5 | `~/.grok/managed_config.toml` | TOML | grok-config | loader.rs:83-95 |
| 6 | `/etc/grok/managed_config.toml` | TOML | grok-config | paths.rs:72 |
| 7 | `~/.grok/requirements.toml` | TOML | requirements-config | validation.rs:66-124 |
| 8 | `/etc/grok/requirements.toml` | TOML | requirements-config | validation.rs:66-124 |
| 9 | macOS MDM `ai.x.grok` / `requirements_toml_base64` | base64 TOML | requirements-config | macos_managed.rs:8-40 |
| 10 | `~/.grok/sandbox.toml` | TOML | sandbox-config | profiles.rs:1-70 |
| 11 | `<project>/.grok/sandbox.toml` (additive-only) | TOML | sandbox-config | profiles.rs:1-70 |
| 12 | `~/.grok/pager.toml` (hot-reloadable appearance) | TOML | pager-config | app_view.rs:639 |
| 13 | `~/.grok/trusted_folders.toml` | TOML | trusted-folders | trust.rs:41-55 |
| 14 | `~/.grok/hooks/*.json` | JSON | grok-hooks | discovery.rs:126-141 |
| 15 | `<git-root>/.grok/hooks/*.json` | JSON | grok-hooks | discovery.rs:126-141 |
| 16 | `~/.grok/hooks/imported-from-claude.json` | JSON | grok-hooks | claude_import.rs:720-751 |
| 17 | `.cursor/hooks.json` (gated by `cursor_hooks_enabled`) | JSON | grok-hooks | folder_trust.rs:367; config-types lib.rs:591 |
| 18 | `hooks` key of Claude settings files | JSON | grok-hooks | hooks config.rs:223-226 |
| 19 | `~/.grok/disabled-hooks` (plain-text hook-name list) | text | - | hooks trust.rs:78-79; hooks-plugins-types lib.rs:205 |
| 20 | `<cwd..git-root>/.mcp.json` | JSON | mcp-json | managed_mcp.rs:5-12 |
| 21 | Plugin `.mcp.json` (or inline `mcpServers` in manifest) | JSON | mcp-json | manifest.rs:127-138 |
| 22 | `mcpServers` key of `~/.claude.json` | JSON | mcp-json | managed_mcp.rs:5-12; claude_import_state.rs:24 |
| 23 | `mcpServers` key of `.cursor/mcp.json` | JSON | mcp-json | claude_import.rs (cursor discovery) |
| 24 | `[mcp_servers]` section of `config.toml` | TOML | grok-config | config-types mcp.rs (McpConfig) |
| 25 | `plugin.json` | JSON | plugin-manifest | manifest.rs:138 |
| 26 | `.grok-plugin/plugin.json` | JSON | plugin-manifest | manifest.rs:138 |
| 27 | `.claude-plugin/plugin.json` | JSON | plugin-manifest | manifest.rs:138 |
| 28 | `.grok-plugin/marketplace.json` | JSON | marketplace-index | index.rs:18,42 |
| 29 | `.claude-plugin/marketplace.json` | JSON | marketplace-index | index.rs:18,42 |
| 30 | `.grok/marketplace.json` | JSON | marketplace-index | index.rs:18,42 |
| 31 | `.claude/marketplace.json` | JSON | marketplace-index | index.rs:18,42 |
| 32 | `plugin-index.json` (marketplace catalog) | JSON | plugin-catalog | catalog.rs:23,31 |
| 33 | `~/.claude/plugins/known_marketplaces.json` | JSON | known-marketplaces | marketplace config.rs:184-220; marketplace.rs:152-176 |
| 34 | `~/.claude/plugins/installed_plugins.json` | JSON | installed-plugins | plugins discovery.rs:806-812 |
| 35 | `~/.grok/lsp.json` | JSON | lsp-config | lsp/mod.rs:99 |
| 36 | `<cwd>/.grok/lsp.json` (trust-gated) | JSON | lsp-config | lsp/mod.rs:99; folder_trust.rs:330-331 |
| 37 | `~/.grok/campaigns_state.json` | JSON | campaigns-state | loader.rs:375-386 |
| 38 | `crates/codegen/xai-grok-models/default_models.json` (embedded) | JSON | default-models | models lib.rs:15,27 |
| 39 | Remote settings payload (`GET /v1/settings`) | JSON | remote-settings | config-types lib.rs:421 |
| 40 | `<dir>/.claude/settings.json` (cwd..repo-root) | JSON | claude-settings-compat | claude_settings.rs:337-461 |
| 41 | `<dir>/.claude/settings.local.json` (cwd..repo-root) | JSON | claude-settings-compat | claude_settings.rs:337-461 |
| 42 | `~/.claude/settings.json` | JSON | claude-settings-compat | claude_settings.rs:337-461 |
| 43 | `~/.claude/settings.local.json` | JSON | claude-settings-compat | claude_settings.rs:337-461 |
| 44 | `/Library/Application Support/ClaudeCode/managed-settings.json` | JSON | managed-settings-compat | paths.rs:9-12,82-97 |
| 45 | `/etc/claude-code/managed-settings.json` | JSON | managed-settings-compat | paths.rs:9-12,82-97 |
| 46 | `AGENTS.md` / `AGENT.md` (cwd..repo-root, home) | Markdown | - | agents_md.rs:1,150-180 |
| 47 | `CLAUDE.md` / `CLAUDE.local.md` (compat) | Markdown | - | agents_md.rs:1 |
| 48 | `.grok/rules/*.md`, `~/.grok/rules/*.md` | Markdown | - | agents_md.rs:175 |
| 49 | `skills/*/SKILL.md` (local/repo/user/server/bundled tiers) | Markdown | - | prompt/skills.rs:49-58 |
| 50 | `agents/*.md` (agent definitions; also inside plugins) | Markdown | - | agent config.rs:2243 |
| 51 | `~/.grok/memory/MEMORY.md` | Markdown | - | memory_context.rs:105 |
| 52 | `~/.grok/plugins/` (local plugin dir) | dir | plugin-manifest per plugin | shell plugin.rs:1387 |
| 53 | `~/.grok/installed-plugins/` | dir | plugin-manifest per plugin | extensions_modal.rs:2252 |
| 54 | `~/.grok/workflows/` | dir | - | workflow/manager.rs:235 |
| 55 | `~/.grok/roles/`, `.grok/roles/` | dir | - | folder_trust.rs:390 |
| 56 | `~/.grok/personas/`, `.grok/personas/`, `~/.grok/bundled/personas/` | dir | - | agents_modal.rs:478-492 |
| 57 | `~/.grok/bundled/` (bundled skills/personas) | dir | - | prompt/skills.rs:324 |
| 58 | `~/.grok/marketplace-cache/<url-hash>/` (cache, excluded) | dir | - | marketplace git.rs:4,155 |
| 59 | `managed_config.sig.json`, `managed_identity.sig.json` (excluded) | JSON | - | signed_policy.rs:64,67,279,316 |
| 60 | `managed_config_cache.json` (excluded) | JSON | - | managed_cache.rs:15,172,182 |
| 61 | `~/.grok/logs/` (log output, excluded) | dir | - | telemetry hooks_log.rs:15-62 |

Unqualified filenames above abbreviate the crate paths spelled out in the
schema descriptions (e.g. `loader.rs` =
`crates/codegen/xai-grok-config/src/loader.rs`, `claude_settings.rs` =
`crates/codegen/xai-grok-workspace/src/permission/claude_settings.rs`,
`manifest.rs` = `crates/codegen/xai-grok-agent/src/plugins/manifest.rs`).

## Appendix B -- Environment variables

Compiled at SOURCE_REV `0f4d7c91b8b2b408333f6de1e8a76cb8eaa71899`. Test-only
knobs (`GROK_TEST_*`, `PTY_*`) are intentionally excluded.

### Centralized endpoint overrides (`crates/codegen/xai-grok-env/src/lib.rs`)

Prefix `GROK_PRODUCTION` (`env_prefix()` lib.rs:52-56, `resolve()` lib.rs:64-67).
All are string URLs overriding compiled defaults:

| Variable | Default | Source |
|---|---|---|
| `GROK_PRODUCTION_CLI_CHAT_PROXY_BASE_URL` | `https://cli-chat-proxy.grok.com/v1` | lib.rs:68-73 |
| `GROK_PRODUCTION_WS_ORIGIN` | `https://grok.com` | lib.rs:74-76 |
| `GROK_PRODUCTION_ASSET_SERVER_URL` | `https://assets.grok.com` | lib.rs:77-79 |
| `GROK_PRODUCTION_WS_URL` | `wss://code.grok.com/ws/code-agent` | lib.rs:83-85 |
| `GROK_PRODUCTION_GATEWAY_WS_URL` | `wss://grok.com/ws/gw/` | lib.rs:88-90 |

### Auth / endpoints

- `XAI_API_KEY` [auth_method.rs:26]; legacy alias `GROK_CODE_XAI_API_KEY` [:30]
- `GROK_XAI_API_BASE_URL` [voice_probe.rs:121]
- `GROK_GATEWAY_URL` [shell-base env.rs:23,44]; `GROK_DISABLE_CUSTOM_BRIDGE` [:29,33]
- Auth family (xai-grok-shell): `GROK_AUTH_JSON`, `GROK_AUTH_PATH`, `GROK_AUTH`,
  `GROK_LOCAL_AUTH`, `GROK_OAUTH2_*`, `GROK_OIDC_*`, `GROK_DISABLE_API_KEY_AUTH`

### Paths

- `GROK_HOME` -- overrides `~/.grok` [config paths.rs:38-39]

### Agent behavior

- `GROK_CHAT_MODE` [chat_modes.rs:20]; `GROK_AGENT` [tools util/env.rs:15]
- `GROK_AUTO_PERMISSION_MODE` [resolve/auto_mode.rs:5];
  `GROK_REMEMBER_TOOL_APPROVALS` [resolve/tool_approvals.rs:5]
- `GROK_SYSTEM_PROMPT_LABEL` [resolve/system_prompt.rs:1];
  `GROK_CONTEXTUAL_HINTS` [config/hints.rs:233]
- `GROK_SESSION_REGISTRY` [util/config/mcp.rs:1418];
  `GROK_GOAL_USE_CURRENT_MODEL_ONLY` [agent/config.rs:9685]
- `GROK_SCHEDULER_BACKGROUND_LOOPS`, `GROK_LOGIN_ENV` [resolve/toolset.rs:196,65];
  `GROK_ASK_USER_QUESTION_TIMEOUT_ENABLED` / `_SECS` [ask_user_question/mod.rs:70]

### Compaction

- `GROK_COMPACTION_TOOL_CHOICE`, `GROK_AUTO_COMPACT_THRESHOLD_PERCENT`,
  `GROK_COMPACTION_WALL_CLOCK_SECS` [resolve/compaction.rs:23,38,134]
- `GROK_COMPACTION_MODE`, `GROK_COMPACTION_DETAIL` (set via hidden CLI flags),
  `GROK_COMPACTION_VERBATIM_INPUT`, `GROK_TWO_PASS_COMPACTION`

### UI / rendering (xai-grok-pager unless noted)

- `GROK_SHOW_THINKING_BLOCKS`, `GROK_GROUP_TOOL_VERBS`,
  `GROK_COLLAPSED_EDIT_BLOCKS` [resolve/ui.rs:5,55,84]
- `GROK_SCREEN_MODE` [screen_mode_relaunch.rs:23]; `GROK_COPY_FILE`
  [pager-render clipboard/mod.rs:22]
- `GROK_ESC_DOUBLE_PRESS_MS` [app_view.rs:530];
  `GROK_PROMPT_SUGGESTIONS` / `_MODEL` [prompt_suggestion.rs:23,27];
  `GROK_SUGGESTIONS_AI_MODEL` [suggestion_controller/mod.rs:344]
- `GROK_FPS` [fps_hud.rs:56]; `GROK_SCROLL_DEBUG` [scroll_debug_hud.rs:48];
  `GROK_SCROLL_LOG` [input/scroll_log.rs:179]; `GROK_NERD_FONTS`
  [git_info.rs:319]; `GROK_SESSION_PICKER_GROUPED` [event_loop.rs:872];
  `GROK_OPEN_DASHBOARD_AT_STARTUP` [event_loop.rs:1652];
  `GROK_AGENT_DASHBOARD` [dashboard/mod.rs:89]; `GROK_HUNK_TRACKER`
  [app/mod.rs:569]; `GROK_SUBSCRIPTION_WATCH_INTERVAL_SECS` [subscription.rs:67]
- Terminal detection (std): `NO_COLOR`, `TERM_PROGRAM`, `TERM`,
  `ITERM_SESSION_ID`/`PROFILE`, `WEZTERM_VERSION`, `KITTY_WINDOW_ID`,
  `ALACRITTY_SOCKET` [markdown colors.rs:56-129]; `SHELL`, `EDITOR`, `VISUAL`,
  `PAGER`, `PATH` (external tool resolution)

### Sandbox / shell

- `GROK_SANDBOX` (also clap env fallback for `--sandbox`) [pager cli.rs:658];
  `GROK_SANDBOX_PROFILE`; `GROK_SANDBOX_AUTO_ALLOW_BASH`
- `GROK_SHELL` (Windows shell override: pwsh|powershell|bash|cmd)
  [config shell.rs:35]

### Telemetry / logging (xai-grok-telemetry)

- `GROK_EXTERNAL_OTEL` [external/config.rs:46]; `GROK_INSTRUMENTATION` / `_LOG`
  [instrumentation.rs:18-19]; `GROK_OTEL_FILTER` [otel_layer/mod.rs:18]
- `GROK_HOOKS_LOG` [hooks_log.rs:15,35,62 -- `=1` enables, a path value
  overrides `~/.grok/logs/hooks.log`]; `GROK_MEMORY_LOG` [memory_log.rs:40];
  `GROK_LOG_SAMPLING` (also clap env for `--log-sampling`) [sampling_log.rs:15];
  `GROK_LOG_FILE`, `GROK_DEBUG_LOG` [debug_log.rs:420-421]
- `GROK_TELEMETRY_SPECIAL_USER` [id.rs:153]; `XAI_ROOT` + `XAI_USER`
  (internal-user detection) [id.rs:144]; `SENTRY_DSN` [sentry.rs:39]
- `GROK_CLIENT_NAME`, `GROK_CLIENT_VERSION` [http.rs:15,19]
- Build-time (`option_env!`, baked): `GROK_TELEMETRY_BUILD_EVENTS_URL`,
  `_EVENTS_API_KEY`, `_MIXPANEL_TOKEN` [config.rs:131-133]

### Workspace / worktree / infra

- `GROK_WORKSPACE_COMMAND` (enables hidden `workspace` subcommand)
  [pager-bin main.rs:368]; `GROK_WORKSPACE_*` family (timeouts/backoffs/rewind,
  e.g. `GROK_WORKSPACE_REWIND_GIT` [workspace session/git.rs:1937])
- `GROK_WORKTREE_AUTO_GC`, `_DRY_RUN`, `_MAX_AGE`, `_REBUILD`
  [fast-worktree auto_gc.rs:19-25]
- `GROK_SQLITE_JOURNAL_MODE` [sqlite-journal lib.rs:40];
  `GROK_FSNOTIFY_SAPLING`, `_PER_DIR`, `_MAX_WATCHES`
  [fsnotify watcher.rs:124,146,163]
- `GROK_GIX_STATUS_THREADS` [gix-status lib.rs:14];
  `GROK_UPLOAD_QUEUE_AUTH_PROBE_SECS`, `_MAX_BYTES`
  [file-utils queue.rs:582,598]
- `XAI_TOOL_SERVER_GLOBAL_MAX_INFLIGHT` [computer-hub-sdk admission.rs:41];
  `XAI_VALIDATE_TYPE_TIMEOUT_MS` [task/backend.rs:301];
  `GROK_MAX_MCP_OUTPUT_BYTES` [tools mcp_truncate.rs:41]

### Managed config / misc overrides

- `GROK_MANAGED_CONFIG_FAIL_CLOSED` [config validation.rs:30];
  `GROK_MANAGED_CONFIG_URL` [shell agent/config.rs:190,561]
- `GROK_ANNOUNCEMENTS_OVERRIDE` (JSON) [announcements lib.rs:211];
  `GROK_CAMPAIGNS`, `GROK_CAMPAIGNS_OVERRIDE` [config loader.rs:353,271];
  `GROK_TIPS_OVERRIDE` [util/config/tips.rs]
- `GROK_LEADER_SOCKET` [shell leader/lock.rs:44]; `GROK_AGENT_SECRET`
  (clap env for `agent serve --secret`) [cli.rs:356]
- `GROK_VERSION` (build-time) [version lib.rs:7]
- Note: `GROK_CHANNEL` is NOT a runtime var (test-only); the release channel is
  set via `grok update --alpha/--stable/--enterprise`

## Appendix C -- CLI flags (binary `grok`)

Parser: `PagerArgs` (derive `Parser`) at
`crates/codegen/xai-grok-pager/src/app/cli.rs:399-420`; subcommand enum
`Command` at cli.rs:8-143; parsed via `parse_from` at cli.rs:782. Exactly 3
clap env fallbacks: `--sandbox` = `GROK_SANDBOX` (cli.rs:658),
`agent serve --secret` = `GROK_AGENT_SECRET` (cli.rs:356),
`--log-sampling` = `GROK_LOG_SAMPLING` (cli.rs:709).

### Top-level flags (cli.rs:420-734)

| Flag | Notes | Line |
|---|---|---|
| `--version` / `-v` | | 420 |
| `--cwd` | | |
| `--leader-socket` | | |
| `--debug`, `--debug-file` | | |
| `--always-approve` | aliases `--yolo`, `--dangerously-skip-permissions` | 447 |
| `--trust` | | 454 |
| `--allow` | alias `--allowedTools` | 457 |
| `--deny` | alias `--disallowedTools` | 465 |
| `--single` / `-p` | alias `--print` | 473 |
| `--prompt-json` | | 483 |
| `--prompt-file` | | 491 |
| `--verbatim` | | 500 |
| `--output-format` | default `plain` | 503 |
| `--json-schema` | structured-output schema | 508 |
| `--model` / `-m` | | 511 |
| `--reasoning-effort` | alias `--effort` | 514 |
| `--rules` | alias `--append-system-prompt` | 522 |
| `--compaction-mode` | hidden | 527 |
| `--compaction-detail` | hidden | 532 |
| `--system-prompt-override` | | 535 |
| `--resume` / `-r` | | 542 |
| `--continue` / `-c` | | 560 |
| `--session-id` / `-s` | | 572 |
| `--fork-session` | | 576 |
| `--worktree` / `-w` | | 579 |
| `--worktree-ref` | | 583 |
| `--restore-code` | | 586 |
| `--no-plan` | | 589 |
| `--no-subagents` | | 592 |
| `--no-ask-user` | hidden | 595 |
| `--experimental-memory` | | 598 |
| `--no-memory` | | 601 |
| `--agent` | | 604 |
| `--agents` | JSON value | 607 |
| `--tools` | | 610 |
| `--disallowed-tools` | | 613 |
| `--max-turns` | | 616 |
| `--permission-mode` | | 623 |
| `--disable-web-search` | | 632 |
| `--no-wait-for-background` | hidden | 640 |
| `--background-wait-timeout` | hidden, default 600 | 648 |
| `--sandbox` | env `GROK_SANDBOX` | 658 |
| `--storage-mode` | hidden | 661 |
| `--client-identifier` | hidden | 664 |
| `--hunk-tracker-mode` | hidden | 668 |
| `--terminal` | hidden | 671 |
| `--fs-read` | hidden | 674 |
| `--fs-write` | hidden | 677 |
| `--no-auto-update` | hidden | 680 |
| `--todo-gate` | hidden | 687 |
| `--installer` | hidden | 690 |
| `--no-alt-screen` | | 693 |
| `--minimal` | | 700 |
| `--fullscreen` | | 706 |
| `--log-sampling` | hidden, env `GROK_LOG_SAMPLING` | 709 |
| `--force-login` | hidden | 712 |
| `--oauth` | | 715 |
| `--leader` | hidden | 718 |
| `--no-leader` | hidden | 721 |
| `[PROMPT]` | positional | 724 |

### Subcommands (cli.rs:8-143)

- `agent` (`AgentArgs` cli.rs:253-304: `--reauth`, `--model`,
  `--reasoning-effort`, `--always-approve`, `--agent-profile`, `--plugin-dir`,
  `--leader`/`--no-leader`; `HeadlessArgs` cli.rs:342-348:
  `--grok-ws-origin`/`--grok-ws-url`; subcommands `stdio` | `headless` |
  `serve` | `leader`; `ServeArgs` cli.rs:350-364: `--bind` default
  `127.0.0.1:2419`, `--secret`, `--remote`; `LeaderArgs` cli.rs:379-398)
- `inspect`, `doctor`, `leader` (`list` | `info` | `kill`), `logout`,
  `login` (`--oauth`/`--oidc`, `--device-auth`), `mcp`, `plugin`, `memory`,
  `models`, `sessions`, `setup`, `share` (hidden), `wrap`, `export`, `trace`
- `update` (`--check`, `--json`, `--force-reinstall`, `--version`,
  `--alpha` | `--stable` | `--enterprise` mutually exclusive)
- `version`, `completions <shell>`, `worktree`, `workspace` (hidden, gated by
  `GROK_WORKSPACE_COMMAND=1`; `WorkspaceStartArgs` cli.rs:234-251), `dashboard`
