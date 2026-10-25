import { css } from "lit";

import { controlStyles, themeTokens } from "./styles";

export const dashboardCardStyles = [
  themeTokens,
  controlStyles,
  css`
    :host {
      display: block;
      height: 100%;
      min-width: 0;
      --uit-text-secondary: var(
        --ha-color-text-secondary,
        var(--primary-text-color)
      );
    }
    ha-card {
      height: 100%;
      box-sizing: border-box;
      padding: var(--ha-space-4, 16px);
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-3, 12px);
      overflow: hidden;
      container-type: inline-size;
      min-height: 0;
    }
    .header {
      flex-shrink: 0;
      flex-wrap: wrap;
    }
    .content {
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-3, 12px);
      flex: 1 1 auto;
      min-height: 0;
      overflow: auto;
      padding: var(--ha-space-1, 4px);
      margin: calc(-1 * var(--ha-space-1, 4px));
    }
    .content > * {
      flex-shrink: 0;
    }
    .content > .list {
      flex: 1 1 auto;
      min-height: 44px;
      overflow: auto;
      padding: var(--ha-space-1, 4px);
      margin: calc(-1 * var(--ha-space-1, 4px));
      align-content: start;
    }
    .icon {
      width: 20px;
      height: 20px;
      fill: currentColor;
      flex: none;
    }
    .header,
    .header-main,
    .header-actions,
    .hero-banner,
    .hero-left,
    .kpi-top,
    .item-card,
    .item-top,
    .item-meta,
    .ring-card {
      display: flex;
      align-items: center;
    }
    .header,
    .hero-banner,
    .kpi-top,
    .item-top {
      justify-content: space-between;
      gap: 10px;
    }
    .header-main,
    .hero-left,
    .item-card,
    .ring-card {
      gap: 10px;
      min-width: 0;
    }
    .header-icon,
    .item-icon,
    .ring-gauge,
    .empty-hero .icon-badge {
      display: grid;
      place-items: center;
      flex: none;
    }
    .header-icon {
      width: 38px;
      height: 38px;
      border-radius: var(--ha-border-radius-lg, 12px);
      background: color-mix(
        in srgb,
        var(--card-accent, var(--primary-color)) 15%,
        transparent
      );
      color: var(--card-accent, var(--primary-color));
    }
    .header-titles {
      min-width: 0;
    }
    .header-title,
    .hero-title,
    .item-name,
    .kpi-sub {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .header-title,
    .hero-title,
    .item-name,
    .empty-title,
    .chip,
    .kpi-top,
    .pill-tab,
    button.link {
      font-weight: var(--ha-font-weight-medium, 600);
    }
    .header-title {
      font-size: var(--ha-font-size-l, 1.02rem);
      line-height: var(--ha-line-height-condensed, 1.25);
      margin: 0;
    }
    .header-subtitle,
    .hero-meta,
    .kpi-sub,
    .item-meta,
    .empty-sub {
      font-size: var(--ha-font-size-s, 0.75rem);
      color: var(--uit-text-secondary);
    }
    .header-actions,
    .kpi-top,
    .item-meta,
    .empty-hero,
    .chip {
      gap: var(--ha-space-2, 6px);
    }
    .state {
      flex-shrink: 0;
      color: var(--uit-text-secondary);
      font-size: var(--ha-font-size-m, 0.88rem);
      padding: var(--ha-space-3, 12px);
      border-radius: var(--ha-border-radius-lg, 12px);
      background: color-mix(in srgb, var(--primary-text-color) 4%, transparent);
      display: flex;
      align-items: center;
      gap: var(--ha-space-2, 8px);
    }
    .item-icon.offline {
      color: var(--uit-offline);
      background: color-mix(in srgb, var(--uit-offline) 14%, transparent);
    }
    .pulse-dot,
    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: currentColor;
      display: inline-block;
      flex: none;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: var(--ha-font-size-s, 0.74rem);
      text-transform: capitalize;
      background: color-mix(in srgb, var(--primary-text-color) 8%, transparent);
      color: var(--primary-text-color);
    }
    .item-icon.online,
    .empty-hero .icon-badge {
      background: color-mix(in srgb, var(--uit-online) 16%, transparent);
      color: var(--uit-online);
    }
    .hero-banner {
      padding: 10px 12px;
      border-radius: var(--ha-border-radius-lg, 12px);
      background: color-mix(
        in srgb,
        var(--card-accent, var(--primary-color)) 8%,
        transparent
      );
      border: 1px solid
        color-mix(
          in srgb,
          var(--card-accent, var(--primary-color)) 20%,
          transparent
        );
    }
    .hero-left .icon {
      color: var(--card-accent, var(--primary-color));
    }
    .hero-title {
      font-size: var(--ha-font-size-m, 0.9rem);
    }
    .kpi-grid,
    .ring-strip,
    .list {
      display: grid;
      gap: var(--ha-space-2, 8px);
    }
    .kpi-grid {
      grid-template-columns: repeat(auto-fit, minmax(92px, 1fr));
    }
    .ring-strip {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .kpi-tile,
    .ring-card,
    .item-card {
      border-radius: var(--ha-border-radius-lg, 12px);
      background: color-mix(in srgb, var(--primary-text-color) 4%, transparent);
      border: 1px solid var(--uit-line);
    }
    .kpi-tile {
      appearance: none;
      align-items: stretch;
      padding: 10px 12px;
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-1, 4px);
      min-width: 0;
      text-align: start;
      font: inherit;
      color: inherit;
    }
    .kpi-tile:disabled {
      cursor: default;
      opacity: 1;
    }
    .kpi-tile.clickable {
      cursor: pointer;
      transition: background var(--ha-animation-duration-fast, 150ms) ease;
    }
    .kpi-tile.clickable:hover {
      background: color-mix(in srgb, var(--primary-color) 10%, transparent);
    }
    .kpi-top {
      font-size: var(--ha-font-size-s, 0.73rem);
      text-transform: uppercase;
      letter-spacing: 0.03em;
      color: var(--uit-text-secondary);
    }
    .kpi-value {
      font-size: var(--ha-font-size-xl, 1.18rem);
      font-weight: var(--ha-font-weight-bold, 700);
      font-variant-numeric: tabular-nums;
      line-height: var(--ha-line-height-condensed, 1.2);
      text-transform: capitalize;
    }
    .bar-track {
      width: 100%;
      height: 6px;
      border-radius: 999px;
      background: color-mix(
        in srgb,
        var(--primary-text-color) 10%,
        transparent
      );
      overflow: hidden;
      display: flex;
    }
    .bar-fill {
      height: 100%;
      border-radius: 999px;
      background: var(--primary-color);
      transition: width var(--ha-animation-duration-normal, 250ms) ease;
    }
    .bar-fill.ok {
      background: var(--uit-online);
    }
    .bar-fill.warning {
      background: var(--uit-warning);
    }
    .bar-fill.critical {
      background: var(--uit-offline);
    }
    .bar-fill.secondary {
      background: var(--purple-color, var(--primary-color));
    }
    .ring-card,
    .item-card {
      padding: 8px 10px;
    }
    .ring-gauge {
      width: 40px;
      height: 40px;
      border-radius: 50%;
      background: conic-gradient(
        var(--ring-color, var(--uit-online)) calc(var(--pct, 0) * 1%),
        color-mix(in srgb, var(--primary-text-color) 10%, transparent) 0
      );
      position: relative;
    }
    .ring-gauge::before {
      content: "";
      position: absolute;
      inset: 5px;
      border-radius: 50%;
      background: var(--card-background-color, #fff);
    }
    .ring-gauge span {
      position: relative;
      font-size: var(--ha-font-size-s, 0.7rem);
      font-weight: var(--ha-font-weight-bold, 700);
      font-variant-numeric: tabular-nums;
    }
    .item-icon {
      width: 34px;
      height: 34px;
      border-radius: 10px;
      background: color-mix(in srgb, var(--primary-color) 12%, transparent);
      color: var(--primary-color);
    }
    .item-body {
      flex: 1;
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-1, 4px);
    }
    .item-name {
      font-size: var(--ha-font-size-m, 0.88rem);
    }
    .item-value {
      font-size: var(--ha-font-size-m, 0.85rem);
      font-weight: var(--ha-font-weight-bold, 700);
      font-variant-numeric: tabular-nums;
      flex-shrink: 0;
    }
    .pill-tabs {
      display: inline-flex;
      background: color-mix(in srgb, var(--primary-text-color) 6%, transparent);
      border-radius: 999px;
      padding: 2px;
      gap: 2px;
    }
    .pill-tab {
      border: 0;
      background: transparent;
      color: var(--uit-text-secondary);
      font: inherit;
      font-size: var(--ha-font-size-s, 0.7rem);
      padding: 3px 8px;
      border-radius: 999px;
      cursor: pointer;
      text-transform: uppercase;
      min-width: 44px;
      min-height: 44px;
      justify-content: center;
    }
    .pill-tab[aria-pressed="true"] {
      background: var(
        --ha-color-fill-primary-quiet-resting,
        color-mix(in srgb, var(--primary-color) 16%, transparent)
      );
      color: var(--ha-color-text-primary, var(--primary-text-color));
    }
    .empty-hero {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      padding: 18px 12px;
      border-radius: 14px;
      background: color-mix(in srgb, var(--uit-online) 7%, transparent);
      border: 1px dashed color-mix(in srgb, var(--uit-online) 30%, transparent);
    }
    .empty-hero .icon-badge {
      width: 42px;
      height: 42px;
      border-radius: 50%;
    }
    .empty-title {
      font-size: var(--ha-font-size-l, 0.92rem);
    }
    button.link {
      border: 0;
      background: transparent;
      color: var(--ha-color-text-primary, var(--primary-text-color));
      cursor: pointer;
      padding: 0;
      text-align: start;
      font: inherit;
      min-width: 44px;
      min-height: 44px;
      text-decoration: underline;
      text-underline-offset: 3px;
      white-space: normal;
      overflow-wrap: anywhere;
    }
    .chip.ok,
    .chip.healthy,
    .chip.online {
      color: var(--ha-color-text-primary, var(--primary-text-color));
      background: var(
        --ha-color-fill-success-quiet-resting,
        color-mix(in srgb, var(--uit-online) 16%, transparent)
      );
    }
    .chip.warning,
    .chip.degraded,
    .chip.partial {
      color: var(--ha-color-text-primary, var(--primary-text-color));
      background: var(
        --ha-color-fill-warning-quiet-resting,
        color-mix(in srgb, var(--uit-warning) 18%, transparent)
      );
    }
    .chip.critical,
    .chip.offline,
    .state.error {
      color: var(--ha-color-text-primary, var(--primary-text-color));
      background: var(
        --ha-color-fill-danger-quiet-resting,
        color-mix(in srgb, var(--uit-offline) 14%, transparent)
      );
    }
    .state-detail {
      font-size: var(--ha-font-size-s, 12px);
      display: block;
    }
    .header-main {
      flex: 1 1 140px;
    }
    .header-actions {
      flex-wrap: wrap;
    }
    .hero-left > div,
    .ring-card > div,
    .item-top {
      min-width: 0;
    }
    .hero-meta,
    .item-meta {
      flex-wrap: wrap;
      overflow-wrap: anywhere;
    }
    @container (max-width: 360px) {
      .header-actions {
        max-width: 100%;
      }
      .hero-banner,
      .item-top {
        flex-wrap: wrap;
      }
      .hero-title,
      .item-name {
        white-space: normal;
        overflow-wrap: anywhere;
      }
    }
    @container (max-width: 260px) {
      .ring-strip,
      .kpi-grid {
        grid-template-columns: minmax(0, 1fr);
      }
      .item-card {
        align-items: flex-start;
      }
    }
  `,
];
