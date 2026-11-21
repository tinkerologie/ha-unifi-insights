import{E as e,K as t,O as n,T as r,W as i,Z as a,ft as o,g as s,i as c,m as l,t as u,x as d,y as f,z as p}from"./chunks/localize-DuXs5QH_.js";import{f as m,g as h,n as g,p as _,r as v,t as y}from"./chunks/register-dashboard-card-DB8D56VC.js";function b(n,a){let o=String(n.kind??`event`),s=o===`smartDetect`||o===`smartDetectZone`,c=s&&Array.isArray(n.smart_detect_types)?n.smart_detect_types.filter(e=>typeof e==`string`&&e.trim()).map(e=>e.trim()):[],l=c.length>0?c:[s?`motion`:o];return[...new Set(l.map(e=>e===`vehicle`?`car`:e))].map(n=>{switch(n){case`person`:return{label:a(`timeline.person`),icon:d};case`animal`:return{label:a(`timeline.animal`),icon:t};case`car`:return{label:a(`timeline.car`),icon:e};case`motion`:return{label:a(`timeline.motion`),icon:i};case`ring`:return{label:a(`timeline.doorbell`),icon:r};default:return{label:s&&c.length>0?a(`timeline.unknown_object`,{value:n}):n,icon:s&&c.length>0?p:i}}})}var x=class extends g{static editorTag=_;static styles=[...g.styles,f`
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
    `];get cardType(){return`custom:${m}`}get sourceCommand(){return o}get subscribeCommand(){return`unifi_insights/timeline/subscribe`}get defaultTitle(){return`Event Timeline`}get headerIcon(){return a}renderHeaderBadge(){if(!this.snapshot)return l;let e=Array.isArray(this.snapshot.items)?this.snapshot.items:[];return s`<span class="chip ${e.length>0?`warning`:`ok`}">
      ${e.length} Events
    </span>`}renderContent(){let e=this.snapshot;if(!e)return s`<div class="state">No events</div>`;let t=Array.isArray(e.items)?e.items:[];return t.length===0?e.status===`ok`?s`
        <div class="empty-hero">
          <div class="icon-badge">${c(n)}</div>
          <div class="empty-title">All Quiet · 0 Events</div>
          <div class="empty-sub">
            No security or motion detections in the recent window.
          </div>
        </div>
      `:s`<div class="state">
          ${u(this.hass?.language)(`dashboard.no_events`)}
        </div>`:s`
      <div class="list" tabindex="0" role="region" aria-label="Recent events">
        ${t.slice(0,6).map(e=>{let t=e.source??{},n=b(e,u(this.hass?.language)),r=String(e.severity??`info`),i=h(e.timestamp);return s`
            <div class="item-card">
              <div
                class="item-icon ${r===`warning`?`offline`:`online`}"
              >
                ${c(n[0].icon)}
              </div>
              <div class="item-body">
                <div class="item-top">
                  <span class="item-name">
                    ${String(t.name??`Src`)}
                  </span>
                  <span class="event-labels"
                    >${n.map(({label:e,icon:t})=>s`
                        <span
                          class="chip ${r===`warning`?`warning`:`ok`}"
                        >
                          ${c(t)} ${e}
                        </span>
                      `)}</span
                  >
                </div>
                ${i?s`<div class="item-meta">
                      <span>${i}</span>
                    </div>`:l}
              </div>
            </div>
          `})}
      </div>
    `}};y({tag:m,editorTag:_,card:x,editor:class extends v{constructor(){super(m,o)}},name:`UniFi Event Timeline`,description:`Recent UniFi Protect security and device activity timeline.`});