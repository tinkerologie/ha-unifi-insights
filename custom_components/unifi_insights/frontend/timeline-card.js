import{Ft as e,M as t,P as n,T as r,U as i,V as a,g as o,h as s,n as c,nt as l,o as u,st as d,t as f,y as p}from"./chunks/register-dashboard-card-COnrwOmv.js";f({tag:s,editorTag:o,card:class extends c{static editorTag=o;get cardType(){return`custom:${s}`}get sourceCommand(){return e}get subscribeCommand(){return`unifi_insights/timeline/subscribe`}get defaultTitle(){return`Event Timeline`}get headerIcon(){return d}renderHeaderBadge(){if(!this.snapshot)return t;let e=Array.isArray(this.snapshot.items)?this.snapshot.items:[];return n`<span class="chip ${e.length>0?`warning`:`ok`}">
      ${e.length} Events
    </span>`}renderContent(){let e=this.snapshot;if(!e)return n`<div class="state">No events</div>`;let o=Array.isArray(e.items)?e.items:[];return o.length===0?n`
        <div class="empty-hero">
          <div class="icon-badge">${r(i)}</div>
          <div class="empty-title">All Quiet · 0 Events</div>
          <div class="empty-sub">
            No security or motion detections in the recent window.
          </div>
        </div>
      `:n`
      <div class="list">
        ${o.slice(0,6).map(e=>{let i=e.source??{},o=String(e.kind??`event`),s=String(e.severity??`info`),c=p(e.timestamp);return n`
            <div class="item-card">
              <div
                class="item-icon ${s===`warning`?`offline`:`online`}"
              >
                ${r(o===`ring`?a:l)}
              </div>
              <div class="item-body">
                <div class="item-top">
                  <span class="item-name">
                    ${String(i.name??`Src`)}
                  </span>
                  <span
                    class="chip ${s===`warning`?`warning`:`ok`}"
                  >
                    ${o}
                  </span>
                </div>
                ${c?n`<div class="item-meta">
                      <span>${c}</span>
                    </div>`:t}
              </div>
            </div>
          `})}
      </div>
    `}},editor:class extends u{constructor(){super(s,e)}},name:`UniFi Event Timeline`,description:`Recent UniFi Protect security and device activity timeline.`});