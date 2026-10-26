import{Bt as e,C as t,F as n,N as r,Y as i,b as a,c as o,i as s,n as c,s as l,t as u,y as d}from"./chunks/register-dashboard-card-DEafGS50.js";u({tag:l,editorTag:o,card:class extends c{static editorTag=o;get cardType(){return`custom:${l}`}get sourceCommand(){return e}get subscribeCommand(){return`unifi_insights/performance/subscribe`}get defaultTitle(){return`Device Performance`}get headerIcon(){return i}renderHeaderBadge(){let e=Array.isArray(this.snapshot?.devices)?this.snapshot.devices:[];return this.snapshot?n`<span
      class="chip ${this.snapshot.status===`partial`?`partial`:``}"
      >${e.length} Devices</span
    >`:r}renderContent(){let e=this.snapshot;if(!e)return n`<div class="state">No devices</div>`;let i=Array.isArray(e.devices)?e.devices:[],o=i.map(e=>e.cpu_pct).filter(e=>typeof e==`number`&&Number.isFinite(e)&&e>=0),s=i.map(e=>e.memory_pct).filter(e=>typeof e==`number`&&Number.isFinite(e)&&e>=0),c=o.length>0?Math.round(o.reduce((e,t)=>e+t,0)/o.length):void 0,l=s.length>0?Math.round(s.reduce((e,t)=>e+t,0)/s.length):void 0;return n`
      <div class="ring-strip">
        <div class="ring-card">
          <div
            class="ring-gauge"
            style=${`--pct: ${c??0}; --ring-color: var(--uit-${c===void 0?`unknown`:a(c)===`ok`?`online`:a(c)===`warning`?`warning`:`offline`})`}
          >
            <span>${c===void 0?`--`:`${c}%`}</span>
          </div>
          <div>
            <div class="kpi-top">Avg CPU</div>
            <div class="kpi-sub">${i.length} Devices</div>
          </div>
        </div>
        <div class="ring-card">
          <div
            class="ring-gauge"
            style=${`--pct: ${l??0}; --ring-color: var(--primary-color)`}
          >
            <span>${l===void 0?`--`:`${l}%`}</span>
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
        ${i.slice(0,6).map(e=>{let i=String(e.name??`Device`),o=String(e.kind??`other`),s=Number(e.cpu_pct),c=e.cpu_pct==null||!Number.isFinite(s)?null:Math.min(100,Math.max(0,Math.round(s))),l=e.memory_pct==null?null:Math.round(Number(e.memory_pct)),u=c==null?`ok`:a(c),f=typeof e.clients==`number`?`${e.clients} clients`:void 0;return n`
            <div class="item-card">
              <div class="item-icon">${t(d(o))}</div>
              <div class="item-body">
                <div class="item-top">
                  <span class="item-name">${i}</span>
                  <span class="item-value">
                    ${c==null?`--`:`${c}%`}
                  </span>
                </div>
                <div class="bar-track" aria-hidden="true">
                  <div
                    class="bar-fill ${u}"
                    style=${`width: ${c??0}%`}
                  ></div>
                </div>
                ${l!=null||f?n`<div class="item-meta">
                      ${l==null?r:n`<span>RAM ${l}%</span>`}
                      ${f?n`<span>· ${f}</span>`:r}
                    </div>`:r}
              </div>
            </div>
          `})}
      </div>
    `}},editor:class extends s{constructor(){super(l,e)}},name:`UniFi Device Performance`,description:`Infrastructure CPU, memory, PoE, client load, and throughput summary.`});