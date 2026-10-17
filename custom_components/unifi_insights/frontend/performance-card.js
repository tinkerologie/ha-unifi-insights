import{Ft as e,K as t,M as n,P as r,S as i,T as a,l as o,n as s,o as c,t as l,u,x as d}from"./chunks/register-dashboard-card-COnrwOmv.js";l({tag:o,editorTag:u,card:class extends s{static editorTag=u;get cardType(){return`custom:${o}`}get sourceCommand(){return e}get subscribeCommand(){return`unifi_insights/performance/subscribe`}get defaultTitle(){return`Device Performance`}get headerIcon(){return t}renderHeaderBadge(){let e=Array.isArray(this.snapshot?.devices)?this.snapshot.devices:[];return this.snapshot?r`<span class="chip ok">${e.length} Devices</span>`:n}renderContent(){let e=this.snapshot;if(!e)return r`<div class="state">No devices</div>`;let t=Array.isArray(e.devices)?e.devices:[],o=t.map(e=>Number(e.cpu_pct)).filter(e=>Number.isFinite(e)&&e>=0),s=t.map(e=>Number(e.memory_pct)).filter(e=>Number.isFinite(e)&&e>=0),c=o.length>0?Math.round(o.reduce((e,t)=>e+t,0)/o.length):0,l=s.length>0?Math.round(s.reduce((e,t)=>e+t,0)/s.length):0;return r`
      <div class="ring-strip">
        <div class="ring-card">
          <div
            class="ring-gauge"
            style=${`--pct: ${c}; --ring-color: var(--uit-${i(c)===`ok`?`online`:i(c)===`warning`?`warning`:`offline`})`}
          >
            <span>${c}%</span>
          </div>
          <div>
            <div class="kpi-top">Avg CPU</div>
            <div class="kpi-sub">${t.length} Devices</div>
          </div>
        </div>
        <div class="ring-card">
          <div
            class="ring-gauge"
            style=${`--pct: ${l}; --ring-color: var(--primary-color)`}
          >
            <span>${l}%</span>
          </div>
          <div>
            <div class="kpi-top">Avg Memory</div>
            <div class="kpi-sub">System Load</div>
          </div>
        </div>
      </div>

      <div class="list">
        ${t.slice(0,6).map(e=>{let t=String(e.name??`Device`),o=String(e.kind??`other`),s=Number(e.cpu_pct),c=e.cpu_pct==null||!Number.isFinite(s)?null:Math.min(100,Math.max(0,Math.round(s))),l=e.memory_pct==null?null:Math.round(Number(e.memory_pct)),u=c==null?`ok`:i(c),f=typeof e.clients==`number`?`${e.clients} clients`:void 0;return r`
            <div class="item-card">
              <div class="item-icon">${a(d(o))}</div>
              <div class="item-body">
                <div class="item-top">
                  <span class="item-name">${t}</span>
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
                ${l!=null||f?r`<div class="item-meta">
                      ${l==null?n:r`<span>RAM ${l}%</span>`}
                      ${f?r`<span>· ${f}</span>`:n}
                    </div>`:n}
              </div>
            </div>
          `})}
      </div>
    `}},editor:class extends c{constructor(){super(o,e)}},name:`UniFi Device Performance`,description:`Infrastructure CPU, memory, PoE, client load, and throughput summary.`});