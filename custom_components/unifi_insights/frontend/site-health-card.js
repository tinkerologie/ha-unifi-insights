import{Ft as e,M as t,P as n,T as r,Y as i,Z as a,at as o,b as s,ct as c,it as l,m as u,n as d,o as f,p,t as m}from"./chunks/register-dashboard-card-COnrwOmv.js";m({tag:p,editorTag:u,card:class extends d{static editorTag=u;get cardType(){return`custom:${p}`}get sourceCommand(){return e}get subscribeCommand(){return`unifi_insights/site_health/subscribe`}get defaultTitle(){return`Site Health`}get headerIcon(){return o}get headerAccent(){let e=String(this.snapshot?.health?.level??`healthy`);return e===`critical`?`var(--uit-offline)`:e===`degraded`?`var(--uit-warning)`:`var(--uit-online)`}renderHeaderBadge(){if(!this.snapshot)return t;let e=this.snapshot.health??{},r=String(e.level??`unknown`);return n`<span class="chip ${r}">
      <span class="status-dot"></span>
      <span>${r}</span>
    </span>`}renderContent(){let e=this.snapshot;if(!e)return n`<div class="state">Choose site.</div>`;let o=e.health??{},u=e.gateway??{},d=e.clients??{},f=e.devices??{},p=String(o.level??`unknown`),m=String(u.internet??`unknown`),h=Number(d.total??0),g=Number(d.wired??0),_=Number(d.wireless??0),v=h>0?Math.round(_/h*100):50,y=0,b=0;for(let e of Object.values(f))if(e&&typeof e==`object`){let t=Number(e.online??0),n=Number(e.offline??0),r=Number(e.unknown??0);y+=t,b+=t+n+r}let x=typeof u.name==`string`&&u.name?u.name:`UniFi Gateway`,S=s(u.uptime_s);return n`
      <div class="hero-banner">
        <div class="hero-left">
          ${r(l)}
          <div>
            <div class="hero-title">${x}</div>
            <div class="hero-meta">
              Status: ${p}${S?` · Uptime ${S}`:``}
            </div>
          </div>
        </div>
        <span class="chip ${m===`online`?`ok`:`warning`}">
          <span class="status-dot"></span>
          ${m}
        </span>
      </div>

      <div class="kpi-grid">
        <div class="kpi-tile">
          <div class="kpi-top">
            <span>Internet</span>
            ${r(a)}
          </div>
          <div class="kpi-value">${m}</div>
          <div class="kpi-sub">WAN Health · ${p}</div>
        </div>

        <div class="kpi-tile">
          <div class="kpi-top">
            <span>Clients</span>
            ${r(c)}
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
                ${r(i)}
              </div>
              <div class="kpi-value">${y}/${b}</div>
              <div class="kpi-sub">Infrastructure online</div>
            </div>`:t}
      </div>
    `}},editor:class extends f{constructor(){super(p,e)}},name:`UniFi Site Health`,description:`Compact site health summary with WAN, gateway, device, and client status.`});