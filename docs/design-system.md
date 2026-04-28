# Design System — LLM Gateway Console

**Date:** 2026-04-28
**Owner:** ihor.roid@aretihealth.com
**Source specs:** `tech-task.txt`, `spec-addons.md`
**Scope:** Vue.js web console (tenant dashboard, admin UI, public status page, docs portal, future CLI/IDE surfaces).

This is a *new-system extend* output — there is no prior UI to audit. It defines the foundational tokens, core components, and product patterns required to ship the Stage 1 MVP UI and to scale through Stage 3 without a rewrite.

---

## 1. Principles

1. **Operator-first density.** ICP users are developers and platform admins. They scan tables, not hero sections. Default to high information density; never sacrifice scanability for whitespace.
2. **Numbers must be trustworthy.** Tokens, spend, latency, and quota figures appear everywhere. Use tabular numerals, consistent units, and never abbreviate currency without the exact value on hover.
3. **Status is a first-class citizen.** Health, mode (sandbox/live), and policy outcome (allow/warn/block) drive most workflows. Promote status to a token-level concept.
4. **Boring beats clever.** This is a compliance product. Customers should feel the UI was built by people who write audit logs. Avoid playful motion, surprise interactions, or non-standard widgets.
5. **Composability over rigidity.** Components expose slots and props rather than encoding tenant-specific layouts.
6. **Document what ships.** A component without a documented variant set, state matrix, and a11y contract does not ship.

---

## 2. Design Tokens

Tokens are framework-agnostic JSON, consumed by Vue/Tailwind via a build step. Three layers: **primitive → semantic → component**. Components reference *only* semantic tokens.

### 2.1 Color — primitives

Single-source hue ramps (50–950) for: `gray`, `blue`, `green`, `amber`, `red`, `violet`, `teal`. WCAG AA contrast verified for 600+ on white and 300– on `gray-900`.

### 2.2 Color — semantic

| Token | Light | Dark | Use |
|---|---|---|---|
| `surface.canvas` | `gray-50` | `gray-950` | App background |
| `surface.raised` | `white` | `gray-900` | Cards, panels |
| `surface.sunken` | `gray-100` | `gray-900` | Code blocks, log rows |
| `surface.overlay` | `white` α95 | `gray-900` α95 | Modals, popovers |
| `border.default` | `gray-200` | `gray-800` | Dividers |
| `border.strong` | `gray-300` | `gray-700` | Inputs, focus rings |
| `text.primary` | `gray-900` | `gray-50` | Body |
| `text.secondary` | `gray-600` | `gray-400` | Labels, meta |
| `text.tertiary` | `gray-500` | `gray-500` | Placeholders, disabled |
| `text.inverse` | `white` | `gray-950` | On filled buttons |
| `accent.brand` | `blue-600` | `blue-500` | Primary actions, links |
| `status.success` | `green-600` | `green-500` | Allow, healthy, paid |
| `status.warning` | `amber-600` | `amber-500` | Warn-mode, near quota |
| `status.danger` | `red-600` | `red-500` | Block, failure, over budget |
| `status.info` | `blue-600` | `blue-500` | Neutral notices |
| `status.neutral` | `gray-500` | `gray-500` | Pending, unknown |
| `mode.live` | `green-600` | `green-500` | Production keys/requests |
| `mode.sandbox` | `violet-600` | `violet-500` | Test-mode keys (spec-addons §2.3) |
| `policy.allow` | `green-600` | `green-500` | Guardrail outcome |
| `policy.warn` | `amber-600` | `amber-500` | Guardrail outcome |
| `policy.block` | `red-600` | `red-500` | Guardrail outcome |

`mode.sandbox` is intentionally violet (not gray) so sandbox traffic is *visually obvious* in mixed dashboards — this prevents "thought I was in sandbox" billing surprises.

### 2.3 Typography

| Role | Family | Size | Weight | LH | Tracking |
|---|---|---|---|---|---|
| `display` | Inter | 30 | 600 | 36 | -1% |
| `h1` | Inter | 24 | 600 | 32 | -1% |
| `h2` | Inter | 20 | 600 | 28 | 0 |
| `h3` | Inter | 16 | 600 | 24 | 0 |
| `body` | Inter | 14 | 400 | 20 | 0 |
| `body-strong` | Inter | 14 | 500 | 20 | 0 |
| `small` | Inter | 12 | 400 | 16 | 0 |
| `label` | Inter | 12 | 500 | 16 | +2% (uppercase optional) |
| `code` | JetBrains Mono | 13 | 400 | 20 | 0 |
| `numeric` | Inter `tnum` | inherit | inherit | inherit | 0 |

`numeric` is a feature-flag variant (`font-feature-settings: "tnum"`) applied to *every* surface that renders tokens, cost, latency, counts, or quota. Non-negotiable.

### 2.4 Spacing

4-px base. Scale: `0, 1(4), 2(8), 3(12), 4(16), 5(20), 6(24), 8(32), 10(40), 12(48), 16(64), 20(80), 24(96)`.

Component-level paddings reference scale tokens, never raw px.

### 2.5 Radius

`none(0), sm(4), md(6), lg(8), xl(12), pill(9999)`. Default for cards/inputs/buttons: `md`. Modals: `lg`. Badges: `pill`.

### 2.6 Elevation

| Token | Shadow | Use |
|---|---|---|
| `flat` | none | Default cards in dense views |
| `raised` | `0 1px 2px rgba(0,0,0,.06), 0 1px 3px rgba(0,0,0,.04)` | Hover, dropdown triggers |
| `overlay` | `0 8px 24px rgba(0,0,0,.12)` | Popovers, menus |
| `modal` | `0 24px 48px rgba(0,0,0,.18)` | Dialogs |

Dark mode replaces shadows with `border.default` borders — shadow is not legible on dark surfaces.

### 2.7 Motion

| Token | Duration | Easing | Use |
|---|---|---|---|
| `instant` | 80ms | linear | State swaps, focus |
| `quick` | 160ms | `cubic-bezier(.2,.0,.0,1)` | Hover, tooltip |
| `standard` | 240ms | `cubic-bezier(.2,.0,.0,1)` | Modal, drawer |
| `slow` | 400ms | `cubic-bezier(.4,.0,.2,1)` | Page transitions (rare) |

Respect `prefers-reduced-motion: reduce` — drop all transitions to `instant`.

### 2.8 Z-index

`base(0), sticky(100), dropdown(1000), drawer(1100), modal(1200), toast(1300), tooltip(1400)`. Never inline z-index outside this scale.

---

## 3. Component Inventory

Three-tier inventory: **MVP (Stage 1)**, **Stage 2**, **Stage 3**. Each component must ship with: prop API, variant matrix, state matrix, a11y contract, and Storybook entry.

### 3.1 MVP components

| # | Component | Why MVP needs it |
|---|---|---|
| 1 | Button | Actions everywhere |
| 2 | IconButton | Table row actions |
| 3 | TextField | Forms, search |
| 4 | Select | Provider/model pickers |
| 5 | Combobox | Searchable model picker, tag picker |
| 6 | Checkbox / Radio / Switch | Settings, scopes |
| 7 | Textarea | Prompt/response viewers (read-only variant), policy notes |
| 8 | FormField (label+hint+error wrapper) | Consistent form a11y |
| 9 | Badge | Status, mode, policy outcome |
| 10 | StatusDot | Compact health in tables |
| 11 | Tag / Chip | Request tags (spec-addons §2.4), key scopes |
| 12 | Card | Dashboard panels |
| 13 | StatCard | KPI tiles (tokens, spend, p95, error rate) |
| 14 | DataTable | The workhorse — usage logs, keys, audit |
| 15 | Pagination | Tables |
| 16 | Tabs | Section nav inside detail pages |
| 17 | Breadcrumb | Tenant → Resource → Detail nav |
| 18 | Sidebar / NavRail | Primary nav |
| 19 | Topbar | Tenant switcher, user menu, mode toggle |
| 20 | Modal | Confirms, key creation |
| 21 | Drawer | Log row detail (right side) |
| 22 | Toast | Async confirmations, errors |
| 23 | InlineAlert | Page/section-level messages |
| 24 | Tooltip | Token/cost explanations |
| 25 | Popover | Filter pickers, "what is this?" |
| 26 | Menu | Row actions, kebab menus |
| 27 | CodeBlock | Quickstart curl/JSON, prompt/response |
| 28 | KeyValueList | Request metadata |
| 29 | Skeleton | Loading state for cards/tables |
| 30 | EmptyState | First-run, no-results |
| 31 | ErrorState | Retryable failure |
| 32 | ProgressBar | Quota fill, onboarding stepper |
| 33 | MeterBar | Multi-segment quota (used / pending / available) |
| 34 | Stepper | Self-serve onboarding wizard |
| 35 | DateRangePicker | Usage filters |
| 36 | NumberInput (currency / token-aware) | Quota caps, budget |
| 37 | CopyButton | API keys, request IDs |
| 38 | RevealField | One-time secret reveal (key creation) |
| 39 | DiffViewer | Prompt versions (basic; richer in Stage 2) |
| 40 | Toggle (mode) | Live ↔ Sandbox global toggle |

### 3.2 Stage 2 components

Chart primitives (`LineChart`, `BarChart`, `StackedAreaChart`, `Sparkline`) with shared legend, tooltip, and empty/loading wrappers. `JSONTree` for log payload inspection. `RuleBuilder` for the policy builder UI. `ApprovalCard` for human-review queue. `ProviderHealthGrid` for the public status page. `WebhookTester`. `RegionPicker`. `SSOConfigCard`.

### 3.3 Stage 3 components

`KMSKeyCard` (BYOK). `EvidencePackBuilder`. `LineagePath` (linear stage diagram). `ReplayPanel`. `RecommendationCard` (optimization suggestions). `SIEMConnectorCard`. `FeedbackThumbs` (embedded in tenants' apps via JS snippet — published as a separate package but designed here). `CLIInstallPanel`.

---

## 4. Detailed component specs (MVP critical path)

Specs below are normative for the four components most reused in the product. Remaining components follow the same template in Storybook.

### 4.1 Button

| Variant | Use |
|---|---|
| `primary` | One per view — the committing action |
| `secondary` | Non-committing or alternative actions |
| `ghost` | In-row actions, toolbars |
| `danger` | Destructive (revoke key, delete tenant) |
| `link` | Inline navigation that looks like text |

**Sizes:** `sm(28px)`, `md(36px)`, `lg(44px)`. Default `md`.

**States:** default, hover, active, focus-visible, disabled, loading, success (transient).

**Props:** `variant`, `size`, `iconLeading`, `iconTrailing`, `loading`, `disabled`, `fullWidth`, `as` (renders `<a>` for links), `aria-label` (required when icon-only).

**A11y:** focus ring uses `border.strong` + 2px outset offset. `loading` sets `aria-busy=true` and disables pointer events but does *not* remove from tab order. Danger variant requires confirm modal for irreversible ops.

**Don'ts:** never two primaries in one view; never use `danger` for "Cancel"; never rely on color alone — destructive icons reinforce intent.

### 4.2 DataTable

The single most-used component. Spec must be tight.

**Capabilities:** column resize, column show/hide, sticky header, sticky first column (optional), row selection (single/multi), sort (single column MVP, multi in Stage 2), per-column filter, server-side pagination, density (`comfortable` / `compact` / `dense`), row click → drawer or route, kebab menu per row, bulk action bar on selection.

**Cell types:** text, numeric (tabular), code, badge, status-dot, timestamp (relative + absolute on hover), money, tokens (with K/M abbreviation + exact on hover), tags, key-id (monospace + copy on hover), avatar+name, action menu.

**States:** loading (Skeleton rows match column widths), empty (EmptyState slot), error (ErrorState with retry), partial (some rows loaded, more streaming).

**A11y:** real `<table>` with `<thead>`/`<tbody>`, `scope="col"`, sortable headers as `<button>` with `aria-sort`. Row selection checkboxes have visible labels (sr-only). Keyboard: arrow keys move focus by cell, `Enter` opens row drawer, `Space` toggles selection.

**Performance budget:** 100 rows render < 50ms; virtualize beyond 200 rows.

**Don'ts:** no horizontal scroll without sticky first column; no truncation without tooltip showing full value; no client-side filtering on datasets > 1k rows.

### 4.3 StatCard

KPI tile used on every dashboard.

**Anatomy:** label (top, `label` token) → value (large, `numeric`) → delta (vs. prior period, color-coded by direction-good/bad — *not* by sign, because "fewer errors" is good while "fewer requests" may be bad) → sparkline (optional) → footnote (optional, e.g. "excludes sandbox").

**Variants:** `default`, `currency`, `tokens`, `latency`, `percent`. Each chooses formatter, unit, and abbreviation rules.

**States:** loading (skeleton with same heights — no layout shift), empty ("No data for this range"), error.

**A11y:** value has accessible name including unit ("1,240 dollars" not "$1,240").

### 4.4 Badge

Compact status pill — drives most "what's going on?" scanning.

**Tones:** `neutral`, `info`, `success`, `warning`, `danger`, `mode-live`, `mode-sandbox`, `policy-allow`, `policy-warn`, `policy-block`.

**Variants:** `solid` (filled), `subtle` (tinted bg, colored text), `outline`. Default `subtle` — least visually noisy in dense tables.

**Sizes:** `sm(20px)`, `md(24px)`.

**A11y:** color is reinforced by an icon glyph for `policy-*` and `mode-*` (colorblind users must distinguish allow/block without color).

---

## 5. Patterns

Patterns combine components for recurring product flows. Each is documented separately in Storybook with a working composition.

### 5.1 Resource list page

Layout: page header (title, primary action, secondary actions) → filter bar (search, date range, mode toggle, tag picker) → DataTable → pagination. Used for: Keys, Usage logs, Audit log, Webhooks, Incidents, Tenants (admin), Users.

### 5.2 Detail drawer

Right-side drawer (640px) for log row inspection: header (request ID + copy, timestamp, status badge) → tabs (Overview / Request / Response / Guardrails / Cost / Trace) → footer actions (Replay, Open in new tab). Drawer over modal because users frequently inspect adjacent rows.

### 5.3 Self-serve onboarding wizard

5-step Stepper: Create tenant → Add provider key → Issue platform key → Send test request → See in dashboard. Each step is resumable; progress persists server-side. Step 4 includes a CodeBlock with copyable curl + a "Run from here" button.

### 5.4 Policy / guardrail editor

Two-column: left = RuleBuilder (Stage 2), right = live preview testing the rule against pasted prompt/response. Saving prompts a confirmation if the rule is set to `block` mode and currently-live traffic would be affected — show projected block rate from the last 24h.

### 5.5 Quota & spend confirm

Any action that raises a quota or removes a hard cap requires a Modal that restates the current value, the new value, the projected monthly impact in dollars, and a typed confirmation matching the tenant slug. Aligned with finance team's audit expectations.

### 5.6 Key reveal flow

Creating an API key: form → submit → modal showing key *once* via `RevealField` + CopyButton + checkbox "I've stored this securely" (required to dismiss). Closing the modal removes the secret from memory. Never re-display.

### 5.7 Mode switch (Live / Sandbox)

Global toggle in the Topbar, persistent per user-tenant pair. While in Sandbox: Topbar is tinted with `mode.sandbox`, every StatCard prefixes "Sandbox", and the DataTable filter is locked to `env: sandbox`. Prevents the "billed for tests" failure mode.

### 5.8 Empty / first-run

Every list view ships with an EmptyState that includes: explanation, primary CTA to the create flow, secondary CTA to docs, and an inline curl example for API-first users.

### 5.9 Public status page

Standalone surface (`status.<domain>`), no auth, no internal nav. Sections: overall status (green/amber/red banner) → ProviderHealthGrid → active incident timeline → 90-day uptime history → subscribe CTA. Same tokens, simplified component set (no DataTable, no Drawer).

### 5.10 Loading & error

- Skeletons match the *eventual* layout; no spinners on content regions.
- Toasts only for *async* confirmations (save, copy, send). Never for navigation outcomes.
- ErrorState for retryable failures; redirects for unauthorized; full-page boundary for unhandled.

---

## 6. Accessibility contract

- WCAG 2.1 AA across all surfaces.
- Color contrast verified at token-pair level in CI.
- Keyboard support: all interactive components reachable, focus-visible always rendered, no focus traps outside modals/drawers, modals trap focus and restore on close.
- Screen reader: every form field has a programmatic label; tables use proper semantics; live regions announce toasts and async errors.
- Respect `prefers-reduced-motion` and `prefers-color-scheme`.
- Touch targets ≥ 36px (compact-density tables relax to 28px with confirmed user research; defer to Stage 2).

---

## 7. Information density modes

Three density presets at the app level: `comfortable`, `compact` (default), `dense`. Affects table row height, card padding, and form spacing — driven by a single token swap. `dense` is required for power-users on large monitors monitoring live logs; `comfortable` is the default first impression for new tenants.

---

## 8. Theming

Light + dark from day one, switched via OS pref by default and overridable per user. No tenant white-labelling in MVP — Stage 3 introduces `accent.brand` override per tenant in the admin product (not per end-user).

---

## 9. Implementation stack

- **Component library base:** Headless UI (Vue) or Radix-Vue for behavior, our tokens for styling. Not a heavy opinionated library — we want to own the look.
- **Styling:** Tailwind with our token preset; no raw hex / px in components.
- **Charts:** ECharts wrapped in our chart primitives (sane defaults, our tokens, no library-default tooltips).
- **Icons:** Lucide (consistent stroke). Single icon set, no mixing.
- **Storybook:** required for every component; doubles as the design-system docs site.
- **Visual regression:** Chromatic or Playwright + pixelmatch on the Storybook stories.
- **Token pipeline:** Style Dictionary → CSS vars + Tailwind preset + (later) iOS/Android exports.

---

## 10. Versioning & migration

- SemVer at the design-system package level.
- Breaking changes ship behind a codemod or a deprecation period of one minor.
- Component removals require a 1-release deprecation with a console warning.
- Token renames require both old and new to resolve for one minor.

---

## 11. Open questions

- **Brand identity:** color `accent.brand` is placeholder `blue-600`. Needs brand decision before public beta.
- **Logo & wordmark:** out of scope here; design-system absorbs them once defined.
- **Marketing site styling:** uses tokens but its own components — not part of this system.
- **Tenant-level theming in Stage 3:** scope and limits TBD (accent only? logo? full theme?).
- **Mobile:** not in MVP. Console is desktop-first; status page is responsive.
- **i18n:** copy is English-only in MVP; component APIs already localization-ready (no hardcoded strings inside components).

---

## 12. Build order

| Sprint | Deliverable |
|---|---|
| 1 | Token pipeline, Storybook, Button, TextField, FormField, Badge, Card, Tooltip, Toast, InlineAlert |
| 2 | DataTable v1 (no virtualization), Pagination, Tabs, Breadcrumb, Sidebar, Topbar, Mode toggle |
| 3 | Modal, Drawer, Menu, Popover, CodeBlock, CopyButton, RevealField, EmptyState, ErrorState, Skeleton |
| 4 | StatCard, MeterBar, ProgressBar, Stepper, DateRangePicker, NumberInput, Combobox, Tag |
| 5 | Onboarding wizard pattern, Resource list pattern, Detail drawer pattern, Key reveal pattern, Quota confirm pattern |
| 6 | Visual regression in CI, a11y CI (axe), token contrast CI, dark mode pass |

Stage 2 / 3 components are layered on this base without breaking changes.

---

## 13. Definition of Done (per component)

- [ ] Prop API documented in Storybook.
- [ ] All variants × all states rendered as Storybook stories.
- [ ] Light + dark snapshots pass visual regression.
- [ ] axe-core: zero violations.
- [ ] Keyboard interaction matrix verified.
- [ ] Unit tests: happy path + critical negative paths.
- [ ] Used by at least one real product surface (no orphan components).
