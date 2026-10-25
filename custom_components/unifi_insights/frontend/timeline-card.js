import{C as e,F as t,H as n,It as r,N as i,W as a,_ as o,ct as s,i as c,m as l,n as u,p as d,r as f,rt as p,t as m}from"./chunks/register-dashboard-card-CBK2jPj2.js";m({tag:d,editorTag:l,card:class extends u{static editorTag=l;get cardType(){return`custom:${d}`}get sourceCommand(){return r}get subscribeCommand(){return`unifi_insights/timeline/subscribe`}get defaultTitle(){return`Event Timeline`}get headerIcon(){return s}renderHeaderBadge(){if(!this.snapshot)return i;let e=Array.isArray(this.snapshot.items)?this.snapshot.items:[];return t`<span class="chip ${e.length>0?`warning`:`ok`}">
      ${e.length} Events
    </span>`}renderContent(){let r=this.snapshot;if(!r)return t`<div class="state">No events</div>`;let s=Array.isArray(r.items)?r.items:[];return s.length===0?r.status===`ok`?t`
        <div class="empty-hero">
          <div class="icon-badge">${e(a)}</div>
          <div class="empty-title">All Quiet · 0 Events</div>
          <div class="empty-sub">
            No security or motion detections in the recent window.
          </div>
        </div>
      `:t`<div class="state">
          ${f(this.hass?.language)(`dashboard.no_events`)}
        </div>`:t`
      <div class="list" tabindex="0" role="region" aria-label="Recent events">
        ${s.slice(0,6).map(r=>{let a=r.source??{},s=String(r.kind??`event`),c=String(r.severity??`info`),l=o(r.timestamp);return t`
            <div class="item-card">
              <div
                class="item-icon ${c===`warning`?`offline`:`online`}"
              >
                ${e(s===`ring`?n:p)}
              </div>
              <div class="item-body">
                <div class="item-top">
                  <span class="item-name">
                    ${String(a.name??`Src`)}
                  </span>
                  <span
                    class="chip ${c===`warning`?`warning`:`ok`}"
                  >
                    ${s}
                  </span>
                </div>
                ${l?t`<div class="item-meta">
                      <span>${l}</span>
                    </div>`:i}
              </div>
            </div>
          `})}
      </div>
    `}},editor:class extends c{constructor(){super(d,r)}},name:`UniFi Event Timeline`,description:`Recent UniFi Protect security and device activity timeline.`});