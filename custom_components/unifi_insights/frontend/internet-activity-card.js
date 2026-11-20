import{C as e,L as t,Y as n,ft as r,g as i,i as a,m as o,w as s}from"./chunks/localize-DuXs5QH_.js";import{a as c,h as l,i as u,m as d,n as f,r as p,t as m}from"./chunks/register-dashboard-card-DB8D56VC.js";var h=class extends f{static editorTag=c;static properties={...f.properties,selectedWindow:{state:!0}};constructor(){super(),this.selectedWindow=`1d`}get cardType(){return`custom:${u}`}get sourceCommand(){return r}get subscribeCommand(){return`unifi_insights/internet_activity/subscribe`}get defaultTitle(){return`Internet Activity`}get headerIcon(){return n}resolveActiveWindow(e){let t=[`1h`,`1d`,`1w`,`1m`].filter(t=>t in e);return{keys:t,activeWindow:this.selectedWindow in e?this.selectedWindow:t[0]??`1d`}}renderHeaderBadge(){let e=this.snapshot?.windows??{},{keys:t,activeWindow:n}=this.resolveActiveWindow(e);return t.length<=1?o:i`
      <div class="pill-tabs" role="group" aria-label="Time window">
        ${t.map(e=>i`
            <button
              type="button"
              class="pill-tab"
              aria-pressed=${String(n===e)}
              @click=${()=>{this.selectedWindow=e}}
            >
              ${e}
            </button>
          `)}
      </div>
    `}renderContent(){let n=this.snapshot;if(!n)return i`<div class="state">No data</div>`;let r=n.windows??{},{activeWindow:c}=this.resolveActiveWindow(r),u=r[c]??r[`1d`]??r[`1h`]??{},f=e=>typeof e==`number`&&Number.isFinite(e)&&e>=0?e:void 0,p=f(u.download_bytes),m=f(u.upload_bytes),h=p!==void 0&&m!==void 0?p+m:void 0,g=e=>e===void 0?`--`:`${Math.round(e/1e6)} MB`,_=e=>e===void 0?`--`:l(e),v=g(p),y=g(m),b=g(h),x=h!==void 0&&p!==void 0?h>0?Math.round(p/h*100):0:void 0,S=h!==void 0&&m!==void 0?h>0?100-(x??0):0:void 0,C=n.throughput??{},w=d(C.rx_bps),T=d(C.tx_bps),E=n.entity_ids??{},D=E[`download_${c}`],O=E[`upload_${c}`];return i`
      ${w||T?i`<div class="hero-banner">
            <div class="hero-left">
              ${a(t)}
              <div>
                <div class="hero-title">
                  ${w&&T?`Live Throughput`:`Throughput`}
                </div>
                <div class="hero-meta">
                  ↓ ${w??`--`} · ↑ ${T??`--`}
                </div>
              </div>
            </div>
            ${w&&T?i`<span class="chip ok">
                  <span class="status-dot"></span>Live
                </span>`:o}
          </div>`:o}

      <div class="kpi-grid">
        <button
          type="button"
          class="kpi-tile ${D?`clickable`:``}"
          ?disabled=${!D}
          @click=${D?()=>this.openMoreInfo(D):o}
        >
          <div class="kpi-top">
            <span>Download</span>
            ${a(e)}
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
          @click=${O?()=>this.openMoreInfo(O):o}
        >
          <div class="kpi-top">
            <span>Upload</span>
            ${a(s)}
          </div>
          <div class="kpi-value">${_(m)}</div>
          <div class="kpi-sub">
            ${y} · ${S===void 0?`--`:`${S}%`}
          </div>
        </button>
      </div>

      <div class="kpi-tile">
        <div class="item-top">
          <span class="kpi-top">Total Traffic (${c})</span>
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
    `}};m({tag:u,editorTag:c,card:h,editor:class extends p{constructor(){super(u,r)}},name:`UniFi Internet Activity`,description:`Historical WAN download/upload activity and live gateway throughput.`});