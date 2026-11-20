import{$ as e,D as t,F as n,P as r,S as i,T as a,X as o,a as s,b as c,c as l,f as u,g as d,i as f,m as p,p as m,q as h,s as g,t as _,ut as v,y}from"./localize-DuXs5QH_.js";var b=`unifi-insights-topology-card`,x=`unifi-insights-topology-card-editor`,S=`custom:${b}`,C=[`graph`,`list`],w=[`collapsed`,`expanded`,`hidden`],T=[`comfortable`,`compact`],E=[`vertical`,`horizontal`],D=[`tree`,`tiered`];function O(e,t,n){if(e!==void 0&&!(typeof e==`string`&&t.includes(e)))throw Error(`${n} must be one of: ${t.join(`, `)}`)}function k(e){if(typeof e!=`object`||!e||Array.isArray(e))throw Error(`Card configuration must be an object`);let t=e;if(typeof t.type!=`string`)throw Error(`type is required`);for(let e of[`entry_id`,`site_id`,`title`]){let n=t[e];if(n!==void 0&&(typeof n!=`string`||n===``))throw Error(`${e} must be a non-empty string`)}if(t.entry_id===void 0!=(t.site_id===void 0))throw Error(`entry_id and site_id must be set together`);O(t.view,C,`view`),O(t.clients,w,`clients`),O(t.density,T,`density`),O(t.orientation,E,`orientation`),O(t.layout,D,`layout`);for(let e of[`show_site_selector`,`show_labels`,`show_wan`])if(t[e]!==void 0&&typeof t[e]!=`boolean`)throw Error(`${e} must be true or false`);if(t.kinds!==void 0&&(!Array.isArray(t.kinds)||t.kinds.length===0||!t.kinds.every(e=>v.includes(e))))throw Error(`kinds must be a non-empty list of: ${v.join(`, `)}`);let n=t.max_clients;if(n!==void 0&&(typeof n!=`number`||!Number.isInteger(n)||n<1||n>500))throw Error(`max_clients must be a whole number from 1 to 500`);return t}function A(e){let t=e.show_wan??!0;return{entry_id:e.entry_id,site_id:e.site_id,title:e.title,view:e.view??`graph`,show_site_selector:e.show_site_selector??!1,clients:e.clients??`collapsed`,kinds:(e.kinds??v).filter(e=>t||e!==`wan`),density:e.density,orientation:e.orientation??`vertical`,layout:e.layout??`tree`,show_labels:e.show_labels??!0,show_wan:t,max_clients:e.max_clients??500}}function j(e){return e.flatMap(e=>e.sites.map(t=>({binding:{entry_id:e.entry_id,site_id:t.id},label:`${e.title} — ${t.name}`})))}function M(e,t){if(e.entry_id!==void 0&&e.site_id!==void 0)return{entry_id:e.entry_id,site_id:e.site_id};let n=j(t??[]);return n.length===1?n[0].binding:void 0}function N(e,t){return e?.entry_id===t?.entry_id&&e?.site_id===t?.site_id}var P=`unifi-insights-site-health-card`,F=`unifi-insights-site-health-card-editor`,I=`unifi-insights-internet-activity-card`,L=`unifi-insights-internet-activity-card-editor`,R=`unifi-insights-performance-card`,z=`unifi-insights-performance-card-editor`,B=`unifi-insights-protect-status-card`,V=`unifi-insights-protect-status-card-editor`,H=`unifi-insights-timeline-card`,U=`unifi-insights-timeline-card-editor`;function W(e){let t=Math.round(e/1e6);return e>=1e9?`${(e/1e9).toFixed(1)} GB`:`${t} MB`}function G(e){if(!(typeof e!=`number`||!Number.isFinite(e)||e<0))return e>=1e9?`${(e/1e9).toFixed(1)} Gbps`:e>=1e6?`${(e/1e6).toFixed(1)} Mbps`:e>=1e3?`${Math.round(e/1e3)} Kbps`:`${Math.round(e)} bps`}function K(e){if(typeof e!=`number`||!Number.isFinite(e)||e<=0)return;let t=Math.floor(e/86400),n=Math.floor(e%86400/3600);if(t>0)return`${t}d ${n}h`;let r=Math.max(1,Math.floor(e%3600/60));return n>0?`${n}h ${r}m`:`${r}m`}function q(e){if(typeof e!=`string`||!e)return``;let t=new Date(e);if(Number.isNaN(t.getTime()))return``;let n=Math.max(0,Math.round((Date.now()-t.getTime())/6e4));if(n<1)return`Just now`;if(n<60)return`${n}m ago`;let r=Math.floor(n/60);return r<24?`${r}h ago`:t.toLocaleTimeString([],{hour:`2-digit`,minute:`2-digit`})}function J(e){switch(String(e??``)){case`gateway`:return h;case`switch`:return o;case`access_point`:return c;case`camera`:return t;case`doorbell`:return n;case`chime`:case`ring`:return a;default:return r}}function Y(e){return e>=85?`critical`:e>=60?`warning`:`ok`}var X=class extends m{static properties={hass:{attribute:!1},config:{state:!0}};cardType;sourceCommand;options;optionsRequested=!1;constructor(e,t){super(),this.cardType=e,this.sourceCommand=t,this.config={type:`custom:${e}`},this.options=[]}setConfig(e){this.config={...e}}willUpdate(){this.hass&&!this.optionsRequested&&(this.optionsRequested=!0,this.loadOptions())}async loadOptions(){if(this.hass)try{let e=await this.hass.callWS({type:this.sourceCommand});this.options=this.sourceCommand===`unifi_insights/protect/sources`?e.map(e=>({entryId:e.entry_id,siteId:`*`,label:e.title})):j(e).map(e=>({entryId:e.binding.entry_id,siteId:e.binding.site_id,label:e.label})),this.requestUpdate()}catch{this.options=[],this.requestUpdate()}}applySite(e){let t={...this.config,type:this.config.type||`custom:${this.cardType}`};if(!e)delete t.entry_id,delete t.site_id;else{let n=this.options[Number.parseInt(e,10)];n&&(t.entry_id=n.entryId,t.site_id=n.siteId)}this.config=t,l(this,`config-changed`,{config:t})}applyTitle(e){let t={...this.config};e.trim()?t.title=e.trim():delete t.title,this.config=t,l(this,`config-changed`,{config:t})}render(){let e=this.config.entry_id&&this.config.site_id?String(this.options.findIndex(e=>e.entryId===this.config.entry_id&&e.siteId===this.config.site_id)):``;return d`
      <div class="editor">
        <label for="source"
          >${this.sourceCommand===`unifi_insights/protect/sources`?`Integration`:`Site`}</label
        >
        <select
          id="source"
          @change=${e=>this.applySite(e.target.value)}
        >
          <option value="">Auto</option>
          ${this.options.map((t,n)=>{let r=String(n);return d`<option value=${r} ?selected=${r===e}>
              ${t.label}
            </option>`})}
        </select>
        <label for="title">Title</label>
        <input
          id="title"
          .value=${this.config.title??``}
          @input=${e=>this.applyTitle(e.target.value)}
        />
      </div>
    `}static styles=[g,s,y`
      :host {
        display: block;
        min-width: 0;
      }
      .editor {
        display: grid;
        gap: 8px;
      }
      label {
        font-size: 0.85rem;
        color: var(--secondary-text-color);
      }
      select,
      input {
        min-height: 44px;
        width: 100%;
        min-width: 0;
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        padding: 4px 8px;
        background: var(--card-background-color);
        color: var(--primary-text-color);
      }
    `]},Z=[g,s,y`
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
  `],Q=class extends m{static editorTag=``;static async getConfigElement(){return document.createElement(this.editorTag)}static properties={hass:{attribute:!1},config:{state:!0},snapshot:{state:!0},sources:{state:!0},loading:{state:!0},error:{state:!0}};unsubscribe;bindingKey;syncGeneration=0;failedBindingKey;retryAfterMs=0;retryTimer;constructor(){super(),this.config={type:``},this.snapshot=void 0,this.sources=[],this.loading=!1,this.error=void 0}get headerAccent(){return`var(--primary-color, #03a9f4)`}renderHeaderBadge(){return p}get loadingLabel(){return`Loading`}get includeSiteInSubscribeMessage(){return!0}connectedCallback(){super.connectedCallback(),this.sync()}disconnectedCallback(){super.disconnectedCallback(),this.syncGeneration+=1,this.retryTimer&&=(clearTimeout(this.retryTimer),void 0),this.unsubscribe?.(),this.unsubscribe=void 0}willUpdate(e){let t=e.has(`hass`),n=e.has(`config`),r=e.get(`hass`),i=t&&r?.connection!==this.hass?.connection;(n||i)&&this.sync()}setConfig(e){if(!e||typeof e.type!=`string`)throw Error(`Invalid card config`);this.config={...e}}getCardSize(){return 4}getGridOptions(){return{columns:6,rows:4,min_columns:3,min_rows:3}}getBinding(){return this.config.entry_id&&this.config.site_id?{entry_id:this.config.entry_id,site_id:this.config.site_id}:M({entry_id:this.config.entry_id,site_id:this.config.site_id,title:this.config.title,view:`graph`,show_site_selector:!1,clients:`collapsed`,kinds:[`gateway`,`switch`,`access_point`,`client`,`other`],density:void 0,orientation:`vertical`,layout:`tree`,show_labels:!0,show_wan:!0,max_clients:500},this.sources)}async sync(){let e=++this.syncGeneration;if(!this.hass)return;this.loading=!0;try{if(this.sources=await this.hass.callWS({type:this.sourceCommand}),e!==this.syncGeneration||!this.isConnected){this.loading=!1;return}this.error=void 0}catch(t){if(e!==this.syncGeneration||!this.isConnected){this.loading=!1;return}this.error=u(t),this.loading=!1;return}let t=this.getBinding(),n=t?`${t.entry_id}:${t.site_id}`:void 0;if(n===this.bindingKey&&this.unsubscribe||(this.bindingKey=n,n!==this.failedBindingKey&&(this.failedBindingKey=void 0,this.retryAfterMs=0),this.snapshot=void 0,await this.unsubscribe?.(),this.unsubscribe=void 0,!t)){this.loading=!1;return}if(n!==void 0&&n===this.failedBindingKey&&Date.now()<this.retryAfterMs){this.loading=!1,this.retryTimer&&clearTimeout(this.retryTimer),this.retryTimer=setTimeout(()=>{e===this.syncGeneration&&this.isConnected&&this.sync()},Math.max(0,this.retryAfterMs-Date.now()));return}let r={type:this.subscribeCommand,entry_id:t.entry_id};this.includeSiteInSubscribeMessage&&(r.site_id=t.site_id);try{let t=await this.hass.connection.subscribeMessage(t=>{e===this.syncGeneration&&this.isConnected&&((Array.isArray(t.issues)?t.issues:[]).some(e=>e?.code===`entry_unloaded`)?(this.unsubscribe?.().catch(()=>void 0),this.unsubscribe=void 0,this.bindingKey=void 0,this.retryTimer&&clearTimeout(this.retryTimer),this.retryTimer=setTimeout(()=>{e===this.syncGeneration&&this.isConnected&&this.sync()},1e3)):(this.failedBindingKey=void 0,this.retryAfterMs=0,this.error=void 0,this.snapshot=t,this.loading=!1))},r,{resubscribe:!1});if(e!==this.syncGeneration||!this.isConnected){await t(),this.loading=!1;return}this.unsubscribe=t}catch(t){if(e!==this.syncGeneration||!this.isConnected){this.loading=!1;return}this.failedBindingKey=n,this.retryAfterMs=Date.now()+5e3,this.error=u(t),this.loading=!1,this.retryTimer=setTimeout(()=>{e===this.syncGeneration&&this.isConnected&&this.sync()},5e3)}}openMoreInfo(e){l(this,`hass-more-info`,{entityId:e})}renderStatus(){let e=_(this.hass?.language??this.hass?.locale?.language);if(this.loading)return d`<div
        class="state loading-box"
        role="status"
        aria-live="polite"
      >
        <span class="pulse-dot" aria-hidden="true"></span>
        ${e(`dashboard.loading`,{title:this.defaultTitle})}
      </div>`;if(this.error)return d`<div class="state error" role="alert">
        ${f(i)}
        <div>
          ${e(`dashboard.error`)}
          <span class="state-detail">${this.error.code}</span>
        </div>
      </div>`;let t=this.snapshot?.status;return t===`unavailable`?d`<div class="state error" role="alert">
        ${f(i)}
        ${e(`dashboard.unavailable`)}
      </div>`:t===`no_protect`?d`<div class="state" role="status" aria-live="polite">
        ${e(`dashboard.no_protect`)}
      </div>`:t===`partial`||t===`degraded`||this.snapshot?.freshness===`stale`?d`<div class="state" role="status" aria-live="polite">
        ${f(i)}
        <div>
          ${e(this.snapshot?.freshness===`stale`?`dashboard.stale`:`dashboard.partial`)}
          ${Array.isArray(this.snapshot?.warnings)&&this.snapshot.warnings.length?d`<span class="state-detail"
                >${this.snapshot.warnings.map(e=>String(e).replaceAll(`_`,` `)).join(` · `)}</span
              >`:p}
        </div>
      </div>`:p}render(){let e=!this.loading&&!this.error&&this.snapshot?.status!==`unavailable`&&this.snapshot?.status!==`no_protect`,t=typeof this.snapshot?.site_name==`string`&&this.snapshot.site_name.trim()?this.snapshot.site_name:typeof this.snapshot?.entry_title==`string`&&this.snapshot.entry_title.trim()?this.snapshot.entry_title:void 0;return d`
      <ha-card style=${`--card-accent: ${this.headerAccent}`}>
        <div class="header">
          <div class="header-main">
            <div class="header-icon">${f(this.headerIcon)}</div>
            <div class="header-titles">
              <h2 class="header-title" id="card-title">
                ${this.config.title??this.defaultTitle}
              </h2>
              ${t?d`<div class="header-subtitle">${t}</div>`:p}
            </div>
          </div>
          <div class="header-actions">
            ${e?this.renderHeaderBadge():p}
          </div>
        </div>
        <div
          class="content"
          tabindex="0"
          role="region"
          aria-labelledby="card-title"
        >
          ${this.renderStatus()}
          ${e?this.renderContent():p}
        </div>
      </ha-card>
    `}static styles=Z};function $(t){e(t.tag,t.card),e(t.editorTag,t.editor),window.customCards??=[],window.customCards.some(e=>e.type===t.tag)||window.customCards.push({type:t.tag,name:t.name,description:t.description,preview:!0,documentationURL:`https://github.com/tinkerologie/ha-unifi-insights#dashboard-cards`})}export{A,T as C,C as D,E,k as M,j as O,w as S,D as T,K as _,L as a,b,B as c,F as d,H as f,q as g,W as h,I as i,N as j,M as k,V as l,G as m,Q as n,R as o,U as p,X as r,z as s,$ as t,P as u,J as v,x as w,S as x,Y as y};