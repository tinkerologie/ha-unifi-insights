import{A as e,C as t,D as n,E as r,M as i,O as a,S as o,T as s,b as c,j as l,k as u,w as d,x as f}from"./chunks/register-dashboard-card-DB8D56VC.js";import{$ as p,A as m,B as h,G as g,H as _,I as v,M as y,N as b,R as x,U as S,a as C,at as ee,c as w,ct as te,d as ne,dt as re,et as ie,f as ae,ft as oe,g as T,i as E,it as se,k as ce,l as le,m as D,n as ue,nt as de,o as fe,ot as pe,p as O,q as me,r as he,rt as ge,s as _e,st as ve,t as ye,tt as be,u as k,ut as A,v as j,y as xe}from"./chunks/localize-DuXs5QH_.js";import"./internet-activity-card.js";import"./performance-card.js";import"./protect-status-card.js";import"./site-health-card.js";import"./timeline-card.js";import{a as Se,i as Ce,n as we,o as Te,r as Ee,t as M}from"./chunks/repeat-5Yw3wr94.js";var De={status:`idle`},Oe=class{onChange;current=De;keyId;connection;generation=0;constructor(e){this.onChange=e}get state(){return this.current}select(e,t){let n=t&&k(e)&&!t.node_id.startsWith(`group:`)?JSON.stringify([t.entry_id,t.site_id,t.node_id]):void 0,r=e?.connection;if(n===this.keyId&&r===this.connection||(this.stop(),this.keyId=n,this.connection=r,!e||!t||n===void 0))return;let i=this.generation;this.current={status:`loading`};let a={type:re,...t};this.fetch(e,a,i)}stop(){this.generation++,this.current=De,this.keyId=void 0,this.connection=void 0}async fetch(e,t,n){let r;try{r={status:`ready`,details:await e.callWS(t)}}catch{r=De}n===this.generation&&(this.current=r,this.onChange())}},ke=e=>typeof e==`string`&&e!==``;function Ae(e,t){if(!t||!e)return[];let n=[];return ke(e.ip_address)&&n.push(`copy_ip`),ke(e.mac_address)&&n.push(`copy_mac`),ke(e.device_id)&&n.push(`open_device`),ke(e.entity_id)&&n.push(`open_entity`),n}var je=class{last=-1/0;pending;timer;emit;intervalMs;constructor(e,t=5e3){this.emit=e,this.intervalMs=t}announce(e){let t=this.last+this.intervalMs-Date.now();t<=0&&this.timer===void 0?(this.last=Date.now(),this.emit(e)):(this.pending=e,this.timer??=setTimeout(()=>{this.timer=void 0;let e=this.pending;this.pending=void 0,e!==void 0&&(this.last=Date.now(),this.emit(e))},Math.max(t,0)))}dispose(){this.timer!==void 0&&clearTimeout(this.timer),this.timer=void 0,this.pending=void 0}},Me={[te]:{key:`issue.site_unavailable`},[se]:{key:`issue.devices_unavailable`,action:`integration`},[pe]:{key:`issue.legacy_uplink_missing`},[ve]:{key:`issue.parents_unresolved`},[ge]:{key:`issue.clients_truncated`},[ee]:{key:`issue.entry_unloaded`}},Ne={[ie]:{key:`error.entry_not_found`,action:`edit`},[de]:{key:`error.site_not_selected`,action:`edit`},[be]:{key:`error.entry_not_loaded`,action:`integration`}};function Pe(e,t,n){let r=Me[e.code];if(!r)return{code:e.code,severity:e.severity,key:`issue.unknown`,vars:{code:e.code}};let i={code:e.code,severity:e.severity,key:r.key};return e.code===`parents_unresolved`&&(i.vars={count:t.unresolved.length}),e.code===`clients_truncated`&&t.truncation&&(i.vars={included:t.truncation.clients_included,total:t.truncation.clients_total,max:n}),r.action&&(i.action=r.action),i}function Fe(e){let t=Ne[e.code];if(!t)return{code:e.code,severity:`error`,key:`error.unknown`,vars:{code:e.code}};let n={code:e.code,severity:`error`,key:t.key};return t.action&&(n.action=t.action),n}function Ie(e){if(e.incompatible)return{phase:`incompatible`,stale:!1,notices:[]};if(e.error)return{phase:`error`,render:e.lastGood,stale:e.lastGood!==void 0,notices:[Fe(e.error)]};if(e.disconnected){let t=e.snapshot,n=t&&t.nodes.length>0?t:e.lastGood;if(n)return{phase:`reloading`,render:n,stale:!0,notices:[]}}if(e.sources!==void 0&&e.sources.length===0&&e.snapshot===void 0)return{phase:`no_sources`,stale:!1,notices:[]};if(e.binding===void 0)return{phase:e.sources===void 0?`loading`:`unconfigured`,stale:!1,notices:[]};let t=e.snapshot;if(t===void 0)return{phase:`loading`,stale:!1,notices:[]};let n=t.issues.map(n=>Pe(n,t,e.maxClients));if(t.status===`unavailable`){let r=t.issues.some(e=>e.code===ee),i=t.nodes.length>0?t:e.lastGood;return{phase:r?`reloading`:`unavailable`,render:i,stale:i!==void 0,notices:n}}return t.nodes.length===0?{phase:`empty`,stale:!1,notices:n}:{phase:t.status===`partial`?`partial`:`ok`,render:t,stale:!1,notices:n}}var Le=`group:unconnected`,Re=`group:root`,ze=e=>`group:${e}`,Be={wan:0,gateway:1,switch:2,access_point:3,other:4,client:5},Ve=(e,t)=>e.name.localeCompare(t.name)||e.id.localeCompare(t.id),He=(e,t)=>Be[e.kind]-Be[t.kind]||Ve(e,t);function Ue(e,t){let n=e.toggledGroups.has(t);return e.clients===`expanded`?!n:n}var We=()=>({total:0,wired:0,wireless:0,offline:0});function Ge(e,t){e.total++,t.connection===`wired`&&e.wired++,t.connection===`wireless`&&e.wireless++,t.state===`offline`&&e.offline++}function Ke(e,t,n){let r=new Set;for(let i of e){let e=new Set,a=i.id;for(;a!==void 0&&!r.has(a);){e.add(a);let r=t.get(a);if(r!==void 0&&e.has(r)){t.delete(a),n.delete(a);break}a=r}for(let t of e)r.add(t)}}function qe(e,t){let n=new Map(e.nodes.map(e=>[e.id,e])),r=new Map,i=new Map,a=new Map;for(let t of e.edges){let e=n.get(t.source),o=n.get(t.target);o?.kind===`wan`?e&&e.kind!==`wan`&&!a.has(o.id)&&a.set(o.id,{owner:e.id,edge:t}):e?.kind!==`wan`&&e&&o&&e.id!==o.id&&o.kind!==`client`&&!r.has(e.id)&&(r.set(e.id,o.id),i.set(e.id,t))}Ke(e.nodes,r,i);let o=e.nodes.filter(e=>e.kind!==`client`&&e.kind!==`wan`).sort(He),s=e.nodes.filter(e=>e.kind===`client`).sort(Ve),c=new Map;for(let e of s){let t=r.get(e.id);if(t===void 0)continue;let n=c.get(t);n||c.set(t,n=We()),Ge(n,e)}let l=new Map,u=new Map,d=new Map,f=new Map,p=[],m=e=>t.kinds.has(e.kind),h=e=>{let t=[],i=r.get(e);for(;i!==void 0;){let e=n.get(i);if(m(e))return{id:i,skipped:t};t.push(e.kind),i=r.get(i)}return{id:void 0,skipped:t}},g=(e,t,n,r)=>{if(t===void 0){p.push(e);return}d.set(e,t),f.set(e,{childId:e,parentId:t,edge:n,viaHidden:[...new Set(r)]});let i=u.get(t);i?i.push(e):u.set(t,[e])};for(let e of o){if(!m(e))continue;l.set(e.id,{type:`device`,id:e.id,node:e});let t=h(e.id);g(e.id,t.id,i.get(e.id),t.skipped)}let _=new Map,v=new Map,y=[];for(let t of e.nodes){let e=a.get(t.id);if(!e||!m(t)||!l.has(e.owner))continue;l.set(t.id,{type:`device`,id:t.id,node:t});let n=_.get(e.owner);n?n.push(t.id):_.set(e.owner,[t.id]),v.set(t.id,e.owner),y.push({childId:e.owner,parentId:t.id,edge:e.edge,viaHidden:[]})}if(t.clients!==`hidden`&&t.kinds.has(`client`)){let e=new Map;for(let t of s){let i=r.get(t.id),a,o=[],s;if(i===void 0)s=Le;else{let e=n.get(i);if(m(e))a=i;else{let t=h(i);a=t.id,o=[e.kind,...t.skipped]}s=a===void 0?Re:ze(a)}let c=e.get(s);c||e.set(s,c={parent:a,members:[],skipped:new Set}),c.members.push(t);for(let e of o)c.skipped.add(e)}let a=[...e].sort(([e],[t])=>Number(e===Le)-Number(t===Le));for(let[e,n]of a){let r=We();for(let e of n.members)Ge(r,e);let a=Ue(t,e);if(l.set(e,{type:`group`,id:e,members:n.members,counts:r,expanded:a}),g(e,n.parent,void 0,n.skipped),a)for(let t of n.members)l.set(t.id,{type:`client`,id:t.id,node:t}),g(t.id,e,i.get(t.id),[])}}return{snapshot:e,roots:p,visuals:l,children:u,parentOf:d,links:f,nodes:n,realParent:r,edges:i,clientCounts:c,uplinks:_,uplinkOwner:v,uplinkLinks:y,stats:{devices:o.length,clients:s.length,offlineDevices:o.filter(e=>e.state===`offline`).length}}}function Je(){let e,t,n;return(r,i)=>(n&&r===e&&i===t||(e=r,t=i,n=qe(r,i)),n)}function Ye(e,t){let n=e.uplinkOwner.get(t);if(n!==void 0)return e.uplinks.get(n);let r=e.parentOf.get(t);return r===void 0?e.roots:e.children.get(r)??[]}function Xe(e,t){for(let n of e.visuals.values())if(n.type===`group`&&n.members.some(e=>e.id===t))return n}var Ze={wan:`kind.wan`,gateway:`kind.gateway`,switch:`kind.switch`,access_point:`kind.access_point`,client:`kind.client`,other:`kind.other`},Qe={online:`state.online`,offline:`state.offline`,unknown:`state.unknown`},$e={online:`state.connected`,offline:`state.disconnected`,unknown:`state.unknown`},et={graph:`view.graph`,list:`view.list`},tt={wired:`medium.wired`,wireless:`medium.wireless`,unknown:`medium.unknown`};function nt(e,t){let n=[...e];return n.length>t?`${n.slice(0,t-1).join(``)}…`:e}var rt=e=>Number.isInteger(e)?String(e):e.toFixed(1);function it(e){return e>=1e3?`${rt(e/1e3)}G`:`${e}M`}function at(e){return e>=1e3?`${rt(e/1e3)} gigabit`:`${e} megabit`}var ot=new Map([[`RJ45`,`RJ45`],[`SFP`,`SFP`],[`SFPPLUS`,`SFP+`],[`SFP28`,`SFP28`],[`QSFP28`,`QSFP28`]]);function st(e,t){switch(e){case`UP`:return t(`ports.up`);case`DOWN`:return t(`ports.down`);case`LIMITED`:return t(`ports.limited`);case`UNKNOWN`:return t(`state.unknown`);default:return e}}function ct(e,t){return e>=1e3?t(`ports.speed_gbps`,{speed:rt(e/1e3)}):t(`ports.speed_mbps`,{speed:e})}function lt(e,t){let n=[];typeof e.state==`string`&&n.push(st(e.state,t)),typeof e.speed_mbps==`number`&&Number.isFinite(e.speed_mbps)&&n.push(ct(e.speed_mbps,t)),typeof e.max_speed_mbps==`number`&&Number.isFinite(e.max_speed_mbps)&&n.push(t(`ports.max_speed`,{speed:ct(e.max_speed_mbps,t)}));let r=e.poe;return typeof r?.enabled==`boolean`&&n.push(t(r.enabled?`ports.poe_enabled`:`ports.poe_disabled`)),typeof r?.state==`string`&&n.push(t(`ports.poe_state`,{state:st(r.state,t)})),typeof r?.standard==`string`&&n.push(t(`ports.poe_standard`,{standard:r.standard})),Number.isInteger(r?.type)&&typeof r?.type==`number`&&n.push(t(`ports.poe_type`,{type:r.type})),n}function ut(e){if(!e)return``;let t=[];return e.parent_port!==void 0&&t.push(`p${e.parent_port}`),e.speed_mbps&&t.push(it(e.speed_mbps)),e.poe_power_w!==void 0&&t.push(`PoE`),t.join(` · `)}function dt(e,t){return e.type===`group`?e.id===`group:unconnected`?t(`group.unconnected`):e.id===`group:root`?t(`group.root`):e.counts.total===1?t(`group.client_one`):t(`group.clients`,{count:e.counts.total}):e.node.name}function ft(e,t){return e?.kind===`wan`?$e[t]:Qe[t]}function pt(e){return ft(e.type===`group`?void 0:e.node,mt(e))}function mt(e){return e.type===`group`?e.counts.offline===e.counts.total?`offline`:`online`:e.node.state}function ht(e,t,n){if(t.type===`group`)return`${dt(t,n)}: ${n(`group.summary`,{wireless:t.counts.wireless,offline:t.counts.offline})}`;let r=t.node,i=[n(`node.label`,{kind:n(Ze[r.kind]),name:r.name,state:n(ft(r,r.state)).toLocaleLowerCase()})],a=e.uplinkOwner.get(r.id);a!==void 0&&i.push(n(`node.wan_of`,{device:e.nodes.get(a).name}));let o=e.clientCounts.get(r.id)?.total??0;o>0&&i.push(o===1?n(`node.client_one`):n(`node.clients`,{count:o}));let s=e.edges.get(r.id);return s?.parent_port===void 0?r.connection&&i.push(n(tt[r.connection]).toLocaleLowerCase()):i.push(s.speed_mbps?n(`node.uplink_speed`,{port:s.parent_port,speed:at(s.speed_mbps)}):n(`node.uplink`,{port:s.parent_port})),i.join(`, `)}var gt=Ee(class extends Ce{constructor(){super(...arguments),this.key=D}render(e,t){return this.key=e,t}update(e,[t,n]){return t!==this.key&&(we(e),this.key=t),n}});function _t(){let e=document.activeElement;for(;e?.shadowRoot?.activeElement;)e=e.shadowRoot.activeElement;return e instanceof HTMLElement?e:null}function vt(e,t){let n=_t(),r=document.createElement(`textarea`);r.value=e,r.setAttribute(`readonly`,``),r.setAttribute(`aria-hidden`,`true`),r.style.cssText=`position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;pointer-events:none`,t.append(r);try{return r.focus(),r.select(),typeof document.execCommand==`function`&&document.execCommand(`copy`)}catch{return!1}finally{r.remove(),n?.focus()}}async function yt(e,t=document.body){if(navigator.clipboard?.writeText)try{return await navigator.clipboard.writeText(e),!0}catch{}return vt(e,t)}var bt={copy_ip:`menu.copy_ip`,copy_mac:`menu.copy_mac`,open_device:`detail.open_device`,open_entity:`menu.open_entity`},xt={copy_ip:b,copy_mac:b,open_device:g,open_entity:h},St=0;p(`uit-node-menu`,class extends O{static properties={actions:{attribute:!1},localize:{attribute:!1},open:{state:!0}};menuId=`uit-node-menu-${++St}`;listening=!1;tabClose;constructor(){super(),this.actions=[],this.open=!1}get trigger(){return this.renderRoot.querySelector(`button.trigger`)}get menu(){return this.renderRoot.querySelector(`[role="menu"]`)}get items(){return[...this.renderRoot.querySelectorAll(`[role="menuitem"]`)]}get focusedItem(){let e=this.renderRoot.activeElement;return e instanceof HTMLElement&&this.menu?.contains(e)?e:null}get hasContent(){return this.actions.length>0}connectedCallback(){super.connectedCallback(),this.listenOutside(this.open)}disconnectedCallback(){super.disconnectedCallback(),this.hide(!1),this.listenOutside(!1)}async show(e=`first`){if(!this.hasContent)return;clearTimeout(this.tabClose),this.open=!0,await this.updateComplete;let t=this.items,n=t[e===`first`?0:t.length-1];for(let e of t)e.tabIndex=e===n?0:-1;n?.focus()}hide(e=!0){clearTimeout(this.tabClose),this.tabClose=void 0,this.open&&(this.open=!1,e&&this.trigger?.focus())}willUpdate(){this.hasContent||(this.open=!1)}updated(e){e.has(`open`)&&this.listenOutside(this.open)}listenOutside(e){e!==this.listening&&(this.listening=e,e?document.addEventListener(`pointerdown`,this.onOutsidePress,!0):document.removeEventListener(`pointerdown`,this.onOutsidePress,!0))}onOutsidePress=e=>{e.composedPath().includes(this)||this.hide(!1)};onTriggerKeydown(e){e.key===`ArrowDown`||e.key===`ArrowUp`?(e.preventDefault(),this.show(e.key===`ArrowDown`?`first`:`last`)):e.key===`Escape`&&this.open&&(this.swallow(e),this.hide())}swallow(e){e.preventDefault(),e.stopPropagation()}onMenuKeydown(e){e.stopPropagation();let t=this.items,n=this.focusedItem?t.indexOf(this.focusedItem):-1,r;switch(e.key){case`ArrowDown`:r=(n+1)%t.length;break;case`ArrowUp`:r=n<=0?t.length-1:n-1;break;case`Home`:r=0;break;case`End`:r=t.length-1;break;case`Escape`:this.swallow(e),this.hide();return;case`Tab`:this.tabClose=setTimeout(()=>this.hide(!1),0);return;default:return}e.preventDefault();for(let e of t)e.tabIndex=e===t[r]?0:-1;t[r]?.focus()}choose(e){this.hide(),w(this,`uit-action`,{action:e})}render(){let{localize:e}=this;if(!e||!this.hasContent)return D;let t=e(`menu.label`);return T`<button
        class="trigger"
        type="button"
        aria-haspopup="menu"
        aria-expanded=${String(this.open)}
        aria-controls=${this.menuId}
        aria-label=${t}
        title=${t}
        @click=${e=>{e.stopPropagation(),this.open?this.hide():this.show()}}
        @keydown=${e=>this.onTriggerKeydown(e)}
      >
        ${E(v)}<span>${t}</span>
      </button>
      ${this.open?T`<div
            id=${this.menuId}
            class="menu"
            role="menu"
            aria-label=${t}
            @click=${e=>e.stopPropagation()}
            @pointerdown=${e=>e.stopPropagation()}
            @contextmenu=${e=>e.stopPropagation()}
            @keydown=${e=>this.onMenuKeydown(e)}
          >
            ${M(this.actions,e=>e,t=>T`<button
                  type="button"
                  role="menuitem"
                  tabindex="-1"
                  data-action=${t}
                  @click=${()=>this.choose(t)}
                >
                  ${E(xt[t])}<span
                    >${e(bt[t])}</span
                  >
                </button>`)}
          </div>`:D}`}static styles=[_e,C,fe]});var Ct=class extends O{static properties={model:{attribute:!1},selectedId:{attribute:!1},localize:{attribute:!1},hass:{attribute:!1},menuRequest:{attribute:!1},details:{attribute:!1},narrow:{type:Boolean,reflect:!0},memberQuery:{state:!0},modal:{state:!0},copyStatus:{state:!0},portsExpanded:{state:!0}};focusPorts=!1;currentPorts;pressedBackdrop=!1;menuPending=!1;copyScope;copyGeneration=0;constructor(){super(),this.narrow=!1,this.memberQuery=``,this.modal=!1,this.portsExpanded=!1}disconnectedCallback(){super.disconnectedCallback(),this.closeMenu(),this.menuPending=!1,this.copyGeneration++,this.copyStatus=void 0,this.renderRoot.querySelector(`dialog`)?.close()}willUpdate(e){(e.has(`selectedId`)||e.has(`model`)&&(this.model?.snapshot.entry_id!==e.get(`model`)?.snapshot.entry_id||this.model?.snapshot.site_id!==e.get(`model`)?.snapshot.site_id))&&(this.memberQuery=``,this.portsExpanded=!1);let t=JSON.stringify([this.model?.snapshot.entry_id,this.model?.snapshot.site_id,this.selectedId,k(this.hass)]);if(t!==this.copyScope&&(this.closeMenu(),this.menuPending=!1,this.copyScope=t,this.copyGeneration++,this.copyStatus=void 0),this.selectedId===void 0&&(this.modal=!1),e.has(`menuRequest`)){let e=this.menuRequest;this.menuPending=e!==void 0&&e.node_id===this.selectedId&&e.entry_id===this.model?.snapshot.entry_id&&e.site_id===this.model?.snapshot.site_id}}updated(e){if(!this.isConnected)return;let t=this.selectedId===void 0?void 0:this.model?.nodes.get(this.selectedId)?.port_inventory,n=this.portsExpanded&&this.currentPorts!==t;this.currentPorts=t;let r=this.renderRoot.querySelector(`dialog`);if(r)r.open||r.showModal(),this.focusPorts&&=(this.renderRoot.querySelector(`.port-toggle`)?.focus(),!1);else if(e.has(`selectedId`)||e.has(`narrow`)||e.has(`details`)||e.has(`portsExpanded`)||n){let e=this.renderRoot.querySelector(`.panel`);if(e&&e.scrollHeight>e.clientHeight+1){this.focusPorts=this.renderRoot.querySelector(`.port-toggle`)===this.shadowRoot?.activeElement,this.modal=!0;return}}this.openRequestedMenu()}closeMenu(){this.renderRoot.querySelector(`uit-node-menu`)?.hide(!1)}get menuOpen(){return this.renderRoot.querySelector(`uit-node-menu`)?.open===!0}openRequestedMenu(){this.menuPending&&this.details&&(this.menuPending=!1,this.renderRoot.querySelector(`uit-node-menu`)?.show())}close(){this.closeMenu(),this.menuPending=!1;let e=this.renderRoot.querySelector(`dialog`);e?e.close():w(this,`uit-close`)}render(){let{model:e,selectedId:t,localize:n}=this;if(!e||!t||!n)return D;let r=e.visuals.get(t);if(r?.type===`group`)return this.shell(dt(r,n),this.groupBody(r,n));let i=e.nodes.get(t);if(!i)return D;let a=i.kind===`client`?this.clientBody(e,i,n):this.deviceBody(e,i,n);return this.shell(i.name,a)}shell(e,t){let n=this.panel(e,t);return this.modal?T`<dialog
      aria-labelledby="title"
      @close=${()=>{this.closeMenu(),this.menuPending=!1,w(this,`uit-close`)}}
      @cancel=${e=>{this.menuOpen&&(e.preventDefault(),this.renderRoot.querySelector(`uit-node-menu`)?.hide())}}
      @keydown=${e=>{e.key===`Escape`&&(e.preventDefault(),e.stopPropagation(),this.close())}}
      @pointerdown=${{capture:!0,handleEvent:e=>{this.pressedBackdrop=e.target===e.currentTarget}}}
      @click=${e=>{e.target===e.currentTarget&&this.pressedBackdrop&&this.close()}}
    >
      ${n}
    </dialog>`:n}panel(e,t){let n=this.localize;return T`<section
      class="panel"
      role=${this.modal?D:`region`}
      aria-label=${this.modal?D:e}
      @keydown=${e=>{e.key===`Escape`&&this.menuOpen&&(e.preventDefault(),e.stopPropagation(),this.renderRoot.querySelector(`uit-node-menu`)?.hide())}}
    >
      <header>
        <h3 id="title" title=${e}>${e}</h3>
        <button
          class="close"
          aria-label=${n(`detail.close`)}
          title=${n(`detail.close`)}
          @click=${()=>this.close()}
        >
          ${E(y)}
        </button>
      </header>
      ${t}${this.statusRegion(n)}
    </section>`}menu(e,t){let n=Ae(this.details,k(this.hass));return n.length===0?D:T`${gt(e.id,T`<uit-node-menu
        .actions=${n}
        .localize=${t}
        @uit-action=${e=>this.runAction(e.detail.action)}
      ></uit-node-menu>`)}`}statusRegion(e){let t=this.copyStatus?.value===void 0||k(this.hass)?this.copyStatus:void 0;return T`<div
      class="status ${t?`has-message`:``}"
      role="status"
      aria-live="polite"
    >
      ${t?T`<span>${e(t.key,t.vars)}</span
            >${t.value===void 0?D:T`<code class="manual">${t.value}</code>`}`:D}
    </div>`}runAction(e){if(!k(this.hass))return;let t=this.details;switch(e){case`copy_ip`:this.copy(t?.ip_address,`menu.copied_ip`);break;case`copy_mac`:this.copy(t?.mac_address,`menu.copied_mac`);break;case`open_device`:{let e=t?.device_id;e&&this.openDevice(e);break}case`open_entity`:t?.entity_id&&(this.renderRoot.querySelector(`dialog`)?.close(),le(this,t.entity_id))}}async copy(e,t){if(!e)return;this.copyStatus=void 0;let n=this.copyGeneration,r=await yt(e,this.renderRoot.querySelector(`dialog`)??this.renderRoot);if(n===this.copyGeneration&&(this.copyStatus=r?{key:t}:{key:`menu.copy_failed`,value:e},!r)){if(await this.updateComplete,n!==this.copyGeneration)return;let e=this.renderRoot.querySelector(`.manual`);if(e){let t=document.createRange();t.selectNodeContents(e);let n=window.getSelection();n?.removeAllRanges(),n?.addRange(t)}}}openDevice(e){this.renderRoot.querySelector(`dialog`)?.close(),ne(`/config/devices/device/${encodeURIComponent(e)}`)}rows(e){let t=e.filter(e=>e[1]!==void 0&&e[1]!==``);return T`<dl>
      ${t.map(([e,t])=>T`<div class="row">
            <dt>${this.localize(e)}</dt>
            <dd>${t}</dd>
          </div>`)}
    </dl>`}uplinkRows(e,t,n,r){let i=e.realParent.get(t)??e.uplinkOwner.get(t),a=e.edges.get(t),o;return a?.parent_port!==void 0&&(o=a.child_port===void 0?String(a.parent_port):`${a.parent_port} → ${a.child_port}`),[[`detail.parent`,i===void 0?void 0:e.nodes.get(i)?.name],[`detail.port`,o],[`detail.speed`,a?.speed_mbps?it(a.speed_mbps):void 0],[`detail.medium`,r&&a?n(tt[a.medium]):void 0],[`detail.poe`,a?.poe_power_w===void 0?void 0:`${a.poe_power_w.toFixed(1)} W`]]}deviceBody(e,t,n){let r=e.clientCounts.get(t.id),i=e.links.get(t.id)?.viaHidden??[],a=t.ha_device_id?T`<button
          class="action"
          @click=${()=>this.openDevice(t.ha_device_id)}
        >
          ${E(g)}${n(`detail.open_device`)}
        </button>`:D;return T`${this.menu(t,n)}${this.rows([[`detail.kind`,n(Ze[t.kind])],[`detail.model`,t.model],[`detail.state`,n(ft(t,t.state))],...this.uplinkRows(e,t.id,n,!0),[`detail.clients`,r?n(`detail.clients_value`,{total:r.total,wired:r.wired,wireless:r.wireless}):void 0],[`detail.via_hidden`,i.length>0?i.map(e=>n(Ze[e])).join(`, `):void 0]])}
    ${t.port_inventory===void 0?a:this.portBody(t.port_inventory,a,n)}`}portBody(e,t,n){let r=e?.status,i=r===`ok`?void 0:r===`stale`?`ports.stale`:r===`unavailable`?`ports.unavailable`:`ports.unknown`,a=(r===`ok`||r===`stale`)&&Array.isArray(e.ports)?e.ports.filter(e=>Number.isInteger(e?.idx)&&e.idx>0):[],o=n(`ports.summary`,{count:a.length,up:a.filter(e=>e.state===`UP`).length});return T`<div class="port-section">
      <div class="port-header">
        ${t}
        ${a.length?T`<button
              class="port-toggle"
              aria-expanded=${String(this.portsExpanded)}
              aria-controls="port-list"
              @click=${()=>{this.portsExpanded=!this.portsExpanded}}
            >
              ${n(`ports.title`)} · ${o}
            </button>`:T`<strong>${n(`ports.title`)}</strong>`}
      </div>
      ${i?T`<p>${n(i)}</p>`:a.length===0?T`<p>${n(`ports.empty`)}</p>`:D}
      <div id="port-list" ?hidden=${!this.portsExpanded}>
        ${this.portsExpanded?this.portGroups(a,n):D}
      </div>
    </div>`}portGroups(e,t){let n=new Map;for(let t of e){let e=typeof t.connector==`string`&&ot.has(t.connector)?t.connector:``,r=n.get(e);r?r.push(t):n.set(e,[t])}return[...ot.keys(),``].flatMap(e=>{let r=n.get(e);if(!r)return[];let i=ot.get(e)??t(`ports.group_other`);return[T`<div class="port-group" role="group" aria-label=${i}>
          <strong>${i}</strong>
          <ul class="port-grid">
            ${r.sort((e,t)=>e.idx-t.idx).map(n=>{let r=t(`ports.port`,{index:n.idx}),i=lt(n,t),a=typeof n.connector==`string`?ot.get(n.connector)??n.connector:``;return T`<li
                  aria-label=${[r,a,...i].filter(Boolean).join(`, `)}
                >
                  <strong>${r}</strong>
                  ${!e&&a?T`<span>${a}</span>`:D}
                  ${i.map(e=>T`<span>${e}</span>`)}
                </li>`})}
          </ul>
        </div>`]})}clientBody(e,t,n){return T`${this.menu(t,n)}${this.rows([[`detail.state`,n(Qe[t.state])],[`detail.connection`,t.connection?n(tt[t.connection]):void 0],[`detail.vlan`,t.vlan_id===void 0?void 0:String(t.vlan_id)],[`detail.network`,t.network_name],...this.uplinkRows(e,t.id,n,!1)])}`}groupBody(e,t){let n=this.memberQuery.trim().toLowerCase(),r=n?e.members.filter(e=>e.name.toLowerCase().includes(n)):e.members,i=e.counts;return T`${this.rows([[`detail.clients`,t(`detail.clients_value`,{total:i.total,wired:i.wired,wireless:i.wireless})]])}
      <input
        type="search"
        .value=${this.memberQuery}
        placeholder=${t(`detail.search_members`)}
        aria-label=${t(`detail.search_members`)}
        @input=${e=>{this.memberQuery=e.target.value}}
      />
      <ul class="members">
        ${r.map(e=>T`<li>
              <button
                class="member ${e.state}"
                @click=${()=>w(this,`uit-select`,{id:e.id})}
              >
                ${E(he(e))}<span class="name" title=${e.name}
                  >${e.name}</span
                >
                ${e.state===`online`?D:T`<span class="state-text"
                      >${t(Qe[e.state])}</span
                    >`}
              </button>
            </li>`)}
      </ul>`}static styles=[_e,C,xe`
      :host {
        position: absolute;
        top: 8px;
        right: 8px;
        bottom: 8px;
        width: min(320px, 45%);
        display: flex;
        flex-direction: column;
        z-index: 2;
        pointer-events: none;
      }
      :host([narrow]) {
        top: auto;
        left: 0;
        right: 0;
        bottom: 0;
        width: auto;
        max-height: calc(100% - 8px);
      }
      .panel {
        pointer-events: auto;
        box-sizing: border-box;
        flex: 1 1 auto;
        min-height: 0;
        overflow: auto;
        padding: 0 16px 16px;
        background: var(--card-background-color);
        border: 1px solid var(--uit-line);
        border-radius: var(--ha-card-border-radius, 12px);
        box-shadow: var(--ha-card-box-shadow, none);
      }
      header {
        position: sticky;
        top: 0;
        z-index: 1;
        display: flex;
        align-items: center;
        gap: 8px;
        padding-top: 4px;
        background: var(--card-background-color);
      }
      h3 {
        flex: 1;
        min-width: 0;
        margin: 0;
        font-size: 1.1em;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      button.close {
        border: none;
        padding: 0;
      }
      dl {
        margin: 8px 0;
      }
      .row {
        display: flex;
        gap: 12px;
        padding: 4px 0;
      }
      dt {
        color: var(--secondary-text-color);
        min-width: 7em;
      }
      dd {
        margin: 0;
        overflow-wrap: anywhere;
      }
      input {
        width: 100%;
      }
      .port-header {
        display: grid;
        grid-template-columns: max-content minmax(0, 1fr);
        align-items: center;
        gap: 8px;
      }
      .port-header button.action {
        margin: 0;
      }
      .port-section p {
        margin: 8px 0;
        color: var(--secondary-text-color);
      }
      .port-toggle {
        justify-self: start;
        max-width: 100%;
        padding-inline: 8px;
        text-align: start;
        white-space: normal;
      }
      .port-group {
        margin-block-start: 12px;
      }
      .port-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(min(100%, 108px), 1fr));
        gap: 6px;
        margin: 6px 0 0;
        padding: 0;
        list-style: none;
      }
      .port-grid li {
        display: grid;
        align-content: start;
        gap: 3px;
        padding: 8px;
        border: 1px solid var(--uit-line);
        border-radius: var(--ha-card-border-radius, 12px);
        overflow-wrap: anywhere;
      }
      .members {
        list-style: none;
        margin: 8px 0 0;
        padding: 0;
      }
      .member {
        width: 100%;
        border: none;
        border-radius: 8px;
        justify-content: flex-start;
      }
      .member .name {
        flex: 1;
        min-width: 0;
        text-align: start;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .member.offline .name {
        opacity: 0.55;
      }
      .state-text {
        color: var(--uit-offline);
        font-weight: 600;
      }
      .action {
        margin-top: 8px;
      }
      .status.has-message {
        margin-top: 8px;
        color: var(--secondary-text-color);
      }
      .manual {
        display: block;
        margin-top: 4px;
        padding: 6px 8px;
        border: 1px solid var(--uit-line);
        border-radius: 8px;
        color: var(--primary-text-color);
        overflow-wrap: anywhere;
        user-select: text;
      }
      dialog {
        pointer-events: auto;
        box-sizing: border-box;
        width: min(400px, calc(100% - 32px));
        max-width: none;
        max-height: calc(100% - 32px);
        padding: 0;
        border: none;
        background: none;
        color: inherit;
        overflow: visible;
      }
      dialog::backdrop {
        background: rgba(0, 0, 0, 0.32);
      }
      dialog .panel {
        max-height: calc(100vh - 32px);
        max-height: calc(100dvh - 32px);
      }
    `]};p(`uit-detail-panel`,Ct);var wt=class{pointers=new Set;origin={x:0,y:0,moved:!1};down(e){e.isPrimary?(this.pointers=new Set([e.pointerId]),this.origin={x:e.clientX,y:e.clientY,moved:!1}):(this.pointers.add(e.pointerId),this.origin.moved=!0)}move(e){!this.origin.moved&&this.pointers.has(e.pointerId)&&Math.hypot(e.clientX-this.origin.x,e.clientY-this.origin.y)>8&&(this.origin.moved=!0)}end(e){this.pointers.delete(e.pointerId)}get allowsMenu(){return!this.origin.moved&&this.pointers.size<=1}},Tt=`http://www.w3.org/1999/xhtml`,Et={svg:`http://www.w3.org/2000/svg`,xhtml:Tt,xlink:`http://www.w3.org/1999/xlink`,xml:`http://www.w3.org/XML/1998/namespace`,xmlns:`http://www.w3.org/2000/xmlns/`};function Dt(e){var t=e+=``,n=t.indexOf(`:`);return n>=0&&(t=e.slice(0,n))!==`xmlns`&&(e=e.slice(n+1)),Et.hasOwnProperty(t)?{space:Et[t],local:e}:e}function Ot(e){return function(){var t=this.ownerDocument,n=this.namespaceURI;return n===Tt&&t.documentElement.namespaceURI===Tt?t.createElement(e):t.createElementNS(n,e)}}function kt(e){return function(){return this.ownerDocument.createElementNS(e.space,e.local)}}function At(e){var t=Dt(e);return(t.local?kt:Ot)(t)}function jt(){}function Mt(e){return e==null?jt:function(){return this.querySelector(e)}}function Nt(e){typeof e!=`function`&&(e=Mt(e));for(var t=this._groups,n=t.length,r=Array(n),i=0;i<n;++i)for(var a=t[i],o=a.length,s=r[i]=Array(o),c,l,u=0;u<o;++u)(c=a[u])&&(l=e.call(c,c.__data__,u,a))&&(`__data__`in c&&(l.__data__=c.__data__),s[u]=l);return new P(r,this._parents)}function Pt(e){return e==null?[]:Array.isArray(e)?e:Array.from(e)}function Ft(){return[]}function It(e){return e==null?Ft:function(){return this.querySelectorAll(e)}}function Lt(e){return function(){return Pt(e.apply(this,arguments))}}function Rt(e){e=typeof e==`function`?Lt(e):It(e);for(var t=this._groups,n=t.length,r=[],i=[],a=0;a<n;++a)for(var o=t[a],s=o.length,c,l=0;l<s;++l)(c=o[l])&&(r.push(e.call(c,c.__data__,l,o)),i.push(c));return new P(r,i)}function zt(e){return function(){return this.matches(e)}}function Bt(e){return function(t){return t.matches(e)}}var Vt=Array.prototype.find;function Ht(e){return function(){return Vt.call(this.children,e)}}function Ut(){return this.firstElementChild}function Wt(e){return this.select(e==null?Ut:Ht(typeof e==`function`?e:Bt(e)))}var Gt=Array.prototype.filter;function Kt(){return Array.from(this.children)}function qt(e){return function(){return Gt.call(this.children,e)}}function Jt(e){return this.selectAll(e==null?Kt:qt(typeof e==`function`?e:Bt(e)))}function Yt(e){typeof e!=`function`&&(e=zt(e));for(var t=this._groups,n=t.length,r=Array(n),i=0;i<n;++i)for(var a=t[i],o=a.length,s=r[i]=[],c,l=0;l<o;++l)(c=a[l])&&e.call(c,c.__data__,l,a)&&s.push(c);return new P(r,this._parents)}function Xt(e){return Array(e.length)}function Zt(){return new P(this._enter||this._groups.map(Xt),this._parents)}function Qt(e,t){this.ownerDocument=e.ownerDocument,this.namespaceURI=e.namespaceURI,this._next=null,this._parent=e,this.__data__=t}Qt.prototype={constructor:Qt,appendChild:function(e){return this._parent.insertBefore(e,this._next)},insertBefore:function(e,t){return this._parent.insertBefore(e,t)},querySelector:function(e){return this._parent.querySelector(e)},querySelectorAll:function(e){return this._parent.querySelectorAll(e)}};function $t(e){return function(){return e}}function en(e,t,n,r,i,a){for(var o=0,s,c=t.length,l=a.length;o<l;++o)(s=t[o])?(s.__data__=a[o],r[o]=s):n[o]=new Qt(e,a[o]);for(;o<c;++o)(s=t[o])&&(i[o]=s)}function tn(e,t,n,r,i,a,o){var s,c,l=new Map,u=t.length,d=a.length,f=Array(u),p;for(s=0;s<u;++s)(c=t[s])&&(f[s]=p=o.call(c,c.__data__,s,t)+``,l.has(p)?i[s]=c:l.set(p,c));for(s=0;s<d;++s)p=o.call(e,a[s],s,a)+``,(c=l.get(p))?(r[s]=c,c.__data__=a[s],l.delete(p)):n[s]=new Qt(e,a[s]);for(s=0;s<u;++s)(c=t[s])&&l.get(f[s])===c&&(i[s]=c)}function nn(e){return e.__data__}function rn(e,t){if(!arguments.length)return Array.from(this,nn);var n=t?tn:en,r=this._parents,i=this._groups;typeof e!=`function`&&(e=$t(e));for(var a=i.length,o=Array(a),s=Array(a),c=Array(a),l=0;l<a;++l){var u=r[l],d=i[l],f=d.length,p=an(e.call(u,u&&u.__data__,l,r)),m=p.length,h=s[l]=Array(m),g=o[l]=Array(m);n(u,d,h,g,c[l]=Array(f),p,t);for(var _=0,v=0,y,b;_<m;++_)if(y=h[_]){for(_>=v&&(v=_+1);!(b=g[v])&&++v<m;);y._next=b||null}}return o=new P(o,r),o._enter=s,o._exit=c,o}function an(e){return typeof e==`object`&&`length`in e?e:Array.from(e)}function on(){return new P(this._exit||this._groups.map(Xt),this._parents)}function sn(e,t,n){var r=this.enter(),i=this,a=this.exit();return typeof e==`function`?(r=e(r),r&&=r.selection()):r=r.append(e+``),t!=null&&(i=t(i),i&&=i.selection()),n==null?a.remove():n(a),r&&i?r.merge(i).order():i}function cn(e){for(var t=e.selection?e.selection():e,n=this._groups,r=t._groups,i=n.length,a=r.length,o=Math.min(i,a),s=Array(i),c=0;c<o;++c)for(var l=n[c],u=r[c],d=l.length,f=s[c]=Array(d),p,m=0;m<d;++m)(p=l[m]||u[m])&&(f[m]=p);for(;c<i;++c)s[c]=n[c];return new P(s,this._parents)}function ln(){for(var e=this._groups,t=-1,n=e.length;++t<n;)for(var r=e[t],i=r.length-1,a=r[i],o;--i>=0;)(o=r[i])&&(a&&o.compareDocumentPosition(a)^4&&a.parentNode.insertBefore(o,a),a=o);return this}function un(e){e||=dn;function t(t,n){return t&&n?e(t.__data__,n.__data__):!t-!n}for(var n=this._groups,r=n.length,i=Array(r),a=0;a<r;++a){for(var o=n[a],s=o.length,c=i[a]=Array(s),l,u=0;u<s;++u)(l=o[u])&&(c[u]=l);c.sort(t)}return new P(i,this._parents).order()}function dn(e,t){return e<t?-1:e>t?1:e>=t?0:NaN}function fn(){var e=arguments[0];return arguments[0]=this,e.apply(null,arguments),this}function pn(){return Array.from(this)}function mn(){for(var e=this._groups,t=0,n=e.length;t<n;++t)for(var r=e[t],i=0,a=r.length;i<a;++i){var o=r[i];if(o)return o}return null}function hn(){let e=0;for(let t of this)++e;return e}function gn(){return!this.node()}function _n(e){for(var t=this._groups,n=0,r=t.length;n<r;++n)for(var i=t[n],a=0,o=i.length,s;a<o;++a)(s=i[a])&&e.call(s,s.__data__,a,i);return this}function vn(e){return function(){this.removeAttribute(e)}}function yn(e){return function(){this.removeAttributeNS(e.space,e.local)}}function bn(e,t){return function(){this.setAttribute(e,t)}}function xn(e,t){return function(){this.setAttributeNS(e.space,e.local,t)}}function Sn(e,t){return function(){var n=t.apply(this,arguments);n==null?this.removeAttribute(e):this.setAttribute(e,n)}}function Cn(e,t){return function(){var n=t.apply(this,arguments);n==null?this.removeAttributeNS(e.space,e.local):this.setAttributeNS(e.space,e.local,n)}}function wn(e,t){var n=Dt(e);if(arguments.length<2){var r=this.node();return n.local?r.getAttributeNS(n.space,n.local):r.getAttribute(n)}return this.each((t==null?n.local?yn:vn:typeof t==`function`?n.local?Cn:Sn:n.local?xn:bn)(n,t))}function Tn(e){return e.ownerDocument&&e.ownerDocument.defaultView||e.document&&e||e.defaultView}function En(e){return function(){this.style.removeProperty(e)}}function Dn(e,t,n){return function(){this.style.setProperty(e,t,n)}}function On(e,t,n){return function(){var r=t.apply(this,arguments);r==null?this.style.removeProperty(e):this.style.setProperty(e,r,n)}}function kn(e,t,n){return arguments.length>1?this.each((t==null?En:typeof t==`function`?On:Dn)(e,t,n??``)):N(this.node(),e)}function N(e,t){return e.style.getPropertyValue(t)||Tn(e).getComputedStyle(e,null).getPropertyValue(t)}function An(e){return function(){delete this[e]}}function jn(e,t){return function(){this[e]=t}}function Mn(e,t){return function(){var n=t.apply(this,arguments);n==null?delete this[e]:this[e]=n}}function Nn(e,t){return arguments.length>1?this.each((t==null?An:typeof t==`function`?Mn:jn)(e,t)):this.node()[e]}function Pn(e){return e.trim().split(/^|\s+/)}function Fn(e){return e.classList||new In(e)}function In(e){this._node=e,this._names=Pn(e.getAttribute(`class`)||``)}In.prototype={add:function(e){this._names.indexOf(e)<0&&(this._names.push(e),this._node.setAttribute(`class`,this._names.join(` `)))},remove:function(e){var t=this._names.indexOf(e);t>=0&&(this._names.splice(t,1),this._node.setAttribute(`class`,this._names.join(` `)))},contains:function(e){return this._names.indexOf(e)>=0}};function Ln(e,t){for(var n=Fn(e),r=-1,i=t.length;++r<i;)n.add(t[r])}function Rn(e,t){for(var n=Fn(e),r=-1,i=t.length;++r<i;)n.remove(t[r])}function zn(e){return function(){Ln(this,e)}}function Bn(e){return function(){Rn(this,e)}}function Vn(e,t){return function(){(t.apply(this,arguments)?Ln:Rn)(this,e)}}function Hn(e,t){var n=Pn(e+``);if(arguments.length<2){for(var r=Fn(this.node()),i=-1,a=n.length;++i<a;)if(!r.contains(n[i]))return!1;return!0}return this.each((typeof t==`function`?Vn:t?zn:Bn)(n,t))}function Un(){this.textContent=``}function Wn(e){return function(){this.textContent=e}}function Gn(e){return function(){var t=e.apply(this,arguments);this.textContent=t??``}}function Kn(e){return arguments.length?this.each(e==null?Un:(typeof e==`function`?Gn:Wn)(e)):this.node().textContent}function qn(){this.innerHTML=``}function Jn(e){return function(){this.innerHTML=e}}function Yn(e){return function(){var t=e.apply(this,arguments);this.innerHTML=t??``}}function Xn(e){return arguments.length?this.each(e==null?qn:(typeof e==`function`?Yn:Jn)(e)):this.node().innerHTML}function Zn(){this.nextSibling&&this.parentNode.appendChild(this)}function Qn(){return this.each(Zn)}function $n(){this.previousSibling&&this.parentNode.insertBefore(this,this.parentNode.firstChild)}function er(){return this.each($n)}function tr(e){var t=typeof e==`function`?e:At(e);return this.select(function(){return this.appendChild(t.apply(this,arguments))})}function nr(){return null}function rr(e,t){var n=typeof e==`function`?e:At(e),r=t==null?nr:typeof t==`function`?t:Mt(t);return this.select(function(){return this.insertBefore(n.apply(this,arguments),r.apply(this,arguments)||null)})}function ir(){var e=this.parentNode;e&&e.removeChild(this)}function ar(){return this.each(ir)}function or(){var e=this.cloneNode(!1),t=this.parentNode;return t?t.insertBefore(e,this.nextSibling):e}function sr(){var e=this.cloneNode(!0),t=this.parentNode;return t?t.insertBefore(e,this.nextSibling):e}function cr(e){return this.select(e?sr:or)}function lr(e){return arguments.length?this.property(`__data__`,e):this.node().__data__}function ur(e){return function(t){e.call(this,t,this.__data__)}}function dr(e){return e.trim().split(/^|\s+/).map(function(e){var t=``,n=e.indexOf(`.`);return n>=0&&(t=e.slice(n+1),e=e.slice(0,n)),{type:e,name:t}})}function fr(e){return function(){var t=this.__on;if(t){for(var n=0,r=-1,i=t.length,a;n<i;++n)a=t[n],(!e.type||a.type===e.type)&&a.name===e.name?this.removeEventListener(a.type,a.listener,a.options):t[++r]=a;++r?t.length=r:delete this.__on}}}function pr(e,t,n){return function(){var r=this.__on,i,a=ur(t);if(r){for(var o=0,s=r.length;o<s;++o)if((i=r[o]).type===e.type&&i.name===e.name){this.removeEventListener(i.type,i.listener,i.options),this.addEventListener(i.type,i.listener=a,i.options=n),i.value=t;return}}this.addEventListener(e.type,a,n),i={type:e.type,name:e.name,value:t,listener:a,options:n},r?r.push(i):this.__on=[i]}}function mr(e,t,n){var r=dr(e+``),i,a=r.length,o;if(arguments.length<2){var s=this.node().__on;if(s){for(var c=0,l=s.length,u;c<l;++c)for(i=0,u=s[c];i<a;++i)if((o=r[i]).type===u.type&&o.name===u.name)return u.value}}else{for(s=t?pr:fr,i=0;i<a;++i)this.each(s(r[i],t,n));return this}}function hr(e,t,n){var r=Tn(e),i=r.CustomEvent;typeof i==`function`?i=new i(t,n):(i=r.document.createEvent(`Event`),n?(i.initEvent(t,n.bubbles,n.cancelable),i.detail=n.detail):i.initEvent(t,!1,!1)),e.dispatchEvent(i)}function gr(e,t){return function(){return hr(this,e,t)}}function _r(e,t){return function(){return hr(this,e,t.apply(this,arguments))}}function vr(e,t){return this.each((typeof t==`function`?_r:gr)(e,t))}function*yr(){for(var e=this._groups,t=0,n=e.length;t<n;++t)for(var r=e[t],i=0,a=r.length,o;i<a;++i)(o=r[i])&&(yield o)}var br=[null];function P(e,t){this._groups=e,this._parents=t}function xr(){return new P([[document.documentElement]],br)}function Sr(){return this}P.prototype=xr.prototype={constructor:P,select:Nt,selectAll:Rt,selectChild:Wt,selectChildren:Jt,filter:Yt,data:rn,enter:Zt,exit:on,join:sn,merge:cn,selection:Sr,order:ln,sort:un,call:fn,nodes:pn,node:mn,size:hn,empty:gn,each:_n,attr:wn,style:kn,property:Nn,classed:Hn,text:Kn,html:Xn,raise:Qn,lower:er,append:tr,insert:rr,remove:ar,clone:cr,datum:lr,on:mr,dispatch:vr,[Symbol.iterator]:yr};function F(e){return typeof e==`string`?new P([[document.querySelector(e)]],[document.documentElement]):new P([[e]],br)}var Cr=0;function wr(){return new Tr}function Tr(){this._=`@`+(++Cr).toString(36)}Tr.prototype=wr.prototype={constructor:Tr,get:function(e){for(var t=this._;!(t in e);)if(!(e=e.parentNode))return;return e[t]},set:function(e,t){return e[this._]=t},remove:function(e){return this._ in e&&delete e[this._]},toString:function(){return this._}};function Er(e){let t;for(;t=e.sourceEvent;)e=t;return e}function I(e,t){if(e=Er(e),t===void 0&&(t=e.currentTarget),t){var n=t.ownerSVGElement||t;if(n.createSVGPoint){var r=n.createSVGPoint();return r.x=e.clientX,r.y=e.clientY,r=r.matrixTransform(t.getScreenCTM().inverse()),[r.x,r.y]}if(t.getBoundingClientRect){var i=t.getBoundingClientRect();return[e.clientX-i.left-t.clientLeft,e.clientY-i.top-t.clientTop]}}return[e.pageX,e.pageY]}var Dr={value:()=>{}};function Or(){for(var e=0,t=arguments.length,n={},r;e<t;++e){if(!(r=arguments[e]+``)||r in n||/[\s.]/.test(r))throw Error(`illegal type: `+r);n[r]=[]}return new kr(n)}function kr(e){this._=e}function Ar(e,t){return e.trim().split(/^|\s+/).map(function(e){var n=``,r=e.indexOf(`.`);if(r>=0&&(n=e.slice(r+1),e=e.slice(0,r)),e&&!t.hasOwnProperty(e))throw Error(`unknown type: `+e);return{type:e,name:n}})}kr.prototype=Or.prototype={constructor:kr,on:function(e,t){var n=this._,r=Ar(e+``,n),i,a=-1,o=r.length;if(arguments.length<2){for(;++a<o;)if((i=(e=r[a]).type)&&(i=jr(n[i],e.name)))return i}else{if(t!=null&&typeof t!=`function`)throw Error(`invalid callback: `+t);for(;++a<o;)if(i=(e=r[a]).type)n[i]=Mr(n[i],e.name,t);else if(t==null)for(i in n)n[i]=Mr(n[i],e.name,null);return this}},copy:function(){var e={},t=this._;for(var n in t)e[n]=t[n].slice();return new kr(e)},call:function(e,t){if((i=arguments.length-2)>0)for(var n=Array(i),r=0,i,a;r<i;++r)n[r]=arguments[r+2];if(!this._.hasOwnProperty(e))throw Error(`unknown type: `+e);for(a=this._[e],r=0,i=a.length;r<i;++r)a[r].value.apply(t,n)},apply:function(e,t,n){if(!this._.hasOwnProperty(e))throw Error(`unknown type: `+e);for(var r=this._[e],i=0,a=r.length;i<a;++i)r[i].value.apply(t,n)}};function jr(e,t){for(var n=0,r=e.length,i;n<r;++n)if((i=e[n]).name===t)return i.value}function Mr(e,t,n){for(var r=0,i=e.length;r<i;++r)if(e[r].name===t){e[r]=Dr,e=e.slice(0,r).concat(e.slice(r+1));break}return n!=null&&e.push({name:t,value:n}),e}var L=0,Nr=0,Pr=0,Fr=1e3,Ir,Lr,Rr=0,R=0,zr=0,Br=typeof performance==`object`&&performance.now?performance:Date,Vr=typeof window==`object`&&window.requestAnimationFrame?window.requestAnimationFrame.bind(window):function(e){setTimeout(e,17)};function Hr(){return R||=(Vr(Ur),Br.now()+zr)}function Ur(){R=0}function Wr(){this._call=this._time=this._next=null}Wr.prototype=Gr.prototype={constructor:Wr,restart:function(e,t,n){if(typeof e!=`function`)throw TypeError(`callback is not a function`);n=(n==null?Hr():+n)+(t==null?0:+t),!this._next&&Lr!==this&&(Lr?Lr._next=this:Ir=this,Lr=this),this._call=e,this._time=n,Xr()},stop:function(){this._call&&(this._call=null,this._time=1/0,Xr())}};function Gr(e,t,n){var r=new Wr;return r.restart(e,t,n),r}function Kr(){Hr(),++L;for(var e=Ir,t;e;)(t=R-e._time)>=0&&e._call.call(void 0,t),e=e._next;--L}function qr(){R=(Rr=Br.now())+zr,L=Nr=0;try{Kr()}finally{L=0,Yr(),R=0}}function Jr(){var e=Br.now(),t=e-Rr;t>Fr&&(zr-=t,Rr=e)}function Yr(){for(var e,t=Ir,n,r=1/0;t;)t._call?(r>t._time&&(r=t._time),e=t,t=t._next):(n=t._next,t._next=null,t=e?e._next=n:Ir=n);Lr=e,Xr(r)}function Xr(e){L||(Nr&&=clearTimeout(Nr),e-R>24?(e<1/0&&(Nr=setTimeout(qr,e-Br.now()-zr)),Pr&&=clearInterval(Pr)):(Pr||=(Rr=Br.now(),setInterval(Jr,Fr)),L=1,Vr(qr)))}function Zr(e,t,n){var r=new Wr;return t=t==null?0:+t,r.restart(n=>{r.stop(),e(n+t)},t,n),r}function Qr(e,t,n){e.prototype=t.prototype=n,n.constructor=e}function $r(e,t){var n=Object.create(e.prototype);for(var r in t)n[r]=t[r];return n}function ei(){}var ti=.7,ni=1/ti,z=`\\s*([+-]?\\d+)\\s*`,ri=`\\s*([+-]?(?:\\d*\\.)?\\d+(?:[eE][+-]?\\d+)?)\\s*`,B=`\\s*([+-]?(?:\\d*\\.)?\\d+(?:[eE][+-]?\\d+)?)%\\s*`,ii=/^#([0-9a-f]{3,8})$/,ai=RegExp(`^rgb\\(${z},${z},${z}\\)$`),oi=RegExp(`^rgb\\(${B},${B},${B}\\)$`),si=RegExp(`^rgba\\(${z},${z},${z},${ri}\\)$`),ci=RegExp(`^rgba\\(${B},${B},${B},${ri}\\)$`),li=RegExp(`^hsl\\(${ri},${B},${B}\\)$`),ui=RegExp(`^hsla\\(${ri},${B},${B},${ri}\\)$`),di={aliceblue:15792383,antiquewhite:16444375,aqua:65535,aquamarine:8388564,azure:15794175,beige:16119260,bisque:16770244,black:0,blanchedalmond:16772045,blue:255,blueviolet:9055202,brown:10824234,burlywood:14596231,cadetblue:6266528,chartreuse:8388352,chocolate:13789470,coral:16744272,cornflowerblue:6591981,cornsilk:16775388,crimson:14423100,cyan:65535,darkblue:139,darkcyan:35723,darkgoldenrod:12092939,darkgray:11119017,darkgreen:25600,darkgrey:11119017,darkkhaki:12433259,darkmagenta:9109643,darkolivegreen:5597999,darkorange:16747520,darkorchid:10040012,darkred:9109504,darksalmon:15308410,darkseagreen:9419919,darkslateblue:4734347,darkslategray:3100495,darkslategrey:3100495,darkturquoise:52945,darkviolet:9699539,deeppink:16716947,deepskyblue:49151,dimgray:6908265,dimgrey:6908265,dodgerblue:2003199,firebrick:11674146,floralwhite:16775920,forestgreen:2263842,fuchsia:16711935,gainsboro:14474460,ghostwhite:16316671,gold:16766720,goldenrod:14329120,gray:8421504,green:32768,greenyellow:11403055,grey:8421504,honeydew:15794160,hotpink:16738740,indianred:13458524,indigo:4915330,ivory:16777200,khaki:15787660,lavender:15132410,lavenderblush:16773365,lawngreen:8190976,lemonchiffon:16775885,lightblue:11393254,lightcoral:15761536,lightcyan:14745599,lightgoldenrodyellow:16448210,lightgray:13882323,lightgreen:9498256,lightgrey:13882323,lightpink:16758465,lightsalmon:16752762,lightseagreen:2142890,lightskyblue:8900346,lightslategray:7833753,lightslategrey:7833753,lightsteelblue:11584734,lightyellow:16777184,lime:65280,limegreen:3329330,linen:16445670,magenta:16711935,maroon:8388608,mediumaquamarine:6737322,mediumblue:205,mediumorchid:12211667,mediumpurple:9662683,mediumseagreen:3978097,mediumslateblue:8087790,mediumspringgreen:64154,mediumturquoise:4772300,mediumvioletred:13047173,midnightblue:1644912,mintcream:16121850,mistyrose:16770273,moccasin:16770229,navajowhite:16768685,navy:128,oldlace:16643558,olive:8421376,olivedrab:7048739,orange:16753920,orangered:16729344,orchid:14315734,palegoldenrod:15657130,palegreen:10025880,paleturquoise:11529966,palevioletred:14381203,papayawhip:16773077,peachpuff:16767673,peru:13468991,pink:16761035,plum:14524637,powderblue:11591910,purple:8388736,rebeccapurple:6697881,red:16711680,rosybrown:12357519,royalblue:4286945,saddlebrown:9127187,salmon:16416882,sandybrown:16032864,seagreen:3050327,seashell:16774638,sienna:10506797,silver:12632256,skyblue:8900331,slateblue:6970061,slategray:7372944,slategrey:7372944,snow:16775930,springgreen:65407,steelblue:4620980,tan:13808780,teal:32896,thistle:14204888,tomato:16737095,turquoise:4251856,violet:15631086,wheat:16113331,white:16777215,whitesmoke:16119285,yellow:16776960,yellowgreen:10145074};Qr(ei,gi,{copy(e){return Object.assign(new this.constructor,this,e)},displayable(){return this.rgb().displayable()},hex:fi,formatHex:fi,formatHex8:pi,formatHsl:mi,formatRgb:hi,toString:hi});function fi(){return this.rgb().formatHex()}function pi(){return this.rgb().formatHex8()}function mi(){return Ei(this).formatHsl()}function hi(){return this.rgb().formatRgb()}function gi(e){var t,n;return e=(e+``).trim().toLowerCase(),(t=ii.exec(e))?(n=t[1].length,t=parseInt(t[1],16),n===6?_i(t):n===3?new V(t>>8&15|t>>4&240,t>>4&15|t&240,(t&15)<<4|t&15,1):n===8?vi(t>>24&255,t>>16&255,t>>8&255,(t&255)/255):n===4?vi(t>>12&15|t>>8&240,t>>8&15|t>>4&240,t>>4&15|t&240,((t&15)<<4|t&15)/255):null):(t=ai.exec(e))?new V(t[1],t[2],t[3],1):(t=oi.exec(e))?new V(t[1]*255/100,t[2]*255/100,t[3]*255/100,1):(t=si.exec(e))?vi(t[1],t[2],t[3],t[4]):(t=ci.exec(e))?vi(t[1]*255/100,t[2]*255/100,t[3]*255/100,t[4]):(t=li.exec(e))?Ti(t[1],t[2]/100,t[3]/100,1):(t=ui.exec(e))?Ti(t[1],t[2]/100,t[3]/100,t[4]):di.hasOwnProperty(e)?_i(di[e]):e===`transparent`?new V(NaN,NaN,NaN,0):null}function _i(e){return new V(e>>16&255,e>>8&255,e&255,1)}function vi(e,t,n,r){return r<=0&&(e=t=n=NaN),new V(e,t,n,r)}function yi(e){return e instanceof ei||(e=gi(e)),e?(e=e.rgb(),new V(e.r,e.g,e.b,e.opacity)):new V}function bi(e,t,n,r){return arguments.length===1?yi(e):new V(e,t,n,r??1)}function V(e,t,n,r){this.r=+e,this.g=+t,this.b=+n,this.opacity=+r}Qr(V,bi,$r(ei,{brighter(e){return e=e==null?ni:ni**+e,new V(this.r*e,this.g*e,this.b*e,this.opacity)},darker(e){return e=e==null?ti:ti**+e,new V(this.r*e,this.g*e,this.b*e,this.opacity)},rgb(){return this},clamp(){return new V(H(this.r),H(this.g),H(this.b),wi(this.opacity))},displayable(){return-.5<=this.r&&this.r<255.5&&-.5<=this.g&&this.g<255.5&&-.5<=this.b&&this.b<255.5&&0<=this.opacity&&this.opacity<=1},hex:xi,formatHex:xi,formatHex8:Si,formatRgb:Ci,toString:Ci}));function xi(){return`#${U(this.r)}${U(this.g)}${U(this.b)}`}function Si(){return`#${U(this.r)}${U(this.g)}${U(this.b)}${U((isNaN(this.opacity)?1:this.opacity)*255)}`}function Ci(){let e=wi(this.opacity);return`${e===1?`rgb(`:`rgba(`}${H(this.r)}, ${H(this.g)}, ${H(this.b)}${e===1?`)`:`, ${e})`}`}function wi(e){return isNaN(e)?1:Math.max(0,Math.min(1,e))}function H(e){return Math.max(0,Math.min(255,Math.round(e)||0))}function U(e){return e=H(e),(e<16?`0`:``)+e.toString(16)}function Ti(e,t,n,r){return r<=0?e=t=n=NaN:n<=0||n>=1?e=t=NaN:t<=0&&(e=NaN),new W(e,t,n,r)}function Ei(e){if(e instanceof W)return new W(e.h,e.s,e.l,e.opacity);if(e instanceof ei||(e=gi(e)),!e)return new W;if(e instanceof W)return e;e=e.rgb();var t=e.r/255,n=e.g/255,r=e.b/255,i=Math.min(t,n,r),a=Math.max(t,n,r),o=NaN,s=a-i,c=(a+i)/2;return s?(o=t===a?(n-r)/s+(n<r)*6:n===a?(r-t)/s+2:(t-n)/s+4,s/=c<.5?a+i:2-a-i,o*=60):s=c>0&&c<1?0:o,new W(o,s,c,e.opacity)}function Di(e,t,n,r){return arguments.length===1?Ei(e):new W(e,t,n,r??1)}function W(e,t,n,r){this.h=+e,this.s=+t,this.l=+n,this.opacity=+r}Qr(W,Di,$r(ei,{brighter(e){return e=e==null?ni:ni**+e,new W(this.h,this.s,this.l*e,this.opacity)},darker(e){return e=e==null?ti:ti**+e,new W(this.h,this.s,this.l*e,this.opacity)},rgb(){var e=this.h%360+(this.h<0)*360,t=isNaN(e)||isNaN(this.s)?0:this.s,n=this.l,r=n+(n<.5?n:1-n)*t,i=2*n-r;return new V(Ai(e>=240?e-240:e+120,i,r),Ai(e,i,r),Ai(e<120?e+240:e-120,i,r),this.opacity)},clamp(){return new W(Oi(this.h),ki(this.s),ki(this.l),wi(this.opacity))},displayable(){return(0<=this.s&&this.s<=1||isNaN(this.s))&&0<=this.l&&this.l<=1&&0<=this.opacity&&this.opacity<=1},formatHsl(){let e=wi(this.opacity);return`${e===1?`hsl(`:`hsla(`}${Oi(this.h)}, ${ki(this.s)*100}%, ${ki(this.l)*100}%${e===1?`)`:`, ${e})`}`}}));function Oi(e){return e=(e||0)%360,e<0?e+360:e}function ki(e){return Math.max(0,Math.min(1,e||0))}function Ai(e,t,n){return(e<60?t+(n-t)*e/60:e<180?n:e<240?t+(n-t)*(240-e)/60:t)*255}var ji=e=>()=>e;function Mi(e,t){return function(n){return e+n*t}}function Ni(e,t,n){return e**=+n,t=t**+n-e,n=1/n,function(r){return(e+r*t)**+n}}function Pi(e){return(e=+e)==1?Fi:function(t,n){return n-t?Ni(t,n,e):ji(isNaN(t)?n:t)}}function Fi(e,t){var n=t-e;return n?Mi(e,n):ji(isNaN(e)?t:e)}var Ii=(function e(t){var n=Pi(t);function r(e,t){var r=n((e=bi(e)).r,(t=bi(t)).r),i=n(e.g,t.g),a=n(e.b,t.b),o=Fi(e.opacity,t.opacity);return function(t){return e.r=r(t),e.g=i(t),e.b=a(t),e.opacity=o(t),e+``}}return r.gamma=e,r})(1);function G(e,t){return e=+e,t=+t,function(n){return e*(1-n)+t*n}}var Li=/[-+]?(?:\d+\.?\d*|\.?\d+)(?:[eE][-+]?\d+)?/g,Ri=new RegExp(Li.source,`g`);function zi(e){return function(){return e}}function Bi(e){return function(t){return e(t)+``}}function Vi(e,t){var n=Li.lastIndex=Ri.lastIndex=0,r,i,a,o=-1,s=[],c=[];for(e+=``,t+=``;(r=Li.exec(e))&&(i=Ri.exec(t));)(a=i.index)>n&&(a=t.slice(n,a),s[o]?s[o]+=a:s[++o]=a),(r=r[0])===(i=i[0])?s[o]?s[o]+=i:s[++o]=i:(s[++o]=null,c.push({i:o,x:G(r,i)})),n=Ri.lastIndex;return n<t.length&&(a=t.slice(n),s[o]?s[o]+=a:s[++o]=a),s.length<2?c[0]?Bi(c[0].x):zi(t):(t=c.length,function(e){for(var n=0,r;n<t;++n)s[(r=c[n]).i]=r.x(e);return s.join(``)})}var Hi=180/Math.PI,Ui={translateX:0,translateY:0,rotate:0,skewX:0,scaleX:1,scaleY:1};function Wi(e,t,n,r,i,a){var o,s,c;return(o=Math.sqrt(e*e+t*t))&&(e/=o,t/=o),(c=e*n+t*r)&&(n-=e*c,r-=t*c),(s=Math.sqrt(n*n+r*r))&&(n/=s,r/=s,c/=s),e*r<t*n&&(e=-e,t=-t,c=-c,o=-o),{translateX:i,translateY:a,rotate:Math.atan2(t,e)*Hi,skewX:Math.atan(c)*Hi,scaleX:o,scaleY:s}}var Gi;function Ki(e){let t=new(typeof DOMMatrix==`function`?DOMMatrix:WebKitCSSMatrix)(e+``);return t.isIdentity?Ui:Wi(t.a,t.b,t.c,t.d,t.e,t.f)}function qi(e){return e==null||(Gi||=document.createElementNS(`http://www.w3.org/2000/svg`,`g`),Gi.setAttribute(`transform`,e),!(e=Gi.transform.baseVal.consolidate()))?Ui:(e=e.matrix,Wi(e.a,e.b,e.c,e.d,e.e,e.f))}function Ji(e,t,n,r){function i(e){return e.length?e.pop()+` `:``}function a(e,r,i,a,o,s){if(e!==i||r!==a){var c=o.push(`translate(`,null,t,null,n);s.push({i:c-4,x:G(e,i)},{i:c-2,x:G(r,a)})}else(i||a)&&o.push(`translate(`+i+t+a+n)}function o(e,t,n,a){e===t?t&&n.push(i(n)+`rotate(`+t+r):(e-t>180?t+=360:t-e>180&&(e+=360),a.push({i:n.push(i(n)+`rotate(`,null,r)-2,x:G(e,t)}))}function s(e,t,n,a){e===t?t&&n.push(i(n)+`skewX(`+t+r):a.push({i:n.push(i(n)+`skewX(`,null,r)-2,x:G(e,t)})}function c(e,t,n,r,a,o){if(e!==n||t!==r){var s=a.push(i(a)+`scale(`,null,`,`,null,`)`);o.push({i:s-4,x:G(e,n)},{i:s-2,x:G(t,r)})}else(n!==1||r!==1)&&a.push(i(a)+`scale(`+n+`,`+r+`)`)}return function(t,n){var r=[],i=[];return t=e(t),n=e(n),a(t.translateX,t.translateY,n.translateX,n.translateY,r,i),o(t.rotate,n.rotate,r,i),s(t.skewX,n.skewX,r,i),c(t.scaleX,t.scaleY,n.scaleX,n.scaleY,r,i),t=n=null,function(e){for(var t=-1,n=i.length,a;++t<n;)r[(a=i[t]).i]=a.x(e);return r.join(``)}}}var Yi=Ji(Ki,`px, `,`px)`,`deg)`),Xi=Ji(qi,`, `,`)`,`)`),Zi=1e-12;function Qi(e){return((e=Math.exp(e))+1/e)/2}function $i(e){return((e=Math.exp(e))-1/e)/2}function ea(e){return((e=Math.exp(2*e))-1)/(e+1)}var ta=(function e(t,n,r){function i(e,i){var a=e[0],o=e[1],s=e[2],c=i[0],l=i[1],u=i[2],d=c-a,f=l-o,p=d*d+f*f,m,h;if(p<Zi)h=Math.log(u/s)/t,m=function(e){return[a+e*d,o+e*f,s*Math.exp(t*e*h)]};else{var g=Math.sqrt(p),_=(u*u-s*s+r*p)/(2*s*n*g),v=(u*u-s*s-r*p)/(2*u*n*g),y=Math.log(Math.sqrt(_*_+1)-_);h=(Math.log(Math.sqrt(v*v+1)-v)-y)/t,m=function(e){var r=e*h,i=Qi(y),c=s/(n*g)*(i*ea(t*r+y)-$i(y));return[a+c*d,o+c*f,s*i/Qi(t*r+y)]}}return m.duration=h*1e3*t/Math.SQRT2,m}return i.rho=function(t){var n=Math.max(.001,+t),r=n*n;return e(n,r,r*r)},i})(Math.SQRT2,2,4);function na(e){return((e*=2)<=1?e*e*e:(e-=2)*e*e+2)/2}var ra=Or(`start`,`end`,`cancel`,`interrupt`),ia=[],aa=0,oa=1,sa=2,ca=3,la=4,ua=5,da=6;function fa(e,t,n,r,i,a){var o=e.__transition;if(!o)e.__transition={};else if(n in o)return;ma(e,n,{name:t,index:r,group:i,on:ra,tween:ia,time:a.time,delay:a.delay,duration:a.duration,ease:a.ease,timer:null,state:aa})}function pa(e,t){var n=q(e,t);if(n.state>aa)throw Error(`too late; already scheduled`);return n}function K(e,t){var n=q(e,t);if(n.state>ca)throw Error(`too late; already running`);return n}function q(e,t){var n=e.__transition;if(!n||!(n=n[t]))throw Error(`transition not found`);return n}function ma(e,t,n){var r=e.__transition,i;r[t]=n,n.timer=Gr(a,0,n.time);function a(e){n.state=oa,n.timer.restart(o,n.delay,n.time),n.delay<=e&&o(e-n.delay)}function o(a){var l,u,d,f;if(n.state!==oa)return c();for(l in r)if(f=r[l],f.name===n.name){if(f.state===ca)return Zr(o);f.state===la?(f.state=da,f.timer.stop(),f.on.call(`interrupt`,e,e.__data__,f.index,f.group),delete r[l]):+l<t&&(f.state=da,f.timer.stop(),f.on.call(`cancel`,e,e.__data__,f.index,f.group),delete r[l])}if(Zr(function(){n.state===ca&&(n.state=la,n.timer.restart(s,n.delay,n.time),s(a))}),n.state=sa,n.on.call(`start`,e,e.__data__,n.index,n.group),n.state===sa){for(n.state=ca,i=Array(d=n.tween.length),l=0,u=-1;l<d;++l)(f=n.tween[l].value.call(e,e.__data__,n.index,n.group))&&(i[++u]=f);i.length=u+1}}function s(t){for(var r=t<n.duration?n.ease.call(null,t/n.duration):(n.timer.restart(c),n.state=ua,1),a=-1,o=i.length;++a<o;)i[a].call(e,r);n.state===ua&&(n.on.call(`end`,e,e.__data__,n.index,n.group),c())}function c(){for(var i in n.state=da,n.timer.stop(),delete r[t],r)return;delete e.__transition}}function ha(e,t){var n=e.__transition,r,i,a=!0,o;if(n){for(o in t=t==null?null:t+``,n)(r=n[o]).name===t?(i=r.state>sa&&r.state<ua,r.state=da,r.timer.stop(),r.on.call(i?`interrupt`:`cancel`,e,e.__data__,r.index,r.group),delete n[o]):a=!1;a&&delete e.__transition}}function ga(e){return this.each(function(){ha(this,e)})}function _a(e,t){var n,r;return function(){var i=K(this,e),a=i.tween;if(a!==n){r=n=a;for(var o=0,s=r.length;o<s;++o)if(r[o].name===t){r=r.slice(),r.splice(o,1);break}}i.tween=r}}function va(e,t,n){var r,i;if(typeof n!=`function`)throw Error();return function(){var a=K(this,e),o=a.tween;if(o!==r){i=(r=o).slice();for(var s={name:t,value:n},c=0,l=i.length;c<l;++c)if(i[c].name===t){i[c]=s;break}c===l&&i.push(s)}a.tween=i}}function ya(e,t){var n=this._id;if(e+=``,arguments.length<2){for(var r=q(this.node(),n).tween,i=0,a=r.length,o;i<a;++i)if((o=r[i]).name===e)return o.value;return null}return this.each((t==null?_a:va)(n,e,t))}function ba(e,t,n){var r=e._id;return e.each(function(){var e=K(this,r);(e.value||={})[t]=n.apply(this,arguments)}),function(e){return q(e,r).value[t]}}function xa(e,t){var n;return(typeof t==`number`?G:t instanceof gi?Ii:(n=gi(t))?(t=n,Ii):Vi)(e,t)}function Sa(e){return function(){this.removeAttribute(e)}}function Ca(e){return function(){this.removeAttributeNS(e.space,e.local)}}function wa(e,t,n){var r,i=n+``,a;return function(){var o=this.getAttribute(e);return o===i?null:o===r?a:a=t(r=o,n)}}function Ta(e,t,n){var r,i=n+``,a;return function(){var o=this.getAttributeNS(e.space,e.local);return o===i?null:o===r?a:a=t(r=o,n)}}function Ea(e,t,n){var r,i,a;return function(){var o,s=n(this),c;return s==null?void this.removeAttribute(e):(o=this.getAttribute(e),c=s+``,o===c?null:o===r&&c===i?a:(i=c,a=t(r=o,s)))}}function Da(e,t,n){var r,i,a;return function(){var o,s=n(this),c;return s==null?void this.removeAttributeNS(e.space,e.local):(o=this.getAttributeNS(e.space,e.local),c=s+``,o===c?null:o===r&&c===i?a:(i=c,a=t(r=o,s)))}}function Oa(e,t){var n=Dt(e),r=n===`transform`?Xi:xa;return this.attrTween(e,typeof t==`function`?(n.local?Da:Ea)(n,r,ba(this,`attr.`+e,t)):t==null?(n.local?Ca:Sa)(n):(n.local?Ta:wa)(n,r,t))}function ka(e,t){return function(n){this.setAttribute(e,t.call(this,n))}}function Aa(e,t){return function(n){this.setAttributeNS(e.space,e.local,t.call(this,n))}}function ja(e,t){var n,r;function i(){var i=t.apply(this,arguments);return i!==r&&(n=(r=i)&&Aa(e,i)),n}return i._value=t,i}function Ma(e,t){var n,r;function i(){var i=t.apply(this,arguments);return i!==r&&(n=(r=i)&&ka(e,i)),n}return i._value=t,i}function Na(e,t){var n=`attr.`+e;if(arguments.length<2)return(n=this.tween(n))&&n._value;if(t==null)return this.tween(n,null);if(typeof t!=`function`)throw Error();var r=Dt(e);return this.tween(n,(r.local?ja:Ma)(r,t))}function Pa(e,t){return function(){pa(this,e).delay=+t.apply(this,arguments)}}function Fa(e,t){return t=+t,function(){pa(this,e).delay=t}}function Ia(e){var t=this._id;return arguments.length?this.each((typeof e==`function`?Pa:Fa)(t,e)):q(this.node(),t).delay}function La(e,t){return function(){K(this,e).duration=+t.apply(this,arguments)}}function Ra(e,t){return t=+t,function(){K(this,e).duration=t}}function za(e){var t=this._id;return arguments.length?this.each((typeof e==`function`?La:Ra)(t,e)):q(this.node(),t).duration}function Ba(e,t){if(typeof t!=`function`)throw Error();return function(){K(this,e).ease=t}}function Va(e){var t=this._id;return arguments.length?this.each(Ba(t,e)):q(this.node(),t).ease}function Ha(e,t){return function(){var n=t.apply(this,arguments);if(typeof n!=`function`)throw Error();K(this,e).ease=n}}function Ua(e){if(typeof e!=`function`)throw Error();return this.each(Ha(this._id,e))}function Wa(e){typeof e!=`function`&&(e=zt(e));for(var t=this._groups,n=t.length,r=Array(n),i=0;i<n;++i)for(var a=t[i],o=a.length,s=r[i]=[],c,l=0;l<o;++l)(c=a[l])&&e.call(c,c.__data__,l,a)&&s.push(c);return new J(r,this._parents,this._name,this._id)}function Ga(e){if(e._id!==this._id)throw Error();for(var t=this._groups,n=e._groups,r=t.length,i=n.length,a=Math.min(r,i),o=Array(r),s=0;s<a;++s)for(var c=t[s],l=n[s],u=c.length,d=o[s]=Array(u),f,p=0;p<u;++p)(f=c[p]||l[p])&&(d[p]=f);for(;s<r;++s)o[s]=t[s];return new J(o,this._parents,this._name,this._id)}function Ka(e){return(e+``).trim().split(/^|\s+/).every(function(e){var t=e.indexOf(`.`);return t>=0&&(e=e.slice(0,t)),!e||e===`start`})}function qa(e,t,n){var r,i,a=Ka(t)?pa:K;return function(){var o=a(this,e),s=o.on;s!==r&&(i=(r=s).copy()).on(t,n),o.on=i}}function Ja(e,t){var n=this._id;return arguments.length<2?q(this.node(),n).on.on(e):this.each(qa(n,e,t))}function Ya(e){return function(){var t=this.parentNode;for(var n in this.__transition)if(+n!==e)return;t&&t.removeChild(this)}}function Xa(){return this.on(`end.remove`,Ya(this._id))}function Za(e){var t=this._name,n=this._id;typeof e!=`function`&&(e=Mt(e));for(var r=this._groups,i=r.length,a=Array(i),o=0;o<i;++o)for(var s=r[o],c=s.length,l=a[o]=Array(c),u,d,f=0;f<c;++f)(u=s[f])&&(d=e.call(u,u.__data__,f,s))&&(`__data__`in u&&(d.__data__=u.__data__),l[f]=d,fa(l[f],t,n,f,l,q(u,n)));return new J(a,this._parents,t,n)}function Qa(e){var t=this._name,n=this._id;typeof e!=`function`&&(e=It(e));for(var r=this._groups,i=r.length,a=[],o=[],s=0;s<i;++s)for(var c=r[s],l=c.length,u,d=0;d<l;++d)if(u=c[d]){for(var f=e.call(u,u.__data__,d,c),p,m=q(u,n),h=0,g=f.length;h<g;++h)(p=f[h])&&fa(p,t,n,h,f,m);a.push(f),o.push(u)}return new J(a,o,t,n)}var $a=xr.prototype.constructor;function eo(){return new $a(this._groups,this._parents)}function to(e,t){var n,r,i;return function(){var a=N(this,e),o=(this.style.removeProperty(e),N(this,e));return a===o?null:a===n&&o===r?i:i=t(n=a,r=o)}}function no(e){return function(){this.style.removeProperty(e)}}function ro(e,t,n){var r,i=n+``,a;return function(){var o=N(this,e);return o===i?null:o===r?a:a=t(r=o,n)}}function io(e,t,n){var r,i,a;return function(){var o=N(this,e),s=n(this),c=s+``;return s??(c=s=(this.style.removeProperty(e),N(this,e))),o===c?null:o===r&&c===i?a:(i=c,a=t(r=o,s))}}function ao(e,t){var n,r,i,a=`style.`+t,o=`end.`+a,s;return function(){var c=K(this,e),l=c.on,u=c.value[a]==null?s||=no(t):void 0;(l!==n||i!==u)&&(r=(n=l).copy()).on(o,i=u),c.on=r}}function oo(e,t,n){var r=(e+=``)==`transform`?Yi:xa;return t==null?this.styleTween(e,to(e,r)).on(`end.style.`+e,no(e)):typeof t==`function`?this.styleTween(e,io(e,r,ba(this,`style.`+e,t))).each(ao(this._id,e)):this.styleTween(e,ro(e,r,t),n).on(`end.style.`+e,null)}function so(e,t,n){return function(r){this.style.setProperty(e,t.call(this,r),n)}}function co(e,t,n){var r,i;function a(){var a=t.apply(this,arguments);return a!==i&&(r=(i=a)&&so(e,a,n)),r}return a._value=t,a}function lo(e,t,n){var r=`style.`+(e+=``);if(arguments.length<2)return(r=this.tween(r))&&r._value;if(t==null)return this.tween(r,null);if(typeof t!=`function`)throw Error();return this.tween(r,co(e,t,n??``))}function uo(e){return function(){this.textContent=e}}function fo(e){return function(){var t=e(this);this.textContent=t??``}}function po(e){return this.tween(`text`,typeof e==`function`?fo(ba(this,`text`,e)):uo(e==null?``:e+``))}function mo(e){return function(t){this.textContent=e.call(this,t)}}function ho(e){var t,n;function r(){var r=e.apply(this,arguments);return r!==n&&(t=(n=r)&&mo(r)),t}return r._value=e,r}function go(e){var t=`text`;if(arguments.length<1)return(t=this.tween(t))&&t._value;if(e==null)return this.tween(t,null);if(typeof e!=`function`)throw Error();return this.tween(t,ho(e))}function _o(){for(var e=this._name,t=this._id,n=bo(),r=this._groups,i=r.length,a=0;a<i;++a)for(var o=r[a],s=o.length,c,l=0;l<s;++l)if(c=o[l]){var u=q(c,t);fa(c,e,n,l,o,{time:u.time+u.delay+u.duration,delay:0,duration:u.duration,ease:u.ease})}return new J(r,this._parents,e,n)}function vo(){var e,t,n=this,r=n._id,i=n.size();return new Promise(function(a,o){var s={value:o},c={value:function(){--i===0&&a()}};n.each(function(){var n=K(this,r),i=n.on;i!==e&&(t=(e=i).copy(),t._.cancel.push(s),t._.interrupt.push(s),t._.end.push(c)),n.on=t}),i===0&&a()})}var yo=0;function J(e,t,n,r){this._groups=e,this._parents=t,this._name=n,this._id=r}function bo(){return++yo}var Y=xr.prototype;J.prototype={constructor:J,select:Za,selectAll:Qa,selectChild:Y.selectChild,selectChildren:Y.selectChildren,filter:Wa,merge:Ga,selection:eo,transition:_o,call:Y.call,nodes:Y.nodes,node:Y.node,size:Y.size,empty:Y.empty,each:Y.each,on:Ja,attr:Oa,attrTween:Na,style:oo,styleTween:lo,text:po,textTween:go,remove:Xa,tween:ya,delay:Ia,duration:za,ease:Va,easeVarying:Ua,end:vo,[Symbol.iterator]:Y[Symbol.iterator]};var xo={time:null,delay:0,duration:250,ease:na};function So(e,t){for(var n;!(n=e.__transition)||!(n=n[t]);)if(!(e=e.parentNode))throw Error(`transition ${t} not found`);return n}function Co(e){var t,n;e instanceof J?(t=e._id,e=e._name):(t=bo(),(n=xo).time=Hr(),e=e==null?null:e+``);for(var r=this._groups,i=r.length,a=0;a<i;++a)for(var o=r[a],s=o.length,c,l=0;l<s;++l)(c=o[l])&&fa(c,e,t,l,o,n||So(c,t));return new J(r,this._parents,e,t)}xr.prototype.interrupt=ga,xr.prototype.transition=Co;var wo={capture:!0,passive:!1};function To(e){e.preventDefault(),e.stopImmediatePropagation()}function Eo(e){var t=e.document.documentElement,n=F(e).on(`dragstart.drag`,To,wo);`onselectstart`in t?n.on(`selectstart.drag`,To,wo):(t.__noselect=t.style.MozUserSelect,t.style.MozUserSelect=`none`)}function Do(e,t){var n=e.document.documentElement,r=F(e).on(`dragstart.drag`,null);t&&(r.on(`click.drag`,To,wo),setTimeout(function(){r.on(`click.drag`,null)},0)),`onselectstart`in n?r.on(`selectstart.drag`,null):(n.style.MozUserSelect=n.__noselect,delete n.__noselect)}var Oo=e=>()=>e;function ko(e,{sourceEvent:t,target:n,transform:r,dispatch:i}){Object.defineProperties(this,{type:{value:e,enumerable:!0,configurable:!0},sourceEvent:{value:t,enumerable:!0,configurable:!0},target:{value:n,enumerable:!0,configurable:!0},transform:{value:r,enumerable:!0,configurable:!0},_:{value:i}})}function X(e,t,n){this.k=e,this.x=t,this.y=n}X.prototype={constructor:X,scale:function(e){return e===1?this:new X(this.k*e,this.x,this.y)},translate:function(e,t){return e===0&t===0?this:new X(this.k,this.x+this.k*e,this.y+this.k*t)},apply:function(e){return[e[0]*this.k+this.x,e[1]*this.k+this.y]},applyX:function(e){return e*this.k+this.x},applyY:function(e){return e*this.k+this.y},invert:function(e){return[(e[0]-this.x)/this.k,(e[1]-this.y)/this.k]},invertX:function(e){return(e-this.x)/this.k},invertY:function(e){return(e-this.y)/this.k},rescaleX:function(e){return e.copy().domain(e.range().map(this.invertX,this).map(e.invert,e))},rescaleY:function(e){return e.copy().domain(e.range().map(this.invertY,this).map(e.invert,e))},toString:function(){return`translate(`+this.x+`,`+this.y+`) scale(`+this.k+`)`}};var Ao=new X(1,0,0);jo.prototype=X.prototype;function jo(e){for(;!e.__zoom;)if(!(e=e.parentNode))return Ao;return e.__zoom}function Mo(e){e.stopImmediatePropagation()}function No(e){e.preventDefault(),e.stopImmediatePropagation()}function Po(e){return(!e.ctrlKey||e.type===`wheel`)&&!e.button}function Fo(){var e=this;return e instanceof SVGElement?(e=e.ownerSVGElement||e,e.hasAttribute(`viewBox`)?(e=e.viewBox.baseVal,[[e.x,e.y],[e.x+e.width,e.y+e.height]]):[[0,0],[e.width.baseVal.value,e.height.baseVal.value]]):[[0,0],[e.clientWidth,e.clientHeight]]}function Io(){return this.__zoom||Ao}function Lo(e){return-e.deltaY*(e.deltaMode===1?.05:e.deltaMode?1:.002)*(e.ctrlKey?10:1)}function Ro(){return navigator.maxTouchPoints||`ontouchstart`in this}function zo(e,t,n){var r=e.invertX(t[0][0])-n[0][0],i=e.invertX(t[1][0])-n[1][0],a=e.invertY(t[0][1])-n[0][1],o=e.invertY(t[1][1])-n[1][1];return e.translate(i>r?(r+i)/2:Math.min(0,r)||Math.max(0,i),o>a?(a+o)/2:Math.min(0,a)||Math.max(0,o))}function Bo(){var e=Po,t=Fo,n=zo,r=Lo,i=Ro,a=[0,1/0],o=[[-1/0,-1/0],[1/0,1/0]],s=250,c=ta,l=Or(`start`,`zoom`,`end`),u,d,f,p=500,m=150,h=0,g=10;function _(e){e.property(`__zoom`,Io).on(`wheel.zoom`,ee,{passive:!1}).on(`mousedown.zoom`,w).on(`dblclick.zoom`,te).filter(i).on(`touchstart.zoom`,ne).on(`touchmove.zoom`,re).on(`touchend.zoom touchcancel.zoom`,ie).style(`-webkit-tap-highlight-color`,`rgba(0,0,0,0)`)}_.transform=function(e,t,n,r){var i=e.selection?e.selection():e;i.property(`__zoom`,Io),e===i?i.interrupt().each(function(){S(this,arguments).event(r).start().zoom(null,typeof t==`function`?t.apply(this,arguments):t).end()}):x(e,t,n,r)},_.scaleBy=function(e,t,n,r){_.scaleTo(e,function(){return this.__zoom.k*(typeof t==`function`?t.apply(this,arguments):t)},n,r)},_.scaleTo=function(e,r,i,a){_.transform(e,function(){var e=t.apply(this,arguments),a=this.__zoom,s=i==null?b(e):typeof i==`function`?i.apply(this,arguments):i,c=a.invert(s),l=typeof r==`function`?r.apply(this,arguments):r;return n(y(v(a,l),s,c),e,o)},i,a)},_.translateBy=function(e,r,i,a){_.transform(e,function(){return n(this.__zoom.translate(typeof r==`function`?r.apply(this,arguments):r,typeof i==`function`?i.apply(this,arguments):i),t.apply(this,arguments),o)},null,a)},_.translateTo=function(e,r,i,a,s){_.transform(e,function(){var e=t.apply(this,arguments),s=this.__zoom,c=a==null?b(e):typeof a==`function`?a.apply(this,arguments):a;return n(Ao.translate(c[0],c[1]).scale(s.k).translate(typeof r==`function`?-r.apply(this,arguments):-r,typeof i==`function`?-i.apply(this,arguments):-i),e,o)},a,s)};function v(e,t){return t=Math.max(a[0],Math.min(a[1],t)),t===e.k?e:new X(t,e.x,e.y)}function y(e,t,n){var r=t[0]-n[0]*e.k,i=t[1]-n[1]*e.k;return r===e.x&&i===e.y?e:new X(e.k,r,i)}function b(e){return[(+e[0][0]+ +e[1][0])/2,(+e[0][1]+ +e[1][1])/2]}function x(e,n,r,i){e.on(`start.zoom`,function(){S(this,arguments).event(i).start()}).on(`interrupt.zoom end.zoom`,function(){S(this,arguments).event(i).end()}).tween(`zoom`,function(){var e=this,a=arguments,o=S(e,a).event(i),s=t.apply(e,a),l=r==null?b(s):typeof r==`function`?r.apply(e,a):r,u=Math.max(s[1][0]-s[0][0],s[1][1]-s[0][1]),d=e.__zoom,f=typeof n==`function`?n.apply(e,a):n,p=c(d.invert(l).concat(u/d.k),f.invert(l).concat(u/f.k));return function(e){if(e===1)e=f;else{var t=p(e),n=u/t[2];e=new X(n,l[0]-t[0]*n,l[1]-t[1]*n)}o.zoom(null,e)}})}function S(e,t,n){return!n&&e.__zooming||new C(e,t)}function C(e,n){this.that=e,this.args=n,this.active=0,this.sourceEvent=null,this.extent=t.apply(e,n),this.taps=0}C.prototype={event:function(e){return e&&(this.sourceEvent=e),this},start:function(){return++this.active===1&&(this.that.__zooming=this,this.emit(`start`)),this},zoom:function(e,t){return this.mouse&&e!==`mouse`&&(this.mouse[1]=t.invert(this.mouse[0])),this.touch0&&e!==`touch`&&(this.touch0[1]=t.invert(this.touch0[0])),this.touch1&&e!==`touch`&&(this.touch1[1]=t.invert(this.touch1[0])),this.that.__zoom=t,this.emit(`zoom`),this},end:function(){return--this.active===0&&(delete this.that.__zooming,this.emit(`end`)),this},emit:function(e){var t=F(this.that).datum();l.call(e,this.that,new ko(e,{sourceEvent:this.sourceEvent,target:_,transform:this.that.__zoom,dispatch:l}),t)}};function ee(t,...i){if(!e.apply(this,arguments))return;var s=S(this,i).event(t),c=this.__zoom,l=Math.max(a[0],Math.min(a[1],c.k*2**r.apply(this,arguments))),u=I(t);if(s.wheel)(s.mouse[0][0]!==u[0]||s.mouse[0][1]!==u[1])&&(s.mouse[1]=c.invert(s.mouse[0]=u)),clearTimeout(s.wheel);else if(c.k===l)return;else s.mouse=[u,c.invert(u)],ha(this),s.start();No(t),s.wheel=setTimeout(d,m),s.zoom(`mouse`,n(y(v(c,l),s.mouse[0],s.mouse[1]),s.extent,o));function d(){s.wheel=null,s.end()}}function w(t,...r){if(f||!e.apply(this,arguments))return;var i=t.currentTarget,a=S(this,r,!0).event(t),s=F(t.view).on(`mousemove.zoom`,d,!0).on(`mouseup.zoom`,p,!0),c=I(t,i),l=t.clientX,u=t.clientY;Eo(t.view),Mo(t),a.mouse=[c,this.__zoom.invert(c)],ha(this),a.start();function d(e){if(No(e),!a.moved){var t=e.clientX-l,r=e.clientY-u;a.moved=t*t+r*r>h}a.event(e).zoom(`mouse`,n(y(a.that.__zoom,a.mouse[0]=I(e,i),a.mouse[1]),a.extent,o))}function p(e){s.on(`mousemove.zoom mouseup.zoom`,null),Do(e.view,a.moved),No(e),a.event(e).end()}}function te(r,...i){if(e.apply(this,arguments)){var a=this.__zoom,c=I(r.changedTouches?r.changedTouches[0]:r,this),l=a.invert(c),u=a.k*(r.shiftKey?.5:2),d=n(y(v(a,u),c,l),t.apply(this,i),o);No(r),s>0?F(this).transition().duration(s).call(x,d,c,r):F(this).call(_.transform,d,c,r)}}function ne(t,...n){if(e.apply(this,arguments)){var r=t.touches,i=r.length,a=S(this,n,t.changedTouches.length===i).event(t),o,s,c,l;for(Mo(t),s=0;s<i;++s)c=r[s],l=I(c,this),l=[l,this.__zoom.invert(l),c.identifier],a.touch0?!a.touch1&&a.touch0[2]!==l[2]&&(a.touch1=l,a.taps=0):(a.touch0=l,o=!0,a.taps=1+!!u);u&&=clearTimeout(u),o&&(a.taps<2&&(d=l[0],u=setTimeout(function(){u=null},p)),ha(this),a.start())}}function re(e,...t){if(this.__zooming){var r=S(this,t).event(e),i=e.changedTouches,a=i.length,s,c,l,u;for(No(e),s=0;s<a;++s)c=i[s],l=I(c,this),r.touch0&&r.touch0[2]===c.identifier?r.touch0[0]=l:r.touch1&&r.touch1[2]===c.identifier&&(r.touch1[0]=l);if(c=r.that.__zoom,r.touch1){var d=r.touch0[0],f=r.touch0[1],p=r.touch1[0],m=r.touch1[1],h=(h=p[0]-d[0])*h+(h=p[1]-d[1])*h,g=(g=m[0]-f[0])*g+(g=m[1]-f[1])*g;c=v(c,Math.sqrt(h/g)),l=[(d[0]+p[0])/2,(d[1]+p[1])/2],u=[(f[0]+m[0])/2,(f[1]+m[1])/2]}else if(r.touch0)l=r.touch0[0],u=r.touch0[1];else return;r.zoom(`touch`,n(y(c,l,u),r.extent,o))}}function ie(e,...t){if(this.__zooming){var n=S(this,t).event(e),r=e.changedTouches,i=r.length,a,o;for(Mo(e),f&&clearTimeout(f),f=setTimeout(function(){f=null},p),a=0;a<i;++a)o=r[a],n.touch0&&n.touch0[2]===o.identifier?delete n.touch0:n.touch1&&n.touch1[2]===o.identifier&&delete n.touch1;if(n.touch1&&!n.touch0&&(n.touch0=n.touch1,delete n.touch1),n.touch0)n.touch0[1]=this.__zoom.invert(n.touch0[0]);else if(n.end(),n.taps===2&&(o=I(o,this),Math.hypot(d[0]-o[0],d[1]-o[1])<g)){var s=F(this).on(`dblclick.zoom`);s&&s.apply(this,arguments)}}}return _.wheelDelta=function(e){return arguments.length?(r=typeof e==`function`?e:Oo(+e),_):r},_.filter=function(t){return arguments.length?(e=typeof t==`function`?t:Oo(!!t),_):e},_.touchable=function(e){return arguments.length?(i=typeof e==`function`?e:Oo(!!e),_):i},_.extent=function(e){return arguments.length?(t=typeof e==`function`?e:Oo([[+e[0][0],+e[0][1]],[+e[1][0],+e[1][1]]]),_):t},_.scaleExtent=function(e){return arguments.length?(a[0]=+e[0],a[1]=+e[1],_):[a[0],a[1]]},_.translateExtent=function(e){return arguments.length?(o[0][0]=+e[0][0],o[1][0]=+e[1][0],o[0][1]=+e[0][1],o[1][1]=+e[1][1],_):[[o[0][0],o[0][1]],[o[1][0],o[1][1]]]},_.constrain=function(e){return arguments.length?(n=e,_):n},_.duration=function(e){return arguments.length?(s=+e,_):s},_.interpolate=function(e){return arguments.length?(c=e,_):c},_.on=function(){var e=l.on.apply(l,arguments);return e===l?_:e},_.clickDistance=function(e){return arguments.length?(h=(e=+e)*e,_):Math.sqrt(h)},_.tapDistance=function(e){return arguments.length?(g=+e,_):g},_}function Vo(e){var t=0,n=e.children,r=n&&n.length;if(!r)t=1;else for(;--r>=0;)t+=n[r].value;e.value=t}function Ho(){return this.eachAfter(Vo)}function Uo(e,t){let n=-1;for(let r of this)e.call(t,r,++n,this);return this}function Wo(e,t){for(var n=this,r=[n],i,a,o=-1;n=r.pop();)if(e.call(t,n,++o,this),i=n.children)for(a=i.length-1;a>=0;--a)r.push(i[a]);return this}function Go(e,t){for(var n=this,r=[n],i=[],a,o,s,c=-1;n=r.pop();)if(i.push(n),a=n.children)for(o=0,s=a.length;o<s;++o)r.push(a[o]);for(;n=i.pop();)e.call(t,n,++c,this);return this}function Ko(e,t){let n=-1;for(let r of this)if(e.call(t,r,++n,this))return r}function qo(e){return this.eachAfter(function(t){for(var n=+e(t.data)||0,r=t.children,i=r&&r.length;--i>=0;)n+=r[i].value;t.value=n})}function Jo(e){return this.eachBefore(function(t){t.children&&t.children.sort(e)})}function Yo(e){for(var t=this,n=Xo(t,e),r=[t];t!==n;)t=t.parent,r.push(t);for(var i=r.length;e!==n;)r.splice(i,0,e),e=e.parent;return r}function Xo(e,t){if(e===t)return e;var n=e.ancestors(),r=t.ancestors(),i=null;for(e=n.pop(),t=r.pop();e===t;)i=e,e=n.pop(),t=r.pop();return i}function Zo(){for(var e=this,t=[e];e=e.parent;)t.push(e);return t}function Qo(){return Array.from(this)}function $o(){var e=[];return this.eachBefore(function(t){t.children||e.push(t)}),e}function es(){var e=this,t=[];return e.each(function(n){n!==e&&t.push({source:n.parent,target:n})}),t}function*ts(){var e=this,t,n=[e],r,i,a;do for(t=n.reverse(),n=[];e=t.pop();)if(yield e,r=e.children)for(i=0,a=r.length;i<a;++i)n.push(r[i]);while(n.length)}function ns(e,t){e instanceof Map?(e=[void 0,e],t===void 0&&(t=as)):t===void 0&&(t=is);for(var n=new cs(e),r,i=[n],a,o,s,c;r=i.pop();)if((o=t(r.data))&&(c=(o=Array.from(o)).length))for(r.children=o,s=c-1;s>=0;--s)i.push(a=o[s]=new cs(o[s])),a.parent=r,a.depth=r.depth+1;return n.eachBefore(ss)}function rs(){return ns(this).eachBefore(os)}function is(e){return e.children}function as(e){return Array.isArray(e)?e[1]:null}function os(e){e.data.value!==void 0&&(e.value=e.data.value),e.data=e.data.data}function ss(e){var t=0;do e.height=t;while((e=e.parent)&&e.height<++t)}function cs(e){this.data=e,this.depth=this.height=0,this.parent=null}cs.prototype=ns.prototype={constructor:cs,count:Ho,each:Uo,eachAfter:Go,eachBefore:Wo,find:Ko,sum:qo,sort:Jo,path:Yo,ancestors:Zo,descendants:Qo,leaves:$o,links:es,copy:rs,[Symbol.iterator]:ts};function ls(e,t,n,r,i){for(var a=e.children,o,s=-1,c=a.length,l=e.value&&(r-t)/e.value;++s<c;)o=a[s],o.y0=n,o.y1=i,o.x0=t,o.x1=t+=o.value*l}function us(e,t){return e.parent===t.parent?1:2}function ds(e){var t=e.children;return t?t[0]:e.t}function fs(e){var t=e.children;return t?t[t.length-1]:e.t}function ps(e,t,n){var r=n/(t.i-e.i);t.c-=r,t.s+=n,e.c+=r,t.z+=n,t.m+=n}function ms(e){for(var t=0,n=0,r=e.children,i=r.length,a;--i>=0;)a=r[i],a.z+=t,a.m+=t,t+=a.s+(n+=a.c)}function hs(e,t,n){return e.a.parent===t.parent?e.a:n}function gs(e,t){this._=e,this.parent=null,this.children=null,this.A=null,this.a=this,this.z=0,this.m=0,this.c=0,this.s=0,this.t=null,this.i=t}gs.prototype=Object.create(cs.prototype);function _s(e){for(var t=new gs(e,0),n,r=[t],i,a,o,s;n=r.pop();)if(a=n._.children)for(n.children=Array(s=a.length),o=s-1;o>=0;--o)r.push(i=n.children[o]=new gs(a[o],o)),i.parent=n;return(t.parent=new gs(null,0)).children=[t],t}function vs(){var e=us,t=1,n=1,r=null;function i(i){var s=_s(i);if(s.eachAfter(a),s.parent.m=-s.z,s.eachBefore(o),r)i.eachBefore(c);else{var l=i,u=i,d=i;i.eachBefore(function(e){e.x<l.x&&(l=e),e.x>u.x&&(u=e),e.depth>d.depth&&(d=e)});var f=l===u?1:e(l,u)/2,p=f-l.x,m=t/(u.x+f+p),h=n/(d.depth||1);i.eachBefore(function(e){e.x=(e.x+p)*m,e.y=e.depth*h})}return i}function a(t){var n=t.children,r=t.parent.children,i=t.i?r[t.i-1]:null;if(n){ms(t);var a=(n[0].z+n[n.length-1].z)/2;i?(t.z=i.z+e(t._,i._),t.m=t.z-a):t.z=a}else i&&(t.z=i.z+e(t._,i._));t.parent.A=s(t,i,t.parent.A||r[0])}function o(e){e._.x=e.z+e.parent.m,e.m+=e.parent.m}function s(t,n,r){if(n){for(var i=t,a=t,o=n,s=i.parent.children[0],c=i.m,l=a.m,u=o.m,d=s.m,f;o=fs(o),i=ds(i),o&&i;)s=ds(s),a=fs(a),a.a=t,f=o.z+u-i.z-c+e(o._,i._),f>0&&(ps(hs(o,t,r),t,f),c+=f,l+=f),u+=o.m,c+=i.m,d+=s.m,l+=a.m;o&&!fs(a)&&(a.t=o,a.m+=u-l),i&&!ds(s)&&(s.t=i,s.m+=c-d,r=t)}return r}function c(e){e.x*=t,e.y=e.depth*n}return i.separation=function(t){return arguments.length?(e=t,i):e},i.size=function(e){return arguments.length?(r=!1,t=+e[0],n=+e[1],i):r?null:[t,n]},i.nodeSize=function(e){return arguments.length?(r=!0,t=+e[0],n=+e[1],i):r?[t,n]:null},i}function ys(e,t,n,r,i){for(var a=e.children,o,s=-1,c=a.length,l=e.value&&(i-n)/e.value;++s<c;)o=a[s],o.x0=t,o.x1=r,o.y0=n,o.y1=n+=o.value*l}var bs=(1+Math.sqrt(5))/2;function xs(e,t,n,r,i,a){for(var o=[],s=t.children,c,l,u=0,d=0,f=s.length,p,m,h=t.value,g,_,v,y,b,x,S;u<f;){p=i-n,m=a-r;do g=s[d++].value;while(!g&&d<f);for(_=v=g,x=Math.max(m/p,p/m)/(h*e),S=g*g*x,b=Math.max(v/S,S/_);d<f;++d){if(g+=l=s[d].value,l<_&&(_=l),l>v&&(v=l),S=g*g*x,y=Math.max(v/S,S/_),y>b){g-=l;break}b=y}o.push(c={value:g,dice:p<m,children:s.slice(u,d)}),c.dice?ls(c,n,r,i,h?r+=m*g/h:a):ys(c,n,r,h?n+=p*g/h:i,a),h-=g,u=d}return o}(function e(t){function n(e,n,r,i,a){xs(t,e,n,r,i,a)}return n.ratio=function(t){return e((t=+t)>1?t:1)},n})(bs),(function e(t){function n(e,n,r,i,a){if((o=e._squarify)&&o.ratio===t)for(var o,s,c,l,u=-1,d,f=o.length,p=e.value;++u<f;){for(s=o[u],c=s.children,l=s.value=0,d=c.length;l<d;++l)s.value+=c[l].value;s.dice?ls(s,n,r,i,p?r+=(a-r)*s.value/p:a):ys(s,n,r,p?n+=(i-n)*s.value/p:i,a),p-=s.value}else e._squarify=o=xs(t,e,n,r,i,a),o.ratio=t}return n.ratio=function(t){return e((t=+t)>1?t:1)},n})(bs);var Ss={comfortable:{breadth:132,depth:150},compact:{breadth:92,depth:112}},Cs=`\0root`,ws=()=>({positions:new Map,bounds:{minX:0,minY:0,maxX:0,maxY:0}}),Ts=(e,t,n)=>n===`vertical`?{x:e,y:t}:{x:t,y:e};function Es(e){let t={minX:1/0,minY:1/0,maxX:-1/0,maxY:-1/0};for(let n of e.values())t.minX=Math.min(t.minX,n.x),t.minY=Math.min(t.minY,n.y),t.maxX=Math.max(t.maxX,n.x),t.maxY=Math.max(t.maxY,n.y);return{positions:e,bounds:t}}function Ds(e,t,n,r=`tree`){return r===`tiered`?js(e,t,n):Os(e,t,n)}function Os(e,t,n){if(e.roots.length===0)return ws();let{breadth:r,depth:i}=Ss[t],a=ns(Cs,t=>t===Cs?e.roots:e.children.get(t)),o=vs().nodeSize([r,i]).separation((e,t)=>e.parent===t.parent?1:1.25)(a),s=new Map,c=new Map;for(let t of o.descendants()){if(t.data===Cs)continue;let a=(t.depth-1)*i;s.set(t.data,Ts(t.x,a,n));let o=e.uplinks.get(t.data)??[];if(o.length===0)continue;let l=c.get(a-i)??[];c.set(a-i,l),o.forEach((e,n)=>l.push({id:e,along:t.x+(n-(o.length-1)/2)*r}))}for(let[e,t]of c)t.sort((e,t)=>e.along-t.along),t.forEach((i,a)=>{a>0&&(i.along=Math.max(i.along,t[a-1].along+r)),s.set(i.id,Ts(i.along,e,n))});return Es(s)}var Z={wan:0,gateway:1,switch:2,access_point:3,other:4,client:5},ks=Z.client+1,As=e=>e.type===`group`?Z.client:Z[e.node.kind]??Z.other;function js(e,t,n){if(e.roots.length===0)return ws();let{breadth:r,depth:i}=Ss[t],a=new Map,o=[...e.roots].reverse(),s=[],c=Array(ks).fill(0);for(;o.length>0;){let t=o.pop();s.push(t);let n=As(e.visuals.get(t)),r=e.parentOf.get(t),i=r===void 0?void 0:a.get(r),l=Math.max(n,i?.tier??n),u={tier:l,sub:i?.tier===l?i.sub+1:0};a.set(t,u),c[u.tier]=Math.max(c[u.tier],u.sub+1);for(let n of e.uplinks.get(t)??[])s.push(n),a.set(n,{tier:Z.wan,sub:0}),c[Z.wan]=1;o.push(...[...e.children.get(t)??[]].reverse())}let l=[],u=0;for(let e of c)l.push(u),u+=e;let d=Array.from({length:u},()=>[]);for(let e of s){let{tier:t,sub:n}=a.get(e);d[l[t]+n].push(e)}let f=new Map;return d.forEach((e,t)=>{e.forEach((a,o)=>{let s=(o-(e.length-1)/2)*r;f.set(a,Ts(s,t*i,n))})}),Es(f)}function Ms(e,t,n,r=48){let i=Math.max(e.maxX-e.minX,1),a=Math.max(e.maxY-e.minY,1),o=Math.min(1.5,Math.max(.2,Math.min((t-2*r)/i,(n-2*r)/a))),s=(e.minX+e.maxX)/2,c=(e.minY+e.maxY)/2;return{k:o,x:t/2-s*o,y:n/2-c*o}}function Ns(e,t,n,r,i=120){let a=new Set;for(let[o,s]of e.positions){let e=s.x*t.k+t.x,c=s.y*t.k+t.y;e>=-i&&e<=n+i&&c>=-i&&c<=r+i&&a.add(o)}return a}function Ps(e,t,n,r){let i=r===`vertical`,a={parent:i?`ArrowUp`:`ArrowLeft`,child:i?`ArrowDown`:`ArrowRight`,previous:i?`ArrowLeft`:`ArrowUp`,next:i?`ArrowRight`:`ArrowDown`};if(!Object.values(a).includes(n))return;if(t===void 0||!e.visuals.has(t))return e.roots[0];let o=Ye(e,t),s=o.indexOf(t);switch(n){case a.parent:return e.parentOf.get(t)??e.uplinks.get(t)?.[0]??t;case a.child:return e.children.get(t)?.[0]??e.uplinkOwner.get(t)??t;case a.previous:return o[s-1]??t;default:return o[s+1]??t}}var Fs={comfortable:22,compact:16},Is=250,Ls=22;function Rs(e,t){if(e.type===`wheel`){let n=e;return t&&!n.ctrlKey&&!n.metaKey?`hint`:`zoom`}let n=e;return n.ctrlKey||(n.button??0)!==0?`ignore`:`zoom`}var zs=class extends O{static properties={model:{attribute:!1},density:{attribute:!1},orientation:{attribute:!1},layoutMode:{attribute:!1},showLabels:{attribute:!1},selectedId:{attribute:!1},localize:{attribute:!1},siteName:{attribute:!1},ctrlZoom:{attribute:!1},reducedMotion:{attribute:!1},focusId:{state:!0},hintVisible:{state:!0}};layout;entering=new Set;exiting=new Map;lastVisuals=new Map;exitTimer;hintTimer;transform={x:0,y:0,k:1};width=0;height=0;fitted=!1;zoomBehavior;resizeObserver;gesture=new wt;constructor(){super(),this.density=`comfortable`,this.orientation=`vertical`,this.layoutMode=`tree`,this.showLabels=!0,this.siteName=``,this.ctrlZoom=!0,this.reducedMotion=!1,this.hintVisible=!1}connectedCallback(){super.connectedCallback(),this.resizeObserver=new ResizeObserver(e=>{let t=e[0]?.contentRect;t&&this.setViewportSize(t.width,t.height)}),this.resizeObserver.observe(this)}disconnectedCallback(){super.disconnectedCallback(),this.resizeObserver?.disconnect(),this.exitTimer!==void 0&&clearTimeout(this.exitTimer),this.hintTimer!==void 0&&clearTimeout(this.hintTimer),this.exitTimer=void 0,this.hintTimer=void 0}setViewportSize(e,t){this.width=e,this.height=t,this.fitted?this.needsCulling&&this.requestUpdate():this.tryInitialFit()}get needsCulling(){return(this.layout?.positions.size??0)>300}get svgEl(){return this.renderRoot.querySelector(`svg.canvas`)}willUpdate(e){if(e.has(`layoutMode`)&&(this.fitted=!1),this.model&&(e.has(`model`)||e.has(`density`)||e.has(`orientation`)||e.has(`layoutMode`))){let e=Ds(this.model,this.density,this.orientation,this.layoutMode),t=this.layout;this.entering=new Set(t?[...e.positions.keys()].filter(e=>!t.positions.has(e)):[]);for(let t of e.positions.keys())this.exiting.delete(t);if(t&&!this.reducedMotion){for(let[n,r]of t.positions){let t=this.lastVisuals.get(n);!e.positions.has(n)&&t&&this.exiting.set(n,{point:r,visual:t})}this.scheduleExitCleanup()}this.layout=e,this.lastVisuals=new Map(this.model.visuals),this.focusId!==void 0&&this.model.visuals.has(this.focusId)||(this.focusId=this.model.roots[0])}}firstUpdated(){let e=this.svgEl;e&&(this.zoomBehavior=Bo().scaleExtent([.2,4]).extent(()=>[[0,0],[Math.max(this.width,1),Math.max(this.height,1)]]).filter(e=>{let t=Rs(e,this.ctrlZoom);return t===`hint`&&this.flashHint(),t===`zoom`}).on(`zoom`,e=>this.onZoom(e.transform)).on(`end`,()=>{this.needsCulling&&this.requestUpdate()}),F(e).call(this.zoomBehavior).on(`dblclick.zoom`,null),this.tryInitialFit())}updated(){this.tryInitialFit()}tryInitialFit(){this.fitted||!this.layout||!this.zoomBehavior||this.width<=0||this.height<=0||(this.fitted=!0,this.fit(!1))}onZoom(e){this.transform={x:e.x,y:e.y,k:e.k},this.renderRoot.querySelector(`g.viewport`)?.setAttribute(`transform`,this.transformAttr())}transformAttr(){let{x:e,y:t,k:n}=this.transform;return`translate(${e},${t}) scale(${n})`}fit(e=!0){let t=this.svgEl;if(!this.layout||!this.zoomBehavior||!t)return;let n=Ms(this.layout.bounds,this.width,this.height),r=Ao.translate(n.x,n.y).scale(n.k);e&&!this.reducedMotion?this.zoomBehavior.transform(F(t).transition().duration(300),r):this.zoomBehavior.transform(F(t),r)}zoomBy(e){let t=this.svgEl;this.zoomBehavior&&t&&(this.reducedMotion?this.zoomBehavior.scaleBy(F(t),e):this.zoomBehavior.scaleBy(F(t).transition().duration(200),e))}async focusNode(e){this.focusId=e,await this.updateComplete;let t=this.layout?.positions.get(e),n=this.svgEl;if(t&&n&&this.zoomBehavior&&this.width>0){let e=t.x*this.transform.k+this.transform.x,r=t.y*this.transform.k+this.transform.y;(e<40||e>this.width-40||r<40||r>this.height-40)&&(this.zoomBehavior.translateTo(F(n),t.x,t.y),await this.updateComplete)}for(let t of this.renderRoot.querySelectorAll(`g.nodes g.node`))t.getAttribute(`data-id`)===e&&t.focus()}flashHint(){this.hintVisible=!0,this.hintTimer!==void 0&&clearTimeout(this.hintTimer),this.hintTimer=setTimeout(()=>{this.hintVisible=!1},1500)}scheduleExitCleanup(){this.exiting.size!==0&&this.exitTimer===void 0&&(this.exitTimer=setTimeout(()=>{this.exitTimer=void 0,this.exiting.clear(),this.requestUpdate()},Is))}highlightedPath(){let e=new Set,t=this.model;if(!t||this.selectedId===void 0)return e;let n=t.visuals.has(this.selectedId)?this.selectedId:Xe(t,this.selectedId)?.id;for(;n!==void 0;)e.add(n),n=t.parentOf.get(n)??t.uplinkOwner.get(n);return e}onKeydown(e){let t=this.model;if(!t)return;let n=Ps(t,this.focusId,e.key,this.orientation);n===void 0?(e.key===`Enter`||e.key===` `)&&this.focusId!==void 0?(e.preventDefault(),w(this,`uit-activate`,{id:this.focusId})):e.key===`+`||e.key===`=`?(e.preventDefault(),this.zoomBy(1.25)):e.key===`-`?(e.preventDefault(),this.zoomBy(.8)):e.key===`0`?(e.preventDefault(),this.fit()):(e.key===`ContextMenu`||e.key===`F10`&&e.shiftKey)&&this.focusId!==void 0&&(e.preventDefault(),w(this,`uit-context`,{id:this.focusId})):(e.preventDefault(),this.focusNode(n))}onPointerDown(e){this.gesture.down(e)}onPointerMove(e){this.gesture.move(e)}onPointerEnd(e){this.gesture.end(e)}onContextMenu(e,t){e.preventDefault(),this.gesture.allowsMenu&&w(this,`uit-context`,{id:t})}render(){let{model:e,layout:t,localize:n}=this;if(!e||!t||!n)return D;let r=Fs[this.density],i=[...t.positions.keys()],a=[...e.links.values()],o=e.uplinkLinks;if(this.needsCulling&&this.width>0){let e=Ns(t,this.transform,this.width,this.height);this.focusId!==void 0&&e.add(this.focusId),this.selectedId!==void 0&&e.add(this.selectedId),i=i.filter(t=>e.has(t));let n=t=>e.has(t.childId)||e.has(t.parentId);a=a.filter(n),o=o.filter(n)}let s=this.highlightedPath();return T`
      <svg
        class="canvas ${this.reducedMotion?`still`:``}"
        role="application"
        aria-roledescription=${n(`graph.roledescription`)}
        aria-label=${n(`graph.label`,{site:this.siteName,devices:e.stats.devices,clients:e.stats.clients})}
        @keydown=${this.onKeydown}
        @pointerdown=${this.onPointerDown}
        @pointermove=${this.onPointerMove}
        @pointerup=${this.onPointerEnd}
        @pointercancel=${this.onPointerEnd}
      >
        <g class="viewport" transform=${this.transformAttr()}>
          <g class="links">
            ${M(a,e=>e.childId,e=>this.renderLink(e,t,s))}
            ${M(o,e=>e.parentId,e=>this.renderLink(e,t,s,!0))}
          </g>
          <g class="nodes">
            ${M(i,e=>e,n=>this.renderNode(e,e.visuals.get(n),t.positions.get(n),r,s,!0))}
          </g>
          <g class="exits" aria-hidden="true">
            ${M([...this.exiting],([e])=>e,([,t])=>this.renderGhost(e,t,r))}
          </g>
        </g>
      </svg>
      <div class="hint" aria-hidden="true" ?hidden=${!this.hintVisible}>
        ${n(`zoom.hint`)}
      </div>
    `}renderLink(e,t,n,r=!1){let i=t.positions.get(e.parentId),a=t.positions.get(e.childId);if(!i||!a)return D;let o=this.orientation===`vertical`?(i.y+a.y)/2:(i.x+a.x)/2,s=this.orientation===`vertical`?`M${i.x},${i.y} C${i.x},${o} ${a.x},${o} ${a.x},${a.y}`:`M${i.x},${i.y} C${o},${i.y} ${o},${a.y} ${a.x},${a.y}`,c=this.showLabels?ut(e.edge):``,l=[`link`,e.edge?.medium??`unknown`,e.viaHidden.length>0?`via-hidden`:``,n.has(r?e.parentId:e.childId)?`on-path`:``,r&&this.model?.nodes.get(e.parentId)?.state===`offline`?`down`:``];return j`<path class=${l.join(` `)} d=${s}></path>${c?j`<text class="link-label" x=${(i.x+a.x)/2} y=${(i.y+a.y)/2}>${c}</text>`:D}`}renderNode(e,t,n,r,i,a){let o=this.localize,s=t.id,c=mt(t),l=dt(t,o),u=t.type===`group`?`group`:t.node.kind,d=[`node`,t.type,u,c,s===this.selectedId?`selected`:``,i.has(s)?`on-path`:``,this.entering.has(s)?`enter`:``],f=t.type===`group`?ue:he(t.node),p=r+16;return j`<g
      class=${d.join(` `)}
      data-id=${s}
      role=${a?`button`:D}
      tabindex=${a?s===this.focusId?0:-1:D}
      aria-label=${a?ht(e,t,o):D}
      aria-expanded=${a&&t.type===`group`?String(t.expanded):D}
      style=${`transform: translate(${n.x}px, ${n.y}px)`}
      @click=${a?()=>w(this,`uit-activate`,{id:s}):D}
      @contextmenu=${a?e=>this.onContextMenu(e,s):D}
      @focus=${a?()=>{this.focusId=s}:D}
    >
      <title>${l}</title>
      <circle class="hit" r=${Math.max(r,22)}></circle>
      <circle class="ring" r=${r+5}></circle>
      <circle class="disc" r=${r}></circle>
      <svg class="glyph" x=${-r*.6} y=${-r*.6} width=${r*1.2} height=${r*1.2} viewBox="0 0 24 24" aria-hidden="true">
        <path d=${f}></path>
      </svg>
      <circle class="status" cx=${r*.72} cy=${-r*.72} r=${Math.max(4,r*.24)}></circle>
      ${t.type===`group`&&(!this.showLabels||t.id===`group:root`||t.id===`group:unconnected`)?j`<text class="badge" x=${r*.95} y=${r+2}>${t.counts.total}</text>`:D}
      ${this.showLabels?j`<text class="label" y=${p}>${nt(l,Ls)}</text>`:D}
      ${c===`offline`?j`<text class="state-text" y=${this.showLabels?p+14:p}>${o(pt(t))}</text>`:D}
    </g>`}renderGhost(e,t,n){return this.renderNode(e,t.visual,t.point,n,new Set,!1)}static styles=[_e,xe`
      :host {
        display: block;
        position: relative;
        flex: 1;
        min-width: 0;
        min-height: 0;
      }
      svg.canvas {
        display: block;
        width: 100%;
        height: 100%;
        touch-action: none;
        user-select: none;
      }
      .node {
        cursor: pointer;
        outline: none;
        transition: transform 250ms ease;
      }
      .still .node {
        transition: none;
      }
      .node.enter {
        animation: uit-fade-in 250ms ease;
      }
      .exits .node {
        animation: uit-fade-out 250ms ease forwards;
        pointer-events: none;
      }
      .still .node.enter,
      .still .exits .node {
        animation: none;
      }
      @keyframes uit-fade-in {
        from {
          opacity: 0;
        }
      }
      @keyframes uit-fade-out {
        to {
          opacity: 0;
        }
      }
      .hit {
        fill: transparent;
      }
      .ring {
        fill: none;
        stroke: none;
      }
      .node:focus-visible .ring {
        stroke: var(--uit-focus);
        stroke-width: 2;
      }
      .disc {
        fill: var(--card-background-color, #fff);
        stroke: color-mix(in srgb, var(--primary-color) 40%, var(--uit-line));
        stroke-width: 2;
        filter: drop-shadow(0 2px 5px rgba(0, 0, 0, 0.1));
      }
      .selected .disc,
      .on-path .disc {
        stroke: var(--uit-focus);
      }
      .selected .disc {
        stroke-width: 3;
      }
      .glyph path {
        fill: var(--primary-color);
      }
      .offline .disc,
      .offline .glyph {
        opacity: 0.55;
      }
      .status {
        fill: var(--uit-online);
        stroke: var(--card-background-color, #fff);
        stroke-width: 2;
      }
      .offline .status {
        fill: var(--uit-offline);
      }
      .unknown .status {
        fill: var(--uit-unknown);
      }
      text {
        font-size: 12px;
        font-weight: 500;
        fill: var(--primary-text-color);
        text-anchor: middle;
        dominant-baseline: hanging;
      }
      .badge {
        font-weight: 700;
        text-anchor: start;
      }
      .state-text {
        fill: var(--uit-offline);
        font-weight: 600;
      }
      .link {
        fill: none;
        stroke: color-mix(
          in srgb,
          var(--primary-color) 45%,
          var(--secondary-text-color)
        );
        stroke-opacity: 0.65;
        stroke-width: 1.75;
      }
      .link.wireless {
        stroke-dasharray: 2 4;
      }
      .link.via-hidden {
        stroke-dasharray: 8 4;
      }
      .link.down {
        stroke: var(--uit-offline);
        stroke-dasharray: 4 4;
      }
      .link.on-path {
        stroke: var(--uit-focus);
        stroke-opacity: 1;
        stroke-width: 2.5;
      }
      .link-label {
        font-size: 10px;
        fill: var(--secondary-text-color);
        paint-order: stroke;
        stroke: var(--card-background-color, #fff);
        stroke-width: 3;
      }
      .hint {
        position: absolute;
        left: 50%;
        bottom: 12px;
        transform: translateX(-50%);
        padding: 6px 12px;
        border-radius: 16px;
        background: var(--primary-text-color);
        color: var(--card-background-color, #fff);
        font-size: 12px;
        pointer-events: none;
      }
      .hint[hidden] {
        display: none;
      }
      @media (prefers-reduced-motion: reduce) {
        .node {
          transition: none;
        }
        .node.enter,
        .exits .node {
          animation: none;
        }
      }
    `]};p(`uit-graph-view`,zs);function Bs(e,t){return e.type===`group`?e.members.some(e=>e.name.toLowerCase().includes(t)):e.node.name.toLowerCase().includes(t)}function Vs(e,t,n){let r=n.trim().toLowerCase(),i;if(r){i=new Set;for(let[t,n]of e.visuals)if(Bs(n,r))for(let n=t;n!==void 0&&!i.has(n);n=e.parentOf.get(n)??e.uplinkOwner.get(n))i.add(n)}let a=[],o=(n,s,c)=>{let l=n.flatMap(t=>[...e.uplinks.get(t)??[],t]),u=i?l.filter(e=>i.has(e)):l;u.forEach((n,i)=>{let l=e.visuals.get(n),d=e.children.get(n)??[],f=l.type===`group`,p=f||d.length>0,m=f?l.expanded:r!==``||!t.has(n);a.push({id:n,visual:l,level:s,posinset:i+1,setsize:u.length,hasChildren:p,expanded:p&&m,parentId:c}),m&&d.length>0&&o(d,s+1,n)})};return o(e.roots,1,void 0),a}var Hs=class extends O{static properties={model:{attribute:!1},selectedId:{attribute:!1},localize:{attribute:!1},siteName:{attribute:!1},query:{state:!0},collapsed:{state:!0},focusId:{state:!0}};rows=[];gesture=new wt;typeahead=``;typeaheadTimer;constructor(){super(),this.siteName=``,this.query=``,this.collapsed=new Set}willUpdate(){this.model&&(this.rows=Vs(this.model,this.collapsed,this.query),this.rows.some(e=>e.id===this.focusId)||(this.focusId=this.rows[0]?.id))}render(){let{model:e,localize:t}=this;return!e||!t?D:T`
      <div class="search">
        <input
          type="search"
          .value=${this.query}
          placeholder=${t(`list.search`)}
          aria-label=${t(`list.search`)}
          @input=${e=>{this.query=e.target.value}}
        />
      </div>
      ${this.rows.length===0?T`<p class="empty">${t(`list.no_matches`)}</p>`:T`<div
            class="tree"
            role="tree"
            aria-label=${t(`list.label`,{site:this.siteName})}
            @keydown=${this.onKeydown}
          >
            ${M(this.rows,e=>e.id,n=>this.renderRow(e,n,t))}
          </div>`}
    `}renderRow(e,t,n){let r=t.visual,i=dt(r,n),a=mt(r);return T`<div
      class="row ${a} ${t.id===this.selectedId?`selected`:``}"
      role="treeitem"
      data-id=${t.id}
      aria-level=${t.level}
      aria-setsize=${t.setsize}
      aria-posinset=${t.posinset}
      aria-selected=${String(t.id===this.selectedId)}
      aria-expanded=${t.hasChildren?String(t.expanded):D}
      aria-label=${ht(e,r,n)}
      tabindex=${t.id===this.focusId?0:-1}
      style=${`--level: ${t.level}`}
      @click=${()=>w(this,`uit-activate`,{id:t.id})}
      @pointerdown=${e=>this.gesture.down(e)}
      @pointermove=${e=>this.gesture.move(e)}
      @pointerup=${e=>this.gesture.end(e)}
      @pointercancel=${e=>this.gesture.end(e)}
      @contextmenu=${e=>{e.preventDefault(),this.gesture.allowsMenu&&w(this,`uit-context`,{id:t.id})}}
      @focus=${()=>{this.focusId=t.id}}
    >
      <span
        class="chevron"
        aria-hidden="true"
        @click=${e=>{e.stopPropagation(),t.hasChildren&&this.toggle(t)}}
        >${t.hasChildren?E(t.expanded?ce:m):D}</span
      >
      ${E(r.type===`group`?ue:he(r.node))}
      <span class="name" title=${i}>${i}</span>
      ${r.type===`group`?T`<span class="count">${r.counts.total}</span>`:T`<span class="dot" aria-hidden="true"></span>${a===`online`?D:T`<span class="state-text"
                  >${n(pt(r))}</span
                >`}`}
    </div>`}toggle(e){if(e.visual.type===`group`){w(this,`uit-toggle-group`,{id:e.id});return}let t=new Set(this.collapsed);t.has(e.id)?t.delete(e.id):t.add(e.id),this.collapsed=t}onKeydown(e){let t=this.rows,n=t.findIndex(e=>e.id===this.focusId),r=t[n];if(!r)return;if(e.key===`ContextMenu`||e.key===`F10`&&e.shiftKey){e.preventDefault(),w(this,`uit-context`,{id:r.id});return}let i;switch(e.key){case`ArrowDown`:i=t[n+1]?.id;break;case`ArrowUp`:i=t[n-1]?.id;break;case`Home`:i=t[0]?.id;break;case`End`:i=t.at(-1)?.id;break;case`ArrowRight`:r.hasChildren&&!r.expanded?this.toggle(r):r.expanded&&(i=t[n+1]?.id);break;case`ArrowLeft`:r.expanded?this.toggle(r):i=r.parentId;break;case`Enter`:case` `:w(this,`uit-activate`,{id:r.id});break;default:e.key.length!==1||e.ctrlKey||e.metaKey||e.altKey||(e.preventDefault(),this.typeAhead(e.key,n));return}e.preventDefault(),i!==void 0&&this.focusRow(i)}typeAhead(e,t){this.typeahead+=e.toLowerCase(),this.typeaheadTimer!==void 0&&clearTimeout(this.typeaheadTimer),this.typeaheadTimer=setTimeout(()=>{this.typeahead=``},500);let n=this.rows;for(let e=1;e<=n.length;e++){let r=n[(t+e)%n.length];if(dt(r.visual,this.localize).toLowerCase().startsWith(this.typeahead)){this.focusRow(r.id);return}}}async focusRow(e){this.focusId=e,await this.updateComplete;let t=this.renderRoot.querySelectorAll(`[role="treeitem"]`);for(let n of t)n.getAttribute(`data-id`)===e&&n.focus()}static styles=[_e,C,xe`
      :host {
        display: flex;
        flex-direction: column;
        flex: 1;
        min-height: 0;
        min-width: 0;
      }
      .search {
        padding: 8px 12px;
      }
      .search input {
        width: 100%;
      }
      .tree {
        overflow: auto;
        flex: 1;
        padding: 0 4px 8px;
      }
      .row {
        display: flex;
        align-items: center;
        gap: 8px;
        min-height: 44px;
        padding-inline-start: calc((var(--level) - 1) * 20px + 4px);
        padding-inline-end: 12px;
        border-radius: 8px;
        cursor: pointer;
      }
      .row.selected {
        background: color-mix(in srgb, var(--uit-focus) 16%, transparent);
      }
      .row.offline .icon,
      .row.offline .name {
        opacity: 0.55;
      }
      .chevron {
        width: 24px;
        display: inline-flex;
      }
      .name {
        flex: 1;
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .dot {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: var(--uit-online);
        flex: none;
      }
      .offline .dot {
        background: var(--uit-offline);
      }
      .unknown .dot {
        background: var(--uit-unknown);
      }
      .state-text {
        color: var(--uit-offline);
        font-weight: 600;
        font-size: 0.85em;
      }
      .count {
        color: var(--secondary-text-color);
      }
      .empty {
        padding: 16px;
        color: var(--secondary-text-color);
      }
    `]};p(`uit-list-view`,Hs);var Us=600,Ws=`/config/integrations/integration/unifi_insights`,Gs={integration:`action.integration`,edit:`action.edit`},Ks={loading:{key:`state.loading`},no_sources:{key:`state.no_sources`,action:`integration`},unconfigured:{key:`state.unconfigured`,action:`edit`},empty:{key:`state.empty`},incompatible:{key:`state.incompatible`},reloading:{key:`state.reconnecting`}},qs=e=>e?.nodes.filter(e=>e.kind!==`client`&&e.kind!==`wan`&&e.state===`offline`).length??0,Js=class extends O{static properties={hass:{attribute:!1},layout:{attribute:!1},config:{state:!0},sources:{state:!0},snapshot:{state:!0},lastGood:{state:!0},error:{state:!0},incompatible:{state:!0},disconnected:{state:!0},ui:{state:!0},view:{state:!0},selectedId:{state:!0},menuRequest:{state:!0},siteOverride:{state:!0},narrow:{state:!0},reducedMotion:{state:!0},announcement:{state:!0}};subscription=new Se({onSnapshot:e=>this.applySnapshot(e),onError:e=>{this.error=e},onIncompatible:()=>{this.incompatible=!0},onDisconnected:()=>{this.disconnected=!0},onReconnected:()=>{this.hass&&this.loadSources(this.hass)}});details=new Oe(()=>this.requestUpdate());selectionScope;buildModel=Je();announcer=new je(e=>{this.announcement=e});sourcesFor;sourcesPending=!1;sourcesRetry;sourcesAttempt=0;sourcesError;boundKey;cardState={phase:`loading`,stale:!1,notices:[]};model;resizeObserver;motionQuery;localizeLang;localizeFn;constructor(){super(),this.incompatible=!1,this.disconnected=!1,this.ui={kinds:new Set(A),clients:`collapsed`,toggledGroups:new Set},this.view=`graph`,this.narrow=!1,this.reducedMotion=!1,this.announcement=``}setConfig(t){let n=e(i(t));this.config=n,this.view=n.view,this.ui={kinds:new Set(n.kinds),clients:n.clients,toggledGroups:new Set},this.siteOverride=void 0}getCardSize(){return 8}getGridOptions(){return{columns:12,rows:8,min_columns:6,min_rows:4}}static getConfigElement(){return document.createElement(d)}static async getStubConfig(e){try{let t=a(await e.callWS({type:oe}))[0];if(t)return{type:f,...t.binding}}catch{}return{type:f}}connectedCallback(){super.connectedCallback(),this.resizeObserver=new ResizeObserver(e=>{let t=e[0]?.contentRect.width??0;this.narrow=t>0&&t<Us}),this.resizeObserver.observe(this),this.motionQuery=window.matchMedia(`(prefers-reduced-motion: reduce)`),this.motionQuery.addEventListener(`change`,this.onMotionChange),this.onMotionChange(),this.sync()}disconnectedCallback(){super.disconnectedCallback(),this.subscription.stop(),this.details.stop(),this.menuRequest=void 0,this.announcer.dispose(),this.resizeObserver?.disconnect(),this.resizeObserver=void 0,this.motionQuery?.removeEventListener(`change`,this.onMotionChange),this.sourcesFor=void 0,this.clearSourcesRetry()}shouldUpdate(e){if(e.size!==1||!e.has(`hass`))return!0;let t=e.get(`hass`),n=this.hass;return!t||!n||t.connection!==n.connection||(t.locale?.language??t.language)!==(n.locale?.language??n.language)||k(t)!==k(n)}willUpdate(e){(e.has(`hass`)||e.has(`config`)||e.has(`sources`)||e.has(`siteOverride`))&&this.sync();let t=this.config;if(!t)return;this.cardState=Ie({sources:this.sources,binding:this.binding,snapshot:this.snapshot,lastGood:this.lastGood,error:this.error,incompatible:this.incompatible,disconnected:this.disconnected,maxClients:t.max_clients}),this.model=this.cardState.render?this.buildModel(this.cardState.render,this.ui):void 0;let n=this.selectedId;n!==void 0&&!(this.model?.visuals.has(n)||this.model?.nodes.has(n))&&this.deselect();let r=this.binding,i=r&&this.selectedId!==void 0?{...r,node_id:this.selectedId}:void 0,a=JSON.stringify(i);a!==this.selectionScope&&(this.selectionScope=a,JSON.stringify(this.menuRequest)!==a&&(this.menuRequest=void 0)),this.details.select(this.isConnected?this.hass:void 0,i)}get binding(){return this.config?this.siteOverride??u(this.config,this.sources):void 0}get localize(){let e=this.hass?.locale?.language??this.hass?.language??`en`;return e===this.localizeLang&&this.localizeFn||(this.localizeLang=e,this.localizeFn=ye(e)),this.localizeFn}get graphView(){return this.renderRoot.querySelector(`uit-graph-view`)}sync(){let{hass:e,config:t}=this;if(!this.isConnected||!e||!t)return;this.sourcesFor!==e.connection&&(this.sourcesFor=e.connection,this.disconnected=!1,this.clearSourcesRetry(),this.sourcesAttempt=0,this.loadSources(e));let n=this.binding,r=n?`${n.entry_id}\u0000${n.site_id}`:void 0;r!==this.boundKey&&(this.boundKey=r,this.snapshot=void 0,this.lastGood=void 0,this.error=void 0,this.sourcesError=void 0,this.incompatible=!1,this.deselect()),this.subscription.update(e.connection,n?{...n,max_clients:t.max_clients}:void 0)}async loadSources(e){if(this.sourcesPending)return;this.sourcesPending=!0,this.clearSourcesRetry();let t=e.connection;try{let n=await e.callWS({type:oe});if(this.sourcesFor!==t)return;this.sources=n,this.sourcesError&&this.error===this.sourcesError&&(this.error=void 0),this.sourcesError=void 0,n.length>0?this.sourcesAttempt=0:this.scheduleSourcesRetry()}catch(e){if(this.sourcesFor!==t)return;let n=this.sourcesError;this.sourcesError=ae(e),this.error&&this.error!==n||(this.error=this.sourcesError),this.scheduleSourcesRetry()}finally{this.sourcesPending=!1,this.isConnected&&this.sourcesFor!==void 0&&this.sourcesFor!==t&&this.hass&&this.loadSources(this.hass)}}scheduleSourcesRetry(){this.isConnected&&(this.sourcesRetry=setTimeout(()=>{this.sourcesRetry=void 0,this.hass&&this.loadSources(this.hass)},Te(this.sourcesAttempt++)))}clearSourcesRetry(){this.sourcesRetry!==void 0&&clearTimeout(this.sourcesRetry),this.sourcesRetry=void 0}applySnapshot(e){let t=this.snapshot;this.snapshot=e,this.error=void 0,this.incompatible=!1,this.disconnected=!1,e.status!==`unavailable`&&e.nodes.length>0&&(this.lastGood=e),this.hass&&this.sources!==void 0&&!this.sources.some(t=>t.entry_id===e.entry_id)&&this.loadSources(this.hass);let n=this.localize,r=n(`announce.updated`),i=e.issues[0],a=qs(e);if(e.status===`unavailable`&&i){let t=Pe(i,e,this.config?.max_clients??0);r=n(t.key,t.vars)}else a>0&&a!==qs(t)&&(r=n(`announce.offline`,{count:a}));this.announcer.announce(r)}onMotionChange=()=>{this.reducedMotion=this.motionQuery?.matches??!1};onActivate=e=>{let t=e.detail.id;this.model?.visuals.get(t)?.type===`group`&&this.toggleGroup(t),this.selectedId=t};onSelect=e=>{let t=e.detail.id,n=this.model;if(n&&!n.visuals.has(t)){let e=Xe(n,t);e&&!e.expanded&&this.toggleGroup(e.id)}this.selectedId=t};onToggleGroup=e=>{this.toggleGroup(e.detail.id)};onContext=e=>{let t=e.detail.id;if(!this.model?.visuals.has(t)&&!this.model?.nodes.has(t))return;this.onSelect(e);let n=this.binding;this.menuRequest=n&&!t.startsWith(`group:`)?{...n,node_id:t}:void 0};onClose=()=>{this.deselect()};onKeydown=e=>{e.key===`Escape`&&this.selectedId!==void 0&&!this.renderRoot.querySelector(`uit-detail-panel`)?.menuOpen&&(e.stopPropagation(),this.deselect())};deselect(){this.selectedId=void 0,this.menuRequest=void 0}toggleGroup(e){let t=new Set(this.ui.toggledGroups);t.has(e)?t.delete(e):t.add(e),this.ui={...this.ui,toggledGroups:t}}toggleKind(e){let t=new Set(this.ui.kinds);if(t.has(e)){if(t.size===1)return;t.delete(e)}else t.add(e);this.ui={...this.ui,kinds:t}}runAction(e){ne(e===`integration`?Ws:`${location.pathname}?edit=1`)}render(){let e=this.config;if(!e)return D;let t=this.localize,n=this.cardState,r=this.model,i=e.title??n.render?.site_name??this.snapshot?.site_name??t(`card.name`),a=n.render?.nodes??[],o=a.filter(e=>e.kind!==`client`&&e.kind!==`wan`).length,s=a.filter(e=>e.kind===`client`).length;return T`<ha-card>
      <div
        class="card ${this.narrow?`narrow`:``}"
        @keydown=${this.onKeydown}
        @uit-activate=${this.onActivate}
        @uit-select=${this.onSelect}
        @uit-context=${this.onContext}
        @uit-toggle-group=${this.onToggleGroup}
        @uit-close=${this.onClose}
      >
        <header>
          <div class="header-icon" aria-hidden="true">
            ${E(me)}
          </div>
          <div class="header-text">
            <h2 class="title" title=${i}>${i}</h2>
            ${a.length>0?T`<div class="subtitle">
                  ${t(`header.summary`,{devices:o,clients:s})}
                </div>`:D}
          </div>
          ${this.renderSiteSelector(e,t)}
        </header>
        ${r?this.renderToolbar(t):D}
        ${r?this.renderNotices(n.notices,t):D}
        <div class="body">
          ${r?this.renderContent(r,n,e,t):this.renderMessage(n,t)}
        </div>
        <div class="sr-only" role="status" aria-live="polite">
          ${this.announcement}
        </div>
      </div>
    </ha-card>`}renderSiteSelector(e,t){let n=a(this.sources??[]);if(!e.show_site_selector||n.length<2)return D;let r=this.binding;return T`<label class="site">
      <span class="sr-only">${t(`toolbar.site`)}</span>
      <select
        @change=${e=>{let t=n[Number(e.target.value)];t&&(this.siteOverride=t.binding)}}
      >
        ${n.map((e,t)=>T`<option
              value=${t}
              ?selected=${l(e.binding,r)}
            >
              ${e.label}
            </option>`)}
      </select>
    </label>`}renderToolbar(e){let t=T`
      <div class="group" role="group" aria-label=${e(`toolbar.view`)}>
        ${n.map(t=>T`<button
              aria-pressed=${String(this.view===t)}
              @click=${()=>{this.view=t}}
            >
              ${e(et[t])}
            </button>`)}
      </div>
      <div class="group" role="group" aria-label=${e(`toolbar.filters`)}>
        ${A.filter(e=>this.config?.show_wan!==!1||e!==`wan`).map(t=>T`<button
              aria-pressed=${String(this.ui.kinds.has(t))}
              @click=${()=>this.toggleKind(t)}
            >
              ${e(Ze[t])}
            </button>`)}
      </div>
      ${this.view===`graph`?T`<div
            class="group"
            role="group"
            aria-label=${e(`toolbar.zoom`)}
          >
            ${this.iconButton(S,e(`zoom.in`),()=>this.graphView?.zoomBy(1.25))}
            ${this.iconButton(_,e(`zoom.out`),()=>this.graphView?.zoomBy(.8))}
            ${this.iconButton(x,e(`zoom.fit`),()=>this.graphView?.fit())}
          </div>`:D}
    `;return this.narrow?T`<details class="toolbar">
          <summary>${e(`toolbar.options`)}</summary>
          <div class="controls">${t}</div>
        </details>`:T`<div class="toolbar">
          <div class="controls">${t}</div>
        </div>`}iconButton(e,t,n){return T`<button
      class="icon-button"
      aria-label=${t}
      title=${t}
      @click=${n}
    >
      ${E(e)}
    </button>`}actionButton(e,t){return T`<button class="action" @click=${()=>this.runAction(e)}>
      ${t(Gs[e])}
    </button>`}renderNotices(e,t){return e.length===0?D:T`<ul class="notices">
      ${e.map(e=>T`<li class=${e.severity}>
            <span>${t(e.key,e.vars)}</span>${e.action?this.actionButton(e.action,t):D}
          </li>`)}
    </ul>`}renderMessage(e,t){let n=Ks[e.phase],r={site:this.snapshot?.site_name??``},i=[];return n&&i.push({...n,vars:r}),i.push(...e.notices),T`<div class="message ${e.phase}">
      ${e.phase===`loading`?T`<svg class="skeleton" viewBox="0 0 120 60" aria-hidden="true">
            <path d="M60 18 L30 37 M60 18 L90 37"></path>
            <circle cx="60" cy="10" r="8"></circle>
            <circle cx="30" cy="45" r="8"></circle>
            <circle cx="90" cy="45" r="8"></circle>
          </svg>`:D}
      ${i.map(e=>T`<p>${t(e.key,e.vars)}</p>
            ${e.action?this.actionButton(e.action,t):D}`)}
    </div>`}renderContent(e,t,n,r){let i=n.density??(this.narrow?`compact`:`comfortable`),a=t.render?.site_name??``,o=t.phase===`reloading`?`${r(`state.stale`)} · ${r(`state.reconnecting`)}`:r(`state.stale`);return T`<div class="content ${t.stale?`stale`:``}">
      ${t.stale?T`<span class="badge stale">${o}</span>`:D}
      ${this.view===`graph`?T`<uit-graph-view
            .model=${e}
            .density=${i}
            .orientation=${n.orientation}
            .layoutMode=${n.layout}
            .showLabels=${n.show_labels}
            .selectedId=${this.selectedId}
            .localize=${r}
            .siteName=${a}
            .ctrlZoom=${this.layout!==`panel`}
            .reducedMotion=${this.reducedMotion}
          ></uit-graph-view>`:T`<uit-list-view
            .model=${e}
            .selectedId=${this.selectedId}
            .localize=${r}
            .siteName=${a}
          ></uit-list-view>`}
      <uit-detail-panel
        .model=${e}
        .selectedId=${this.selectedId}
        .hass=${this.hass}
        .menuRequest=${this.menuRequest}
        .details=${this.details.state.status===`ready`?this.details.state.details:void 0}
        .localize=${r}
        ?narrow=${this.narrow}
      ></uit-detail-panel>
    </div>`}static styles=[_e,C,xe`
      :host {
        display: block;
        height: 100%;
      }
      ha-card {
        height: 100%;
        display: flex;
        flex-direction: column;
        overflow: hidden;
        container-type: inline-size;
      }
      .card {
        position: relative;
        display: flex;
        flex-direction: column;
        flex: 1;
        min-height: 0;
      }
      header {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 14px 16px 6px;
      }
      .header-icon {
        width: 38px;
        height: 38px;
        border-radius: 12px;
        display: grid;
        place-items: center;
        background: color-mix(in srgb, var(--primary-color) 15%, transparent);
        color: var(--primary-color);
        flex: none;
      }
      .header-text {
        flex: 1;
        min-width: 0;
      }
      .title {
        margin: 0;
        font-size: 1.05rem;
        font-weight: 600;
        line-height: 1.25;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .subtitle {
        font-size: 0.76rem;
        color: var(--secondary-text-color);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .toolbar {
        padding: 4px 16px 8px;
      }
      details.toolbar > summary {
        min-height: 44px;
        display: inline-flex;
        align-items: center;
        gap: 6px;
        cursor: pointer;
        padding: 0 14px;
        border-radius: 999px;
        border: 1px solid var(--uit-line);
        background: color-mix(
          in srgb,
          var(--primary-text-color) 5%,
          transparent
        );
        font-size: 0.82rem;
        font-weight: 600;
        list-style: none;
      }
      details.toolbar > summary::-webkit-details-marker {
        display: none;
      }
      details.toolbar[open] > .controls {
        margin-top: 8px;
      }
      .controls {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 8px 16px;
      }
      .group {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
      }
      .icon-button {
        padding: 0;
        justify-content: center;
      }
      .notices {
        list-style: none;
        margin: 4px 16px;
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .notices li {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 6px 10px;
        border-radius: 8px;
        border-inline-start: 4px solid var(--uit-warning);
        background: color-mix(in srgb, var(--uit-warning) 12%, transparent);
      }
      .notices li.info {
        border-color: var(--uit-focus);
        background: color-mix(in srgb, var(--uit-focus) 10%, transparent);
      }
      .notices li.error {
        border-color: var(--uit-offline);
        background: color-mix(in srgb, var(--uit-offline) 12%, transparent);
      }
      .notices li span {
        flex: 1;
      }
      .body {
        position: relative;
        display: flex;
        flex: 1 1 280px;
        min-height: 0;
        margin: 4px 12px 12px;
        border-radius: 14px;
        border: 1px solid var(--uit-line);
        background-color: color-mix(
          in srgb,
          var(--primary-text-color) 2.5%,
          transparent
        );
        background-image: radial-gradient(
          color-mix(in srgb, var(--primary-text-color) 12%, transparent) 1px,
          transparent 1px
        );
        background-size: 18px 18px;
        overflow: hidden;
      }
      .content {
        position: relative;
        display: flex;
        flex: 1;
        min-width: 0;
        min-height: 0;
      }
      .content.stale uit-graph-view,
      .content.stale uit-list-view {
        opacity: 0.55;
        filter: grayscale(1);
      }
      .badge.stale {
        position: absolute;
        top: 8px;
        left: 12px;
        z-index: 1;
        padding: 2px 10px;
        border-radius: 12px;
        background: var(--card-background-color);
        border: 1px solid var(--uit-line);
        font-size: 0.85em;
      }
      .message {
        margin: auto;
        padding: 24px;
        text-align: center;
        color: var(--secondary-text-color);
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 8px;
      }
      .message p {
        margin: 0;
      }
      .skeleton {
        width: 120px;
        fill: var(--uit-line);
        stroke: var(--uit-line);
        stroke-width: 2;
        animation: uit-pulse 1.5s ease-in-out infinite;
      }
      @keyframes uit-pulse {
        50% {
          opacity: 0.4;
        }
      }
      @container (width < 600px) {
        header {
          padding: 8px 12px 0;
        }
        .body {
          flex-basis: 240px;
        }
      }
    `]},Ys=`current`,Xs=``,Zs={collapsed:`clients.collapsed`,expanded:`clients.expanded`,hidden:`clients.hidden`},Qs={auto:`density.auto`,comfortable:`density.comfortable`,compact:`density.compact`},$s={vertical:`orientation.vertical`,horizontal:`orientation.horizontal`},ec={tree:`layout.tree`,tiered:`layout.tiered`},tc={site:`editor.site`,title:`editor.title`,view:`editor.view`,clients:`editor.clients`,kinds:`editor.kinds`,density:`editor.density`,orientation:`editor.orientation`,layout:`editor.layout`,show_site_selector:`editor.show_site_selector`,show_labels:`editor.show_labels`,show_wan:`editor.show_wan`,max_clients:`editor.max_clients`};function Q(e,t,n){return e.map(e=>({value:e,label:n(t[e])}))}var $=(e,t)=>typeof e==`string`&&t.includes(e);function nc(e,t){let{entry_id:n,site_id:r}=e;if(n===void 0||r===void 0)return Xs;let i=t.findIndex(e=>l(e.binding,{entry_id:n,site_id:r}));return i>=0?String(i):Ys}function rc(e,i,a){let c=i.map((e,t)=>({value:String(t),label:e.label}));return nc(e,i)===`current`&&c.push({value:Ys,label:a(`editor.site_unavailable`,{site:e.site_id??``})}),[{name:`site`,selector:{select:{mode:`dropdown`,options:c}}},{name:`title`,selector:{text:{}}},{name:``,type:`grid`,schema:[{name:`view`,selector:{select:{mode:`dropdown`,options:Q(n,et,a)}}},{name:`clients`,selector:{select:{mode:`dropdown`,options:Q(o,Zs,a)}}},{name:`density`,selector:{select:{mode:`dropdown`,options:Q([`auto`,...t],Qs,a)}}},{name:`orientation`,selector:{select:{mode:`dropdown`,options:Q(r,$s,a)}}},{name:`layout`,selector:{select:{mode:`dropdown`,options:Q(s,ec,a)}}}]},{name:`kinds`,selector:{select:{multiple:!0,mode:`list`,options:Q(A,Ze,a)}}},{name:`show_site_selector`,selector:{boolean:{}}},{name:`show_labels`,selector:{boolean:{}}},{name:`show_wan`,selector:{boolean:{}}},{name:`max_clients`,selector:{number:{min:1,max:500,mode:`box`}}}]}function ic(e,t){return{site:nc(e,t),title:e.title??``,view:e.view??`graph`,clients:e.clients??`collapsed`,density:e.density??`auto`,orientation:e.orientation??`vertical`,layout:e.layout??`tree`,kinds:e.kinds?[...e.kinds]:[...A],show_site_selector:e.show_site_selector??!1,show_labels:e.show_labels??!0,show_wan:e.show_wan??!0,max_clients:e.max_clients??500}}function ac(e,i,a){let c={...i},l=e.site??Xs;if(l===Xs)delete c.entry_id,delete c.site_id;else if(l!==`current`){let e=a[Number(l)];e&&(c.entry_id=e.binding.entry_id,c.site_id=e.binding.site_id)}e.title?c.title=e.title:delete c.title,$(e.view,n)&&(c.view=e.view),$(e.clients,o)&&(c.clients=e.clients),$(e.orientation,r)&&(c.orientation=e.orientation),e.layout===`tree`?delete c.layout:$(e.layout,s)&&(c.layout=e.layout),$(e.density,t)?c.density=e.density:e.density===`auto`&&delete c.density;let u=(e.kinds??[]).filter(e=>$(e,A));u.length===A.length?delete c.kinds:u.length>0&&(c.kinds=A.filter(e=>u.includes(e))),typeof e.show_site_selector==`boolean`&&(c.show_site_selector=e.show_site_selector),typeof e.show_labels==`boolean`&&(c.show_labels=e.show_labels),e.show_wan===!0?delete c.show_wan:e.show_wan===!1&&(c.show_wan=!1);let d=e.max_clients;return d===500?delete c.max_clients:typeof d==`number`&&Number.isInteger(d)&&d>=1&&d<=500&&(c.max_clients=d),c}async function oc(e=customElements,t=window.loadCardHelpers){e.get(`ha-form`)||await((await t?.())?.createCardElement({type:`entities`,entities:[]})?.constructor)?.getConfigElement?.()}var sc=class extends O{static properties={hass:{attribute:!1},config:{state:!0},sources:{state:!0},formReady:{state:!0}};sourcesRequested=!1;constructor(){super(),this.formReady=!1}setConfig(e){this.config={...e}}connectedCallback(){super.connectedCallback(),oc().catch(()=>void 0).then(()=>{this.formReady=!0})}willUpdate(){this.hass&&!this.sourcesRequested&&(this.sourcesRequested=!0,this.hass.callWS({type:oe}).then(e=>{this.sources=e},()=>{this.sources=[]}))}render(){let{hass:e,config:t}=this;if(!e||!t)return D;let n=ye(e.locale?.language??e.language);if(!this.formReady)return T`<p>${n(`state.loading`)}</p>`;let r=a(this.sources??[]);return T`<ha-form
      .hass=${e}
      .data=${ic(t,r)}
      .schema=${rc(t,r,n)}
      .computeLabel=${e=>{let t=tc[e.name];return t?n(t):``}}
      @value-changed=${this.onValueChanged}
    ></ha-form>`}onValueChanged=e=>{e.stopPropagation();let t=this.config;if(!t)return;let n=e.detail.value,r=ac(n,t,a(this.sources??[]));this.config=r,w(this,`config-changed`,{config:r})}};p(c,Js),p(d,sc);var cc=ye(`en`);window.customCards??=[],window.customCards.some(e=>e.type===`unifi-insights-topology-card`)||window.customCards.push({type:c,name:cc(`card.name`),description:cc(`card.description`),preview:!0,documentationURL:`https://github.com/tinkerologie/ha-unifi-insights#network-topology-card`});