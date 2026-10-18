import {
  mdiAccessPoint,
  mdiDevices,
  mdiLanConnect,
  mdiRouterNetwork,
  mdiSwitch,
  mdiWeb,
  mdiWifi,
} from "@mdi/js";
import { html, type TemplateResult } from "lit";
import type { TopologyNode } from "./contract";

export {
  mdiAccessPoint,
  mdiChevronDown,
  mdiChevronRight,
  mdiClose,
  mdiContentCopy,
  mdiDevices,
  mdiDotsVertical,
  mdiFitToScreenOutline,
  mdiInformationOutline,
  mdiLanConnect,
  mdiMagnifyMinusOutline,
  mdiMagnifyPlusOutline,
  mdiOpenInNew,
  mdiRouterNetwork,
  mdiSwitch,
  mdiWifi,
} from "@mdi/js";

export const GROUP_ICON = mdiDevices;

/** MDI icon path for a node: by kind, and by connection for clients. */
export function iconFor(node: TopologyNode): string {
  switch (node.kind) {
    case "wan":
      return mdiWeb;
    case "gateway":
      return mdiRouterNetwork;
    case "switch":
      return mdiSwitch;
    case "access_point":
      return mdiAccessPoint;
    case "client":
      return node.connection === "wireless" ? mdiWifi : mdiLanConnect;
    default:
      return mdiDevices;
  }
}

/** Decorative inline icon for HTML contexts; the surrounding control carries the label. */
export function iconTemplate(path: string): TemplateResult {
  return html`<svg
    class="icon"
    viewBox="0 0 24 24"
    aria-hidden="true"
    focusable="false"
  >
    <path d=${path}></path>
  </svg>`;
}
