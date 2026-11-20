import{ft as e,g as t,i as n,j as r,m as i}from"./chunks/localize-DuXs5QH_.js";import{n as a,o,r as s,s as c,t as l,v as u,y as d}from"./chunks/register-dashboard-card-DB8D56VC.js";l({tag:o,editorTag:c,card:class extends a{static editorTag=c;get cardType(){return`custom:${o}`}get sourceCommand(){return e}get subscribeCommand(){return`unifi_insights/performance/subscribe`}get defaultTitle(){return`Device Performance`}get headerIcon(){return r}renderHeaderBadge(){let e=Array.isArray(this.snapshot?.devices)?this.snapshot.devices:[];return this.snapshot?t`<span
      class="chip ${this.snapshot.status===`partial`?`partial`:``}"
      >${e.length} Devices</span
    >`:i}renderContent(){let e=this.snapshot;if(!e)return t`<div class="state">No devices</div>`;let r=Array.isArray(e.devices)?e.devices:[],a=r.map(e=>e.cpu_pct).filter(e=>typeof e==`number`&&Number.isFinite(e)&&e>=0),o=r.map(e=>e.memory_pct).filter(e=>typeof e==`number`&&Number.isFinite(e)&&e>=0),s=a.length>0?Math.round(a.reduce((e,t)=>e+t,0)/a.length):void 0,c=o.length>0?Math.round(o.reduce((e,t)=>e+t,0)/o.length):void 0;return t`
      <div class="ring-strip">
        <div class="ring-card">
          <div
            class="ring-gauge"
            style=${`--pct: ${s??0}; --ring-color: var(--uit-${s===void 0?`unknown`:d(s)===`ok`?`online`:d(s)===`warning`?`warning`:`offline`})`}
          >
            <span>${s===void 0?`--`:`${s}%`}</span>
          </div>
          <div>
            <div class="kpi-top">Avg CPU</div>
            <div class="kpi-sub">${r.length} Devices</div>
          </div>
        </div>
        <div class="ring-card">
          <div
            class="ring-gauge"
            style=${`--pct: ${c??0}; --ring-color: var(--primary-color)`}
          >
            <span>${c===void 0?`--`:`${c}%`}</span>
          </div>
          <div>
            <div class="kpi-top">Avg Memory</div>
            <div class="kpi-sub">System Load</div>
          </div>
        </div>
      </div>

      <div
        class="list"
        tabindex="0"
        role="region"
        aria-label="Device performance"
      >
        ${r.slice(0,6).map(e=>{let r=String(e.name??`Device`),a=String(e.kind??`other`),o=Number(e.cpu_pct),s=e.cpu_pct==null||!Number.isFinite(o)?null:Math.min(100,Math.max(0,Math.round(o))),c=e.memory_pct==null?null:Math.round(Number(e.memory_pct)),l=s==null?`ok`:d(s),f=typeof e.clients==`number`?`${e.clients} clients`:void 0;return t`
            <div class="item-card">
              <div class="item-icon">${n(u(a))}</div>
              <div class="item-body">
                <div class="item-top">
                  <span class="item-name">${r}</span>
                  <span class="item-value">
                    ${s==null?`--`:`${s}%`}
                  </span>
                </div>
                <div class="bar-track" aria-hidden="true">
                  <div
                    class="bar-fill ${l}"
                    style=${`width: ${s??0}%`}
                  ></div>
                </div>
                ${c!=null||f?t`<div class="item-meta">
                      ${c==null?i:t`<span>RAM ${c}%</span>`}
                      ${f?t`<span>· ${f}</span>`:i}
                    </div>`:i}
              </div>
            </div>
          `})}
      </div>
    `}},editor:class extends s{constructor(){super(o,e)}},name:`UniFi Device Performance`,description:`Infrastructure CPU, memory, PoE, client load, and throughput summary.`});