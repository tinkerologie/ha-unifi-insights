import{Bt as e,C as t,F as n,N as r,Q as i,d as a,et as o,f as s,i as c,lt as l,n as u,pt as d,t as f,ut as p,v as m}from"./chunks/register-dashboard-card-DEafGS50.js";f({tag:a,editorTag:s,card:class extends u{static editorTag=s;get cardType(){return`custom:${a}`}get sourceCommand(){return e}get subscribeCommand(){return`unifi_insights/site_health/subscribe`}get defaultTitle(){return`Site Health`}get headerIcon(){return p}get headerAccent(){let e=String(this.snapshot?.health?.level??`unknown`);return e===`critical`?`var(--uit-offline)`:e===`degraded`?`var(--uit-warning)`:e===`healthy`?`var(--uit-online)`:`var(--uit-unknown)`}renderHeaderBadge(){if(!this.snapshot)return r;let e=this.snapshot.health??{},t=String(e.level??`unknown`);return n`<span class="chip ${t}">
      <span class="status-dot"></span>
      <span>${t}</span>
    </span>`}renderContent(){let e=this.snapshot;if(!e)return n`<div class="state">Choose site.</div>`;let a=e.health??{},s=e.gateway??{},c=e.clients??{},u=e.devices??{},f=String(a.level??`unknown`),p=String(s.internet??`unknown`),h=Number(c.total??0),g=Number(c.wired??0),_=Number(c.wireless??0),v=h>0?Math.round(_/h*100):50,y=0,b=0;for(let e of Object.values(u))if(e&&typeof e==`object`){let t=Number(e.online??0),n=Number(e.offline??0),r=Number(e.unknown??0);y+=t,b+=t+n+r}let x=typeof s.name==`string`&&s.name?s.name:`UniFi Gateway`,S=m(s.uptime_s);return n`
      <div class="hero-banner">
        <div class="hero-left">
          ${t(l)}
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
            ${t(o)}
          </div>
          <div class="kpi-value">${p}</div>
          <div class="kpi-sub">WAN Health · ${f}</div>
        </div>

        <div class="kpi-tile">
          <div class="kpi-top">
            <span>Clients</span>
            ${t(d)}
          </div>
          <div class="kpi-value">${h}</div>
          <div class="bar-track" aria-hidden="true">
            <div class="bar-fill" style=${`width: ${v}%`}></div>
          </div>
          <div class="kpi-sub">
            ${_||g?`${_} Wi-Fi · ${g} Wired`:`Connected clients`}
          </div>
        </div>

        ${b>0?n`<div class="kpi-tile">
              <div class="kpi-top">
                <span>Devices</span>
                ${t(i)}
              </div>
              <div class="kpi-value">${y}/${b}</div>
              <div class="kpi-sub">Infrastructure online</div>
            </div>`:r}
      </div>
    `}},editor:class extends c{constructor(){super(a,e)}},name:`UniFi Site Health`,description:`Compact site health summary with WAN, gateway, device, and client status.`});