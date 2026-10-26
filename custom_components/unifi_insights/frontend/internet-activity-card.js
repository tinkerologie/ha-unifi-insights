import{Bt as e,C as t,F as n,H as r,N as i,V as a,a as o,dt as s,et as c,g as l,h as u,i as d,n as f,o as p,t as m}from"./chunks/register-dashboard-card-DEafGS50.js";var h=class extends f{static editorTag=p;static properties={...f.properties,selectedWindow:{state:!0}};constructor(){super(),this.selectedWindow=`1d`}get cardType(){return`custom:${o}`}get sourceCommand(){return e}get subscribeCommand(){return`unifi_insights/internet_activity/subscribe`}get defaultTitle(){return`Internet Activity`}get headerIcon(){return s}resolveActiveWindow(e){let t=[`1h`,`1d`,`1w`,`1m`].filter(t=>t in e);return{keys:t,activeWindow:this.selectedWindow in e?this.selectedWindow:t[0]??`1d`}}renderHeaderBadge(){let e=this.snapshot?.windows??{},{keys:t,activeWindow:r}=this.resolveActiveWindow(e);return t.length<=1?i:n`
      <div class="pill-tabs" role="group" aria-label="Time window">
        ${t.map(e=>n`
            <button
              type="button"
              class="pill-tab"
              aria-pressed=${String(r===e)}
              @click=${()=>{this.selectedWindow=e}}
            >
              ${e}
            </button>
          `)}
      </div>
    `}renderContent(){let e=this.snapshot;if(!e)return n`<div class="state">No data</div>`;let o=e.windows??{},{activeWindow:s}=this.resolveActiveWindow(o),d=o[s]??o[`1d`]??o[`1h`]??{},f=e=>typeof e==`number`&&Number.isFinite(e)&&e>=0?e:void 0,p=f(d.download_bytes),m=f(d.upload_bytes),h=p!==void 0&&m!==void 0?p+m:void 0,g=e=>e===void 0?`--`:`${Math.round(e/1e6)} MB`,_=e=>e===void 0?`--`:l(e),v=g(p),y=g(m),b=g(h),x=h!==void 0&&p!==void 0?h>0?Math.round(p/h*100):0:void 0,S=h!==void 0&&m!==void 0?h>0?100-(x??0):0:void 0,C=e.throughput??{},w=u(C.rx_bps),T=u(C.tx_bps),E=e.entity_ids??{},D=E[`download_${s}`],O=E[`upload_${s}`];return n`
      ${w||T?n`<div class="hero-banner">
            <div class="hero-left">
              ${t(c)}
              <div>
                <div class="hero-title">
                  ${w&&T?`Live Throughput`:`Throughput`}
                </div>
                <div class="hero-meta">
                  ↓ ${w??`--`} · ↑ ${T??`--`}
                </div>
              </div>
            </div>
            ${w&&T?n`<span class="chip ok">
                  <span class="status-dot"></span>Live
                </span>`:i}
          </div>`:i}

      <div class="kpi-grid">
        <button
          type="button"
          class="kpi-tile ${D?`clickable`:``}"
          ?disabled=${!D}
          @click=${D?()=>this.openMoreInfo(D):i}
        >
          <div class="kpi-top">
            <span>Download</span>
            ${t(a)}
          </div>
          <div class="kpi-value">${_(p)}</div>
          <div class="kpi-sub">
            ${v} · ${x===void 0?`--`:`${x}%`}
          </div>
        </button>

        <button
          type="button"
          class="kpi-tile ${O?`clickable`:``}"
          ?disabled=${!O}
          @click=${O?()=>this.openMoreInfo(O):i}
        >
          <div class="kpi-top">
            <span>Upload</span>
            ${t(r)}
          </div>
          <div class="kpi-value">${_(m)}</div>
          <div class="kpi-sub">
            ${y} · ${S===void 0?`--`:`${S}%`}
          </div>
        </button>
      </div>

      <div class="kpi-tile">
        <div class="item-top">
          <span class="kpi-top">Total Traffic (${s})</span>
          <span class="item-value"> ${_(h)} (${b}) </span>
        </div>
        <div class="bar-track" aria-hidden="true">
          <div class="bar-fill" style=${`width: ${x??0}%`}></div>
          <div
            class="bar-fill secondary"
            style=${`width: ${S??0}%`}
          ></div>
        </div>
      </div>
    `}};m({tag:o,editorTag:p,card:h,editor:class extends d{constructor(){super(o,e)}},name:`UniFi Internet Activity`,description:`Historical WAN download/upload activity and live gateway throughput.`});