import{D as e,S as t,g as n,i as r,m as i}from"./chunks/localize-DuXs5QH_.js";import{c as a,l as o,n as s,r as c,t as l,v as u}from"./chunks/register-dashboard-card-DB8D56VC.js";l({tag:a,editorTag:o,card:class extends s{static editorTag=o;get cardType(){return`custom:${a}`}get sourceCommand(){return`unifi_insights/protect/sources`}get subscribeCommand(){return`unifi_insights/protect/subscribe`}get defaultTitle(){return`Protect Status`}get headerIcon(){return e}get includeSiteInSubscribeMessage(){return!1}getBinding(){let e=this.config.entry_id;if(e)return{entry_id:e,site_id:this.config.site_id??`*`};if(this.sources.length===1)return{entry_id:this.sources[0].entry_id,site_id:this.config.site_id??`*`}}renderHeaderBadge(){if(!this.snapshot)return i;let e=Array.isArray(this.snapshot.devices)?this.snapshot.devices:[];if(this.snapshot.status===`degraded`&&e.length===0)return i;let t=e.filter(e=>e.connected===!1).length,r=e.length-t;return n`
      <span
        class="chip ${t>0||this.snapshot.status===`degraded`?`warning`:`ok`}"
      >
        <span class="status-dot"></span>
        ${r}/${e.length} Online
      </span>
    `}renderContent(){let a=this.snapshot;if(!a)return n`<div class="state">No Protect data</div>`;let o=Array.isArray(a.devices)?a.devices:[];if(a.status===`degraded`&&o.length===0)return i;let s=o.filter(e=>e.connected===!1).length,c=o.length-s;return n`
      <div class="kpi-grid">
        <div class="kpi-tile">
          <div class="kpi-top">
            <span>Devices</span>
            ${r(e)}
          </div>
          <div class="kpi-value">${o.length}</div>
          <div class="kpi-sub">${c} active</div>
        </div>
        <div class="kpi-tile">
          <div class="kpi-top">
            <span>Offline</span>
            ${r(t)}
          </div>
          <div class="kpi-value">${s}</div>
          <div class="kpi-sub">
            ${s===0&&a.status!==`degraded`?`All connected`:`Needs attention`}
          </div>
        </div>
      </div>

      <div class="list">
        ${o.slice(0,6).map(e=>{let t=typeof e.camera_entity_id==`string`?e.camera_entity_id:void 0,a=String(e.name??`Device`),o=String(e.kind??`device`),s=e.connected!==!1,c=s?`online`:`offline`,l=`${a} · ${o}`;return n`
            <div class="item-card">
              <div class="item-icon ${c}">
                ${r(u(o))}
              </div>
              <div class="item-body">
                <div class="item-top">
                  ${t?n`<button
                        type="button"
                        class="link item-name"
                        @click=${()=>this.openMoreInfo(t)}
                      >
                        ${l}
                      </button>`:n`<span class="item-name"> ${l} </span>`}
                  <span class="chip ${c}">
                    <span class="status-dot"></span>
                    ${c}
                  </span>
                </div>
                ${e.is_recording||e.motion_active?n`<div class="item-meta">
                      ${e.is_recording&&s?n`<span>● Recording</span>`:i}
                      ${e.motion_active?n`<span>· Motion detected</span>`:i}
                    </div>`:i}
              </div>
            </div>
          `})}
      </div>
    `}},editor:class extends c{constructor(){super(a,`unifi_insights/protect/sources`)}},name:`UniFi Protect Status`,description:`Live UniFi Protect camera, doorbell, chime, and NVR status summary.`});