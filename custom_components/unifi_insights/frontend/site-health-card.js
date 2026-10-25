import{C as e,F as t,It as n,N as r,Q as i,X as a,at as o,d as s,f as c,i as l,lt as u,n as d,ot as f,t as p,v as m}from"./chunks/register-dashboard-card-CBK2jPj2.js";p({tag:s,editorTag:c,card:class extends d{static editorTag=c;get cardType(){return`custom:${s}`}get sourceCommand(){return n}get subscribeCommand(){return`unifi_insights/site_health/subscribe`}get defaultTitle(){return`Site Health`}get headerIcon(){return f}get headerAccent(){let e=String(this.snapshot?.health?.level??`unknown`);return e===`critical`?`var(--uit-offline)`:e===`degraded`?`var(--uit-warning)`:e===`healthy`?`var(--uit-online)`:`var(--uit-unknown)`}renderHeaderBadge(){if(!this.snapshot)return r;let e=this.snapshot.health??{},n=String(e.level??`unknown`);return t`<span class="chip ${n}">
      <span class="status-dot"></span>
      <span>${n}</span>
    </span>`}renderContent(){let n=this.snapshot;if(!n)return t`<div class="state">Choose site.</div>`;let s=n.health??{},c=n.gateway??{},l=n.clients??{},d=n.devices??{},f=String(s.level??`unknown`),p=String(c.internet??`unknown`),h=Number(l.total??0),g=Number(l.wired??0),_=Number(l.wireless??0),v=h>0?Math.round(_/h*100):50,y=0,b=0;for(let e of Object.values(d))if(e&&typeof e==`object`){let t=Number(e.online??0),n=Number(e.offline??0),r=Number(e.unknown??0);y+=t,b+=t+n+r}let x=typeof c.name==`string`&&c.name?c.name:`UniFi Gateway`,S=m(c.uptime_s);return t`
      <div class="hero-banner">
        <div class="hero-left">
          ${e(o)}
          <div>
            <div class="hero-title">${x}</div>
            <div class="hero-meta">
              Status: ${f}${S?` · Uptime ${S}`:``}
            </div>
          </div>
        </div>
        <span class="chip ${p===`online`?`ok`:`warning`}">
          <span class="status-dot"></span>
          ${p}
        </span>
      </div>

      <div class="kpi-grid">
        <div class="kpi-tile">
          <div class="kpi-top">
            <span>Internet</span>
            ${e(i)}
          </div>
          <div class="kpi-value">${p}</div>
          <div class="kpi-sub">WAN Health · ${f}</div>
        </div>

        <div class="kpi-tile">
          <div class="kpi-top">
            <span>Clients</span>
            ${e(u)}
          </div>
          <div class="kpi-value">${h}</div>
          <div class="bar-track" aria-hidden="true">
            <div class="bar-fill" style=${`width: ${v}%`}></div>
          </div>
          <div class="kpi-sub">
            ${_||g?`${_} Wi-Fi · ${g} Wired`:`Connected clients`}
          </div>
        </div>

        ${b>0?t`<div class="kpi-tile">
              <div class="kpi-top">
                <span>Devices</span>
                ${e(a)}
              </div>
              <div class="kpi-value">${y}/${b}</div>
              <div class="kpi-sub">Infrastructure online</div>
            </div>`:r}
      </div>
    `}},editor:class extends l{constructor(){super(s,n)}},name:`UniFi Site Health`,description:`Compact site health summary with WAN, gateway, device, and client status.`});