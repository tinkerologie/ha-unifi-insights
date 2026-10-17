import{B as e,Ft as t,M as n,P as r,T as i,Z as a,_ as o,c as s,n as c,o as l,ot as u,s as d,t as f,v as p,z as m}from"./chunks/register-dashboard-card-COnrwOmv.js";var h=class extends c{static editorTag=s;static properties={...c.properties,selectedWindow:{state:!0}};constructor(){super(),this.selectedWindow=`1d`}get cardType(){return`custom:${d}`}get sourceCommand(){return t}get subscribeCommand(){return`unifi_insights/internet_activity/subscribe`}get defaultTitle(){return`Internet Activity`}get headerIcon(){return u}resolveActiveWindow(e){let t=[`1h`,`1d`,`1w`,`1m`].filter(t=>t in e);return{keys:t,activeWindow:this.selectedWindow in e?this.selectedWindow:t[0]??`1d`}}renderHeaderBadge(){let e=this.snapshot?.windows??{},{keys:t,activeWindow:i}=this.resolveActiveWindow(e);return t.length<=1?n:r`
      <div class="pill-tabs" role="group" aria-label="Time window">
        ${t.map(e=>r`
            <button
              type="button"
              class="pill-tab"
              aria-pressed=${String(i===e)}
              @click=${()=>{this.selectedWindow=e}}
            >
              ${e}
            </button>
          `)}
      </div>
    `}renderContent(){let t=this.snapshot;if(!t)return r`<div class="state">No data</div>`;let s=t.windows??{},{activeWindow:c}=this.resolveActiveWindow(s),l=s[c]??s[`1d`]??s[`1h`]??{},u=Number(l.download_bytes??0),d=Number(l.upload_bytes??0),f=u+d,h=`${Math.round(u/1e6)} MB`,g=`${Math.round(d/1e6)} MB`,_=`${Math.round(f/1e6)} MB`,v=f>0?Math.round(u/f*100):50,y=f>0?100-v:50,b=t.throughput??{},x=o(b.rx_bps),S=o(b.tx_bps),C=t.entity_ids??{},w=C[`download_${c}`],T=C[`upload_${c}`];return r`
      ${x||S?r`<div class="hero-banner">
            <div class="hero-left">
              ${i(a)}
              <div>
                <div class="hero-title">Live Throughput</div>
                <div class="hero-meta">
                  ↓ ${x??`0 bps`} · ↑ ${S??`0 bps`}
                </div>
              </div>
            </div>
            <span class="chip ok"> <span class="status-dot"></span>Live </span>
          </div>`:n}

      <div class="kpi-grid">
        <button
          type="button"
          class="kpi-tile ${w?`clickable`:``}"
          ?disabled=${!w}
          @click=${w?()=>this.openMoreInfo(w):n}
        >
          <div class="kpi-top">
            <span>Download</span>
            ${i(m)}
          </div>
          <div class="kpi-value">${p(u)}</div>
          <div class="kpi-sub">${h} · ${v}%</div>
        </button>

        <button
          type="button"
          class="kpi-tile ${T?`clickable`:``}"
          ?disabled=${!T}
          @click=${T?()=>this.openMoreInfo(T):n}
        >
          <div class="kpi-top">
            <span>Upload</span>
            ${i(e)}
          </div>
          <div class="kpi-value">${p(d)}</div>
          <div class="kpi-sub">${g} · ${y}%</div>
        </button>
      </div>

      <div class="kpi-tile">
        <div class="item-top">
          <span class="kpi-top">Total Traffic (${c})</span>
          <span class="item-value">
            ${p(f)} (${_})
          </span>
        </div>
        <div class="bar-track" aria-hidden="true">
          <div class="bar-fill" style=${`width: ${v}%`}></div>
          <div class="bar-fill secondary" style=${`width: ${y}%`}></div>
        </div>
      </div>
    `}};f({tag:d,editorTag:s,card:h,editor:class extends l{constructor(){super(d,t)}},name:`UniFi Internet Activity`,description:`Historical WAN download/upload activity and live gateway throughput.`});