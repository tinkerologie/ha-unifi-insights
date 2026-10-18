import { ContextGesture } from "./context-gesture";
import { LitElement, css, html, nothing, type TemplateResult } from "lit";
import { repeat } from "lit/directives/repeat.js";
import { defineOnce } from "../define";
import { fireEvent } from "../ha-types";
import {
  GROUP_ICON,
  iconFor,
  iconTemplate,
  mdiChevronDown,
  mdiChevronRight,
} from "../icons";
import type { LocalizeFunc } from "../localize";
import type { GraphModel, Visual } from "../model/graph-model";
import { controlStyles, themeTokens } from "../styles";
import {
  describeVisual,
  visualState,
  visualStateKey,
  visualTitle,
} from "./describe";

export interface ListRow {
  id: string;
  visual: Visual;
  level: number;
  posinset: number;
  setsize: number;
  hasChildren: boolean;
  expanded: boolean;
  parentId?: string | undefined;
}

function matches(visual: Visual, query: string): boolean {
  if (visual.type === "group")
    return visual.members.some((m) => m.name.toLowerCase().includes(query));
  return visual.node.name.toLowerCase().includes(query);
}

/**
 * Visible rows of the ARIA tree: depth-first, filtered to search matches plus
 * their ancestors. WAN uplinks are listed just before the device they serve.
 */
export function listRows(
  model: GraphModel,
  collapsed: ReadonlySet<string>,
  query: string,
): ListRow[] {
  const q = query.trim().toLowerCase();
  let keep: Set<string> | undefined;
  if (q) {
    keep = new Set();
    for (const [id, visual] of model.visuals) {
      if (!matches(visual, q)) continue;
      for (
        let cur: string | undefined = id;
        cur !== undefined && !keep.has(cur);
        cur = model.parentOf.get(cur) ?? model.uplinkOwner.get(cur)
      )
        keep.add(cur);
    }
  }
  const rows: ListRow[] = [];
  const walk = (
    ids: readonly string[],
    level: number,
    parentId: string | undefined,
  ): void => {
    // A device's WAN rows come right before it, at its level.
    const withUplinks = ids.flatMap((id) => [
      ...(model.uplinks.get(id) ?? []),
      id,
    ]);
    const shown = keep ? withUplinks.filter((id) => keep.has(id)) : withUplinks;
    shown.forEach((id, index) => {
      const visual = model.visuals.get(id)!;
      const kids = model.children.get(id) ?? [];
      const isGroup = visual.type === "group";
      const hasChildren = isGroup || kids.length > 0;
      const open = isGroup ? visual.expanded : q !== "" || !collapsed.has(id);
      rows.push({
        id,
        visual,
        level,
        posinset: index + 1,
        setsize: shown.length,
        hasChildren,
        expanded: hasChildren && open,
        parentId,
      });
      if (open && kids.length > 0) walk(kids, level + 1, id);
    });
  };
  walk(model.roots, 1, undefined);
  return rows;
}

/** Accessible tree view of the same model the graph draws. */
export class UitListView extends LitElement {
  static override properties = {
    model: { attribute: false },
    selectedId: { attribute: false },
    localize: { attribute: false },
    siteName: { attribute: false },
    query: { state: true },
    collapsed: { state: true },
    focusId: { state: true },
  };

  declare model?: GraphModel;
  declare selectedId?: string;
  declare localize?: LocalizeFunc;
  declare siteName: string;
  declare query: string;
  declare collapsed: ReadonlySet<string>;
  declare focusId?: string;

  private rows: ListRow[] = [];
  private gesture = new ContextGesture();
  private typeahead = "";
  private typeaheadTimer: ReturnType<typeof setTimeout> | undefined;

  constructor() {
    super();
    this.siteName = "";
    this.query = "";
    this.collapsed = new Set();
  }

  protected override willUpdate(): void {
    if (!this.model) return;
    this.rows = listRows(this.model, this.collapsed, this.query);
    if (!this.rows.some((r) => r.id === this.focusId))
      this.focusId = this.rows[0]?.id;
  }

  protected override render(): TemplateResult | typeof nothing {
    const { model, localize } = this;
    if (!model || !localize) return nothing;
    return html`
      <div class="search">
        <input
          type="search"
          .value=${this.query}
          placeholder=${localize("list.search")}
          aria-label=${localize("list.search")}
          @input=${(e: Event) => {
            this.query = (e.target as HTMLInputElement).value;
          }}
        />
      </div>
      ${this.rows.length === 0
        ? html`<p class="empty">${localize("list.no_matches")}</p>`
        : html`<div
            class="tree"
            role="tree"
            aria-label=${localize("list.label", {
              site: this.siteName,
            })}
            @keydown=${this.onKeydown}
          >
            ${repeat(
              this.rows,
              (r) => r.id,
              (r) => this.renderRow(model, r, localize),
            )}
          </div>`}
    `;
  }

  /** One treeitem row with its chevron, icon, name and state. */
  private renderRow(
    model: GraphModel,
    row: ListRow,
    localize: LocalizeFunc,
  ): TemplateResult {
    const visual = row.visual;
    const title = visualTitle(visual, localize);
    const state = visualState(visual);
    return html`<div
      class="row ${state} ${row.id === this.selectedId ? "selected" : ""}"
      role="treeitem"
      data-id=${row.id}
      aria-level=${row.level}
      aria-setsize=${row.setsize}
      aria-posinset=${row.posinset}
      aria-selected=${String(row.id === this.selectedId)}
      aria-expanded=${row.hasChildren ? String(row.expanded) : nothing}
      aria-label=${describeVisual(model, visual, localize)}
      tabindex=${row.id === this.focusId ? 0 : -1}
      style=${`--level: ${row.level}`}
      @click=${() => fireEvent(this, "uit-activate", { id: row.id })}
      @pointerdown=${(e: PointerEvent) => this.gesture.down(e)}
      @pointermove=${(e: PointerEvent) => this.gesture.move(e)}
      @pointerup=${(e: PointerEvent) => this.gesture.end(e)}
      @pointercancel=${(e: PointerEvent) => this.gesture.end(e)}
      @contextmenu=${(e: Event) => {
        e.preventDefault();
        if (this.gesture.allowsMenu)
          fireEvent(this, "uit-context", { id: row.id });
      }}
      @focus=${() => {
        this.focusId = row.id;
      }}
    >
      <span
        class="chevron"
        aria-hidden="true"
        @click=${(e: Event) => {
          e.stopPropagation();
          if (row.hasChildren) this.toggle(row);
        }}
        >${row.hasChildren
          ? iconTemplate(row.expanded ? mdiChevronDown : mdiChevronRight)
          : nothing}</span
      >
      ${iconTemplate(
        visual.type === "group" ? GROUP_ICON : iconFor(visual.node),
      )}
      <span class="name" title=${title}>${title}</span>
      ${visual.type === "group"
        ? html`<span class="count">${visual.counts.total}</span>`
        : html`<span class="dot" aria-hidden="true"></span>${state === "online"
              ? nothing
              : html`<span class="state-text"
                  >${localize(visualStateKey(visual))}</span
                >`}`}
    </div>`;
  }

  private toggle(row: ListRow): void {
    if (row.visual.type === "group") {
      fireEvent(this, "uit-toggle-group", { id: row.id });
      return;
    }
    const next = new Set(this.collapsed);
    if (next.has(row.id)) next.delete(row.id);
    else next.add(row.id);
    this.collapsed = next;
  }

  private onKeydown(e: KeyboardEvent): void {
    const rows = this.rows;
    const index = rows.findIndex((r) => r.id === this.focusId);
    const row = rows[index];
    if (!row) return;
    if (e.key === "ContextMenu" || (e.key === "F10" && e.shiftKey)) {
      e.preventDefault();
      fireEvent(this, "uit-context", { id: row.id });
      return;
    }
    let target: string | undefined;
    switch (e.key) {
      case "ArrowDown":
        target = rows[index + 1]?.id;
        break;
      case "ArrowUp":
        target = rows[index - 1]?.id;
        break;
      case "Home":
        target = rows[0]?.id;
        break;
      case "End":
        target = rows.at(-1)?.id;
        break;
      case "ArrowRight":
        if (row.hasChildren && !row.expanded) this.toggle(row);
        else if (row.expanded) target = rows[index + 1]?.id;
        break;
      case "ArrowLeft":
        if (row.expanded) this.toggle(row);
        else target = row.parentId;
        break;
      case "Enter":
      case " ":
        fireEvent(this, "uit-activate", { id: row.id });
        break;
      default:
        if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          this.typeAhead(e.key, index);
        }
        return;
    }
    e.preventDefault();
    if (target !== undefined) void this.focusRow(target);
  }

  private typeAhead(char: string, from: number): void {
    this.typeahead += char.toLowerCase();
    if (this.typeaheadTimer !== undefined) clearTimeout(this.typeaheadTimer);
    this.typeaheadTimer = setTimeout(() => {
      this.typeahead = "";
    }, 500);
    const rows = this.rows;
    for (let step = 1; step <= rows.length; step++) {
      const row = rows[(from + step) % rows.length]!;
      if (
        visualTitle(row.visual, this.localize!)
          .toLowerCase()
          .startsWith(this.typeahead)
      ) {
        void this.focusRow(row.id);
        return;
      }
    }
  }

  private async focusRow(id: string): Promise<void> {
    this.focusId = id;
    await this.updateComplete;
    const rows =
      this.renderRoot.querySelectorAll<HTMLElement>('[role="treeitem"]');
    for (const el of rows) if (el.getAttribute("data-id") === id) el.focus();
  }

  static override styles = [
    themeTokens,
    controlStyles,
    css`
      :host {
        display: flex;
        flex-direction: column;
        flex: 1;
        min-height: 0;
        min-width: 0;
      }
      .search {
        padding: 8px 12px;
      }
      .search input {
        width: 100%;
      }
      .tree {
        overflow: auto;
        flex: 1;
        padding: 0 4px 8px;
      }
      .row {
        display: flex;
        align-items: center;
        gap: 8px;
        min-height: 44px;
        padding-inline-start: calc((var(--level) - 1) * 20px + 4px);
        padding-inline-end: 12px;
        border-radius: 8px;
        cursor: pointer;
      }
      .row.selected {
        background: color-mix(in srgb, var(--uit-focus) 16%, transparent);
      }
      .row.offline .icon,
      .row.offline .name {
        opacity: 0.55;
      }
      .chevron {
        width: 24px;
        display: inline-flex;
      }
      .name {
        flex: 1;
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .dot {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: var(--uit-online);
        flex: none;
      }
      .offline .dot {
        background: var(--uit-offline);
      }
      .unknown .dot {
        background: var(--uit-unknown);
      }
      .state-text {
        color: var(--uit-offline);
        font-weight: 600;
        font-size: 0.85em;
      }
      .count {
        color: var(--secondary-text-color);
      }
      .empty {
        padding: 16px;
        color: var(--secondary-text-color);
      }
    `,
  ];
}

defineOnce("uit-list-view", UitListView);

declare global {
  interface HTMLElementTagNameMap {
    "uit-list-view": UitListView;
  }
}
