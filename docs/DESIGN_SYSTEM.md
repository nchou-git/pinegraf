# Pinegraf Design System

`frontend/styles/tokens.css` is the source of truth. This document explains why the token catalog exists, what each group is for, and how future work should extend it without scattering hard-coded design values across the app.

## Color Tokens

| Token | Value | Rationale |
| --- | --- | --- |
| `--green` | &#35;00693E | Dartmouth primary green anchors brand and primary action states. |
| `--green-hover` | &#35;005A35 | Darkens the primary green only for active hover feedback. |
| `--green-tint` | &#35;EAF3EC | Provides low-contrast green surfaces for selected and focus states. |
| `--green-tint-2` | &#35;D5E8D9 | Gives green-tinted controls a slightly stronger border or fill. |
| `--conflict` | &#35;B8541F | Marks conflicts and warnings without reusing brand green. |
| `--conflict-bg` | &#35;FCEEE6 | Supplies the pale background for conflict pills and warning surfaces. |
| `--text` | &#35;1A1A1A | Main foreground color for readable app text. |
| `--text-muted` | &#35;555555 | Secondary foreground for labels, metadata, and helper text. |
| `--text-faint` | &#35;888888 | Tertiary foreground for quiet chrome; this is borderline for AA on white and should be used sparingly. |
| `--line` | &#35;E8E8E8 | Default hairline border for app structure. |
| `--line-strong` | &#35;CCCCCC | Stronger border for controls that need clearer boundaries. |
| `--surface` | &#35;F8F9F8 | Subtle off-white surface for panels, rows, and selected chips. |
| `--surface-2` | &#35;FAFAFA | Alternate quiet surface for nested or secondary regions. |
| `--bg` | &#35;FFFFFF | Base application background and white control fill. |

## Typography Tokens

| Token | Value | Rationale |
| --- | --- | --- |
| `--font-sans` | system sans stack | Keeps the app native-feeling and fast without a font dependency. |
| `--fs-xs` | 12px | Smallest metadata and status text. |
| `--fs-sm` | 13px | Compact labels, badges, and table metadata. |
| `--fs-base` | 14px | Default dense application text. |
| `--fs-md` | 15px | Readable controls and primary row content. |
| `--fs-lg` | 17px | Larger labels and modest section headings. |
| `--fs-xl` | 22px | Page-level secondary headings. |
| `--fs-2xl` | 26px | Top-level focused headings such as Ask empty state. |
| `--lh-tight` | 1.2 | Short headings. |
| `--lh-snug` | 1.4 | Dense metadata and compact paragraphs. |
| `--lh-normal` | 1.5 | Prose and answer text. |

Allowed font weights are `400` and `500`. The app does not use italic text.

## Spacing Tokens

| Token | Value | Rationale |
| --- | --- | --- |
| `--space-1` | 4px | Small icon and control offsets. |
| `--space-2` | 8px | Tight gaps inside dense controls. |
| `--space-3` | 12px | Default compact padding. |
| `--space-4` | 16px | Standard component padding. |
| `--space-5` | 20px | Intermediate spacing when 16px is too tight. |
| `--space-6` | 24px | Section padding and page rhythm. |
| `--space-8` | 32px | Primary page gutter and large gaps. |
| `--space-10` | 40px | Large vertical offsets. |
| `--space-12` | 48px | Empty-state and major section spacing. |
| `--space-16` | 64px | Largest standard vertical rhythm. |

Spacing follows a 4px base grid. Margins, padding, and gaps should resolve to these tokens unless the value is `0`, a percentage, a viewport unit, or a border width.

## Radius Tokens

| Token | Value | Rationale |
| --- | --- | --- |
| `--radius-sm` | 6px | Buttons and compact controls. |
| `--radius-md` | 8px | Menus, cards, and standard panels. |
| `--radius-lg` | 12px | Larger modal and composer surfaces. |
| `--radius-pill` | 999px | Pills, badges, and circular affordances. |

## Shadow Tokens

| Token | Value | Rationale |
| --- | --- | --- |
| `--shadow-sm` | `0 1px 2px rgba(26, 26, 26, 0.04)` | Subtle lift for tiny overlays. |
| `--shadow-md` | `0 4px 12px rgba(26, 26, 26, 0.06)` | Standard shadow for menus and pinned bars. |
| `--shadow-lg` | `0 12px 32px rgba(26, 26, 26, 0.10)` | Highest elevation for drawers and modals. |

Shadows derive from the text color family so elevation remains neutral.

## Border Tokens

Allowed border widths are `1px` for normal borders and `2px` for focus rings. This keeps the dense app interface sharp without half-pixel rendering drift or oversized outlines.

## Motion Tokens

| Token | Value | Rationale |
| --- | --- | --- |
| `--motion` | 80ms ease | Fast feedback for workspace tooling without decorative animation. |

Transitions are limited to `background-color`, `border-color`, `color`, `opacity`, and `transform`.

## Z-Index Tokens

| Token | Value | Rationale |
| --- | --- | --- |
| `--z-base` | 0 | Default stacking context. |
| `--z-sticky` | 50 | Sidebar and sticky layout chrome. |
| `--z-dropdown` | 100 | Menus and popovers. |
| `--z-modal` | 200 | Modals and drawers. |
| `--z-toast` | 300 | Toasts above all other UI. |

Use these tokens instead of magic numbers.

## Layout Tokens

| Token | Value | Rationale |
| --- | --- | --- |
| `--sidebar-width-expanded` | 260px | Standard sidebar width for labels and status. |
| `--sidebar-width-collapsed` | 60px | Icon-only sidebar width. |
| `--content-max-width` | 1280px | Keeps dense data pages readable on wide displays. |
| `--content-gutter` | `var(--space-8)` | Standard horizontal page gutter. |
| `--breakpoint-mobile` | 820px | Boundary where the sidebar becomes an off-canvas drawer. |

## Component Size Tokens

| Token | Value | Rationale |
| --- | --- | --- |
| `--button-h-default` | 36px | Standard action height. |
| `--button-h-small` | 32px | Compact row action height. |
| `--input-h` | 36px | Input height aligned to default buttons. |
| `--row-h-data` | 52px | Dense table row height. |
| `--row-h-header` | 40px | Table and section header row height. |
| `--row-h-nav` | 36px | Sidebar navigation row height. |
| `--row-h-compact` | 32px | Compact list and menu row height. |

## When To Add A New Token

Add a token only when a future task hits a STOP-and-surface case: an approved design requirement cannot be expressed with the catalog above. The process is: propose the token and rationale, get approval, then update `frontend/styles/tokens.css` and this document in the same commit.

## What Is Forbidden

Inline styles are forbidden. Hex literals outside `tokens.css` are forbidden. Font weights other than `400` and `500` are forbidden. Italics are forbidden. Magic z-index values are forbidden. Transitions on `width`, `height`, `top`, or `left` are forbidden.

## Future Scaling Hooks

The `[data-workspace]` selector is reserved for per-workspace token overrides. A later dark mode can use `prefers-color-scheme`, but dark mode is not implemented now.
