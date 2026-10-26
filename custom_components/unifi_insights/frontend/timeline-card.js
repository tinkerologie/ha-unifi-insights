import{Bt as e,C as t,F as n,K as r,N as i,R as a,U as o,W as s,_ as c,ct as l,ft as u,i as d,m as f,n as p,nt as m,ot as h,p as g,r as _,t as v,z as y}from"./chunks/register-dashboard-card-DEafGS50.js";function b(e,t){let n=String(e.kind??`event`),r=n===`smartDetect`||n===`smartDetectZone`,i=r&&Array.isArray(e.smart_detect_types)?e.smart_detect_types.filter(e=>typeof e==`string`&&e.trim()).map(e=>e.trim()):[],a=i.length>0?i:[r?`motion`:n];return[...new Set(a.map(e=>e===`vehicle`?`car`:e))].map(e=>{switch(e){case`person`:return{label:t(`timeline.person`),icon:y};case`animal`:return{label:t(`timeline.animal`),icon:l};case`car`:return{label:t(`timeline.car`),icon:s};case`motion`:return{label:t(`timeline.motion`),icon:h};case`ring`:return{label:t(`timeline.doorbell`),icon:o};default:return{label:r&&i.length>0?t(`timeline.unknown_object`,{value:e}):e,icon:r&&i.length>0?m:h}}})}var x=class extends p{static editorTag=f;static styles=[...p.styles,a`
      .event-labels {
        display: flex;
        flex-wrap: wrap;
        gap: var(--ha-space-2, 6px);
        min-width: 0;
      }
      .event-labels .chip {
        white-space: normal;
        overflow-wrap: anywhere;
        min-width: 0;
      }
    `];get cardType(){return`custom:${g}`}get sourceCommand(){return e}get subscribeCommand(){return`unifi_insights/timeline/subscribe`}get defaultTitle(){return`Event Timeline`}get headerIcon(){return u}renderHeaderBadge(){if(!this.snapshot)return i;let e=Array.isArray(this.snapshot.items)?this.snapshot.items:[];return n`<span class="chip ${e.length>0?`warning`:`ok`}">
      ${e.length} Events
    </span>`}renderContent(){let e=this.snapshot;if(!e)return n`<div class="state">No events</div>`;let a=Array.isArray(e.items)?e.items:[];return a.length===0?e.status===`ok`?n`
        <div class="empty-hero">
          <div class="icon-badge">${t(r)}</div>
          <div class="empty-title">All Quiet · 0 Events</div>
          <div class="empty-sub">
            No security or motion detections in the recent window.
          </div>
        </div>
      `:n`<div class="state">
          ${_(this.hass?.language)(`dashboard.no_events`)}
        </div>`:n`
      <div class="list" tabindex="0" role="region" aria-label="Recent events">
        ${a.slice(0,6).map(e=>{let r=e.source??{},a=b(e,_(this.hass?.language)),o=String(e.severity??`info`),s=c(e.timestamp);return n`
            <div class="item-card">
              <div
                class="item-icon ${o===`warning`?`offline`:`online`}"
              >
                ${t(a[0].icon)}
              </div>
              <div class="item-body">
                <div class="item-top">
                  <span class="item-name">
                    ${String(r.name??`Src`)}
                  </span>
                  <span class="event-labels"
                    >${a.map(({label:e,icon:r})=>n`
                        <span
                          class="chip ${o===`warning`?`warning`:`ok`}"
                        >
                          ${t(r)} ${e}
                        </span>
                      `)}</span
                  >
                </div>
                ${s?n`<div class="item-meta">
                      <span>${s}</span>
                    </div>`:i}
              </div>
            </div>
          `})}
      </div>
    `}};v({tag:g,editorTag:f,card:x,editor:class extends d{constructor(){super(g,e)}},name:`UniFi Event Timeline`,description:`Recent UniFi Protect security and device activity timeline.`});