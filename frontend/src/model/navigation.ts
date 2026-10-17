import type { Orientation } from "../config";
import { siblingsOf, type GraphModel } from "./graph-model";

/**
 * Arrow-key movement over the drawn tree: towards the parent, into the first
 * child, or across siblings, rotated for horizontal layouts. A device's WAN
 * uplinks count as its parents and the WANs as each other's siblings. Returns
 * undefined for keys that are not navigation keys; stays put at edges.
 */
export function nextGraphFocus(
  model: GraphModel,
  current: string | undefined,
  key: string,
  orientation: Orientation,
): string | undefined {
  const vertical = orientation === "vertical";
  const keys = {
    parent: vertical ? "ArrowUp" : "ArrowLeft",
    child: vertical ? "ArrowDown" : "ArrowRight",
    previous: vertical ? "ArrowLeft" : "ArrowUp",
    next: vertical ? "ArrowRight" : "ArrowDown",
  };
  if (!Object.values(keys).includes(key)) return undefined;
  if (current === undefined || !model.visuals.has(current))
    return model.roots[0];
  const siblings = siblingsOf(model, current);
  const index = siblings.indexOf(current);
  switch (key) {
    case keys.parent:
      return (
        model.parentOf.get(current) ??
        model.uplinks.get(current)?.[0] ??
        current
      );
    case keys.child:
      return (
        model.children.get(current)?.[0] ??
        model.uplinkOwner.get(current) ??
        current
      );
    case keys.previous:
      return siblings[index - 1] ?? current;
    default:
      return siblings[index + 1] ?? current;
  }
}
