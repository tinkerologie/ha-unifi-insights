import{C as e,F as t,It as n,N as r,b as i,c as a,i as o,n as s,q as c,s as l,t as u,y as d}from"./chunks/register-dashboard-card-CBK2jPj2.js";u({tag:l,editorTag:a,card:class extends s{static editorTag=a;get cardType(){return`custom:${l}`}get sourceCommand(){return n}get subscribeCommand(){return`unifi_insights/performance/subscribe`}get defaultTitle(){return`Device Performance`}get headerIcon(){return c}renderHeaderBadge(){let e=Array.isArray(this.snapshot?.devices)?this.snapshot.devices:[];return this.snapshot?t`<span
      class="chip ${this.snapshot.status===`partial`?`partial`:``}"
      >${e.length} Devices</span
    >`:r}renderContent(){let n=this.snapshot;if(!n)return t`<div class="state">No devices</div>`;let a=Array.isArray(n.devices)?n.devices:[],o=a.map(e=>e.cpu_pct).filter(e=>typeof e==`number`&&Number.isFinite(e)&&e>=0),s=a.map(e=>e.memory_pct).filter(e=>typeof e==`number`&&Number.isFinite(e)&&e>=0),c=o.length>0?Math.round(o.reduce((e,t)=>e+t,0)/o.length):void 0,l=s.length>0?Math.round(s.reduce((e,t)=>e+t,0)/s.length):void 0;return t`
      <div class="ring-strip">
        <div class="ring-card">
          <div
            class="ring-gauge"
            style=${`--pct: ${c??0}; --ring-color: var(--uit-${c===void 0?`unknown`:i(c)===`ok`?`online`:i(c)===`warning`?`warning`:`offline`})`}
          >
            <span>${c===void 0?`--`:`${c}%`}</span>
          </div>
          <div>
            <div class="kpi-top">Avg CPU</div>
            <div class="kpi-sub">${a.length} Devices</div>
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
        ${a.slice(0,6).map(n=>{let a=String(n.name??`Device`),o=String(n.kind??`other`),s=Number(n.cpu_pct),c=n.cpu_pct==null||!Number.isFinite(s)?null:Math.min(100,Math.max(0,Math.round(s))),l=n.memory_pct==null?null:Math.round(Number(n.memory_pct)),u=c==null?`ok`:i(c),f=typeof n.clients==`number`?`${n.clients} clients`:void 0;return t`
            <div class="item-card">
              <div class="item-icon">${e(d(o))}</div>
              <div class="item-body">
                <div class="item-top">
                  <span class="item-name">${a}</span>
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
                ${l!=null||f?t`<div class="item-meta">
                      ${l==null?r:t`<span>RAM ${l}%</span>`}
                      ${f?t`<span>· ${f}</span>`:r}
                    </div>`:r}
              </div>
            </div>
          `})}
      </div>
    `}},editor:class extends o{constructor(){super(l,n)}},name:`UniFi Device Performance`,description:`Infrastructure CPU, memory, PoE, client load, and throughput summary.`});