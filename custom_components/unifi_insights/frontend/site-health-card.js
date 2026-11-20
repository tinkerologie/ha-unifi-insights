import{J as e,L as t,P as n,Q as r,ft as i,g as a,i as o,m as s,q as c}from"./chunks/localize-DuXs5QH_.js";import{_ as l,d as u,n as d,r as f,t as p,u as m}from"./chunks/register-dashboard-card-DB8D56VC.js";p({tag:m,editorTag:u,card:class extends d{static editorTag=u;get cardType(){return`custom:${m}`}get sourceCommand(){return i}get subscribeCommand(){return`unifi_insights/site_health/subscribe`}get defaultTitle(){return`Site Health`}get headerIcon(){return e}get headerAccent(){let e=String(this.snapshot?.health?.level??`unknown`);return e===`critical`?`var(--uit-offline)`:e===`degraded`?`var(--uit-warning)`:e===`healthy`?`var(--uit-online)`:`var(--uit-unknown)`}renderHeaderBadge(){if(!this.snapshot)return s;let e=this.snapshot.health??{},t=String(e.level??`unknown`);return a`<span class="chip ${t}">
      <span class="status-dot"></span>
      <span>${t}</span>
    </span>`}renderContent(){let e=this.snapshot;if(!e)return a`<div class="state">Choose site.</div>`;let i=e.health??{},u=e.gateway??{},d=e.clients??{},f=e.devices??{},p=String(i.level??`unknown`),m=String(u.internet??`unknown`),h=Number(d.total??0),g=Number(d.wired??0),_=Number(d.wireless??0),v=h>0?Math.round(_/h*100):50,y=0,b=0;for(let e of Object.values(f))if(e&&typeof e==`object`){let t=Number(e.online??0),n=Number(e.offline??0),r=Number(e.unknown??0);y+=t,b+=t+n+r}let x=typeof u.name==`string`&&u.name?u.name:`UniFi Gateway`,S=l(u.uptime_s);return a`
      <div class="hero-banner">
        <div class="hero-left">
          ${o(c)}
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
            ${o(t)}
          </div>
          <div class="kpi-value">${m}</div>
          <div class="kpi-sub">WAN Health · ${p}</div>
        </div>

        <div class="kpi-tile">
          <div class="kpi-top">
            <span>Clients</span>
            ${o(r)}
          </div>
          <div class="kpi-value">${h}</div>
          <div class="bar-track" aria-hidden="true">
            <div class="bar-fill" style=${`width: ${v}%`}></div>
          </div>
          <div class="kpi-sub">
            ${_||g?`${_} Wi-Fi · ${g} Wired`:`Connected clients`}
          </div>
        </div>

        ${b>0?a`<div class="kpi-tile">
              <div class="kpi-top">
                <span>Devices</span>
                ${o(n)}
              </div>
              <div class="kpi-value">${y}/${b}</div>
              <div class="kpi-sub">Infrastructure online</div>
            </div>`:s}
      </div>
    `}},editor:class extends f{constructor(){super(m,i)}},name:`UniFi Site Health`,description:`Compact site health summary with WAN, gateway, device, and client status.`});