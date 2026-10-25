import { ContextGesture } from "./context-gesture";
import { select } from "d3-selection";
import "d3-transition";
import {
  zoom,
  zoomIdentity,
  type D3ZoomEvent,
  type ZoomBehavior,
  type ZoomTransform,
} from "d3-zoom";
import {
  LitElement,
  css,
  html,
  nothing,
  svg,
  type PropertyValues,
  type SVGTemplateResult,
  type TemplateResult,
} from "lit";
import { repeat } from "lit/directives/repeat.js";
import type { Density, LayoutMode, Orientation } from "../config";
import { defineOnce } from "../define";
import { fireEvent } from "../ha-types";
import { GROUP_ICON, iconFor } from "../icons";
import type { LocalizeFunc } from "../localize";
import {
  ROOT_CLIENT_GROUP,
  UNCONNECTED_GROUP,
  groupContaining,
  type GraphModel,
  type Visual,
  type VisualLink,
} from "../model/graph-model";
import {
  CULL_THRESHOLD,
  fitTransform,
  layoutModel,
  visibleIds,
  type Layout,
  type Point,
  type ViewTransform,
} from "../model/layout";
import { nextGraphFocus } from "../model/navigation";
import { themeTokens } from "../styles";
import {
  describeVisual,
  linkLabel,
  truncate,
  visualState,
  visualStateKey,
  visualTitle,
} from "./describe";

const RADIUS: Record<Density, number> = { comfortable: 22, compact: 16 };
const EXIT_MS = 250;
const LABEL_CHARS = 22;

/**
 * d3-zoom gesture filter. In dashboard grids a plain wheel scrolls the page
 * (the card shows a hint instead); Ctrl/⌘+wheel and pinch still zoom. Panel
 * view passes ctrlZoom=false so the wheel zooms directly.
 */
export function zoomFilter(
  event: Event,
  ctrlZoom: boolean,
): "zoom" | "hint" | "ignore" {
  if (event.type === "wheel") {
    const wheel = event as WheelEvent;
    return ctrlZoom && !wheel.ctrlKey && !wheel.metaKey ? "hint" : "zoom";
  }
  const pointer = event as MouseEvent;
  return pointer.ctrlKey || (pointer.button ?? 0) !== 0 ? "ignore" : "zoom";
}

interface Ghost {
  point: Point;
  visual: Visual;
}

/** SVG tree of the model with pan/zoom, keyboard traversal and keyed incremental updates. */
export class UitGraphView extends LitElement {
  static override properties = {
    model: { attribute: false },
    density: { attribute: false },
    orientation: { attribute: false },
    layoutMode: { attribute: false },
    showLabels: { attribute: false },
    selectedId: { attribute: false },
    localize: { attribute: false },
    siteName: { attribute: false },
    ctrlZoom: { attribute: false },
    reducedMotion: { attribute: false },
    focusId: { state: true },
    hintVisible: { state: true },
  };

  declare model?: GraphModel;
  declare density: Density;
  declare orientation: Orientation;
  declare layoutMode: LayoutMode;
  declare showLabels: boolean;
  declare selectedId?: string;
  declare localize?: LocalizeFunc;
  declare siteName: string;
  declare ctrlZoom: boolean;
  declare reducedMotion: boolean;
  declare focusId?: string;
  declare hintVisible: boolean;

  private layout: Layout | undefined;
  private entering = new Set<string>();
  private exiting = new Map<string, Ghost>();
  private lastVisuals = new Map<string, Visual>();
  private exitTimer: ReturnType<typeof setTimeout> | undefined;
  private hintTimer: ReturnType<typeof setTimeout> | undefined;
  private transform: ViewTransform = { x: 0, y: 0, k: 1 };
  private width = 0;
  private height = 0;
  private fitted = false;
  private zoomBehavior: ZoomBehavior<SVGSVGElement, unknown> | undefined;
  private resizeObserver: ResizeObserver | undefined;
  /** Pointers now down, and whether this gesture has panned or pinched. */
  private gesture = new ContextGesture();

  constructor() {
    super();
    this.density = "comfortable";
    this.orientation = "vertical";
    this.layoutMode = "tree";
    this.showLabels = true;
    this.siteName = "";
    this.ctrlZoom = true;
    this.reducedMotion = false;
    this.hintVisible = false;
  }

  override connectedCallback(): void {
    super.connectedCallback();
    this.resizeObserver = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (box) this.setViewportSize(box.width, box.height);
    });
    this.resizeObserver.observe(this);
  }

  override disconnectedCallback(): void {
    super.disconnectedCallback();
    this.resizeObserver?.disconnect();
    if (this.exitTimer !== undefined) clearTimeout(this.exitTimer);
    if (this.hintTimer !== undefined) clearTimeout(this.hintTimer);
    this.exitTimer = undefined;
    this.hintTimer = undefined;
  }

  /** Viewport size in CSS px; called by the ResizeObserver (and directly by tests). */
  setViewportSize(width: number, height: number): void {
    this.width = width;
    this.height = height;
    if (!this.fitted) this.tryInitialFit();
    else if (this.needsCulling) this.requestUpdate();
  }

  private get needsCulling(): boolean {
    return (this.layout?.positions.size ?? 0) > CULL_THRESHOLD;
  }

  private get svgEl(): SVGSVGElement | null {
    return this.renderRoot.querySelector<SVGSVGElement>("svg.canvas");
  }

  protected override willUpdate(changed: PropertyValues<this>): void {
    if (changed.has("layoutMode")) this.fitted = false;
    if (!this.model) return;
    if (
      changed.has("model") ||
      changed.has("density") ||
      changed.has("orientation") ||
      changed.has("layoutMode")
    ) {
      const next = layoutModel(
        this.model,
        this.density,
        this.orientation,
        this.layoutMode,
      );
      const previous = this.layout;
      this.entering = new Set(
        previous
          ? [...next.positions.keys()].filter(
              (id) => !previous.positions.has(id),
            )
          : [],
      );
      for (const id of next.positions.keys()) this.exiting.delete(id);
      if (previous && !this.reducedMotion) {
        for (const [id, point] of previous.positions) {
          const visual = this.lastVisuals.get(id);
          if (!next.positions.has(id) && visual)
            this.exiting.set(id, { point, visual });
        }
        this.scheduleExitCleanup();
      }
      this.layout = next;
      this.lastVisuals = new Map(this.model.visuals);
      if (this.focusId === undefined || !this.model.visuals.has(this.focusId))
        this.focusId = this.model.roots[0];
    }
  }

  protected override firstUpdated(): void {
    const svgEl = this.svgEl;
    if (!svgEl) return;
    this.zoomBehavior = zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.2, 4])
      .extent(
        () =>
          [
            [0, 0],
            [Math.max(this.width, 1), Math.max(this.height, 1)],
          ] as [[number, number], [number, number]],
      )
      .filter((event: Event) => {
        const decision = zoomFilter(event, this.ctrlZoom);
        if (decision === "hint") this.flashHint();
        return decision === "zoom";
      })
      .on("zoom", (event: D3ZoomEvent<SVGSVGElement, unknown>) =>
        this.onZoom(event.transform),
      )
      .on("end", () => {
        if (this.needsCulling) this.requestUpdate();
      });
    select(svgEl).call(this.zoomBehavior).on("dblclick.zoom", null);
    this.tryInitialFit();
  }

  protected override updated(): void {
    this.tryInitialFit();
  }

  private tryInitialFit(): void {
    if (
      this.fitted ||
      !this.layout ||
      !this.zoomBehavior ||
      this.width <= 0 ||
      this.height <= 0
    )
      return;
    this.fitted = true;
    this.fit(false);
  }

  private onZoom(t: ZoomTransform): void {
    this.transform = { x: t.x, y: t.y, k: t.k };
    // Written directly: a pan must not re-render hundreds of nodes.
    this.renderRoot
      .querySelector("g.viewport")
      ?.setAttribute("transform", this.transformAttr());
  }

  private transformAttr(): string {
    const { x, y, k } = this.transform;
    return `translate(${x},${y}) scale(${k})`;
  }

  /** Fit the whole graph into the viewport. */
  fit(animate = true): void {
    const svgEl = this.svgEl;
    if (!this.layout || !this.zoomBehavior || !svgEl) return;
    const t = fitTransform(this.layout.bounds, this.width, this.height);
    const target = zoomIdentity.translate(t.x, t.y).scale(t.k);
    if (animate && !this.reducedMotion)
      this.zoomBehavior.transform(
        select(svgEl).transition().duration(300),
        target,
      );
    else this.zoomBehavior.transform(select(svgEl), target);
  }

  zoomBy(factor: number): void {
    const svgEl = this.svgEl;
    if (!this.zoomBehavior || !svgEl) return;
    if (this.reducedMotion) this.zoomBehavior.scaleBy(select(svgEl), factor);
    else
      this.zoomBehavior.scaleBy(
        select(svgEl).transition().duration(200),
        factor,
      );
  }

  /** Move keyboard focus to a node, panning it into view first if needed. */
  async focusNode(id: string): Promise<void> {
    this.focusId = id;
    await this.updateComplete;
    const p = this.layout?.positions.get(id);
    const svgEl = this.svgEl;
    if (p && svgEl && this.zoomBehavior && this.width > 0) {
      const sx = p.x * this.transform.k + this.transform.x;
      const sy = p.y * this.transform.k + this.transform.y;
      const margin = 40;
      if (
        sx < margin ||
        sx > this.width - margin ||
        sy < margin ||
        sy > this.height - margin
      ) {
        this.zoomBehavior.translateTo(select(svgEl), p.x, p.y);
        await this.updateComplete;
      }
    }
    for (const el of this.renderRoot.querySelectorAll<SVGGElement>(
      "g.nodes g.node",
    )) {
      if (el.getAttribute("data-id") === id) el.focus();
    }
  }

  private flashHint(): void {
    this.hintVisible = true;
    if (this.hintTimer !== undefined) clearTimeout(this.hintTimer);
    this.hintTimer = setTimeout(() => {
      this.hintVisible = false;
    }, 1500);
  }

  private scheduleExitCleanup(): void {
    if (this.exiting.size === 0 || this.exitTimer !== undefined) return;
    this.exitTimer = setTimeout(() => {
      this.exitTimer = undefined;
      this.exiting.clear();
      this.requestUpdate();
    }, EXIT_MS);
  }

  /** The selected node and everything above it, through a WAN's gateway too. */
  private highlightedPath(): Set<string> {
    const path = new Set<string>();
    const model = this.model;
    if (!model || this.selectedId === undefined) return path;
    let current: string | undefined = model.visuals.has(this.selectedId)
      ? this.selectedId
      : groupContaining(model, this.selectedId)?.id;
    while (current !== undefined) {
      path.add(current);
      current = model.parentOf.get(current) ?? model.uplinkOwner.get(current);
    }
    return path;
  }

  private onKeydown(e: KeyboardEvent): void {
    const model = this.model;
    if (!model) return;
    const next = nextGraphFocus(model, this.focusId, e.key, this.orientation);
    if (next !== undefined) {
      e.preventDefault();
      void this.focusNode(next);
      return;
    }
    if ((e.key === "Enter" || e.key === " ") && this.focusId !== undefined) {
      e.preventDefault();
      fireEvent(this, "uit-activate", { id: this.focusId });
    } else if (e.key === "+" || e.key === "=") {
      e.preventDefault();
      this.zoomBy(1.25);
    } else if (e.key === "-") {
      e.preventDefault();
      this.zoomBy(0.8);
    } else if (e.key === "0") {
      e.preventDefault();
      this.fit();
    } else if (
      (e.key === "ContextMenu" || (e.key === "F10" && e.shiftKey)) &&
      this.focusId !== undefined
    ) {
      e.preventDefault();
      fireEvent(this, "uit-context", { id: this.focusId });
    }
  }

  private onPointerDown(e: PointerEvent): void {
    this.gesture.down(e);
  }

  private onPointerMove(e: PointerEvent): void {
    this.gesture.move(e);
  }

  private onPointerEnd(e: PointerEvent): void {
    this.gesture.end(e);
  }

  /**
   * A context click or a long press on a node asks the card for its action
   * menu. The browser's own menu never shows. After a pan or a pinch the
   * press is not a long press, so the node menu stays shut.
   */
  private onContextMenu(e: Event, id: string): void {
    e.preventDefault();
    if (this.gesture.allowsMenu) fireEvent(this, "uit-context", { id });
  }

  /** The pannable SVG: tree links, WAN uplinks, nodes and fading exits. */
  protected override render(): TemplateResult | typeof nothing {
    const { model, layout, localize } = this;
    if (!model || !layout || !localize) return nothing;
    const r = RADIUS[this.density];
    let ids = [...layout.positions.keys()];
    let links = [...model.links.values()];
    let uplinks = model.uplinkLinks;
    if (this.needsCulling && this.width > 0) {
      const shown = visibleIds(layout, this.transform, this.width, this.height);
      if (this.focusId !== undefined) shown.add(this.focusId);
      if (this.selectedId !== undefined) shown.add(this.selectedId);
      ids = ids.filter((id) => shown.has(id));
      /** Keep a link when either end is drawn. */
      const near = (l: VisualLink): boolean =>
        shown.has(l.childId) || shown.has(l.parentId);
      links = links.filter(near);
      uplinks = uplinks.filter(near);
    }
    const path = this.highlightedPath();
    return html`
      <svg
        class="canvas ${this.reducedMotion ? "still" : ""}"
        role="application"
        aria-roledescription=${localize("graph.roledescription")}
        aria-label=${localize("graph.label", {
          site: this.siteName,
          devices: model.stats.devices,
          clients: model.stats.clients,
        })}
        @keydown=${this.onKeydown}
        @pointerdown=${this.onPointerDown}
        @pointermove=${this.onPointerMove}
        @pointerup=${this.onPointerEnd}
        @pointercancel=${this.onPointerEnd}
      >
        <g class="viewport" transform=${this.transformAttr()}>
          <g class="links">
            ${repeat(
              links,
              (l) => l.childId,
              (l) => this.renderLink(l, layout, path),
            )}
            ${repeat(
              uplinks,
              (l) => l.parentId,
              (l) => this.renderLink(l, layout, path, true),
            )}
          </g>
          <g class="nodes">
            ${repeat(
              ids,
              (id) => id,
              (id) =>
                this.renderNode(
                  model,
                  model.visuals.get(id)!,
                  layout.positions.get(id)!,
                  r,
                  path,
                  true,
                ),
            )}
          </g>
          <g class="exits" aria-hidden="true">
            ${repeat(
              [...this.exiting],
              ([id]) => id,
              ([, ghost]) => this.renderGhost(model, ghost, r),
            )}
          </g>
        </g>
      </svg>
      <div class="hint" aria-hidden="true" ?hidden=${!this.hintVisible}>
        ${localize("zoom.hint")}
      </div>
    `;
  }

  /** A tree link is on the path through its child; a WAN uplink through its WAN. */
  private renderLink(
    link: VisualLink,
    layout: Layout,
    path: Set<string>,
    uplink = false,
  ): SVGTemplateResult | typeof nothing {
    const a = layout.positions.get(link.parentId);
    const b = layout.positions.get(link.childId);
    if (!a || !b) return nothing;
    const mid =
      this.orientation === "vertical" ? (a.y + b.y) / 2 : (a.x + b.x) / 2;
    const d =
      this.orientation === "vertical"
        ? `M${a.x},${a.y} C${a.x},${mid} ${b.x},${mid} ${b.x},${b.y}`
        : `M${a.x},${a.y} C${mid},${a.y} ${mid},${b.y} ${b.x},${b.y}`;
    const label = this.showLabels ? linkLabel(link.edge) : "";
    const classes = [
      "link",
      link.edge?.medium ?? "unknown",
      link.viaHidden.length > 0 ? "via-hidden" : "",
      path.has(uplink ? link.parentId : link.childId) ? "on-path" : "",
      uplink && this.model?.nodes.get(link.parentId)?.state === "offline"
        ? "down"
        : "",
    ];
    return svg`<path class=${classes.join(" ")} d=${d}></path>${
      label
        ? svg`<text class="link-label" x=${(a.x + b.x) / 2} y=${(a.y + b.y) / 2}>${label}</text>`
        : nothing
    }`;
  }

  /** One node: disc, glyph, status dot, label and state text. */
  private renderNode(
    model: GraphModel,
    visual: Visual,
    p: Point,
    r: number,
    path: Set<string>,
    interactive: boolean,
  ): SVGTemplateResult {
    const localize = this.localize!;
    const id = visual.id;
    const state = visualState(visual);
    const title = visualTitle(visual, localize);
    const kind = visual.type === "group" ? "group" : visual.node.kind;
    const classes = [
      "node",
      visual.type,
      kind,
      state,
      id === this.selectedId ? "selected" : "",
      path.has(id) ? "on-path" : "",
      this.entering.has(id) ? "enter" : "",
    ];
    const glyph = visual.type === "group" ? GROUP_ICON : iconFor(visual.node);
    const labelY = r + 16;
    return svg`<g
      class=${classes.join(" ")}
      data-id=${id}
      role=${interactive ? "button" : nothing}
      tabindex=${interactive ? (id === this.focusId ? 0 : -1) : nothing}
      aria-label=${interactive ? describeVisual(model, visual, localize) : nothing}
      aria-expanded=${interactive && visual.type === "group" ? String(visual.expanded) : nothing}
      style=${`transform: translate(${p.x}px, ${p.y}px)`}
      @click=${interactive ? () => fireEvent(this, "uit-activate", { id }) : nothing}
      @contextmenu=${interactive ? (e: Event) => this.onContextMenu(e, id) : nothing}
      @focus=${
        interactive
          ? () => {
              this.focusId = id;
            }
          : nothing
      }
    >
      <title>${title}</title>
      <circle class="hit" r=${Math.max(r, 22)}></circle>
      <circle class="ring" r=${r + 5}></circle>
      <circle class="disc" r=${r}></circle>
      <svg class="glyph" x=${-r * 0.6} y=${-r * 0.6} width=${r * 1.2} height=${r * 1.2} viewBox="0 0 24 24" aria-hidden="true">
        <path d=${glyph}></path>
      </svg>
      <circle class="status" cx=${r * 0.72} cy=${-r * 0.72} r=${Math.max(4, r * 0.24)}></circle>
      ${visual.type === "group" && (!this.showLabels || visual.id === ROOT_CLIENT_GROUP || visual.id === UNCONNECTED_GROUP) ? svg`<text class="badge" x=${r * 0.95} y=${r + 2}>${visual.counts.total}</text>` : nothing}
      ${this.showLabels ? svg`<text class="label" y=${labelY}>${truncate(title, LABEL_CHARS)}</text>` : nothing}
      ${
        state === "offline"
          ? svg`<text class="state-text" y=${this.showLabels ? labelY + 14 : labelY}>${localize(visualStateKey(visual))}</text>`
          : nothing
      }
    </g>`;
  }

  /** A removed node fading out; it keeps the visual it had, since the model no longer has it. */
  private renderGhost(
    model: GraphModel,
    ghost: Ghost,
    r: number,
  ): SVGTemplateResult {
    return this.renderNode(
      model,
      ghost.visual,
      ghost.point,
      r,
      new Set(),
      false,
    );
  }

  static override styles = [
    themeTokens,
    css`
      :host {
        display: block;
        position: relative;
        flex: 1;
        min-width: 0;
        min-height: 0;
      }
      svg.canvas {
        display: block;
        width: 100%;
        height: 100%;
        touch-action: none;
        user-select: none;
      }
      .node {
        cursor: pointer;
        outline: none;
        transition: transform 250ms ease;
      }
      .still .node {
        transition: none;
      }
      .node.enter {
        animation: uit-fade-in 250ms ease;
      }
      .exits .node {
        animation: uit-fade-out 250ms ease forwards;
        pointer-events: none;
      }
      .still .node.enter,
      .still .exits .node {
        animation: none;
      }
      @keyframes uit-fade-in {
        from {
          opacity: 0;
        }
      }
      @keyframes uit-fade-out {
        to {
          opacity: 0;
        }
      }
      .hit {
        fill: transparent;
      }
      .ring {
        fill: none;
        stroke: none;
      }
      .node:focus-visible .ring {
        stroke: var(--uit-focus);
        stroke-width: 2;
      }
      .disc {
        fill: var(--card-background-color, #fff);
        stroke: color-mix(in srgb, var(--primary-color) 40%, var(--uit-line));
        stroke-width: 2;
        filter: drop-shadow(0 2px 5px rgba(0, 0, 0, 0.1));
      }
      .selected .disc,
      .on-path .disc {
        stroke: var(--uit-focus);
      }
      .selected .disc {
        stroke-width: 3;
      }
      .glyph path {
        fill: var(--primary-color);
      }
      .offline .disc,
      .offline .glyph {
        opacity: 0.55;
      }
      .status {
        fill: var(--uit-online);
        stroke: var(--card-background-color, #fff);
        stroke-width: 2;
      }
      .offline .status {
        fill: var(--uit-offline);
      }
      .unknown .status {
        fill: var(--uit-unknown);
      }
      text {
        font-size: 12px;
        font-weight: 500;
        fill: var(--primary-text-color);
        text-anchor: middle;
        dominant-baseline: hanging;
      }
      .badge {
        font-weight: 700;
        text-anchor: start;
      }
      .state-text {
        fill: var(--uit-offline);
        font-weight: 600;
      }
      .link {
        fill: none;
        stroke: color-mix(
          in srgb,
          var(--primary-color) 45%,
          var(--secondary-text-color)
        );
        stroke-opacity: 0.65;
        stroke-width: 1.75;
      }
      .link.wireless {
        stroke-dasharray: 2 4;
      }
      .link.via-hidden {
        stroke-dasharray: 8 4;
      }
      .link.down {
        stroke: var(--uit-offline);
        stroke-dasharray: 4 4;
      }
      .link.on-path {
        stroke: var(--uit-focus);
        stroke-opacity: 1;
        stroke-width: 2.5;
      }
      .link-label {
        font-size: 10px;
        fill: var(--secondary-text-color);
        paint-order: stroke;
        stroke: var(--card-background-color, #fff);
        stroke-width: 3;
      }
      .hint {
        position: absolute;
        left: 50%;
        bottom: 12px;
        transform: translateX(-50%);
        padding: 6px 12px;
        border-radius: 16px;
        background: var(--primary-text-color);
        color: var(--card-background-color, #fff);
        font-size: 12px;
        pointer-events: none;
      }
      .hint[hidden] {
        display: none;
      }
      @media (prefers-reduced-motion: reduce) {
        .node {
          transition: none;
        }
        .node.enter,
        .exits .node {
          animation: none;
        }
      }
    `,
  ];
}

defineOnce("uit-graph-view", UitGraphView);

declare global {
  interface HTMLElementTagNameMap {
    "uit-graph-view": UitGraphView;
  }
}
