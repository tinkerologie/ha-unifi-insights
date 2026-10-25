import{C as e,F as t,N as n,U as r,i,l as a,n as o,t as s,u as c,y as l,z as u}from"./chunks/register-dashboard-card-CBK2jPj2.js";s({tag:a,editorTag:c,card:class extends o{static editorTag=c;get cardType(){return`custom:${a}`}get sourceCommand(){return`unifi_insights/protect/sources`}get subscribeCommand(){return`unifi_insights/protect/subscribe`}get defaultTitle(){return`Protect Status`}get headerIcon(){return r}get includeSiteInSubscribeMessage(){return!1}getBinding(){let e=this.config.entry_id;if(e)return{entry_id:e,site_id:this.config.site_id??`*`};if(this.sources.length===1)return{entry_id:this.sources[0].entry_id,site_id:this.config.site_id??`*`}}renderHeaderBadge(){if(!this.snapshot)return n;let e=Array.isArray(this.snapshot.devices)?this.snapshot.devices:[];if(this.snapshot.status===`degraded`&&e.length===0)return n;let r=e.filter(e=>e.connected===!1).length,i=e.length-r;return t`
      <span
        class="chip ${r>0||this.snapshot.status===`degraded`?`warning`:`ok`}"
      >
        <span class="status-dot"></span>
        ${i}/${e.length} Online
      </span>
    `}renderContent(){let i=this.snapshot;if(!i)return t`<div class="state">No Protect data</div>`;let a=Array.isArray(i.devices)?i.devices:[];if(i.status===`degraded`&&a.length===0)return n;let o=a.filter(e=>e.connected===!1).length,s=a.length-o;return t`
      <div class="kpi-grid">
        <div class="kpi-tile">
          <div class="kpi-top">
            <span>Devices</span>
            ${e(r)}
          </div>
          <div class="kpi-value">${a.length}</div>
          <div class="kpi-sub">${s} active</div>
        </div>
        <div class="kpi-tile">
          <div class="kpi-top">
            <span>Offline</span>
            ${e(u)}
          </div>
          <div class="kpi-value">${o}</div>
          <div class="kpi-sub">
            ${o===0&&i.status!==`degraded`?`All connected`:`Needs attention`}
          </div>
        </div>
      </div>

      <div class="list">
        ${a.slice(0,6).map(r=>{let i=typeof r.camera_entity_id==`string`?r.camera_entity_id:void 0,a=String(r.name??`Device`),o=String(r.kind??`device`),s=r.connected!==!1,c=s?`online`:`offline`,u=`${a} · ${o}`;return t`
            <div class="item-card">
              <div class="item-icon ${c}">
                ${e(l(o))}
              </div>
              <div class="item-body">
                <div class="item-top">
                  ${i?t`<button
                        type="button"
                        class="link item-name"
                        @click=${()=>this.openMoreInfo(i)}
                      >
                        ${u}
                      </button>`:t`<span class="item-name"> ${u} </span>`}
                  <span class="chip ${c}">
                    <span class="status-dot"></span>
                    ${c}
                  </span>
                </div>
                ${r.is_recording||r.motion_active?t`<div class="item-meta">
                      ${r.is_recording&&s?t`<span>● Recording</span>`:n}
                      ${r.motion_active?t`<span>· Motion detected</span>`:n}
                    </div>`:n}
              </div>
            </div>
          `})}
      </div>
    `}},editor:class extends i{constructor(){super(a,`unifi_insights/protect/sources`)}},name:`UniFi Protect Status`,description:`Live UniFi Protect camera, doorbell, chime, and NVR status summary.`});