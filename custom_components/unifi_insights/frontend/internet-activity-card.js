import{B as e,C as t,F as n,It as r,N as i,Q as a,V as o,a as s,g as c,h as l,i as u,n as d,o as f,st as p,t as m}from"./chunks/register-dashboard-card-CBK2jPj2.js";var h=class extends d{static editorTag=f;static properties={...d.properties,selectedWindow:{state:!0}};constructor(){super(),this.selectedWindow=`1d`}get cardType(){return`custom:${s}`}get sourceCommand(){return r}get subscribeCommand(){return`unifi_insights/internet_activity/subscribe`}get defaultTitle(){return`Internet Activity`}get headerIcon(){return p}resolveActiveWindow(e){let t=[`1h`,`1d`,`1w`,`1m`].filter(t=>t in e);return{keys:t,activeWindow:this.selectedWindow in e?this.selectedWindow:t[0]??`1d`}}renderHeaderBadge(){let e=this.snapshot?.windows??{},{keys:t,activeWindow:r}=this.resolveActiveWindow(e);return t.length<=1?i:n`
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
    `}renderContent(){let r=this.snapshot;if(!r)return n`<div class="state">No data</div>`;let s=r.windows??{},{activeWindow:u}=this.resolveActiveWindow(s),d=s[u]??s[`1d`]??s[`1h`]??{},f=e=>typeof e==`number`&&Number.isFinite(e)&&e>=0?e:void 0,p=f(d.download_bytes),m=f(d.upload_bytes),h=p!==void 0&&m!==void 0?p+m:void 0,g=e=>e===void 0?`--`:`${Math.round(e/1e6)} MB`,_=e=>e===void 0?`--`:c(e),v=g(p),y=g(m),b=g(h),x=h!==void 0&&p!==void 0?h>0?Math.round(p/h*100):0:void 0,S=h!==void 0&&m!==void 0?h>0?100-(x??0):0:void 0,C=r.throughput??{},w=l(C.rx_bps),T=l(C.tx_bps),E=r.entity_ids??{},D=E[`download_${u}`],O=E[`upload_${u}`];return n`
      ${w||T?n`<div class="hero-banner">
            <div class="hero-left">
              ${t(a)}
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
            ${t(e)}
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
            ${t(o)}
          </div>
          <div class="kpi-value">${_(m)}</div>
          <div class="kpi-sub">
            ${y} · ${S===void 0?`--`:`${S}%`}
          </div>
        </button>
      </div>

      <div class="kpi-tile">
        <div class="item-top">
          <span class="kpi-top">Total Traffic (${u})</span>
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
    `}};m({tag:s,editorTag:f,card:h,editor:class extends u{constructor(){super(s,r)}},name:`UniFi Internet Activity`,description:`Historical WAN download/upload activity and live gateway throughput.`});