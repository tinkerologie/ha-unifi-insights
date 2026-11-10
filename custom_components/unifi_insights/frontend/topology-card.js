import{$ as e,A as t,At as n,Bt as r,C as i,Ct as a,D as o,Dt as s,E as c,Et as l,F as u,Ft as d,Ht as f,I as p,It as m,J as h,L as g,Lt as _,M as v,Mt as y,N as b,Nt as x,O as S,Ot as ee,P as te,Pt as ne,R as re,Rt as C,S as ie,St as ae,T as oe,Tt as se,Vt as ce,X as le,Z as ue,_t as de,at as fe,bt as pe,gt as me,ht as he,it as ge,j as _e,jt as ve,k as w,kt as ye,lt as be,mt as T,q as xe,r as Se,rt as Ce,st as we,tt as Te,vt as Ee,w as De,wt as Oe,x as ke,xt as Ae,yt as je,zt as Me}from"./chunks/register-dashboard-card-DEafGS50.js";import"./internet-activity-card.js";import"./performance-card.js";import"./protect-status-card.js";import"./site-health-card.js";import"./timeline-card.js";var Ne={status:`idle`},Pe=class{onChange;current=Ne;keyId;connection;generation=0;constructor(e){this.onChange=e}get state(){return this.current}select(e,t){let n=t&&w(e)&&!t.node_id.startsWith(`group:`)?JSON.stringify([t.entry_id,t.site_id,t.node_id]):void 0,r=e?.connection;if(n===this.keyId&&r===this.connection||(this.stop(),this.keyId=n,this.connection=r,!e||!t||n===void 0))return;let i=this.generation;this.current={status:`loading`};let a={type:Me,...t};this.fetch(e,a,i)}stop(){this.generation++,this.current=Ne,this.keyId=void 0,this.connection=void 0}async fetch(e,t,n){let r;try{r={status:`ready`,details:await e.callWS(t)}}catch{r=Ne}n===this.generation&&(this.current=r,this.onChange())}},Fe=e=>typeof e==`string`&&e!==``;function Ie(e,t){if(!t||!e)return[];let n=[];return Fe(e.ip_address)&&n.push(`copy_ip`),Fe(e.mac_address)&&n.push(`copy_mac`),Fe(e.device_id)&&n.push(`open_device`),Fe(e.entity_id)&&n.push(`open_entity`),n}var Le=class{last=-1/0;pending;timer;emit;intervalMs;constructor(e,t=5e3){this.emit=e,this.intervalMs=t}announce(e){let t=this.last+this.intervalMs-Date.now();t<=0&&this.timer===void 0?(this.last=Date.now(),this.emit(e)):(this.pending=e,this.timer??=setTimeout(()=>{this.timer=void 0;let e=this.pending;this.pending=void 0,e!==void 0&&(this.last=Date.now(),this.emit(e))},Math.max(t,0)))}dispose(){this.timer!==void 0&&clearTimeout(this.timer),this.timer=void 0,this.pending=void 0}},Re={[m]:{key:`issue.site_unavailable`},[y]:{key:`issue.devices_unavailable`,action:`integration`},[ne]:{key:`issue.legacy_uplink_missing`},[d]:{key:`issue.parents_unresolved`},[ve]:{key:`issue.clients_truncated`},[x]:{key:`issue.entry_unloaded`}},ze={[ee]:{key:`error.entry_not_found`,action:`edit`},[n]:{key:`error.site_not_selected`,action:`edit`},[ye]:{key:`error.entry_not_loaded`,action:`integration`}};function Be(e,t,n){let r=Re[e.code];if(!r)return{code:e.code,severity:e.severity,key:`issue.unknown`,vars:{code:e.code}};let i={code:e.code,severity:e.severity,key:r.key};return e.code===`parents_unresolved`&&(i.vars={count:t.unresolved.length}),e.code===`clients_truncated`&&t.truncation&&(i.vars={included:t.truncation.clients_included,total:t.truncation.clients_total,max:n}),r.action&&(i.action=r.action),i}function Ve(e){let t=ze[e.code];if(!t)return{code:e.code,severity:`error`,key:`error.unknown`,vars:{code:e.code}};let n={code:e.code,severity:`error`,key:t.key};return t.action&&(n.action=t.action),n}function He(e){if(e.incompatible)return{phase:`incompatible`,stale:!1,notices:[]};if(e.error)return{phase:`error`,render:e.lastGood,stale:e.lastGood!==void 0,notices:[Ve(e.error)]};if(e.disconnected){let t=e.snapshot,n=t&&t.nodes.length>0?t:e.lastGood;if(n)return{phase:`reloading`,render:n,stale:!0,notices:[]}}if(e.sources!==void 0&&e.sources.length===0&&e.snapshot===void 0)return{phase:`no_sources`,stale:!1,notices:[]};if(e.binding===void 0)return{phase:e.sources===void 0?`loading`:`unconfigured`,stale:!1,notices:[]};let t=e.snapshot;if(t===void 0)return{phase:`loading`,stale:!1,notices:[]};let n=t.issues.map(n=>Be(n,t,e.maxClients));if(t.status===`unavailable`){let r=t.issues.some(e=>e.code===x),i=t.nodes.length>0?t:e.lastGood;return{phase:r?`reloading`:`unavailable`,render:i,stale:i!==void 0,notices:n}}return t.nodes.length===0?{phase:`empty`,stale:!1,notices:n}:{phase:t.status===`partial`?`partial`:`ok`,render:t,stale:!1,notices:n}}var Ue=new Set([ye,`unknown_command`]),We=2e3,Ge=6e4;function Ke(e,t=Math.random){let n=Math.min(We*2**e,Ge);return Math.round(n*(.8+.4*t()))}var qe=class{generation=0;connection;key;keyId;unsubscribe;retryTimer;attempt=0;lastRevision;isLive=!1;handlers;random;constructor(e,t=Math.random){this.handlers=e,this.random=t}get live(){return this.isLive}update(e,t){let n=t?JSON.stringify([t.entry_id,t.site_id,t.max_clients]):void 0;(e!==this.connection||n!==this.keyId)&&(this.stop(),this.connection=e,this.key=t,this.keyId=n,e&&(e.addEventListener(`ready`,this.onReady),e.addEventListener(`disconnected`,this.onDisconnected)),e&&t&&this.open())}stop(){this.generation++,this.clearRetry(),this.attempt=0,this.release(),this.connection?.removeEventListener(`ready`,this.onReady),this.connection?.removeEventListener(`disconnected`,this.onDisconnected),this.connection=void 0,this.key=void 0,this.keyId=void 0}clearRetry(){this.retryTimer!==void 0&&clearTimeout(this.retryTimer),this.retryTimer=void 0}drop(){this.generation++,this.clearRetry(),this.unsubscribe=void 0,this.isLive=!1,this.lastRevision=void 0}onDisconnected=()=>{this.drop(),this.handlers.onDisconnected()};onReady=()=>{this.drop(),this.attempt=0,this.key&&this.open(),this.handlers.onReconnected()};release(){let e=this.unsubscribe;this.unsubscribe=void 0,this.isLive=!1,this.lastRevision=void 0,e&&e().catch(()=>void 0)}open(){let e=++this.generation,{connection:t,key:n}=this;t&&n&&t.subscribeMessage(t=>{e===this.generation&&this.receive(t)},{type:ce,entry_id:n.entry_id,site_id:n.site_id,max_clients:n.max_clients},{resubscribe:!1}).then(t=>{e===this.generation?this.unsubscribe=t:t().catch(()=>void 0)},t=>{if(e!==this.generation)return;let n=_e(t);this.handlers.onError(n),Ue.has(n.code)&&this.scheduleRetry()})}receive(e){let t;try{t=f(e)}catch(e){e instanceof _?this.handlers.onIncompatible():this.handlers.onError({code:`invalid_payload`,message:String(e)});return}let n=t.issues.some(e=>e.code===x);n||(this.isLive=!0,this.attempt=0),(t.revision===``||t.revision!==this.lastRevision)&&(this.lastRevision=t.revision,this.handlers.onSnapshot(t),n&&(this.generation++,this.release(),this.scheduleRetry()))}scheduleRetry(){let e=this.generation,t=Ke(this.attempt++,this.random);this.retryTimer=setTimeout(()=>{this.retryTimer=void 0,e===this.generation&&this.open()},t)}},Je=`group:unconnected`,Ye=`group:root`,Xe=e=>`group:${e}`,Ze={wan:0,gateway:1,switch:2,access_point:3,other:4,client:5},Qe=(e,t)=>e.name.localeCompare(t.name)||e.id.localeCompare(t.id),$e=(e,t)=>Ze[e.kind]-Ze[t.kind]||Qe(e,t);function et(e,t){let n=e.toggledGroups.has(t);return e.clients===`expanded`?!n:n}var tt=()=>({total:0,wired:0,wireless:0,offline:0});function nt(e,t){e.total++,t.connection===`wired`&&e.wired++,t.connection===`wireless`&&e.wireless++,t.state===`offline`&&e.offline++}function rt(e,t,n){let r=new Set;for(let i of e){let e=new Set,a=i.id;for(;a!==void 0&&!r.has(a);){e.add(a);let r=t.get(a);if(r!==void 0&&e.has(r)){t.delete(a),n.delete(a);break}a=r}for(let t of e)r.add(t)}}function it(e,t){let n=new Map(e.nodes.map(e=>[e.id,e])),r=new Map,i=new Map,a=new Map;for(let t of e.edges){let e=n.get(t.source),o=n.get(t.target);o?.kind===`wan`?e&&e.kind!==`wan`&&!a.has(o.id)&&a.set(o.id,{owner:e.id,edge:t}):e?.kind!==`wan`&&e&&o&&e.id!==o.id&&o.kind!==`client`&&!r.has(e.id)&&(r.set(e.id,o.id),i.set(e.id,t))}rt(e.nodes,r,i);let o=e.nodes.filter(e=>e.kind!==`client`&&e.kind!==`wan`).sort($e),s=e.nodes.filter(e=>e.kind===`client`).sort(Qe),c=new Map;for(let e of s){let t=r.get(e.id);if(t===void 0)continue;let n=c.get(t);n||c.set(t,n=tt()),nt(n,e)}let l=new Map,u=new Map,d=new Map,f=new Map,p=[],m=e=>t.kinds.has(e.kind),h=e=>{let t=[],i=r.get(e);for(;i!==void 0;){let e=n.get(i);if(m(e))return{id:i,skipped:t};t.push(e.kind),i=r.get(i)}return{id:void 0,skipped:t}},g=(e,t,n,r)=>{if(t===void 0){p.push(e);return}d.set(e,t),f.set(e,{childId:e,parentId:t,edge:n,viaHidden:[...new Set(r)]});let i=u.get(t);i?i.push(e):u.set(t,[e])};for(let e of o){if(!m(e))continue;l.set(e.id,{type:`device`,id:e.id,node:e});let t=h(e.id);g(e.id,t.id,i.get(e.id),t.skipped)}let _=new Map,v=new Map,y=[];for(let t of e.nodes){let e=a.get(t.id);if(!e||!m(t)||!l.has(e.owner))continue;l.set(t.id,{type:`device`,id:t.id,node:t});let n=_.get(e.owner);n?n.push(t.id):_.set(e.owner,[t.id]),v.set(t.id,e.owner),y.push({childId:e.owner,parentId:t.id,edge:e.edge,viaHidden:[]})}if(t.clients!==`hidden`&&t.kinds.has(`client`)){let e=new Map;for(let t of s){let i=r.get(t.id),a,o=[],s;if(i===void 0)s=Je;else{let e=n.get(i);if(m(e))a=i;else{let t=h(i);a=t.id,o=[e.kind,...t.skipped]}s=a===void 0?Ye:Xe(a)}let c=e.get(s);c||e.set(s,c={parent:a,members:[],skipped:new Set}),c.members.push(t);for(let e of o)c.skipped.add(e)}let a=[...e].sort(([e],[t])=>Number(e===Je)-Number(t===Je));for(let[e,n]of a){let r=tt();for(let e of n.members)nt(r,e);let a=et(t,e);if(l.set(e,{type:`group`,id:e,members:n.members,counts:r,expanded:a}),g(e,n.parent,void 0,n.skipped),a)for(let t of n.members)l.set(t.id,{type:`client`,id:t.id,node:t}),g(t.id,e,i.get(t.id),[])}}return{snapshot:e,roots:p,visuals:l,children:u,parentOf:d,links:f,nodes:n,realParent:r,edges:i,clientCounts:c,uplinks:_,uplinkOwner:v,uplinkLinks:y,stats:{devices:o.length,clients:s.length,offlineDevices:o.filter(e=>e.state===`offline`).length}}}function at(){let e,t,n;return(r,i)=>(n&&r===e&&i===t||(e=r,t=i,n=it(r,i)),n)}function ot(e,t){let n=e.uplinkOwner.get(t);if(n!==void 0)return e.uplinks.get(n);let r=e.parentOf.get(t);return r===void 0?e.roots:e.children.get(r)??[]}function st(e,t){for(let n of e.visuals.values())if(n.type===`group`&&n.members.some(e=>e.id===t))return n}var ct={wan:`kind.wan`,gateway:`kind.gateway`,switch:`kind.switch`,access_point:`kind.access_point`,client:`kind.client`,other:`kind.other`},lt={online:`state.online`,offline:`state.offline`,unknown:`state.unknown`},ut={online:`state.connected`,offline:`state.disconnected`,unknown:`state.unknown`},dt={graph:`view.graph`,list:`view.list`},ft={wired:`medium.wired`,wireless:`medium.wireless`,unknown:`medium.unknown`};function pt(e,t){let n=[...e];return n.length>t?`${n.slice(0,t-1).join(``)}…`:e}var mt=e=>Number.isInteger(e)?String(e):e.toFixed(1);function ht(e){return e>=1e3?`${mt(e/1e3)}G`:`${e}M`}function gt(e){return e>=1e3?`${mt(e/1e3)} gigabit`:`${e} megabit`}function _t(e){if(!e)return``;let t=[];return e.parent_port!==void 0&&t.push(`p${e.parent_port}`),e.speed_mbps&&t.push(ht(e.speed_mbps)),e.poe_power_w!==void 0&&t.push(`PoE`),t.join(` · `)}function vt(e,t){return e.type===`group`?e.id===`group:unconnected`?t(`group.unconnected`):e.id===`group:root`?t(`group.root`):e.counts.total===1?t(`group.client_one`):t(`group.clients`,{count:e.counts.total}):e.node.name}function yt(e,t){return e?.kind===`wan`?ut[t]:lt[t]}function bt(e){return yt(e.type===`group`?void 0:e.node,xt(e))}function xt(e){return e.type===`group`?e.counts.offline===e.counts.total?`offline`:`online`:e.node.state}function St(e,t,n){if(t.type===`group`)return`${vt(t,n)}: ${n(`group.summary`,{wireless:t.counts.wireless,offline:t.counts.offline})}`;let r=t.node,i=[n(`node.label`,{kind:n(ct[r.kind]),name:r.name,state:n(yt(r,r.state)).toLocaleLowerCase()})],a=e.uplinkOwner.get(r.id);a!==void 0&&i.push(n(`node.wan_of`,{device:e.nodes.get(a).name}));let o=e.clientCounts.get(r.id)?.total??0;o>0&&i.push(o===1?n(`node.client_one`):n(`node.clients`,{count:o}));let s=e.edges.get(r.id);return s?.parent_port===void 0?r.connection&&i.push(n(ft[r.connection]).toLocaleLowerCase()):i.push(s.speed_mbps?n(`node.uplink_speed`,{port:s.parent_port,speed:gt(s.speed_mbps)}):n(`node.uplink`,{port:s.parent_port})),i.join(`, `)}var Ct={ATTRIBUTE:1,CHILD:2,PROPERTY:3,BOOLEAN_ATTRIBUTE:4,EVENT:5,ELEMENT:6},wt=e=>(...t)=>({_$litDirective$:e,values:t}),Tt=class{constructor(e){}get _$AU(){return this._$AM._$AU}_$AT(e,t,n){this._$Ct=e,this._$AM=t,this._$Ci=n}_$AS(e,t){return this.update(e,t)}update(e,t){return this.render(...t)}},{I:Et}=p,Dt=e=>e,Ot=()=>document.createComment(``),kt=(e,t,n)=>{let r=e._$AA.parentNode,i=t===void 0?e._$AB:t._$AA;if(n===void 0)n=new Et(r.insertBefore(Ot(),i),r.insertBefore(Ot(),i),e,e.options);else{let t=n._$AB.nextSibling,a=n._$AM,o=a!==e;if(o){let t;n._$AQ?.(e),n._$AM=e,n._$AP!==void 0&&(t=e._$AU)!==a._$AU&&n._$AP(t)}if(t!==i||o){let e=n._$AA;for(;e!==t;){let t=Dt(e).nextSibling;Dt(r).insertBefore(e,i),e=t}}}return n},E=(e,t,n=e)=>(e._$AI(t,n),e),At={},jt=(e,t=At)=>e._$AH=t,Mt=e=>e._$AH,Nt=e=>{e._$AR(),e._$AA.remove()},Pt=wt(class extends Tt{constructor(){super(...arguments),this.key=b}render(e,t){return this.key=e,t}update(e,[t,n]){return t!==this.key&&(jt(e),this.key=t),n}});function Ft(){let e=document.activeElement;for(;e?.shadowRoot?.activeElement;)e=e.shadowRoot.activeElement;return e instanceof HTMLElement?e:null}function It(e,t){let n=Ft(),r=document.createElement(`textarea`);r.value=e,r.setAttribute(`readonly`,``),r.setAttribute(`aria-hidden`,`true`),r.style.cssText=`position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;pointer-events:none`,t.append(r);try{return r.focus(),r.select(),typeof document.execCommand==`function`&&document.execCommand(`copy`)}catch{return!1}finally{r.remove(),n?.focus()}}async function Lt(e,t=document.body){if(navigator.clipboard?.writeText)try{return await navigator.clipboard.writeText(e),!0}catch{}return It(e,t)}var Rt=(e,t,n)=>{let r=new Map;for(let i=t;i<=n;i++)r.set(e[i],i);return r},D=wt(class extends Tt{constructor(e){if(super(e),e.type!==Ct.CHILD)throw Error(`repeat() can only be used in text expressions`)}dt(e,t,n){let r;n===void 0?n=t:t!==void 0&&(r=t);let i=[],a=[],o=0;for(let t of e)i[o]=r?r(t,o):o,a[o]=n(t,o),o++;return{values:a,keys:i}}render(e,t,n){return this.dt(e,t,n).values}update(e,[t,n,r]){let i=Mt(e),{values:a,keys:o}=this.dt(t,n,r);if(!Array.isArray(i))return this.ut=o,a;let s=this.ut??=[],c=[],l,u,d=0,f=i.length-1,p=0,m=a.length-1;for(;d<=f&&p<=m;)if(i[d]===null)d++;else if(i[f]===null)f--;else if(s[d]===o[p])c[p]=E(i[d],a[p]),d++,p++;else if(s[f]===o[m])c[m]=E(i[f],a[m]),f--,m--;else if(s[d]===o[m])c[m]=E(i[d],a[m]),kt(e,c[m+1],i[d]),d++,m--;else if(s[f]===o[p])c[p]=E(i[f],a[p]),kt(e,i[d],i[f]),f--,p++;else if(l===void 0&&(l=Rt(o,p,m),u=Rt(s,d,f)),l.has(s[d])){if(l.has(s[f])){let t=u.get(o[p]),n=t===void 0?null:i[t];if(n===null){let t=kt(e,i[d]);E(t,a[p]),c[p]=t}else c[p]=E(n,a[p]),kt(e,i[d],n),i[t]=null;p++}else Nt(i[f]),f--}else Nt(i[d]),d++;for(;p<=m;){let t=kt(e,c[m+1]);E(t,a[p]),c[p++]=t}for(;d<=f;){let e=i[d++];e!==null&&Nt(e)}return this.ut=o,jt(e,c),te}}),zt={copy_ip:`menu.copy_ip`,copy_mac:`menu.copy_mac`,open_device:`detail.open_device`,open_entity:`menu.open_entity`},Bt={copy_ip:ue,copy_mac:ue,open_device:we,open_entity:Ce},Vt=0;T(`uit-node-menu`,class extends v{static properties={actions:{attribute:!1},localize:{attribute:!1},open:{state:!0}};menuId=`uit-node-menu-${++Vt}`;listening=!1;tabClose;constructor(){super(),this.actions=[],this.open=!1}get trigger(){return this.renderRoot.querySelector(`button.trigger`)}get menu(){return this.renderRoot.querySelector(`[role="menu"]`)}get items(){return[...this.renderRoot.querySelectorAll(`[role="menuitem"]`)]}get focusedItem(){let e=this.renderRoot.activeElement;return e instanceof HTMLElement&&this.menu?.contains(e)?e:null}get hasContent(){return this.actions.length>0}connectedCallback(){super.connectedCallback(),this.listenOutside(this.open)}disconnectedCallback(){super.disconnectedCallback(),this.hide(!1),this.listenOutside(!1)}async show(e=`first`){if(!this.hasContent)return;clearTimeout(this.tabClose),this.open=!0,await this.updateComplete;let t=this.items,n=t[e===`first`?0:t.length-1];for(let e of t)e.tabIndex=e===n?0:-1;n?.focus()}hide(e=!0){clearTimeout(this.tabClose),this.tabClose=void 0,this.open&&(this.open=!1,e&&this.trigger?.focus())}willUpdate(){this.hasContent||(this.open=!1)}updated(e){e.has(`open`)&&this.listenOutside(this.open)}listenOutside(e){e!==this.listening&&(this.listening=e,e?document.addEventListener(`pointerdown`,this.onOutsidePress,!0):document.removeEventListener(`pointerdown`,this.onOutsidePress,!0))}onOutsidePress=e=>{e.composedPath().includes(this)||this.hide(!1)};onTriggerKeydown(e){e.key===`ArrowDown`||e.key===`ArrowUp`?(e.preventDefault(),this.show(e.key===`ArrowDown`?`first`:`last`)):e.key===`Escape`&&this.open&&(this.swallow(e),this.hide())}swallow(e){e.preventDefault(),e.stopPropagation()}onMenuKeydown(e){e.stopPropagation();let t=this.items,n=this.focusedItem?t.indexOf(this.focusedItem):-1,r;switch(e.key){case`ArrowDown`:r=(n+1)%t.length;break;case`ArrowUp`:r=n<=0?t.length-1:n-1;break;case`Home`:r=0;break;case`End`:r=t.length-1;break;case`Escape`:this.swallow(e),this.hide();return;case`Tab`:this.tabClose=setTimeout(()=>this.hide(!1),0);return;default:return}e.preventDefault();for(let e of t)e.tabIndex=e===t[r]?0:-1;t[r]?.focus()}choose(e){this.hide(),o(this,`uit-action`,{action:e})}render(){let{localize:t}=this;if(!t||!this.hasContent)return b;let n=t(`menu.label`);return u`<button
        class="trigger"
        type="button"
        aria-haspopup="menu"
        aria-expanded=${String(this.open)}
        aria-controls=${this.menuId}
        aria-label=${n}
        title=${n}
        @click=${e=>{e.stopPropagation(),this.open?this.hide():this.show()}}
        @keydown=${e=>this.onTriggerKeydown(e)}
      >
        ${i(e)}<span>${n}</span>
      </button>
      ${this.open?u`<div
            id=${this.menuId}
            class="menu"
            role="menu"
            aria-label=${n}
            @click=${e=>e.stopPropagation()}
            @pointerdown=${e=>e.stopPropagation()}
            @contextmenu=${e=>e.stopPropagation()}
            @keydown=${e=>this.onMenuKeydown(e)}
          >
            ${D(this.actions,e=>e,e=>u`<button
                  type="button"
                  role="menuitem"
                  tabindex="-1"
                  data-action=${e}
                  @click=${()=>this.choose(e)}
                >
                  ${i(Bt[e])}<span
                    >${t(zt[e])}</span
                  >
                </button>`)}
          </div>`:b}`}static styles=[c,De,oe]});var Ht=class extends v{static properties={model:{attribute:!1},selectedId:{attribute:!1},localize:{attribute:!1},hass:{attribute:!1},menuRequest:{attribute:!1},details:{attribute:!1},narrow:{type:Boolean,reflect:!0},memberQuery:{state:!0},modal:{state:!0},copyStatus:{state:!0}};pressedBackdrop=!1;menuPending=!1;copyScope;copyGeneration=0;constructor(){super(),this.narrow=!1,this.memberQuery=``,this.modal=!1}disconnectedCallback(){super.disconnectedCallback(),this.closeMenu(),this.menuPending=!1,this.copyGeneration++,this.copyStatus=void 0,this.renderRoot.querySelector(`dialog`)?.close()}willUpdate(e){e.has(`selectedId`)&&(this.memberQuery=``);let t=JSON.stringify([this.model?.snapshot.entry_id,this.model?.snapshot.site_id,this.selectedId,w(this.hass)]);if(t!==this.copyScope&&(this.closeMenu(),this.menuPending=!1,this.copyScope=t,this.copyGeneration++,this.copyStatus=void 0),this.selectedId===void 0&&(this.modal=!1),e.has(`menuRequest`)){let e=this.menuRequest;this.menuPending=e!==void 0&&e.node_id===this.selectedId&&e.entry_id===this.model?.snapshot.entry_id&&e.site_id===this.model?.snapshot.site_id}}updated(e){if(!this.isConnected)return;let t=this.renderRoot.querySelector(`dialog`);if(t)t.open||t.showModal();else if(e.has(`selectedId`)||e.has(`narrow`)||e.has(`details`)){let e=this.renderRoot.querySelector(`.panel`);if(e&&e.scrollHeight>e.clientHeight+1){this.modal=!0;return}}this.openRequestedMenu()}closeMenu(){this.renderRoot.querySelector(`uit-node-menu`)?.hide(!1)}get menuOpen(){return this.renderRoot.querySelector(`uit-node-menu`)?.open===!0}openRequestedMenu(){this.menuPending&&this.details&&(this.menuPending=!1,this.renderRoot.querySelector(`uit-node-menu`)?.show())}close(){this.closeMenu(),this.menuPending=!1;let e=this.renderRoot.querySelector(`dialog`);e?e.close():o(this,`uit-close`)}render(){let{model:e,selectedId:t,localize:n}=this;if(!e||!t||!n)return b;let r=e.visuals.get(t);if(r?.type===`group`)return this.shell(vt(r,n),this.groupBody(r,n));let i=e.nodes.get(t);if(!i)return b;let a=i.kind===`client`?this.clientBody(e,i,n):this.deviceBody(e,i,n);return this.shell(i.name,a)}shell(e,t){let n=this.panel(e,t);return this.modal?u`<dialog
      aria-labelledby="title"
      @close=${()=>{this.closeMenu(),this.menuPending=!1,o(this,`uit-close`)}}
      @cancel=${e=>{this.menuOpen&&(e.preventDefault(),this.renderRoot.querySelector(`uit-node-menu`)?.hide())}}
      @keydown=${e=>{e.key===`Escape`&&(e.preventDefault(),e.stopPropagation(),this.close())}}
      @pointerdown=${{capture:!0,handleEvent:e=>{this.pressedBackdrop=e.target===e.currentTarget}}}
      @click=${e=>{e.target===e.currentTarget&&this.pressedBackdrop&&this.close()}}
    >
      ${n}
    </dialog>`:n}panel(e,t){let n=this.localize;return u`<section
      class="panel"
      role=${this.modal?b:`region`}
      aria-label=${this.modal?b:e}
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
          ${i(le)}
        </button>
      </header>
      ${t}${this.statusRegion(n)}
    </section>`}menu(e,t){let n=Ie(this.details,w(this.hass));return n.length===0?b:u`${Pt(e.id,u`<uit-node-menu
        .actions=${n}
        .localize=${t}
        @uit-action=${e=>this.runAction(e.detail.action)}
      ></uit-node-menu>`)}`}statusRegion(e){let t=this.copyStatus?.value===void 0||w(this.hass)?this.copyStatus:void 0;return u`<div
      class="status ${t?`has-message`:``}"
      role="status"
      aria-live="polite"
    >
      ${t?u`<span>${e(t.key,t.vars)}</span
            >${t.value===void 0?b:u`<code class="manual">${t.value}</code>`}`:b}
    </div>`}runAction(e){if(!w(this.hass))return;let t=this.details;switch(e){case`copy_ip`:this.copy(t?.ip_address,`menu.copied_ip`);break;case`copy_mac`:this.copy(t?.mac_address,`menu.copied_mac`);break;case`open_device`:{let e=t?.device_id;e&&this.openDevice(e);break}case`open_entity`:t?.entity_id&&(this.renderRoot.querySelector(`dialog`)?.close(),S(this,t.entity_id))}}async copy(e,t){if(!e)return;this.copyStatus=void 0;let n=this.copyGeneration,r=await Lt(e,this.renderRoot.querySelector(`dialog`)??this.renderRoot);if(n===this.copyGeneration&&(this.copyStatus=r?{key:t}:{key:`menu.copy_failed`,value:e},!r)){if(await this.updateComplete,n!==this.copyGeneration)return;let e=this.renderRoot.querySelector(`.manual`);if(e){let t=document.createRange();t.selectNodeContents(e);let n=window.getSelection();n?.removeAllRanges(),n?.addRange(t)}}}openDevice(e){this.renderRoot.querySelector(`dialog`)?.close(),t(`/config/devices/device/${encodeURIComponent(e)}`)}rows(e){let t=e.filter(e=>e[1]!==void 0&&e[1]!==``);return u`<dl>
      ${t.map(([e,t])=>u`<div class="row">
            <dt>${this.localize(e)}</dt>
            <dd>${t}</dd>
          </div>`)}
    </dl>`}uplinkRows(e,t,n,r){let i=e.realParent.get(t)??e.uplinkOwner.get(t),a=e.edges.get(t),o;return a?.parent_port!==void 0&&(o=a.child_port===void 0?String(a.parent_port):`${a.parent_port} → ${a.child_port}`),[[`detail.parent`,i===void 0?void 0:e.nodes.get(i)?.name],[`detail.port`,o],[`detail.speed`,a?.speed_mbps?ht(a.speed_mbps):void 0],[`detail.medium`,r&&a?n(ft[a.medium]):void 0],[`detail.poe`,a?.poe_power_w===void 0?void 0:`${a.poe_power_w.toFixed(1)} W`]]}deviceBody(e,t,n){let r=e.clientCounts.get(t.id),a=e.links.get(t.id)?.viaHidden??[];return u`${this.menu(t,n)}${this.rows([[`detail.kind`,n(ct[t.kind])],[`detail.model`,t.model],[`detail.state`,n(yt(t,t.state))],...this.uplinkRows(e,t.id,n,!0),[`detail.clients`,r?n(`detail.clients_value`,{total:r.total,wired:r.wired,wireless:r.wireless}):void 0],[`detail.via_hidden`,a.length>0?a.map(e=>n(ct[e])).join(`, `):void 0]])}
    ${t.ha_device_id?u`<button
          class="action"
          @click=${()=>this.openDevice(t.ha_device_id)}
        >
          ${i(we)}${n(`detail.open_device`)}
        </button>`:b}`}clientBody(e,t,n){return u`${this.menu(t,n)}${this.rows([[`detail.state`,n(lt[t.state])],[`detail.connection`,t.connection?n(ft[t.connection]):void 0],[`detail.vlan`,t.vlan_id===void 0?void 0:String(t.vlan_id)],[`detail.network`,t.network_name],...this.uplinkRows(e,t.id,n,!1)])}`}groupBody(e,t){let n=this.memberQuery.trim().toLowerCase(),r=n?e.members.filter(e=>e.name.toLowerCase().includes(n)):e.members,a=e.counts;return u`${this.rows([[`detail.clients`,t(`detail.clients_value`,{total:a.total,wired:a.wired,wireless:a.wireless})]])}
      <input
        type="search"
        .value=${this.memberQuery}
        placeholder=${t(`detail.search_members`)}
        aria-label=${t(`detail.search_members`)}
        @input=${e=>{this.memberQuery=e.target.value}}
      />
      <ul class="members">
        ${r.map(e=>u`<li>
              <button
                class="member ${e.state}"
                @click=${()=>o(this,`uit-select`,{id:e.id})}
              >
                ${i(ie(e))}<span class="name" title=${e.name}
                  >${e.name}</span
                >
                ${e.state===`online`?b:u`<span class="state-text"
                      >${t(lt[e.state])}</span
                    >`}
              </button>
            </li>`)}
      </ul>`}static styles=[c,De,re`
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
    `]};T(`uit-detail-panel`,Ht);var Ut=class{pointers=new Set;origin={x:0,y:0,moved:!1};down(e){e.isPrimary?(this.pointers=new Set([e.pointerId]),this.origin={x:e.clientX,y:e.clientY,moved:!1}):(this.pointers.add(e.pointerId),this.origin.moved=!0)}move(e){!this.origin.moved&&this.pointers.has(e.pointerId)&&Math.hypot(e.clientX-this.origin.x,e.clientY-this.origin.y)>8&&(this.origin.moved=!0)}end(e){this.pointers.delete(e.pointerId)}get allowsMenu(){return!this.origin.moved&&this.pointers.size<=1}},Wt=`http://www.w3.org/1999/xhtml`,Gt={svg:`http://www.w3.org/2000/svg`,xhtml:Wt,xlink:`http://www.w3.org/1999/xlink`,xml:`http://www.w3.org/XML/1998/namespace`,xmlns:`http://www.w3.org/2000/xmlns/`};function Kt(e){var t=e+=``,n=t.indexOf(`:`);return n>=0&&(t=e.slice(0,n))!==`xmlns`&&(e=e.slice(n+1)),Gt.hasOwnProperty(t)?{space:Gt[t],local:e}:e}function qt(e){return function(){var t=this.ownerDocument,n=this.namespaceURI;return n===Wt&&t.documentElement.namespaceURI===Wt?t.createElement(e):t.createElementNS(n,e)}}function Jt(e){return function(){return this.ownerDocument.createElementNS(e.space,e.local)}}function Yt(e){var t=Kt(e);return(t.local?Jt:qt)(t)}function Xt(){}function Zt(e){return e==null?Xt:function(){return this.querySelector(e)}}function Qt(e){typeof e!=`function`&&(e=Zt(e));for(var t=this._groups,n=t.length,r=Array(n),i=0;i<n;++i)for(var a=t[i],o=a.length,s=r[i]=Array(o),c,l,u=0;u<o;++u)(c=a[u])&&(l=e.call(c,c.__data__,u,a))&&(`__data__`in c&&(l.__data__=c.__data__),s[u]=l);return new k(r,this._parents)}function $t(e){return e==null?[]:Array.isArray(e)?e:Array.from(e)}function en(){return[]}function tn(e){return e==null?en:function(){return this.querySelectorAll(e)}}function nn(e){return function(){return $t(e.apply(this,arguments))}}function rn(e){e=typeof e==`function`?nn(e):tn(e);for(var t=this._groups,n=t.length,r=[],i=[],a=0;a<n;++a)for(var o=t[a],s=o.length,c,l=0;l<s;++l)(c=o[l])&&(r.push(e.call(c,c.__data__,l,o)),i.push(c));return new k(r,i)}function an(e){return function(){return this.matches(e)}}function on(e){return function(t){return t.matches(e)}}var sn=Array.prototype.find;function cn(e){return function(){return sn.call(this.children,e)}}function ln(){return this.firstElementChild}function un(e){return this.select(e==null?ln:cn(typeof e==`function`?e:on(e)))}var dn=Array.prototype.filter;function fn(){return Array.from(this.children)}function pn(e){return function(){return dn.call(this.children,e)}}function mn(e){return this.selectAll(e==null?fn:pn(typeof e==`function`?e:on(e)))}function hn(e){typeof e!=`function`&&(e=an(e));for(var t=this._groups,n=t.length,r=Array(n),i=0;i<n;++i)for(var a=t[i],o=a.length,s=r[i]=[],c,l=0;l<o;++l)(c=a[l])&&e.call(c,c.__data__,l,a)&&s.push(c);return new k(r,this._parents)}function gn(e){return Array(e.length)}function _n(){return new k(this._enter||this._groups.map(gn),this._parents)}function vn(e,t){this.ownerDocument=e.ownerDocument,this.namespaceURI=e.namespaceURI,this._next=null,this._parent=e,this.__data__=t}vn.prototype={constructor:vn,appendChild:function(e){return this._parent.insertBefore(e,this._next)},insertBefore:function(e,t){return this._parent.insertBefore(e,t)},querySelector:function(e){return this._parent.querySelector(e)},querySelectorAll:function(e){return this._parent.querySelectorAll(e)}};function yn(e){return function(){return e}}function bn(e,t,n,r,i,a){for(var o=0,s,c=t.length,l=a.length;o<l;++o)(s=t[o])?(s.__data__=a[o],r[o]=s):n[o]=new vn(e,a[o]);for(;o<c;++o)(s=t[o])&&(i[o]=s)}function xn(e,t,n,r,i,a,o){var s,c,l=new Map,u=t.length,d=a.length,f=Array(u),p;for(s=0;s<u;++s)(c=t[s])&&(f[s]=p=o.call(c,c.__data__,s,t)+``,l.has(p)?i[s]=c:l.set(p,c));for(s=0;s<d;++s)p=o.call(e,a[s],s,a)+``,(c=l.get(p))?(r[s]=c,c.__data__=a[s],l.delete(p)):n[s]=new vn(e,a[s]);for(s=0;s<u;++s)(c=t[s])&&l.get(f[s])===c&&(i[s]=c)}function Sn(e){return e.__data__}function Cn(e,t){if(!arguments.length)return Array.from(this,Sn);var n=t?xn:bn,r=this._parents,i=this._groups;typeof e!=`function`&&(e=yn(e));for(var a=i.length,o=Array(a),s=Array(a),c=Array(a),l=0;l<a;++l){var u=r[l],d=i[l],f=d.length,p=wn(e.call(u,u&&u.__data__,l,r)),m=p.length,h=s[l]=Array(m),g=o[l]=Array(m);n(u,d,h,g,c[l]=Array(f),p,t);for(var _=0,v=0,y,b;_<m;++_)if(y=h[_]){for(_>=v&&(v=_+1);!(b=g[v])&&++v<m;);y._next=b||null}}return o=new k(o,r),o._enter=s,o._exit=c,o}function wn(e){return typeof e==`object`&&`length`in e?e:Array.from(e)}function Tn(){return new k(this._exit||this._groups.map(gn),this._parents)}function En(e,t,n){var r=this.enter(),i=this,a=this.exit();return typeof e==`function`?(r=e(r),r&&=r.selection()):r=r.append(e+``),t!=null&&(i=t(i),i&&=i.selection()),n==null?a.remove():n(a),r&&i?r.merge(i).order():i}function Dn(e){for(var t=e.selection?e.selection():e,n=this._groups,r=t._groups,i=n.length,a=r.length,o=Math.min(i,a),s=Array(i),c=0;c<o;++c)for(var l=n[c],u=r[c],d=l.length,f=s[c]=Array(d),p,m=0;m<d;++m)(p=l[m]||u[m])&&(f[m]=p);for(;c<i;++c)s[c]=n[c];return new k(s,this._parents)}function On(){for(var e=this._groups,t=-1,n=e.length;++t<n;)for(var r=e[t],i=r.length-1,a=r[i],o;--i>=0;)(o=r[i])&&(a&&o.compareDocumentPosition(a)^4&&a.parentNode.insertBefore(o,a),a=o);return this}function kn(e){e||=An;function t(t,n){return t&&n?e(t.__data__,n.__data__):!t-!n}for(var n=this._groups,r=n.length,i=Array(r),a=0;a<r;++a){for(var o=n[a],s=o.length,c=i[a]=Array(s),l,u=0;u<s;++u)(l=o[u])&&(c[u]=l);c.sort(t)}return new k(i,this._parents).order()}function An(e,t){return e<t?-1:e>t?1:e>=t?0:NaN}function jn(){var e=arguments[0];return arguments[0]=this,e.apply(null,arguments),this}function Mn(){return Array.from(this)}function Nn(){for(var e=this._groups,t=0,n=e.length;t<n;++t)for(var r=e[t],i=0,a=r.length;i<a;++i){var o=r[i];if(o)return o}return null}function Pn(){let e=0;for(let t of this)++e;return e}function Fn(){return!this.node()}function In(e){for(var t=this._groups,n=0,r=t.length;n<r;++n)for(var i=t[n],a=0,o=i.length,s;a<o;++a)(s=i[a])&&e.call(s,s.__data__,a,i);return this}function Ln(e){return function(){this.removeAttribute(e)}}function Rn(e){return function(){this.removeAttributeNS(e.space,e.local)}}function zn(e,t){return function(){this.setAttribute(e,t)}}function Bn(e,t){return function(){this.setAttributeNS(e.space,e.local,t)}}function Vn(e,t){return function(){var n=t.apply(this,arguments);n==null?this.removeAttribute(e):this.setAttribute(e,n)}}function Hn(e,t){return function(){var n=t.apply(this,arguments);n==null?this.removeAttributeNS(e.space,e.local):this.setAttributeNS(e.space,e.local,n)}}function Un(e,t){var n=Kt(e);if(arguments.length<2){var r=this.node();return n.local?r.getAttributeNS(n.space,n.local):r.getAttribute(n)}return this.each((t==null?n.local?Rn:Ln:typeof t==`function`?n.local?Hn:Vn:n.local?Bn:zn)(n,t))}function Wn(e){return e.ownerDocument&&e.ownerDocument.defaultView||e.document&&e||e.defaultView}function Gn(e){return function(){this.style.removeProperty(e)}}function Kn(e,t,n){return function(){this.style.setProperty(e,t,n)}}function qn(e,t,n){return function(){var r=t.apply(this,arguments);r==null?this.style.removeProperty(e):this.style.setProperty(e,r,n)}}function Jn(e,t,n){return arguments.length>1?this.each((t==null?Gn:typeof t==`function`?qn:Kn)(e,t,n??``)):O(this.node(),e)}function O(e,t){return e.style.getPropertyValue(t)||Wn(e).getComputedStyle(e,null).getPropertyValue(t)}function Yn(e){return function(){delete this[e]}}function Xn(e,t){return function(){this[e]=t}}function Zn(e,t){return function(){var n=t.apply(this,arguments);n==null?delete this[e]:this[e]=n}}function Qn(e,t){return arguments.length>1?this.each((t==null?Yn:typeof t==`function`?Zn:Xn)(e,t)):this.node()[e]}function $n(e){return e.trim().split(/^|\s+/)}function er(e){return e.classList||new tr(e)}function tr(e){this._node=e,this._names=$n(e.getAttribute(`class`)||``)}tr.prototype={add:function(e){this._names.indexOf(e)<0&&(this._names.push(e),this._node.setAttribute(`class`,this._names.join(` `)))},remove:function(e){var t=this._names.indexOf(e);t>=0&&(this._names.splice(t,1),this._node.setAttribute(`class`,this._names.join(` `)))},contains:function(e){return this._names.indexOf(e)>=0}};function nr(e,t){for(var n=er(e),r=-1,i=t.length;++r<i;)n.add(t[r])}function rr(e,t){for(var n=er(e),r=-1,i=t.length;++r<i;)n.remove(t[r])}function ir(e){return function(){nr(this,e)}}function ar(e){return function(){rr(this,e)}}function or(e,t){return function(){(t.apply(this,arguments)?nr:rr)(this,e)}}function sr(e,t){var n=$n(e+``);if(arguments.length<2){for(var r=er(this.node()),i=-1,a=n.length;++i<a;)if(!r.contains(n[i]))return!1;return!0}return this.each((typeof t==`function`?or:t?ir:ar)(n,t))}function cr(){this.textContent=``}function lr(e){return function(){this.textContent=e}}function ur(e){return function(){var t=e.apply(this,arguments);this.textContent=t??``}}function dr(e){return arguments.length?this.each(e==null?cr:(typeof e==`function`?ur:lr)(e)):this.node().textContent}function fr(){this.innerHTML=``}function pr(e){return function(){this.innerHTML=e}}function mr(e){return function(){var t=e.apply(this,arguments);this.innerHTML=t??``}}function hr(e){return arguments.length?this.each(e==null?fr:(typeof e==`function`?mr:pr)(e)):this.node().innerHTML}function gr(){this.nextSibling&&this.parentNode.appendChild(this)}function _r(){return this.each(gr)}function vr(){this.previousSibling&&this.parentNode.insertBefore(this,this.parentNode.firstChild)}function yr(){return this.each(vr)}function br(e){var t=typeof e==`function`?e:Yt(e);return this.select(function(){return this.appendChild(t.apply(this,arguments))})}function xr(){return null}function Sr(e,t){var n=typeof e==`function`?e:Yt(e),r=t==null?xr:typeof t==`function`?t:Zt(t);return this.select(function(){return this.insertBefore(n.apply(this,arguments),r.apply(this,arguments)||null)})}function Cr(){var e=this.parentNode;e&&e.removeChild(this)}function wr(){return this.each(Cr)}function Tr(){var e=this.cloneNode(!1),t=this.parentNode;return t?t.insertBefore(e,this.nextSibling):e}function Er(){var e=this.cloneNode(!0),t=this.parentNode;return t?t.insertBefore(e,this.nextSibling):e}function Dr(e){return this.select(e?Er:Tr)}function Or(e){return arguments.length?this.property(`__data__`,e):this.node().__data__}function kr(e){return function(t){e.call(this,t,this.__data__)}}function Ar(e){return e.trim().split(/^|\s+/).map(function(e){var t=``,n=e.indexOf(`.`);return n>=0&&(t=e.slice(n+1),e=e.slice(0,n)),{type:e,name:t}})}function jr(e){return function(){var t=this.__on;if(t){for(var n=0,r=-1,i=t.length,a;n<i;++n)a=t[n],(!e.type||a.type===e.type)&&a.name===e.name?this.removeEventListener(a.type,a.listener,a.options):t[++r]=a;++r?t.length=r:delete this.__on}}}function Mr(e,t,n){return function(){var r=this.__on,i,a=kr(t);if(r){for(var o=0,s=r.length;o<s;++o)if((i=r[o]).type===e.type&&i.name===e.name){this.removeEventListener(i.type,i.listener,i.options),this.addEventListener(i.type,i.listener=a,i.options=n),i.value=t;return}}this.addEventListener(e.type,a,n),i={type:e.type,name:e.name,value:t,listener:a,options:n},r?r.push(i):this.__on=[i]}}function Nr(e,t,n){var r=Ar(e+``),i,a=r.length,o;if(arguments.length<2){var s=this.node().__on;if(s){for(var c=0,l=s.length,u;c<l;++c)for(i=0,u=s[c];i<a;++i)if((o=r[i]).type===u.type&&o.name===u.name)return u.value}}else{for(s=t?Mr:jr,i=0;i<a;++i)this.each(s(r[i],t,n));return this}}function Pr(e,t,n){var r=Wn(e),i=r.CustomEvent;typeof i==`function`?i=new i(t,n):(i=r.document.createEvent(`Event`),n?(i.initEvent(t,n.bubbles,n.cancelable),i.detail=n.detail):i.initEvent(t,!1,!1)),e.dispatchEvent(i)}function Fr(e,t){return function(){return Pr(this,e,t)}}function Ir(e,t){return function(){return Pr(this,e,t.apply(this,arguments))}}function Lr(e,t){return this.each((typeof t==`function`?Ir:Fr)(e,t))}function*Rr(){for(var e=this._groups,t=0,n=e.length;t<n;++t)for(var r=e[t],i=0,a=r.length,o;i<a;++i)(o=r[i])&&(yield o)}var zr=[null];function k(e,t){this._groups=e,this._parents=t}function Br(){return new k([[document.documentElement]],zr)}function Vr(){return this}k.prototype=Br.prototype={constructor:k,select:Qt,selectAll:rn,selectChild:un,selectChildren:mn,filter:hn,data:Cn,enter:_n,exit:Tn,join:En,merge:Dn,selection:Vr,order:On,sort:kn,call:jn,nodes:Mn,node:Nn,size:Pn,empty:Fn,each:In,attr:Un,style:Jn,property:Qn,classed:sr,text:dr,html:hr,raise:_r,lower:yr,append:br,insert:Sr,remove:wr,clone:Dr,datum:Or,on:Nr,dispatch:Lr,[Symbol.iterator]:Rr};function A(e){return typeof e==`string`?new k([[document.querySelector(e)]],[document.documentElement]):new k([[e]],zr)}var Hr=0;function Ur(){return new Wr}function Wr(){this._=`@`+(++Hr).toString(36)}Wr.prototype=Ur.prototype={constructor:Wr,get:function(e){for(var t=this._;!(t in e);)if(!(e=e.parentNode))return;return e[t]},set:function(e,t){return e[this._]=t},remove:function(e){return this._ in e&&delete e[this._]},toString:function(){return this._}};function Gr(e){let t;for(;t=e.sourceEvent;)e=t;return e}function j(e,t){if(e=Gr(e),t===void 0&&(t=e.currentTarget),t){var n=t.ownerSVGElement||t;if(n.createSVGPoint){var r=n.createSVGPoint();return r.x=e.clientX,r.y=e.clientY,r=r.matrixTransform(t.getScreenCTM().inverse()),[r.x,r.y]}if(t.getBoundingClientRect){var i=t.getBoundingClientRect();return[e.clientX-i.left-t.clientLeft,e.clientY-i.top-t.clientTop]}}return[e.pageX,e.pageY]}var Kr={value:()=>{}};function qr(){for(var e=0,t=arguments.length,n={},r;e<t;++e){if(!(r=arguments[e]+``)||r in n||/[\s.]/.test(r))throw Error(`illegal type: `+r);n[r]=[]}return new Jr(n)}function Jr(e){this._=e}function Yr(e,t){return e.trim().split(/^|\s+/).map(function(e){var n=``,r=e.indexOf(`.`);if(r>=0&&(n=e.slice(r+1),e=e.slice(0,r)),e&&!t.hasOwnProperty(e))throw Error(`unknown type: `+e);return{type:e,name:n}})}Jr.prototype=qr.prototype={constructor:Jr,on:function(e,t){var n=this._,r=Yr(e+``,n),i,a=-1,o=r.length;if(arguments.length<2){for(;++a<o;)if((i=(e=r[a]).type)&&(i=Xr(n[i],e.name)))return i}else{if(t!=null&&typeof t!=`function`)throw Error(`invalid callback: `+t);for(;++a<o;)if(i=(e=r[a]).type)n[i]=Zr(n[i],e.name,t);else if(t==null)for(i in n)n[i]=Zr(n[i],e.name,null);return this}},copy:function(){var e={},t=this._;for(var n in t)e[n]=t[n].slice();return new Jr(e)},call:function(e,t){if((i=arguments.length-2)>0)for(var n=Array(i),r=0,i,a;r<i;++r)n[r]=arguments[r+2];if(!this._.hasOwnProperty(e))throw Error(`unknown type: `+e);for(a=this._[e],r=0,i=a.length;r<i;++r)a[r].value.apply(t,n)},apply:function(e,t,n){if(!this._.hasOwnProperty(e))throw Error(`unknown type: `+e);for(var r=this._[e],i=0,a=r.length;i<a;++i)r[i].value.apply(t,n)}};function Xr(e,t){for(var n=0,r=e.length,i;n<r;++n)if((i=e[n]).name===t)return i.value}function Zr(e,t,n){for(var r=0,i=e.length;r<i;++r)if(e[r].name===t){e[r]=Kr,e=e.slice(0,r).concat(e.slice(r+1));break}return n!=null&&e.push({name:t,value:n}),e}var M=0,Qr=0,$r=0,ei=1e3,ti,N,ni=0,P=0,ri=0,F=typeof performance==`object`&&performance.now?performance:Date,ii=typeof window==`object`&&window.requestAnimationFrame?window.requestAnimationFrame.bind(window):function(e){setTimeout(e,17)};function ai(){return P||=(ii(oi),F.now()+ri)}function oi(){P=0}function si(){this._call=this._time=this._next=null}si.prototype=ci.prototype={constructor:si,restart:function(e,t,n){if(typeof e!=`function`)throw TypeError(`callback is not a function`);n=(n==null?ai():+n)+(t==null?0:+t),!this._next&&N!==this&&(N?N._next=this:ti=this,N=this),this._call=e,this._time=n,pi()},stop:function(){this._call&&(this._call=null,this._time=1/0,pi())}};function ci(e,t,n){var r=new si;return r.restart(e,t,n),r}function li(){ai(),++M;for(var e=ti,t;e;)(t=P-e._time)>=0&&e._call.call(void 0,t),e=e._next;--M}function ui(){P=(ni=F.now())+ri,M=Qr=0;try{li()}finally{M=0,fi(),P=0}}function di(){var e=F.now(),t=e-ni;t>ei&&(ri-=t,ni=e)}function fi(){for(var e,t=ti,n,r=1/0;t;)t._call?(r>t._time&&(r=t._time),e=t,t=t._next):(n=t._next,t._next=null,t=e?e._next=n:ti=n);N=e,pi(r)}function pi(e){M||(Qr&&=clearTimeout(Qr),e-P>24?(e<1/0&&(Qr=setTimeout(ui,e-F.now()-ri)),$r&&=clearInterval($r)):($r||=(ni=F.now(),setInterval(di,ei)),M=1,ii(ui)))}function mi(e,t,n){var r=new si;return t=t==null?0:+t,r.restart(n=>{r.stop(),e(n+t)},t,n),r}function hi(e,t,n){e.prototype=t.prototype=n,n.constructor=e}function gi(e,t){var n=Object.create(e.prototype);for(var r in t)n[r]=t[r];return n}function I(){}var L=.7,_i=1/L,R=`\\s*([+-]?\\d+)\\s*`,z=`\\s*([+-]?(?:\\d*\\.)?\\d+(?:[eE][+-]?\\d+)?)\\s*`,B=`\\s*([+-]?(?:\\d*\\.)?\\d+(?:[eE][+-]?\\d+)?)%\\s*`,vi=/^#([0-9a-f]{3,8})$/,yi=RegExp(`^rgb\\(${R},${R},${R}\\)$`),bi=RegExp(`^rgb\\(${B},${B},${B}\\)$`),xi=RegExp(`^rgba\\(${R},${R},${R},${z}\\)$`),Si=RegExp(`^rgba\\(${B},${B},${B},${z}\\)$`),Ci=RegExp(`^hsl\\(${z},${B},${B}\\)$`),wi=RegExp(`^hsla\\(${z},${B},${B},${z}\\)$`),Ti={aliceblue:15792383,antiquewhite:16444375,aqua:65535,aquamarine:8388564,azure:15794175,beige:16119260,bisque:16770244,black:0,blanchedalmond:16772045,blue:255,blueviolet:9055202,brown:10824234,burlywood:14596231,cadetblue:6266528,chartreuse:8388352,chocolate:13789470,coral:16744272,cornflowerblue:6591981,cornsilk:16775388,crimson:14423100,cyan:65535,darkblue:139,darkcyan:35723,darkgoldenrod:12092939,darkgray:11119017,darkgreen:25600,darkgrey:11119017,darkkhaki:12433259,darkmagenta:9109643,darkolivegreen:5597999,darkorange:16747520,darkorchid:10040012,darkred:9109504,darksalmon:15308410,darkseagreen:9419919,darkslateblue:4734347,darkslategray:3100495,darkslategrey:3100495,darkturquoise:52945,darkviolet:9699539,deeppink:16716947,deepskyblue:49151,dimgray:6908265,dimgrey:6908265,dodgerblue:2003199,firebrick:11674146,floralwhite:16775920,forestgreen:2263842,fuchsia:16711935,gainsboro:14474460,ghostwhite:16316671,gold:16766720,goldenrod:14329120,gray:8421504,green:32768,greenyellow:11403055,grey:8421504,honeydew:15794160,hotpink:16738740,indianred:13458524,indigo:4915330,ivory:16777200,khaki:15787660,lavender:15132410,lavenderblush:16773365,lawngreen:8190976,lemonchiffon:16775885,lightblue:11393254,lightcoral:15761536,lightcyan:14745599,lightgoldenrodyellow:16448210,lightgray:13882323,lightgreen:9498256,lightgrey:13882323,lightpink:16758465,lightsalmon:16752762,lightseagreen:2142890,lightskyblue:8900346,lightslategray:7833753,lightslategrey:7833753,lightsteelblue:11584734,lightyellow:16777184,lime:65280,limegreen:3329330,linen:16445670,magenta:16711935,maroon:8388608,mediumaquamarine:6737322,mediumblue:205,mediumorchid:12211667,mediumpurple:9662683,mediumseagreen:3978097,mediumslateblue:8087790,mediumspringgreen:64154,mediumturquoise:4772300,mediumvioletred:13047173,midnightblue:1644912,mintcream:16121850,mistyrose:16770273,moccasin:16770229,navajowhite:16768685,navy:128,oldlace:16643558,olive:8421376,olivedrab:7048739,orange:16753920,orangered:16729344,orchid:14315734,palegoldenrod:15657130,palegreen:10025880,paleturquoise:11529966,palevioletred:14381203,papayawhip:16773077,peachpuff:16767673,peru:13468991,pink:16761035,plum:14524637,powderblue:11591910,purple:8388736,rebeccapurple:6697881,red:16711680,rosybrown:12357519,royalblue:4286945,saddlebrown:9127187,salmon:16416882,sandybrown:16032864,seagreen:3050327,seashell:16774638,sienna:10506797,silver:12632256,skyblue:8900331,slateblue:6970061,slategray:7372944,slategrey:7372944,snow:16775930,springgreen:65407,steelblue:4620980,tan:13808780,teal:32896,thistle:14204888,tomato:16737095,turquoise:4251856,violet:15631086,wheat:16113331,white:16777215,whitesmoke:16119285,yellow:16776960,yellowgreen:10145074};hi(I,Ai,{copy(e){return Object.assign(new this.constructor,this,e)},displayable(){return this.rgb().displayable()},hex:Ei,formatHex:Ei,formatHex8:Di,formatHsl:Oi,formatRgb:ki,toString:ki});function Ei(){return this.rgb().formatHex()}function Di(){return this.rgb().formatHex8()}function Oi(){return Bi(this).formatHsl()}function ki(){return this.rgb().formatRgb()}function Ai(e){var t,n;return e=(e+``).trim().toLowerCase(),(t=vi.exec(e))?(n=t[1].length,t=parseInt(t[1],16),n===6?ji(t):n===3?new V(t>>8&15|t>>4&240,t>>4&15|t&240,(t&15)<<4|t&15,1):n===8?Mi(t>>24&255,t>>16&255,t>>8&255,(t&255)/255):n===4?Mi(t>>12&15|t>>8&240,t>>8&15|t>>4&240,t>>4&15|t&240,((t&15)<<4|t&15)/255):null):(t=yi.exec(e))?new V(t[1],t[2],t[3],1):(t=bi.exec(e))?new V(t[1]*255/100,t[2]*255/100,t[3]*255/100,1):(t=xi.exec(e))?Mi(t[1],t[2],t[3],t[4]):(t=Si.exec(e))?Mi(t[1]*255/100,t[2]*255/100,t[3]*255/100,t[4]):(t=Ci.exec(e))?zi(t[1],t[2]/100,t[3]/100,1):(t=wi.exec(e))?zi(t[1],t[2]/100,t[3]/100,t[4]):Ti.hasOwnProperty(e)?ji(Ti[e]):e===`transparent`?new V(NaN,NaN,NaN,0):null}function ji(e){return new V(e>>16&255,e>>8&255,e&255,1)}function Mi(e,t,n,r){return r<=0&&(e=t=n=NaN),new V(e,t,n,r)}function Ni(e){return e instanceof I||(e=Ai(e)),e?(e=e.rgb(),new V(e.r,e.g,e.b,e.opacity)):new V}function Pi(e,t,n,r){return arguments.length===1?Ni(e):new V(e,t,n,r??1)}function V(e,t,n,r){this.r=+e,this.g=+t,this.b=+n,this.opacity=+r}hi(V,Pi,gi(I,{brighter(e){return e=e==null?_i:_i**+e,new V(this.r*e,this.g*e,this.b*e,this.opacity)},darker(e){return e=e==null?L:L**+e,new V(this.r*e,this.g*e,this.b*e,this.opacity)},rgb(){return this},clamp(){return new V(H(this.r),H(this.g),H(this.b),Ri(this.opacity))},displayable(){return-.5<=this.r&&this.r<255.5&&-.5<=this.g&&this.g<255.5&&-.5<=this.b&&this.b<255.5&&0<=this.opacity&&this.opacity<=1},hex:Fi,formatHex:Fi,formatHex8:Ii,formatRgb:Li,toString:Li}));function Fi(){return`#${U(this.r)}${U(this.g)}${U(this.b)}`}function Ii(){return`#${U(this.r)}${U(this.g)}${U(this.b)}${U((isNaN(this.opacity)?1:this.opacity)*255)}`}function Li(){let e=Ri(this.opacity);return`${e===1?`rgb(`:`rgba(`}${H(this.r)}, ${H(this.g)}, ${H(this.b)}${e===1?`)`:`, ${e})`}`}function Ri(e){return isNaN(e)?1:Math.max(0,Math.min(1,e))}function H(e){return Math.max(0,Math.min(255,Math.round(e)||0))}function U(e){return e=H(e),(e<16?`0`:``)+e.toString(16)}function zi(e,t,n,r){return r<=0?e=t=n=NaN:n<=0||n>=1?e=t=NaN:t<=0&&(e=NaN),new W(e,t,n,r)}function Bi(e){if(e instanceof W)return new W(e.h,e.s,e.l,e.opacity);if(e instanceof I||(e=Ai(e)),!e)return new W;if(e instanceof W)return e;e=e.rgb();var t=e.r/255,n=e.g/255,r=e.b/255,i=Math.min(t,n,r),a=Math.max(t,n,r),o=NaN,s=a-i,c=(a+i)/2;return s?(o=t===a?(n-r)/s+(n<r)*6:n===a?(r-t)/s+2:(t-n)/s+4,s/=c<.5?a+i:2-a-i,o*=60):s=c>0&&c<1?0:o,new W(o,s,c,e.opacity)}function Vi(e,t,n,r){return arguments.length===1?Bi(e):new W(e,t,n,r??1)}function W(e,t,n,r){this.h=+e,this.s=+t,this.l=+n,this.opacity=+r}hi(W,Vi,gi(I,{brighter(e){return e=e==null?_i:_i**+e,new W(this.h,this.s,this.l*e,this.opacity)},darker(e){return e=e==null?L:L**+e,new W(this.h,this.s,this.l*e,this.opacity)},rgb(){var e=this.h%360+(this.h<0)*360,t=isNaN(e)||isNaN(this.s)?0:this.s,n=this.l,r=n+(n<.5?n:1-n)*t,i=2*n-r;return new V(Wi(e>=240?e-240:e+120,i,r),Wi(e,i,r),Wi(e<120?e+240:e-120,i,r),this.opacity)},clamp(){return new W(Hi(this.h),Ui(this.s),Ui(this.l),Ri(this.opacity))},displayable(){return(0<=this.s&&this.s<=1||isNaN(this.s))&&0<=this.l&&this.l<=1&&0<=this.opacity&&this.opacity<=1},formatHsl(){let e=Ri(this.opacity);return`${e===1?`hsl(`:`hsla(`}${Hi(this.h)}, ${Ui(this.s)*100}%, ${Ui(this.l)*100}%${e===1?`)`:`, ${e})`}`}}));function Hi(e){return e=(e||0)%360,e<0?e+360:e}function Ui(e){return Math.max(0,Math.min(1,e||0))}function Wi(e,t,n){return(e<60?t+(n-t)*e/60:e<180?n:e<240?t+(n-t)*(240-e)/60:t)*255}var Gi=e=>()=>e;function Ki(e,t){return function(n){return e+n*t}}function qi(e,t,n){return e**=+n,t=t**+n-e,n=1/n,function(r){return(e+r*t)**+n}}function Ji(e){return(e=+e)==1?Yi:function(t,n){return n-t?qi(t,n,e):Gi(isNaN(t)?n:t)}}function Yi(e,t){var n=t-e;return n?Ki(e,n):Gi(isNaN(e)?t:e)}var Xi=(function e(t){var n=Ji(t);function r(e,t){var r=n((e=Pi(e)).r,(t=Pi(t)).r),i=n(e.g,t.g),a=n(e.b,t.b),o=Yi(e.opacity,t.opacity);return function(t){return e.r=r(t),e.g=i(t),e.b=a(t),e.opacity=o(t),e+``}}return r.gamma=e,r})(1);function G(e,t){return e=+e,t=+t,function(n){return e*(1-n)+t*n}}var Zi=/[-+]?(?:\d+\.?\d*|\.?\d+)(?:[eE][-+]?\d+)?/g,Qi=new RegExp(Zi.source,`g`);function $i(e){return function(){return e}}function ea(e){return function(t){return e(t)+``}}function ta(e,t){var n=Zi.lastIndex=Qi.lastIndex=0,r,i,a,o=-1,s=[],c=[];for(e+=``,t+=``;(r=Zi.exec(e))&&(i=Qi.exec(t));)(a=i.index)>n&&(a=t.slice(n,a),s[o]?s[o]+=a:s[++o]=a),(r=r[0])===(i=i[0])?s[o]?s[o]+=i:s[++o]=i:(s[++o]=null,c.push({i:o,x:G(r,i)})),n=Qi.lastIndex;return n<t.length&&(a=t.slice(n),s[o]?s[o]+=a:s[++o]=a),s.length<2?c[0]?ea(c[0].x):$i(t):(t=c.length,function(e){for(var n=0,r;n<t;++n)s[(r=c[n]).i]=r.x(e);return s.join(``)})}var na=180/Math.PI,ra={translateX:0,translateY:0,rotate:0,skewX:0,scaleX:1,scaleY:1};function ia(e,t,n,r,i,a){var o,s,c;return(o=Math.sqrt(e*e+t*t))&&(e/=o,t/=o),(c=e*n+t*r)&&(n-=e*c,r-=t*c),(s=Math.sqrt(n*n+r*r))&&(n/=s,r/=s,c/=s),e*r<t*n&&(e=-e,t=-t,c=-c,o=-o),{translateX:i,translateY:a,rotate:Math.atan2(t,e)*na,skewX:Math.atan(c)*na,scaleX:o,scaleY:s}}var aa;function oa(e){let t=new(typeof DOMMatrix==`function`?DOMMatrix:WebKitCSSMatrix)(e+``);return t.isIdentity?ra:ia(t.a,t.b,t.c,t.d,t.e,t.f)}function sa(e){return e==null||(aa||=document.createElementNS(`http://www.w3.org/2000/svg`,`g`),aa.setAttribute(`transform`,e),!(e=aa.transform.baseVal.consolidate()))?ra:(e=e.matrix,ia(e.a,e.b,e.c,e.d,e.e,e.f))}function ca(e,t,n,r){function i(e){return e.length?e.pop()+` `:``}function a(e,r,i,a,o,s){if(e!==i||r!==a){var c=o.push(`translate(`,null,t,null,n);s.push({i:c-4,x:G(e,i)},{i:c-2,x:G(r,a)})}else(i||a)&&o.push(`translate(`+i+t+a+n)}function o(e,t,n,a){e===t?t&&n.push(i(n)+`rotate(`+t+r):(e-t>180?t+=360:t-e>180&&(e+=360),a.push({i:n.push(i(n)+`rotate(`,null,r)-2,x:G(e,t)}))}function s(e,t,n,a){e===t?t&&n.push(i(n)+`skewX(`+t+r):a.push({i:n.push(i(n)+`skewX(`,null,r)-2,x:G(e,t)})}function c(e,t,n,r,a,o){if(e!==n||t!==r){var s=a.push(i(a)+`scale(`,null,`,`,null,`)`);o.push({i:s-4,x:G(e,n)},{i:s-2,x:G(t,r)})}else(n!==1||r!==1)&&a.push(i(a)+`scale(`+n+`,`+r+`)`)}return function(t,n){var r=[],i=[];return t=e(t),n=e(n),a(t.translateX,t.translateY,n.translateX,n.translateY,r,i),o(t.rotate,n.rotate,r,i),s(t.skewX,n.skewX,r,i),c(t.scaleX,t.scaleY,n.scaleX,n.scaleY,r,i),t=n=null,function(e){for(var t=-1,n=i.length,a;++t<n;)r[(a=i[t]).i]=a.x(e);return r.join(``)}}}var la=ca(oa,`px, `,`px)`,`deg)`),ua=ca(sa,`, `,`)`,`)`),da=1e-12;function fa(e){return((e=Math.exp(e))+1/e)/2}function pa(e){return((e=Math.exp(e))-1/e)/2}function ma(e){return((e=Math.exp(2*e))-1)/(e+1)}var ha=(function e(t,n,r){function i(e,i){var a=e[0],o=e[1],s=e[2],c=i[0],l=i[1],u=i[2],d=c-a,f=l-o,p=d*d+f*f,m,h;if(p<da)h=Math.log(u/s)/t,m=function(e){return[a+e*d,o+e*f,s*Math.exp(t*e*h)]};else{var g=Math.sqrt(p),_=(u*u-s*s+r*p)/(2*s*n*g),v=(u*u-s*s-r*p)/(2*u*n*g),y=Math.log(Math.sqrt(_*_+1)-_);h=(Math.log(Math.sqrt(v*v+1)-v)-y)/t,m=function(e){var r=e*h,i=fa(y),c=s/(n*g)*(i*ma(t*r+y)-pa(y));return[a+c*d,o+c*f,s*i/fa(t*r+y)]}}return m.duration=h*1e3*t/Math.SQRT2,m}return i.rho=function(t){var n=Math.max(.001,+t),r=n*n;return e(n,r,r*r)},i})(Math.SQRT2,2,4);function ga(e){return((e*=2)<=1?e*e*e:(e-=2)*e*e+2)/2}var _a=qr(`start`,`end`,`cancel`,`interrupt`),va=[],ya=0,ba=1,xa=2,Sa=3,Ca=4,wa=5,Ta=6;function Ea(e,t,n,r,i,a){var o=e.__transition;if(!o)e.__transition={};else if(n in o)return;Oa(e,n,{name:t,index:r,group:i,on:_a,tween:va,time:a.time,delay:a.delay,duration:a.duration,ease:a.ease,timer:null,state:ya})}function Da(e,t){var n=q(e,t);if(n.state>ya)throw Error(`too late; already scheduled`);return n}function K(e,t){var n=q(e,t);if(n.state>Sa)throw Error(`too late; already running`);return n}function q(e,t){var n=e.__transition;if(!n||!(n=n[t]))throw Error(`transition not found`);return n}function Oa(e,t,n){var r=e.__transition,i;r[t]=n,n.timer=ci(a,0,n.time);function a(e){n.state=ba,n.timer.restart(o,n.delay,n.time),n.delay<=e&&o(e-n.delay)}function o(a){var l,u,d,f;if(n.state!==ba)return c();for(l in r)if(f=r[l],f.name===n.name){if(f.state===Sa)return mi(o);f.state===Ca?(f.state=Ta,f.timer.stop(),f.on.call(`interrupt`,e,e.__data__,f.index,f.group),delete r[l]):+l<t&&(f.state=Ta,f.timer.stop(),f.on.call(`cancel`,e,e.__data__,f.index,f.group),delete r[l])}if(mi(function(){n.state===Sa&&(n.state=Ca,n.timer.restart(s,n.delay,n.time),s(a))}),n.state=xa,n.on.call(`start`,e,e.__data__,n.index,n.group),n.state===xa){for(n.state=Sa,i=Array(d=n.tween.length),l=0,u=-1;l<d;++l)(f=n.tween[l].value.call(e,e.__data__,n.index,n.group))&&(i[++u]=f);i.length=u+1}}function s(t){for(var r=t<n.duration?n.ease.call(null,t/n.duration):(n.timer.restart(c),n.state=wa,1),a=-1,o=i.length;++a<o;)i[a].call(e,r);n.state===wa&&(n.on.call(`end`,e,e.__data__,n.index,n.group),c())}function c(){for(var i in n.state=Ta,n.timer.stop(),delete r[t],r)return;delete e.__transition}}function ka(e,t){var n=e.__transition,r,i,a=!0,o;if(n){for(o in t=t==null?null:t+``,n)(r=n[o]).name===t?(i=r.state>xa&&r.state<wa,r.state=Ta,r.timer.stop(),r.on.call(i?`interrupt`:`cancel`,e,e.__data__,r.index,r.group),delete n[o]):a=!1;a&&delete e.__transition}}function Aa(e){return this.each(function(){ka(this,e)})}function ja(e,t){var n,r;return function(){var i=K(this,e),a=i.tween;if(a!==n){r=n=a;for(var o=0,s=r.length;o<s;++o)if(r[o].name===t){r=r.slice(),r.splice(o,1);break}}i.tween=r}}function Ma(e,t,n){var r,i;if(typeof n!=`function`)throw Error();return function(){var a=K(this,e),o=a.tween;if(o!==r){i=(r=o).slice();for(var s={name:t,value:n},c=0,l=i.length;c<l;++c)if(i[c].name===t){i[c]=s;break}c===l&&i.push(s)}a.tween=i}}function Na(e,t){var n=this._id;if(e+=``,arguments.length<2){for(var r=q(this.node(),n).tween,i=0,a=r.length,o;i<a;++i)if((o=r[i]).name===e)return o.value;return null}return this.each((t==null?ja:Ma)(n,e,t))}function Pa(e,t,n){var r=e._id;return e.each(function(){var e=K(this,r);(e.value||={})[t]=n.apply(this,arguments)}),function(e){return q(e,r).value[t]}}function Fa(e,t){var n;return(typeof t==`number`?G:t instanceof Ai?Xi:(n=Ai(t))?(t=n,Xi):ta)(e,t)}function Ia(e){return function(){this.removeAttribute(e)}}function La(e){return function(){this.removeAttributeNS(e.space,e.local)}}function Ra(e,t,n){var r,i=n+``,a;return function(){var o=this.getAttribute(e);return o===i?null:o===r?a:a=t(r=o,n)}}function za(e,t,n){var r,i=n+``,a;return function(){var o=this.getAttributeNS(e.space,e.local);return o===i?null:o===r?a:a=t(r=o,n)}}function Ba(e,t,n){var r,i,a;return function(){var o,s=n(this),c;return s==null?void this.removeAttribute(e):(o=this.getAttribute(e),c=s+``,o===c?null:o===r&&c===i?a:(i=c,a=t(r=o,s)))}}function Va(e,t,n){var r,i,a;return function(){var o,s=n(this),c;return s==null?void this.removeAttributeNS(e.space,e.local):(o=this.getAttributeNS(e.space,e.local),c=s+``,o===c?null:o===r&&c===i?a:(i=c,a=t(r=o,s)))}}function Ha(e,t){var n=Kt(e),r=n===`transform`?ua:Fa;return this.attrTween(e,typeof t==`function`?(n.local?Va:Ba)(n,r,Pa(this,`attr.`+e,t)):t==null?(n.local?La:Ia)(n):(n.local?za:Ra)(n,r,t))}function Ua(e,t){return function(n){this.setAttribute(e,t.call(this,n))}}function Wa(e,t){return function(n){this.setAttributeNS(e.space,e.local,t.call(this,n))}}function Ga(e,t){var n,r;function i(){var i=t.apply(this,arguments);return i!==r&&(n=(r=i)&&Wa(e,i)),n}return i._value=t,i}function Ka(e,t){var n,r;function i(){var i=t.apply(this,arguments);return i!==r&&(n=(r=i)&&Ua(e,i)),n}return i._value=t,i}function qa(e,t){var n=`attr.`+e;if(arguments.length<2)return(n=this.tween(n))&&n._value;if(t==null)return this.tween(n,null);if(typeof t!=`function`)throw Error();var r=Kt(e);return this.tween(n,(r.local?Ga:Ka)(r,t))}function Ja(e,t){return function(){Da(this,e).delay=+t.apply(this,arguments)}}function Ya(e,t){return t=+t,function(){Da(this,e).delay=t}}function Xa(e){var t=this._id;return arguments.length?this.each((typeof e==`function`?Ja:Ya)(t,e)):q(this.node(),t).delay}function Za(e,t){return function(){K(this,e).duration=+t.apply(this,arguments)}}function Qa(e,t){return t=+t,function(){K(this,e).duration=t}}function $a(e){var t=this._id;return arguments.length?this.each((typeof e==`function`?Za:Qa)(t,e)):q(this.node(),t).duration}function eo(e,t){if(typeof t!=`function`)throw Error();return function(){K(this,e).ease=t}}function to(e){var t=this._id;return arguments.length?this.each(eo(t,e)):q(this.node(),t).ease}function no(e,t){return function(){var n=t.apply(this,arguments);if(typeof n!=`function`)throw Error();K(this,e).ease=n}}function ro(e){if(typeof e!=`function`)throw Error();return this.each(no(this._id,e))}function io(e){typeof e!=`function`&&(e=an(e));for(var t=this._groups,n=t.length,r=Array(n),i=0;i<n;++i)for(var a=t[i],o=a.length,s=r[i]=[],c,l=0;l<o;++l)(c=a[l])&&e.call(c,c.__data__,l,a)&&s.push(c);return new J(r,this._parents,this._name,this._id)}function ao(e){if(e._id!==this._id)throw Error();for(var t=this._groups,n=e._groups,r=t.length,i=n.length,a=Math.min(r,i),o=Array(r),s=0;s<a;++s)for(var c=t[s],l=n[s],u=c.length,d=o[s]=Array(u),f,p=0;p<u;++p)(f=c[p]||l[p])&&(d[p]=f);for(;s<r;++s)o[s]=t[s];return new J(o,this._parents,this._name,this._id)}function oo(e){return(e+``).trim().split(/^|\s+/).every(function(e){var t=e.indexOf(`.`);return t>=0&&(e=e.slice(0,t)),!e||e===`start`})}function so(e,t,n){var r,i,a=oo(t)?Da:K;return function(){var o=a(this,e),s=o.on;s!==r&&(i=(r=s).copy()).on(t,n),o.on=i}}function co(e,t){var n=this._id;return arguments.length<2?q(this.node(),n).on.on(e):this.each(so(n,e,t))}function lo(e){return function(){var t=this.parentNode;for(var n in this.__transition)if(+n!==e)return;t&&t.removeChild(this)}}function uo(){return this.on(`end.remove`,lo(this._id))}function fo(e){var t=this._name,n=this._id;typeof e!=`function`&&(e=Zt(e));for(var r=this._groups,i=r.length,a=Array(i),o=0;o<i;++o)for(var s=r[o],c=s.length,l=a[o]=Array(c),u,d,f=0;f<c;++f)(u=s[f])&&(d=e.call(u,u.__data__,f,s))&&(`__data__`in u&&(d.__data__=u.__data__),l[f]=d,Ea(l[f],t,n,f,l,q(u,n)));return new J(a,this._parents,t,n)}function po(e){var t=this._name,n=this._id;typeof e!=`function`&&(e=tn(e));for(var r=this._groups,i=r.length,a=[],o=[],s=0;s<i;++s)for(var c=r[s],l=c.length,u,d=0;d<l;++d)if(u=c[d]){for(var f=e.call(u,u.__data__,d,c),p,m=q(u,n),h=0,g=f.length;h<g;++h)(p=f[h])&&Ea(p,t,n,h,f,m);a.push(f),o.push(u)}return new J(a,o,t,n)}var mo=Br.prototype.constructor;function ho(){return new mo(this._groups,this._parents)}function go(e,t){var n,r,i;return function(){var a=O(this,e),o=(this.style.removeProperty(e),O(this,e));return a===o?null:a===n&&o===r?i:i=t(n=a,r=o)}}function _o(e){return function(){this.style.removeProperty(e)}}function vo(e,t,n){var r,i=n+``,a;return function(){var o=O(this,e);return o===i?null:o===r?a:a=t(r=o,n)}}function yo(e,t,n){var r,i,a;return function(){var o=O(this,e),s=n(this),c=s+``;return s??(c=s=(this.style.removeProperty(e),O(this,e))),o===c?null:o===r&&c===i?a:(i=c,a=t(r=o,s))}}function bo(e,t){var n,r,i,a=`style.`+t,o=`end.`+a,s;return function(){var c=K(this,e),l=c.on,u=c.value[a]==null?s||=_o(t):void 0;(l!==n||i!==u)&&(r=(n=l).copy()).on(o,i=u),c.on=r}}function xo(e,t,n){var r=(e+=``)==`transform`?la:Fa;return t==null?this.styleTween(e,go(e,r)).on(`end.style.`+e,_o(e)):typeof t==`function`?this.styleTween(e,yo(e,r,Pa(this,`style.`+e,t))).each(bo(this._id,e)):this.styleTween(e,vo(e,r,t),n).on(`end.style.`+e,null)}function So(e,t,n){return function(r){this.style.setProperty(e,t.call(this,r),n)}}function Co(e,t,n){var r,i;function a(){var a=t.apply(this,arguments);return a!==i&&(r=(i=a)&&So(e,a,n)),r}return a._value=t,a}function wo(e,t,n){var r=`style.`+(e+=``);if(arguments.length<2)return(r=this.tween(r))&&r._value;if(t==null)return this.tween(r,null);if(typeof t!=`function`)throw Error();return this.tween(r,Co(e,t,n??``))}function To(e){return function(){this.textContent=e}}function Eo(e){return function(){var t=e(this);this.textContent=t??``}}function Do(e){return this.tween(`text`,typeof e==`function`?Eo(Pa(this,`text`,e)):To(e==null?``:e+``))}function Oo(e){return function(t){this.textContent=e.call(this,t)}}function ko(e){var t,n;function r(){var r=e.apply(this,arguments);return r!==n&&(t=(n=r)&&Oo(r)),t}return r._value=e,r}function Ao(e){var t=`text`;if(arguments.length<1)return(t=this.tween(t))&&t._value;if(e==null)return this.tween(t,null);if(typeof e!=`function`)throw Error();return this.tween(t,ko(e))}function jo(){for(var e=this._name,t=this._id,n=Po(),r=this._groups,i=r.length,a=0;a<i;++a)for(var o=r[a],s=o.length,c,l=0;l<s;++l)if(c=o[l]){var u=q(c,t);Ea(c,e,n,l,o,{time:u.time+u.delay+u.duration,delay:0,duration:u.duration,ease:u.ease})}return new J(r,this._parents,e,n)}function Mo(){var e,t,n=this,r=n._id,i=n.size();return new Promise(function(a,o){var s={value:o},c={value:function(){--i===0&&a()}};n.each(function(){var n=K(this,r),i=n.on;i!==e&&(t=(e=i).copy(),t._.cancel.push(s),t._.interrupt.push(s),t._.end.push(c)),n.on=t}),i===0&&a()})}var No=0;function J(e,t,n,r){this._groups=e,this._parents=t,this._name=n,this._id=r}function Po(){return++No}var Y=Br.prototype;J.prototype={constructor:J,select:fo,selectAll:po,selectChild:Y.selectChild,selectChildren:Y.selectChildren,filter:io,merge:ao,selection:ho,transition:jo,call:Y.call,nodes:Y.nodes,node:Y.node,size:Y.size,empty:Y.empty,each:Y.each,on:co,attr:Ha,attrTween:qa,style:xo,styleTween:wo,text:Do,textTween:Ao,remove:uo,tween:Na,delay:Xa,duration:$a,ease:to,easeVarying:ro,end:Mo,[Symbol.iterator]:Y[Symbol.iterator]};var Fo={time:null,delay:0,duration:250,ease:ga};function Io(e,t){for(var n;!(n=e.__transition)||!(n=n[t]);)if(!(e=e.parentNode))throw Error(`transition ${t} not found`);return n}function Lo(e){var t,n;e instanceof J?(t=e._id,e=e._name):(t=Po(),(n=Fo).time=ai(),e=e==null?null:e+``);for(var r=this._groups,i=r.length,a=0;a<i;++a)for(var o=r[a],s=o.length,c,l=0;l<s;++l)(c=o[l])&&Ea(c,e,t,l,o,n||Io(c,t));return new J(r,this._parents,e,t)}Br.prototype.interrupt=Aa,Br.prototype.transition=Lo;var Ro={capture:!0,passive:!1};function zo(e){e.preventDefault(),e.stopImmediatePropagation()}function Bo(e){var t=e.document.documentElement,n=A(e).on(`dragstart.drag`,zo,Ro);`onselectstart`in t?n.on(`selectstart.drag`,zo,Ro):(t.__noselect=t.style.MozUserSelect,t.style.MozUserSelect=`none`)}function Vo(e,t){var n=e.document.documentElement,r=A(e).on(`dragstart.drag`,null);t&&(r.on(`click.drag`,zo,Ro),setTimeout(function(){r.on(`click.drag`,null)},0)),`onselectstart`in n?r.on(`selectstart.drag`,null):(n.style.MozUserSelect=n.__noselect,delete n.__noselect)}var Ho=e=>()=>e;function Uo(e,{sourceEvent:t,target:n,transform:r,dispatch:i}){Object.defineProperties(this,{type:{value:e,enumerable:!0,configurable:!0},sourceEvent:{value:t,enumerable:!0,configurable:!0},target:{value:n,enumerable:!0,configurable:!0},transform:{value:r,enumerable:!0,configurable:!0},_:{value:i}})}function X(e,t,n){this.k=e,this.x=t,this.y=n}X.prototype={constructor:X,scale:function(e){return e===1?this:new X(this.k*e,this.x,this.y)},translate:function(e,t){return e===0&t===0?this:new X(this.k,this.x+this.k*e,this.y+this.k*t)},apply:function(e){return[e[0]*this.k+this.x,e[1]*this.k+this.y]},applyX:function(e){return e*this.k+this.x},applyY:function(e){return e*this.k+this.y},invert:function(e){return[(e[0]-this.x)/this.k,(e[1]-this.y)/this.k]},invertX:function(e){return(e-this.x)/this.k},invertY:function(e){return(e-this.y)/this.k},rescaleX:function(e){return e.copy().domain(e.range().map(this.invertX,this).map(e.invert,e))},rescaleY:function(e){return e.copy().domain(e.range().map(this.invertY,this).map(e.invert,e))},toString:function(){return`translate(`+this.x+`,`+this.y+`) scale(`+this.k+`)`}};var Wo=new X(1,0,0);Go.prototype=X.prototype;function Go(e){for(;!e.__zoom;)if(!(e=e.parentNode))return Wo;return e.__zoom}function Ko(e){e.stopImmediatePropagation()}function qo(e){e.preventDefault(),e.stopImmediatePropagation()}function Jo(e){return(!e.ctrlKey||e.type===`wheel`)&&!e.button}function Yo(){var e=this;return e instanceof SVGElement?(e=e.ownerSVGElement||e,e.hasAttribute(`viewBox`)?(e=e.viewBox.baseVal,[[e.x,e.y],[e.x+e.width,e.y+e.height]]):[[0,0],[e.width.baseVal.value,e.height.baseVal.value]]):[[0,0],[e.clientWidth,e.clientHeight]]}function Xo(){return this.__zoom||Wo}function Zo(e){return-e.deltaY*(e.deltaMode===1?.05:e.deltaMode?1:.002)*(e.ctrlKey?10:1)}function Qo(){return navigator.maxTouchPoints||`ontouchstart`in this}function $o(e,t,n){var r=e.invertX(t[0][0])-n[0][0],i=e.invertX(t[1][0])-n[1][0],a=e.invertY(t[0][1])-n[0][1],o=e.invertY(t[1][1])-n[1][1];return e.translate(i>r?(r+i)/2:Math.min(0,r)||Math.max(0,i),o>a?(a+o)/2:Math.min(0,a)||Math.max(0,o))}function es(){var e=Jo,t=Yo,n=$o,r=Zo,i=Qo,a=[0,1/0],o=[[-1/0,-1/0],[1/0,1/0]],s=250,c=ha,l=qr(`start`,`zoom`,`end`),u,d,f,p=500,m=150,h=0,g=10;function _(e){e.property(`__zoom`,Xo).on(`wheel.zoom`,te,{passive:!1}).on(`mousedown.zoom`,ne).on(`dblclick.zoom`,re).filter(i).on(`touchstart.zoom`,C).on(`touchmove.zoom`,ie).on(`touchend.zoom touchcancel.zoom`,ae).style(`-webkit-tap-highlight-color`,`rgba(0,0,0,0)`)}_.transform=function(e,t,n,r){var i=e.selection?e.selection():e;i.property(`__zoom`,Xo),e===i?i.interrupt().each(function(){S(this,arguments).event(r).start().zoom(null,typeof t==`function`?t.apply(this,arguments):t).end()}):x(e,t,n,r)},_.scaleBy=function(e,t,n,r){_.scaleTo(e,function(){return this.__zoom.k*(typeof t==`function`?t.apply(this,arguments):t)},n,r)},_.scaleTo=function(e,r,i,a){_.transform(e,function(){var e=t.apply(this,arguments),a=this.__zoom,s=i==null?b(e):typeof i==`function`?i.apply(this,arguments):i,c=a.invert(s),l=typeof r==`function`?r.apply(this,arguments):r;return n(y(v(a,l),s,c),e,o)},i,a)},_.translateBy=function(e,r,i,a){_.transform(e,function(){return n(this.__zoom.translate(typeof r==`function`?r.apply(this,arguments):r,typeof i==`function`?i.apply(this,arguments):i),t.apply(this,arguments),o)},null,a)},_.translateTo=function(e,r,i,a,s){_.transform(e,function(){var e=t.apply(this,arguments),s=this.__zoom,c=a==null?b(e):typeof a==`function`?a.apply(this,arguments):a;return n(Wo.translate(c[0],c[1]).scale(s.k).translate(typeof r==`function`?-r.apply(this,arguments):-r,typeof i==`function`?-i.apply(this,arguments):-i),e,o)},a,s)};function v(e,t){return t=Math.max(a[0],Math.min(a[1],t)),t===e.k?e:new X(t,e.x,e.y)}function y(e,t,n){var r=t[0]-n[0]*e.k,i=t[1]-n[1]*e.k;return r===e.x&&i===e.y?e:new X(e.k,r,i)}function b(e){return[(+e[0][0]+ +e[1][0])/2,(+e[0][1]+ +e[1][1])/2]}function x(e,n,r,i){e.on(`start.zoom`,function(){S(this,arguments).event(i).start()}).on(`interrupt.zoom end.zoom`,function(){S(this,arguments).event(i).end()}).tween(`zoom`,function(){var e=this,a=arguments,o=S(e,a).event(i),s=t.apply(e,a),l=r==null?b(s):typeof r==`function`?r.apply(e,a):r,u=Math.max(s[1][0]-s[0][0],s[1][1]-s[0][1]),d=e.__zoom,f=typeof n==`function`?n.apply(e,a):n,p=c(d.invert(l).concat(u/d.k),f.invert(l).concat(u/f.k));return function(e){if(e===1)e=f;else{var t=p(e),n=u/t[2];e=new X(n,l[0]-t[0]*n,l[1]-t[1]*n)}o.zoom(null,e)}})}function S(e,t,n){return!n&&e.__zooming||new ee(e,t)}function ee(e,n){this.that=e,this.args=n,this.active=0,this.sourceEvent=null,this.extent=t.apply(e,n),this.taps=0}ee.prototype={event:function(e){return e&&(this.sourceEvent=e),this},start:function(){return++this.active===1&&(this.that.__zooming=this,this.emit(`start`)),this},zoom:function(e,t){return this.mouse&&e!==`mouse`&&(this.mouse[1]=t.invert(this.mouse[0])),this.touch0&&e!==`touch`&&(this.touch0[1]=t.invert(this.touch0[0])),this.touch1&&e!==`touch`&&(this.touch1[1]=t.invert(this.touch1[0])),this.that.__zoom=t,this.emit(`zoom`),this},end:function(){return--this.active===0&&(delete this.that.__zooming,this.emit(`end`)),this},emit:function(e){var t=A(this.that).datum();l.call(e,this.that,new Uo(e,{sourceEvent:this.sourceEvent,target:_,transform:this.that.__zoom,dispatch:l}),t)}};function te(t,...i){if(!e.apply(this,arguments))return;var s=S(this,i).event(t),c=this.__zoom,l=Math.max(a[0],Math.min(a[1],c.k*2**r.apply(this,arguments))),u=j(t);if(s.wheel)(s.mouse[0][0]!==u[0]||s.mouse[0][1]!==u[1])&&(s.mouse[1]=c.invert(s.mouse[0]=u)),clearTimeout(s.wheel);else if(c.k===l)return;else s.mouse=[u,c.invert(u)],ka(this),s.start();qo(t),s.wheel=setTimeout(d,m),s.zoom(`mouse`,n(y(v(c,l),s.mouse[0],s.mouse[1]),s.extent,o));function d(){s.wheel=null,s.end()}}function ne(t,...r){if(f||!e.apply(this,arguments))return;var i=t.currentTarget,a=S(this,r,!0).event(t),s=A(t.view).on(`mousemove.zoom`,d,!0).on(`mouseup.zoom`,p,!0),c=j(t,i),l=t.clientX,u=t.clientY;Bo(t.view),Ko(t),a.mouse=[c,this.__zoom.invert(c)],ka(this),a.start();function d(e){if(qo(e),!a.moved){var t=e.clientX-l,r=e.clientY-u;a.moved=t*t+r*r>h}a.event(e).zoom(`mouse`,n(y(a.that.__zoom,a.mouse[0]=j(e,i),a.mouse[1]),a.extent,o))}function p(e){s.on(`mousemove.zoom mouseup.zoom`,null),Vo(e.view,a.moved),qo(e),a.event(e).end()}}function re(r,...i){if(e.apply(this,arguments)){var a=this.__zoom,c=j(r.changedTouches?r.changedTouches[0]:r,this),l=a.invert(c),u=a.k*(r.shiftKey?.5:2),d=n(y(v(a,u),c,l),t.apply(this,i),o);qo(r),s>0?A(this).transition().duration(s).call(x,d,c,r):A(this).call(_.transform,d,c,r)}}function C(t,...n){if(e.apply(this,arguments)){var r=t.touches,i=r.length,a=S(this,n,t.changedTouches.length===i).event(t),o,s,c,l;for(Ko(t),s=0;s<i;++s)c=r[s],l=j(c,this),l=[l,this.__zoom.invert(l),c.identifier],a.touch0?!a.touch1&&a.touch0[2]!==l[2]&&(a.touch1=l,a.taps=0):(a.touch0=l,o=!0,a.taps=1+!!u);u&&=clearTimeout(u),o&&(a.taps<2&&(d=l[0],u=setTimeout(function(){u=null},p)),ka(this),a.start())}}function ie(e,...t){if(this.__zooming){var r=S(this,t).event(e),i=e.changedTouches,a=i.length,s,c,l,u;for(qo(e),s=0;s<a;++s)c=i[s],l=j(c,this),r.touch0&&r.touch0[2]===c.identifier?r.touch0[0]=l:r.touch1&&r.touch1[2]===c.identifier&&(r.touch1[0]=l);if(c=r.that.__zoom,r.touch1){var d=r.touch0[0],f=r.touch0[1],p=r.touch1[0],m=r.touch1[1],h=(h=p[0]-d[0])*h+(h=p[1]-d[1])*h,g=(g=m[0]-f[0])*g+(g=m[1]-f[1])*g;c=v(c,Math.sqrt(h/g)),l=[(d[0]+p[0])/2,(d[1]+p[1])/2],u=[(f[0]+m[0])/2,(f[1]+m[1])/2]}else if(r.touch0)l=r.touch0[0],u=r.touch0[1];else return;r.zoom(`touch`,n(y(c,l,u),r.extent,o))}}function ae(e,...t){if(this.__zooming){var n=S(this,t).event(e),r=e.changedTouches,i=r.length,a,o;for(Ko(e),f&&clearTimeout(f),f=setTimeout(function(){f=null},p),a=0;a<i;++a)o=r[a],n.touch0&&n.touch0[2]===o.identifier?delete n.touch0:n.touch1&&n.touch1[2]===o.identifier&&delete n.touch1;if(n.touch1&&!n.touch0&&(n.touch0=n.touch1,delete n.touch1),n.touch0)n.touch0[1]=this.__zoom.invert(n.touch0[0]);else if(n.end(),n.taps===2&&(o=j(o,this),Math.hypot(d[0]-o[0],d[1]-o[1])<g)){var s=A(this).on(`dblclick.zoom`);s&&s.apply(this,arguments)}}}return _.wheelDelta=function(e){return arguments.length?(r=typeof e==`function`?e:Ho(+e),_):r},_.filter=function(t){return arguments.length?(e=typeof t==`function`?t:Ho(!!t),_):e},_.touchable=function(e){return arguments.length?(i=typeof e==`function`?e:Ho(!!e),_):i},_.extent=function(e){return arguments.length?(t=typeof e==`function`?e:Ho([[+e[0][0],+e[0][1]],[+e[1][0],+e[1][1]]]),_):t},_.scaleExtent=function(e){return arguments.length?(a[0]=+e[0],a[1]=+e[1],_):[a[0],a[1]]},_.translateExtent=function(e){return arguments.length?(o[0][0]=+e[0][0],o[1][0]=+e[1][0],o[0][1]=+e[0][1],o[1][1]=+e[1][1],_):[[o[0][0],o[0][1]],[o[1][0],o[1][1]]]},_.constrain=function(e){return arguments.length?(n=e,_):n},_.duration=function(e){return arguments.length?(s=+e,_):s},_.interpolate=function(e){return arguments.length?(c=e,_):c},_.on=function(){var e=l.on.apply(l,arguments);return e===l?_:e},_.clickDistance=function(e){return arguments.length?(h=(e=+e)*e,_):Math.sqrt(h)},_.tapDistance=function(e){return arguments.length?(g=+e,_):g},_}function ts(e){var t=0,n=e.children,r=n&&n.length;if(!r)t=1;else for(;--r>=0;)t+=n[r].value;e.value=t}function ns(){return this.eachAfter(ts)}function rs(e,t){let n=-1;for(let r of this)e.call(t,r,++n,this);return this}function is(e,t){for(var n=this,r=[n],i,a,o=-1;n=r.pop();)if(e.call(t,n,++o,this),i=n.children)for(a=i.length-1;a>=0;--a)r.push(i[a]);return this}function as(e,t){for(var n=this,r=[n],i=[],a,o,s,c=-1;n=r.pop();)if(i.push(n),a=n.children)for(o=0,s=a.length;o<s;++o)r.push(a[o]);for(;n=i.pop();)e.call(t,n,++c,this);return this}function os(e,t){let n=-1;for(let r of this)if(e.call(t,r,++n,this))return r}function ss(e){return this.eachAfter(function(t){for(var n=+e(t.data)||0,r=t.children,i=r&&r.length;--i>=0;)n+=r[i].value;t.value=n})}function cs(e){return this.eachBefore(function(t){t.children&&t.children.sort(e)})}function ls(e){for(var t=this,n=us(t,e),r=[t];t!==n;)t=t.parent,r.push(t);for(var i=r.length;e!==n;)r.splice(i,0,e),e=e.parent;return r}function us(e,t){if(e===t)return e;var n=e.ancestors(),r=t.ancestors(),i=null;for(e=n.pop(),t=r.pop();e===t;)i=e,e=n.pop(),t=r.pop();return i}function ds(){for(var e=this,t=[e];e=e.parent;)t.push(e);return t}function fs(){return Array.from(this)}function ps(){var e=[];return this.eachBefore(function(t){t.children||e.push(t)}),e}function ms(){var e=this,t=[];return e.each(function(n){n!==e&&t.push({source:n.parent,target:n})}),t}function*hs(){var e=this,t,n=[e],r,i,a;do for(t=n.reverse(),n=[];e=t.pop();)if(yield e,r=e.children)for(i=0,a=r.length;i<a;++i)n.push(r[i]);while(n.length)}function gs(e,t){e instanceof Map?(e=[void 0,e],t===void 0&&(t=ys)):t===void 0&&(t=vs);for(var n=new Ss(e),r,i=[n],a,o,s,c;r=i.pop();)if((o=t(r.data))&&(c=(o=Array.from(o)).length))for(r.children=o,s=c-1;s>=0;--s)i.push(a=o[s]=new Ss(o[s])),a.parent=r,a.depth=r.depth+1;return n.eachBefore(xs)}function _s(){return gs(this).eachBefore(bs)}function vs(e){return e.children}function ys(e){return Array.isArray(e)?e[1]:null}function bs(e){e.data.value!==void 0&&(e.value=e.data.value),e.data=e.data.data}function xs(e){var t=0;do e.height=t;while((e=e.parent)&&e.height<++t)}function Ss(e){this.data=e,this.depth=this.height=0,this.parent=null}Ss.prototype=gs.prototype={constructor:Ss,count:ns,each:rs,eachAfter:as,eachBefore:is,find:os,sum:ss,sort:cs,path:ls,ancestors:ds,descendants:fs,leaves:ps,links:ms,copy:_s,[Symbol.iterator]:hs};function Cs(e,t,n,r,i){for(var a=e.children,o,s=-1,c=a.length,l=e.value&&(r-t)/e.value;++s<c;)o=a[s],o.y0=n,o.y1=i,o.x0=t,o.x1=t+=o.value*l}function ws(e,t){return e.parent===t.parent?1:2}function Ts(e){var t=e.children;return t?t[0]:e.t}function Es(e){var t=e.children;return t?t[t.length-1]:e.t}function Ds(e,t,n){var r=n/(t.i-e.i);t.c-=r,t.s+=n,e.c+=r,t.z+=n,t.m+=n}function Os(e){for(var t=0,n=0,r=e.children,i=r.length,a;--i>=0;)a=r[i],a.z+=t,a.m+=t,t+=a.s+(n+=a.c)}function ks(e,t,n){return e.a.parent===t.parent?e.a:n}function As(e,t){this._=e,this.parent=null,this.children=null,this.A=null,this.a=this,this.z=0,this.m=0,this.c=0,this.s=0,this.t=null,this.i=t}As.prototype=Object.create(Ss.prototype);function js(e){for(var t=new As(e,0),n,r=[t],i,a,o,s;n=r.pop();)if(a=n._.children)for(n.children=Array(s=a.length),o=s-1;o>=0;--o)r.push(i=n.children[o]=new As(a[o],o)),i.parent=n;return(t.parent=new As(null,0)).children=[t],t}function Ms(){var e=ws,t=1,n=1,r=null;function i(i){var s=js(i);if(s.eachAfter(a),s.parent.m=-s.z,s.eachBefore(o),r)i.eachBefore(c);else{var l=i,u=i,d=i;i.eachBefore(function(e){e.x<l.x&&(l=e),e.x>u.x&&(u=e),e.depth>d.depth&&(d=e)});var f=l===u?1:e(l,u)/2,p=f-l.x,m=t/(u.x+f+p),h=n/(d.depth||1);i.eachBefore(function(e){e.x=(e.x+p)*m,e.y=e.depth*h})}return i}function a(t){var n=t.children,r=t.parent.children,i=t.i?r[t.i-1]:null;if(n){Os(t);var a=(n[0].z+n[n.length-1].z)/2;i?(t.z=i.z+e(t._,i._),t.m=t.z-a):t.z=a}else i&&(t.z=i.z+e(t._,i._));t.parent.A=s(t,i,t.parent.A||r[0])}function o(e){e._.x=e.z+e.parent.m,e.m+=e.parent.m}function s(t,n,r){if(n){for(var i=t,a=t,o=n,s=i.parent.children[0],c=i.m,l=a.m,u=o.m,d=s.m,f;o=Es(o),i=Ts(i),o&&i;)s=Ts(s),a=Es(a),a.a=t,f=o.z+u-i.z-c+e(o._,i._),f>0&&(Ds(ks(o,t,r),t,f),c+=f,l+=f),u+=o.m,c+=i.m,d+=s.m,l+=a.m;o&&!Es(a)&&(a.t=o,a.m+=u-l),i&&!Ts(s)&&(s.t=i,s.m+=c-d,r=t)}return r}function c(e){e.x*=t,e.y=e.depth*n}return i.separation=function(t){return arguments.length?(e=t,i):e},i.size=function(e){return arguments.length?(r=!1,t=+e[0],n=+e[1],i):r?null:[t,n]},i.nodeSize=function(e){return arguments.length?(r=!0,t=+e[0],n=+e[1],i):r?[t,n]:null},i}function Ns(e,t,n,r,i){for(var a=e.children,o,s=-1,c=a.length,l=e.value&&(i-n)/e.value;++s<c;)o=a[s],o.x0=t,o.x1=r,o.y0=n,o.y1=n+=o.value*l}var Ps=(1+Math.sqrt(5))/2;function Fs(e,t,n,r,i,a){for(var o=[],s=t.children,c,l,u=0,d=0,f=s.length,p,m,h=t.value,g,_,v,y,b,x,S;u<f;){p=i-n,m=a-r;do g=s[d++].value;while(!g&&d<f);for(_=v=g,x=Math.max(m/p,p/m)/(h*e),S=g*g*x,b=Math.max(v/S,S/_);d<f;++d){if(g+=l=s[d].value,l<_&&(_=l),l>v&&(v=l),S=g*g*x,y=Math.max(v/S,S/_),y>b){g-=l;break}b=y}o.push(c={value:g,dice:p<m,children:s.slice(u,d)}),c.dice?Cs(c,n,r,i,h?r+=m*g/h:a):Ns(c,n,r,h?n+=p*g/h:i,a),h-=g,u=d}return o}(function e(t){function n(e,n,r,i,a){Fs(t,e,n,r,i,a)}return n.ratio=function(t){return e((t=+t)>1?t:1)},n})(Ps),(function e(t){function n(e,n,r,i,a){if((o=e._squarify)&&o.ratio===t)for(var o,s,c,l,u=-1,d,f=o.length,p=e.value;++u<f;){for(s=o[u],c=s.children,l=s.value=0,d=c.length;l<d;++l)s.value+=c[l].value;s.dice?Cs(s,n,r,i,p?r+=(a-r)*s.value/p:a):Ns(s,n,r,p?n+=(i-n)*s.value/p:i,a),p-=s.value}else e._squarify=o=Fs(t,e,n,r,i,a),o.ratio=t}return n.ratio=function(t){return e((t=+t)>1?t:1)},n})(Ps);var Is={comfortable:{breadth:132,depth:150},compact:{breadth:92,depth:112}},Ls=`\0root`,Rs=()=>({positions:new Map,bounds:{minX:0,minY:0,maxX:0,maxY:0}}),zs=(e,t,n)=>n===`vertical`?{x:e,y:t}:{x:t,y:e};function Bs(e){let t={minX:1/0,minY:1/0,maxX:-1/0,maxY:-1/0};for(let n of e.values())t.minX=Math.min(t.minX,n.x),t.minY=Math.min(t.minY,n.y),t.maxX=Math.max(t.maxX,n.x),t.maxY=Math.max(t.maxY,n.y);return{positions:e,bounds:t}}function Vs(e,t,n,r=`tree`){return r===`tiered`?Gs(e,t,n):Hs(e,t,n)}function Hs(e,t,n){if(e.roots.length===0)return Rs();let{breadth:r,depth:i}=Is[t],a=gs(Ls,t=>t===Ls?e.roots:e.children.get(t)),o=Ms().nodeSize([r,i]).separation((e,t)=>e.parent===t.parent?1:1.25)(a),s=new Map,c=new Map;for(let t of o.descendants()){if(t.data===Ls)continue;let a=(t.depth-1)*i;s.set(t.data,zs(t.x,a,n));let o=e.uplinks.get(t.data)??[];if(o.length===0)continue;let l=c.get(a-i)??[];c.set(a-i,l),o.forEach((e,n)=>l.push({id:e,along:t.x+(n-(o.length-1)/2)*r}))}for(let[e,t]of c)t.sort((e,t)=>e.along-t.along),t.forEach((i,a)=>{a>0&&(i.along=Math.max(i.along,t[a-1].along+r)),s.set(i.id,zs(i.along,e,n))});return Bs(s)}var Z={wan:0,gateway:1,switch:2,access_point:3,other:4,client:5},Us=Z.client+1,Ws=e=>e.type===`group`?Z.client:Z[e.node.kind]??Z.other;function Gs(e,t,n){if(e.roots.length===0)return Rs();let{breadth:r,depth:i}=Is[t],a=new Map,o=[...e.roots].reverse(),s=[],c=Array(Us).fill(0);for(;o.length>0;){let t=o.pop();s.push(t);let n=Ws(e.visuals.get(t)),r=e.parentOf.get(t),i=r===void 0?void 0:a.get(r),l=Math.max(n,i?.tier??n),u={tier:l,sub:i?.tier===l?i.sub+1:0};a.set(t,u),c[u.tier]=Math.max(c[u.tier],u.sub+1);for(let n of e.uplinks.get(t)??[])s.push(n),a.set(n,{tier:Z.wan,sub:0}),c[Z.wan]=1;o.push(...[...e.children.get(t)??[]].reverse())}let l=[],u=0;for(let e of c)l.push(u),u+=e;let d=Array.from({length:u},()=>[]);for(let e of s){let{tier:t,sub:n}=a.get(e);d[l[t]+n].push(e)}let f=new Map;return d.forEach((e,t)=>{e.forEach((a,o)=>{let s=(o-(e.length-1)/2)*r;f.set(a,zs(s,t*i,n))})}),Bs(f)}function Ks(e,t,n,r=48){let i=Math.max(e.maxX-e.minX,1),a=Math.max(e.maxY-e.minY,1),o=Math.min(1.5,Math.max(.2,Math.min((t-2*r)/i,(n-2*r)/a))),s=(e.minX+e.maxX)/2,c=(e.minY+e.maxY)/2;return{k:o,x:t/2-s*o,y:n/2-c*o}}function qs(e,t,n,r,i=120){let a=new Set;for(let[o,s]of e.positions){let e=s.x*t.k+t.x,c=s.y*t.k+t.y;e>=-i&&e<=n+i&&c>=-i&&c<=r+i&&a.add(o)}return a}function Js(e,t,n,r){let i=r===`vertical`,a={parent:i?`ArrowUp`:`ArrowLeft`,child:i?`ArrowDown`:`ArrowRight`,previous:i?`ArrowLeft`:`ArrowUp`,next:i?`ArrowRight`:`ArrowDown`};if(!Object.values(a).includes(n))return;if(t===void 0||!e.visuals.has(t))return e.roots[0];let o=ot(e,t),s=o.indexOf(t);switch(n){case a.parent:return e.parentOf.get(t)??e.uplinks.get(t)?.[0]??t;case a.child:return e.children.get(t)?.[0]??e.uplinkOwner.get(t)??t;case a.previous:return o[s-1]??t;default:return o[s+1]??t}}var Ys={comfortable:22,compact:16},Xs=250,Zs=22;function Qs(e,t){if(e.type===`wheel`){let n=e;return t&&!n.ctrlKey&&!n.metaKey?`hint`:`zoom`}let n=e;return n.ctrlKey||(n.button??0)!==0?`ignore`:`zoom`}var $s=class extends v{static properties={model:{attribute:!1},density:{attribute:!1},orientation:{attribute:!1},layoutMode:{attribute:!1},showLabels:{attribute:!1},selectedId:{attribute:!1},localize:{attribute:!1},siteName:{attribute:!1},ctrlZoom:{attribute:!1},reducedMotion:{attribute:!1},focusId:{state:!0},hintVisible:{state:!0}};layout;entering=new Set;exiting=new Map;lastVisuals=new Map;exitTimer;hintTimer;transform={x:0,y:0,k:1};width=0;height=0;fitted=!1;zoomBehavior;resizeObserver;gesture=new Ut;constructor(){super(),this.density=`comfortable`,this.orientation=`vertical`,this.layoutMode=`tree`,this.showLabels=!0,this.siteName=``,this.ctrlZoom=!0,this.reducedMotion=!1,this.hintVisible=!1}connectedCallback(){super.connectedCallback(),this.resizeObserver=new ResizeObserver(e=>{let t=e[0]?.contentRect;t&&this.setViewportSize(t.width,t.height)}),this.resizeObserver.observe(this)}disconnectedCallback(){super.disconnectedCallback(),this.resizeObserver?.disconnect(),this.exitTimer!==void 0&&clearTimeout(this.exitTimer),this.hintTimer!==void 0&&clearTimeout(this.hintTimer),this.exitTimer=void 0,this.hintTimer=void 0}setViewportSize(e,t){this.width=e,this.height=t,this.fitted?this.needsCulling&&this.requestUpdate():this.tryInitialFit()}get needsCulling(){return(this.layout?.positions.size??0)>300}get svgEl(){return this.renderRoot.querySelector(`svg.canvas`)}willUpdate(e){if(e.has(`layoutMode`)&&(this.fitted=!1),this.model&&(e.has(`model`)||e.has(`density`)||e.has(`orientation`)||e.has(`layoutMode`))){let e=Vs(this.model,this.density,this.orientation,this.layoutMode),t=this.layout;this.entering=new Set(t?[...e.positions.keys()].filter(e=>!t.positions.has(e)):[]);for(let t of e.positions.keys())this.exiting.delete(t);if(t&&!this.reducedMotion){for(let[n,r]of t.positions){let t=this.lastVisuals.get(n);!e.positions.has(n)&&t&&this.exiting.set(n,{point:r,visual:t})}this.scheduleExitCleanup()}this.layout=e,this.lastVisuals=new Map(this.model.visuals),this.focusId!==void 0&&this.model.visuals.has(this.focusId)||(this.focusId=this.model.roots[0])}}firstUpdated(){let e=this.svgEl;e&&(this.zoomBehavior=es().scaleExtent([.2,4]).extent(()=>[[0,0],[Math.max(this.width,1),Math.max(this.height,1)]]).filter(e=>{let t=Qs(e,this.ctrlZoom);return t===`hint`&&this.flashHint(),t===`zoom`}).on(`zoom`,e=>this.onZoom(e.transform)).on(`end`,()=>{this.needsCulling&&this.requestUpdate()}),A(e).call(this.zoomBehavior).on(`dblclick.zoom`,null),this.tryInitialFit())}updated(){this.tryInitialFit()}tryInitialFit(){this.fitted||!this.layout||!this.zoomBehavior||this.width<=0||this.height<=0||(this.fitted=!0,this.fit(!1))}onZoom(e){this.transform={x:e.x,y:e.y,k:e.k},this.renderRoot.querySelector(`g.viewport`)?.setAttribute(`transform`,this.transformAttr())}transformAttr(){let{x:e,y:t,k:n}=this.transform;return`translate(${e},${t}) scale(${n})`}fit(e=!0){let t=this.svgEl;if(!this.layout||!this.zoomBehavior||!t)return;let n=Ks(this.layout.bounds,this.width,this.height),r=Wo.translate(n.x,n.y).scale(n.k);e&&!this.reducedMotion?this.zoomBehavior.transform(A(t).transition().duration(300),r):this.zoomBehavior.transform(A(t),r)}zoomBy(e){let t=this.svgEl;this.zoomBehavior&&t&&(this.reducedMotion?this.zoomBehavior.scaleBy(A(t),e):this.zoomBehavior.scaleBy(A(t).transition().duration(200),e))}async focusNode(e){this.focusId=e,await this.updateComplete;let t=this.layout?.positions.get(e),n=this.svgEl;if(t&&n&&this.zoomBehavior&&this.width>0){let e=t.x*this.transform.k+this.transform.x,r=t.y*this.transform.k+this.transform.y;(e<40||e>this.width-40||r<40||r>this.height-40)&&(this.zoomBehavior.translateTo(A(n),t.x,t.y),await this.updateComplete)}for(let t of this.renderRoot.querySelectorAll(`g.nodes g.node`))t.getAttribute(`data-id`)===e&&t.focus()}flashHint(){this.hintVisible=!0,this.hintTimer!==void 0&&clearTimeout(this.hintTimer),this.hintTimer=setTimeout(()=>{this.hintVisible=!1},1500)}scheduleExitCleanup(){this.exiting.size!==0&&this.exitTimer===void 0&&(this.exitTimer=setTimeout(()=>{this.exitTimer=void 0,this.exiting.clear(),this.requestUpdate()},Xs))}highlightedPath(){let e=new Set,t=this.model;if(!t||this.selectedId===void 0)return e;let n=t.visuals.has(this.selectedId)?this.selectedId:st(t,this.selectedId)?.id;for(;n!==void 0;)e.add(n),n=t.parentOf.get(n)??t.uplinkOwner.get(n);return e}onKeydown(e){let t=this.model;if(!t)return;let n=Js(t,this.focusId,e.key,this.orientation);n===void 0?(e.key===`Enter`||e.key===` `)&&this.focusId!==void 0?(e.preventDefault(),o(this,`uit-activate`,{id:this.focusId})):e.key===`+`||e.key===`=`?(e.preventDefault(),this.zoomBy(1.25)):e.key===`-`?(e.preventDefault(),this.zoomBy(.8)):e.key===`0`?(e.preventDefault(),this.fit()):(e.key===`ContextMenu`||e.key===`F10`&&e.shiftKey)&&this.focusId!==void 0&&(e.preventDefault(),o(this,`uit-context`,{id:this.focusId})):(e.preventDefault(),this.focusNode(n))}onPointerDown(e){this.gesture.down(e)}onPointerMove(e){this.gesture.move(e)}onPointerEnd(e){this.gesture.end(e)}onContextMenu(e,t){e.preventDefault(),this.gesture.allowsMenu&&o(this,`uit-context`,{id:t})}render(){let{model:e,layout:t,localize:n}=this;if(!e||!t||!n)return b;let r=Ys[this.density],i=[...t.positions.keys()],a=[...e.links.values()],o=e.uplinkLinks;if(this.needsCulling&&this.width>0){let e=qs(t,this.transform,this.width,this.height);this.focusId!==void 0&&e.add(this.focusId),this.selectedId!==void 0&&e.add(this.selectedId),i=i.filter(t=>e.has(t));let n=t=>e.has(t.childId)||e.has(t.parentId);a=a.filter(n),o=o.filter(n)}let s=this.highlightedPath();return u`
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
            ${D(a,e=>e.childId,e=>this.renderLink(e,t,s))}
            ${D(o,e=>e.parentId,e=>this.renderLink(e,t,s,!0))}
          </g>
          <g class="nodes">
            ${D(i,e=>e,n=>this.renderNode(e,e.visuals.get(n),t.positions.get(n),r,s,!0))}
          </g>
          <g class="exits" aria-hidden="true">
            ${D([...this.exiting],([e])=>e,([,t])=>this.renderGhost(e,t,r))}
          </g>
        </g>
      </svg>
      <div class="hint" aria-hidden="true" ?hidden=${!this.hintVisible}>
        ${n(`zoom.hint`)}
      </div>
    `}renderLink(e,t,n,r=!1){let i=t.positions.get(e.parentId),a=t.positions.get(e.childId);if(!i||!a)return b;let o=this.orientation===`vertical`?(i.y+a.y)/2:(i.x+a.x)/2,s=this.orientation===`vertical`?`M${i.x},${i.y} C${i.x},${o} ${a.x},${o} ${a.x},${a.y}`:`M${i.x},${i.y} C${o},${i.y} ${o},${a.y} ${a.x},${a.y}`,c=this.showLabels?_t(e.edge):``,l=[`link`,e.edge?.medium??`unknown`,e.viaHidden.length>0?`via-hidden`:``,n.has(r?e.parentId:e.childId)?`on-path`:``,r&&this.model?.nodes.get(e.parentId)?.state===`offline`?`down`:``];return g`<path class=${l.join(` `)} d=${s}></path>${c?g`<text class="link-label" x=${(i.x+a.x)/2} y=${(i.y+a.y)/2}>${c}</text>`:b}`}renderNode(e,t,n,r,i,a){let s=this.localize,c=t.id,l=xt(t),u=vt(t,s),d=t.type===`group`?`group`:t.node.kind,f=[`node`,t.type,d,l,c===this.selectedId?`selected`:``,i.has(c)?`on-path`:``,this.entering.has(c)?`enter`:``],p=t.type===`group`?ke:ie(t.node),m=r+16;return g`<g
      class=${f.join(` `)}
      data-id=${c}
      role=${a?`button`:b}
      tabindex=${a?c===this.focusId?0:-1:b}
      aria-label=${a?St(e,t,s):b}
      aria-expanded=${a&&t.type===`group`?String(t.expanded):b}
      style=${`transform: translate(${n.x}px, ${n.y}px)`}
      @click=${a?()=>o(this,`uit-activate`,{id:c}):b}
      @contextmenu=${a?e=>this.onContextMenu(e,c):b}
      @focus=${a?()=>{this.focusId=c}:b}
    >
      <title>${u}</title>
      <circle class="hit" r=${Math.max(r,22)}></circle>
      <circle class="ring" r=${r+5}></circle>
      <circle class="disc" r=${r}></circle>
      <svg class="glyph" x=${-r*.6} y=${-r*.6} width=${r*1.2} height=${r*1.2} viewBox="0 0 24 24" aria-hidden="true">
        <path d=${p}></path>
      </svg>
      <circle class="status" cx=${r*.72} cy=${-r*.72} r=${Math.max(4,r*.24)}></circle>
      ${t.type===`group`&&(!this.showLabels||t.id===`group:root`||t.id===`group:unconnected`)?g`<text class="badge" x=${r*.95} y=${r+2}>${t.counts.total}</text>`:b}
      ${this.showLabels?g`<text class="label" y=${m}>${pt(u,Zs)}</text>`:b}
      ${l===`offline`?g`<text class="state-text" y=${this.showLabels?m+14:m}>${s(bt(t))}</text>`:b}
    </g>`}renderGhost(e,t,n){return this.renderNode(e,t.visual,t.point,n,new Set,!1)}static styles=[c,re`
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
    `]};T(`uit-graph-view`,$s);function ec(e,t){return e.type===`group`?e.members.some(e=>e.name.toLowerCase().includes(t)):e.node.name.toLowerCase().includes(t)}function tc(e,t,n){let r=n.trim().toLowerCase(),i;if(r){i=new Set;for(let[t,n]of e.visuals)if(ec(n,r))for(let n=t;n!==void 0&&!i.has(n);n=e.parentOf.get(n)??e.uplinkOwner.get(n))i.add(n)}let a=[],o=(n,s,c)=>{let l=n.flatMap(t=>[...e.uplinks.get(t)??[],t]),u=i?l.filter(e=>i.has(e)):l;u.forEach((n,i)=>{let l=e.visuals.get(n),d=e.children.get(n)??[],f=l.type===`group`,p=f||d.length>0,m=f?l.expanded:r!==``||!t.has(n);a.push({id:n,visual:l,level:s,posinset:i+1,setsize:u.length,hasChildren:p,expanded:p&&m,parentId:c}),m&&d.length>0&&o(d,s+1,n)})};return o(e.roots,1,void 0),a}var nc=class extends v{static properties={model:{attribute:!1},selectedId:{attribute:!1},localize:{attribute:!1},siteName:{attribute:!1},query:{state:!0},collapsed:{state:!0},focusId:{state:!0}};rows=[];gesture=new Ut;typeahead=``;typeaheadTimer;constructor(){super(),this.siteName=``,this.query=``,this.collapsed=new Set}willUpdate(){this.model&&(this.rows=tc(this.model,this.collapsed,this.query),this.rows.some(e=>e.id===this.focusId)||(this.focusId=this.rows[0]?.id))}render(){let{model:e,localize:t}=this;return!e||!t?b:u`
      <div class="search">
        <input
          type="search"
          .value=${this.query}
          placeholder=${t(`list.search`)}
          aria-label=${t(`list.search`)}
          @input=${e=>{this.query=e.target.value}}
        />
      </div>
      ${this.rows.length===0?u`<p class="empty">${t(`list.no_matches`)}</p>`:u`<div
            class="tree"
            role="tree"
            aria-label=${t(`list.label`,{site:this.siteName})}
            @keydown=${this.onKeydown}
          >
            ${D(this.rows,e=>e.id,n=>this.renderRow(e,n,t))}
          </div>`}
    `}renderRow(e,t,n){let r=t.visual,a=vt(r,n),s=xt(r);return u`<div
      class="row ${s} ${t.id===this.selectedId?`selected`:``}"
      role="treeitem"
      data-id=${t.id}
      aria-level=${t.level}
      aria-setsize=${t.setsize}
      aria-posinset=${t.posinset}
      aria-selected=${String(t.id===this.selectedId)}
      aria-expanded=${t.hasChildren?String(t.expanded):b}
      aria-label=${St(e,r,n)}
      tabindex=${t.id===this.focusId?0:-1}
      style=${`--level: ${t.level}`}
      @click=${()=>o(this,`uit-activate`,{id:t.id})}
      @pointerdown=${e=>this.gesture.down(e)}
      @pointermove=${e=>this.gesture.move(e)}
      @pointerup=${e=>this.gesture.end(e)}
      @pointercancel=${e=>this.gesture.end(e)}
      @contextmenu=${e=>{e.preventDefault(),this.gesture.allowsMenu&&o(this,`uit-context`,{id:t.id})}}
      @focus=${()=>{this.focusId=t.id}}
    >
      <span
        class="chevron"
        aria-hidden="true"
        @click=${e=>{e.stopPropagation(),t.hasChildren&&this.toggle(t)}}
        >${t.hasChildren?i(t.expanded?xe:h):b}</span
      >
      ${i(r.type===`group`?ke:ie(r.node))}
      <span class="name" title=${a}>${a}</span>
      ${r.type===`group`?u`<span class="count">${r.counts.total}</span>`:u`<span class="dot" aria-hidden="true"></span>${s===`online`?b:u`<span class="state-text"
                  >${n(bt(r))}</span
                >`}`}
    </div>`}toggle(e){if(e.visual.type===`group`){o(this,`uit-toggle-group`,{id:e.id});return}let t=new Set(this.collapsed);t.has(e.id)?t.delete(e.id):t.add(e.id),this.collapsed=t}onKeydown(e){let t=this.rows,n=t.findIndex(e=>e.id===this.focusId),r=t[n];if(!r)return;if(e.key===`ContextMenu`||e.key===`F10`&&e.shiftKey){e.preventDefault(),o(this,`uit-context`,{id:r.id});return}let i;switch(e.key){case`ArrowDown`:i=t[n+1]?.id;break;case`ArrowUp`:i=t[n-1]?.id;break;case`Home`:i=t[0]?.id;break;case`End`:i=t.at(-1)?.id;break;case`ArrowRight`:r.hasChildren&&!r.expanded?this.toggle(r):r.expanded&&(i=t[n+1]?.id);break;case`ArrowLeft`:r.expanded?this.toggle(r):i=r.parentId;break;case`Enter`:case` `:o(this,`uit-activate`,{id:r.id});break;default:e.key.length!==1||e.ctrlKey||e.metaKey||e.altKey||(e.preventDefault(),this.typeAhead(e.key,n));return}e.preventDefault(),i!==void 0&&this.focusRow(i)}typeAhead(e,t){this.typeahead+=e.toLowerCase(),this.typeaheadTimer!==void 0&&clearTimeout(this.typeaheadTimer),this.typeaheadTimer=setTimeout(()=>{this.typeahead=``},500);let n=this.rows;for(let e=1;e<=n.length;e++){let r=n[(t+e)%n.length];if(vt(r.visual,this.localize).toLowerCase().startsWith(this.typeahead)){this.focusRow(r.id);return}}}async focusRow(e){this.focusId=e,await this.updateComplete;let t=this.renderRoot.querySelectorAll(`[role="treeitem"]`);for(let n of t)n.getAttribute(`data-id`)===e&&n.focus()}static styles=[c,De,re`
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
    `]};T(`uit-list-view`,nc);var rc=600,ic=`/config/integrations/integration/unifi_insights`,ac={integration:`action.integration`,edit:`action.edit`},oc={loading:{key:`state.loading`},no_sources:{key:`state.no_sources`,action:`integration`},unconfigured:{key:`state.unconfigured`,action:`edit`},empty:{key:`state.empty`},incompatible:{key:`state.incompatible`},reloading:{key:`state.reconnecting`}},sc=e=>e?.nodes.filter(e=>e.kind!==`client`&&e.kind!==`wan`&&e.state===`offline`).length??0,cc=class extends v{static properties={hass:{attribute:!1},layout:{attribute:!1},config:{state:!0},sources:{state:!0},snapshot:{state:!0},lastGood:{state:!0},error:{state:!0},incompatible:{state:!0},disconnected:{state:!0},ui:{state:!0},view:{state:!0},selectedId:{state:!0},menuRequest:{state:!0},siteOverride:{state:!0},narrow:{state:!0},reducedMotion:{state:!0},announcement:{state:!0}};subscription=new qe({onSnapshot:e=>this.applySnapshot(e),onError:e=>{this.error=e},onIncompatible:()=>{this.incompatible=!0},onDisconnected:()=>{this.disconnected=!0},onReconnected:()=>{this.hass&&this.loadSources(this.hass)}});details=new Pe(()=>this.requestUpdate());selectionScope;buildModel=at();announcer=new Le(e=>{this.announcement=e});sourcesFor;sourcesPending=!1;sourcesRetry;sourcesAttempt=0;sourcesError;boundKey;cardState={phase:`loading`,stale:!1,notices:[]};model;resizeObserver;motionQuery;localizeLang;localizeFn;constructor(){super(),this.incompatible=!1,this.disconnected=!1,this.ui={kinds:new Set(C),clients:`collapsed`,toggledGroups:new Set},this.view=`graph`,this.narrow=!1,this.reducedMotion=!1,this.announcement=``}setConfig(e){let t=se(s(e));this.config=t,this.view=t.view,this.ui={kinds:new Set(t.kinds),clients:t.clients,toggledGroups:new Set},this.siteOverride=void 0}getCardSize(){return 8}getGridOptions(){return{columns:12,rows:8,min_columns:6,min_rows:4}}static getConfigElement(){return document.createElement(je)}static async getStubConfig(e){try{let t=a(await e.callWS({type:r}))[0];if(t)return{type:me,...t.binding}}catch{}return{type:me}}connectedCallback(){super.connectedCallback(),this.resizeObserver=new ResizeObserver(e=>{let t=e[0]?.contentRect.width??0;this.narrow=t>0&&t<rc}),this.resizeObserver.observe(this),this.motionQuery=window.matchMedia(`(prefers-reduced-motion: reduce)`),this.motionQuery.addEventListener(`change`,this.onMotionChange),this.onMotionChange(),this.sync()}disconnectedCallback(){super.disconnectedCallback(),this.subscription.stop(),this.details.stop(),this.menuRequest=void 0,this.announcer.dispose(),this.resizeObserver?.disconnect(),this.resizeObserver=void 0,this.motionQuery?.removeEventListener(`change`,this.onMotionChange),this.sourcesFor=void 0,this.clearSourcesRetry()}shouldUpdate(e){if(e.size!==1||!e.has(`hass`))return!0;let t=e.get(`hass`),n=this.hass;return!t||!n||t.connection!==n.connection||(t.locale?.language??t.language)!==(n.locale?.language??n.language)||w(t)!==w(n)}willUpdate(e){(e.has(`hass`)||e.has(`config`)||e.has(`sources`)||e.has(`siteOverride`))&&this.sync();let t=this.config;if(!t)return;this.cardState=He({sources:this.sources,binding:this.binding,snapshot:this.snapshot,lastGood:this.lastGood,error:this.error,incompatible:this.incompatible,disconnected:this.disconnected,maxClients:t.max_clients}),this.model=this.cardState.render?this.buildModel(this.cardState.render,this.ui):void 0;let n=this.selectedId;n!==void 0&&!(this.model?.visuals.has(n)||this.model?.nodes.has(n))&&this.deselect();let r=this.binding,i=r&&this.selectedId!==void 0?{...r,node_id:this.selectedId}:void 0,a=JSON.stringify(i);a!==this.selectionScope&&(this.selectionScope=a,JSON.stringify(this.menuRequest)!==a&&(this.menuRequest=void 0)),this.details.select(this.isConnected?this.hass:void 0,i)}get binding(){return this.config?this.siteOverride??Oe(this.config,this.sources):void 0}get localize(){let e=this.hass?.locale?.language??this.hass?.language??`en`;return e===this.localizeLang&&this.localizeFn||(this.localizeLang=e,this.localizeFn=Se(e)),this.localizeFn}get graphView(){return this.renderRoot.querySelector(`uit-graph-view`)}sync(){let{hass:e,config:t}=this;if(!this.isConnected||!e||!t)return;this.sourcesFor!==e.connection&&(this.sourcesFor=e.connection,this.disconnected=!1,this.clearSourcesRetry(),this.sourcesAttempt=0,this.loadSources(e));let n=this.binding,r=n?`${n.entry_id}\u0000${n.site_id}`:void 0;r!==this.boundKey&&(this.boundKey=r,this.snapshot=void 0,this.lastGood=void 0,this.error=void 0,this.sourcesError=void 0,this.incompatible=!1,this.deselect()),this.subscription.update(e.connection,n?{...n,max_clients:t.max_clients}:void 0)}async loadSources(e){if(this.sourcesPending)return;this.sourcesPending=!0,this.clearSourcesRetry();let t=e.connection;try{let n=await e.callWS({type:r});if(this.sourcesFor!==t)return;this.sources=n,this.sourcesError&&this.error===this.sourcesError&&(this.error=void 0),this.sourcesError=void 0,n.length>0?this.sourcesAttempt=0:this.scheduleSourcesRetry()}catch(e){if(this.sourcesFor!==t)return;let n=this.sourcesError;this.sourcesError=_e(e),this.error&&this.error!==n||(this.error=this.sourcesError),this.scheduleSourcesRetry()}finally{this.sourcesPending=!1,this.isConnected&&this.sourcesFor!==void 0&&this.sourcesFor!==t&&this.hass&&this.loadSources(this.hass)}}scheduleSourcesRetry(){this.isConnected&&(this.sourcesRetry=setTimeout(()=>{this.sourcesRetry=void 0,this.hass&&this.loadSources(this.hass)},Ke(this.sourcesAttempt++)))}clearSourcesRetry(){this.sourcesRetry!==void 0&&clearTimeout(this.sourcesRetry),this.sourcesRetry=void 0}applySnapshot(e){let t=this.snapshot;this.snapshot=e,this.error=void 0,this.incompatible=!1,this.disconnected=!1,e.status!==`unavailable`&&e.nodes.length>0&&(this.lastGood=e),this.hass&&this.sources!==void 0&&!this.sources.some(t=>t.entry_id===e.entry_id)&&this.loadSources(this.hass);let n=this.localize,r=n(`announce.updated`),i=e.issues[0],a=sc(e);if(e.status===`unavailable`&&i){let t=Be(i,e,this.config?.max_clients??0);r=n(t.key,t.vars)}else a>0&&a!==sc(t)&&(r=n(`announce.offline`,{count:a}));this.announcer.announce(r)}onMotionChange=()=>{this.reducedMotion=this.motionQuery?.matches??!1};onActivate=e=>{let t=e.detail.id;this.model?.visuals.get(t)?.type===`group`&&this.toggleGroup(t),this.selectedId=t};onSelect=e=>{let t=e.detail.id,n=this.model;if(n&&!n.visuals.has(t)){let e=st(n,t);e&&!e.expanded&&this.toggleGroup(e.id)}this.selectedId=t};onToggleGroup=e=>{this.toggleGroup(e.detail.id)};onContext=e=>{let t=e.detail.id;if(!this.model?.visuals.has(t)&&!this.model?.nodes.has(t))return;this.onSelect(e);let n=this.binding;this.menuRequest=n&&!t.startsWith(`group:`)?{...n,node_id:t}:void 0};onClose=()=>{this.deselect()};onKeydown=e=>{e.key===`Escape`&&this.selectedId!==void 0&&!this.renderRoot.querySelector(`uit-detail-panel`)?.menuOpen&&(e.stopPropagation(),this.deselect())};deselect(){this.selectedId=void 0,this.menuRequest=void 0}toggleGroup(e){let t=new Set(this.ui.toggledGroups);t.has(e)?t.delete(e):t.add(e),this.ui={...this.ui,toggledGroups:t}}toggleKind(e){let t=new Set(this.ui.kinds);if(t.has(e)){if(t.size===1)return;t.delete(e)}else t.add(e);this.ui={...this.ui,kinds:t}}runAction(e){t(e===`integration`?ic:`${location.pathname}?edit=1`)}render(){let e=this.config;if(!e)return b;let t=this.localize,n=this.cardState,r=this.model,a=e.title??n.render?.site_name??this.snapshot?.site_name??t(`card.name`),o=n.render?.nodes??[],s=o.filter(e=>e.kind!==`client`&&e.kind!==`wan`).length,c=o.filter(e=>e.kind===`client`).length;return u`<ha-card>
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
            ${i(be)}
          </div>
          <div class="header-text">
            <h2 class="title" title=${a}>${a}</h2>
            ${o.length>0?u`<div class="subtitle">
                  ${t(`header.summary`,{devices:s,clients:c})}
                </div>`:b}
          </div>
          ${this.renderSiteSelector(e,t)}
        </header>
        ${r?this.renderToolbar(t):b}
        ${r?this.renderNotices(n.notices,t):b}
        <div class="body">
          ${r?this.renderContent(r,n,e,t):this.renderMessage(n,t)}
        </div>
        <div class="sr-only" role="status" aria-live="polite">
          ${this.announcement}
        </div>
      </div>
    </ha-card>`}renderSiteSelector(e,t){let n=a(this.sources??[]);if(!e.show_site_selector||n.length<2)return b;let r=this.binding;return u`<label class="site">
      <span class="sr-only">${t(`toolbar.site`)}</span>
      <select
        @change=${e=>{let t=n[Number(e.target.value)];t&&(this.siteOverride=t.binding)}}
      >
        ${n.map((e,t)=>u`<option
              value=${t}
              ?selected=${l(e.binding,r)}
            >
              ${e.label}
            </option>`)}
      </select>
    </label>`}renderToolbar(e){let t=u`
      <div class="group" role="group" aria-label=${e(`toolbar.view`)}>
        ${ae.map(t=>u`<button
              aria-pressed=${String(this.view===t)}
              @click=${()=>{this.view=t}}
            >
              ${e(dt[t])}
            </button>`)}
      </div>
      <div class="group" role="group" aria-label=${e(`toolbar.filters`)}>
        ${C.filter(e=>this.config?.show_wan!==!1||e!==`wan`).map(t=>u`<button
              aria-pressed=${String(this.ui.kinds.has(t))}
              @click=${()=>this.toggleKind(t)}
            >
              ${e(ct[t])}
            </button>`)}
      </div>
      ${this.view===`graph`?u`<div
            class="group"
            role="group"
            aria-label=${e(`toolbar.zoom`)}
          >
            ${this.iconButton(fe,e(`zoom.in`),()=>this.graphView?.zoomBy(1.25))}
            ${this.iconButton(ge,e(`zoom.out`),()=>this.graphView?.zoomBy(.8))}
            ${this.iconButton(Te,e(`zoom.fit`),()=>this.graphView?.fit())}
          </div>`:b}
    `;return this.narrow?u`<details class="toolbar">
          <summary>${e(`toolbar.options`)}</summary>
          <div class="controls">${t}</div>
        </details>`:u`<div class="toolbar">
          <div class="controls">${t}</div>
        </div>`}iconButton(e,t,n){return u`<button
      class="icon-button"
      aria-label=${t}
      title=${t}
      @click=${n}
    >
      ${i(e)}
    </button>`}actionButton(e,t){return u`<button class="action" @click=${()=>this.runAction(e)}>
      ${t(ac[e])}
    </button>`}renderNotices(e,t){return e.length===0?b:u`<ul class="notices">
      ${e.map(e=>u`<li class=${e.severity}>
            <span>${t(e.key,e.vars)}</span>${e.action?this.actionButton(e.action,t):b}
          </li>`)}
    </ul>`}renderMessage(e,t){let n=oc[e.phase],r={site:this.snapshot?.site_name??``},i=[];return n&&i.push({...n,vars:r}),i.push(...e.notices),u`<div class="message ${e.phase}">
      ${e.phase===`loading`?u`<svg class="skeleton" viewBox="0 0 120 60" aria-hidden="true">
            <path d="M60 18 L30 37 M60 18 L90 37"></path>
            <circle cx="60" cy="10" r="8"></circle>
            <circle cx="30" cy="45" r="8"></circle>
            <circle cx="90" cy="45" r="8"></circle>
          </svg>`:b}
      ${i.map(e=>u`<p>${t(e.key,e.vars)}</p>
            ${e.action?this.actionButton(e.action,t):b}`)}
    </div>`}renderContent(e,t,n,r){let i=n.density??(this.narrow?`compact`:`comfortable`),a=t.render?.site_name??``,o=t.phase===`reloading`?`${r(`state.stale`)} · ${r(`state.reconnecting`)}`:r(`state.stale`);return u`<div class="content ${t.stale?`stale`:``}">
      ${t.stale?u`<span class="badge stale">${o}</span>`:b}
      ${this.view===`graph`?u`<uit-graph-view
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
          ></uit-graph-view>`:u`<uit-list-view
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
    </div>`}static styles=[c,De,re`
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
    `]},lc=`current`,uc=``,dc={collapsed:`clients.collapsed`,expanded:`clients.expanded`,hidden:`clients.hidden`},fc={auto:`density.auto`,comfortable:`density.comfortable`,compact:`density.compact`},pc={vertical:`orientation.vertical`,horizontal:`orientation.horizontal`},mc={tree:`layout.tree`,tiered:`layout.tiered`},hc={site:`editor.site`,title:`editor.title`,view:`editor.view`,clients:`editor.clients`,kinds:`editor.kinds`,density:`editor.density`,orientation:`editor.orientation`,layout:`editor.layout`,show_site_selector:`editor.show_site_selector`,show_labels:`editor.show_labels`,show_wan:`editor.show_wan`,max_clients:`editor.max_clients`};function Q(e,t,n){return e.map(e=>({value:e,label:n(t[e])}))}var $=(e,t)=>typeof e==`string`&&t.includes(e);function gc(e,t){let{entry_id:n,site_id:r}=e;if(n===void 0||r===void 0)return uc;let i=t.findIndex(e=>l(e.binding,{entry_id:n,site_id:r}));return i>=0?String(i):lc}function _c(e,t,n){let r=t.map((e,t)=>({value:String(t),label:e.label}));return gc(e,t)===`current`&&r.push({value:lc,label:n(`editor.site_unavailable`,{site:e.site_id??``})}),[{name:`site`,selector:{select:{mode:`dropdown`,options:r}}},{name:`title`,selector:{text:{}}},{name:``,type:`grid`,schema:[{name:`view`,selector:{select:{mode:`dropdown`,options:Q(ae,dt,n)}}},{name:`clients`,selector:{select:{mode:`dropdown`,options:Q(de,dc,n)}}},{name:`density`,selector:{select:{mode:`dropdown`,options:Q([`auto`,...Ee],fc,n)}}},{name:`orientation`,selector:{select:{mode:`dropdown`,options:Q(Ae,pc,n)}}},{name:`layout`,selector:{select:{mode:`dropdown`,options:Q(pe,mc,n)}}}]},{name:`kinds`,selector:{select:{multiple:!0,mode:`list`,options:Q(C,ct,n)}}},{name:`show_site_selector`,selector:{boolean:{}}},{name:`show_labels`,selector:{boolean:{}}},{name:`show_wan`,selector:{boolean:{}}},{name:`max_clients`,selector:{number:{min:1,max:500,mode:`box`}}}]}function vc(e,t){return{site:gc(e,t),title:e.title??``,view:e.view??`graph`,clients:e.clients??`collapsed`,density:e.density??`auto`,orientation:e.orientation??`vertical`,layout:e.layout??`tree`,kinds:e.kinds?[...e.kinds]:[...C],show_site_selector:e.show_site_selector??!1,show_labels:e.show_labels??!0,show_wan:e.show_wan??!0,max_clients:e.max_clients??500}}function yc(e,t,n){let r={...t},i=e.site??uc;if(i===uc)delete r.entry_id,delete r.site_id;else if(i!==`current`){let e=n[Number(i)];e&&(r.entry_id=e.binding.entry_id,r.site_id=e.binding.site_id)}e.title?r.title=e.title:delete r.title,$(e.view,ae)&&(r.view=e.view),$(e.clients,de)&&(r.clients=e.clients),$(e.orientation,Ae)&&(r.orientation=e.orientation),e.layout===`tree`?delete r.layout:$(e.layout,pe)&&(r.layout=e.layout),$(e.density,Ee)?r.density=e.density:e.density===`auto`&&delete r.density;let a=(e.kinds??[]).filter(e=>$(e,C));a.length===C.length?delete r.kinds:a.length>0&&(r.kinds=C.filter(e=>a.includes(e))),typeof e.show_site_selector==`boolean`&&(r.show_site_selector=e.show_site_selector),typeof e.show_labels==`boolean`&&(r.show_labels=e.show_labels),e.show_wan===!0?delete r.show_wan:e.show_wan===!1&&(r.show_wan=!1);let o=e.max_clients;return o===500?delete r.max_clients:typeof o==`number`&&Number.isInteger(o)&&o>=1&&o<=500&&(r.max_clients=o),r}async function bc(e=customElements,t=window.loadCardHelpers){e.get(`ha-form`)||await((await t?.())?.createCardElement({type:`entities`,entities:[]})?.constructor)?.getConfigElement?.()}var xc=class extends v{static properties={hass:{attribute:!1},config:{state:!0},sources:{state:!0},formReady:{state:!0}};sourcesRequested=!1;constructor(){super(),this.formReady=!1}setConfig(e){this.config={...e}}connectedCallback(){super.connectedCallback(),bc().catch(()=>void 0).then(()=>{this.formReady=!0})}willUpdate(){this.hass&&!this.sourcesRequested&&(this.sourcesRequested=!0,this.hass.callWS({type:r}).then(e=>{this.sources=e},()=>{this.sources=[]}))}render(){let{hass:e,config:t}=this;if(!e||!t)return b;let n=Se(e.locale?.language??e.language);if(!this.formReady)return u`<p>${n(`state.loading`)}</p>`;let r=a(this.sources??[]);return u`<ha-form
      .hass=${e}
      .data=${vc(t,r)}
      .schema=${_c(t,r,n)}
      .computeLabel=${e=>{let t=hc[e.name];return t?n(t):``}}
      @value-changed=${this.onValueChanged}
    ></ha-form>`}onValueChanged=e=>{e.stopPropagation();let t=this.config;if(!t)return;let n=e.detail.value,r=yc(n,t,a(this.sources??[]));this.config=r,o(this,`config-changed`,{config:r})}};T(he,cc),T(je,xc);var Sc=Se(`en`);window.customCards??=[],window.customCards.some(e=>e.type===`unifi-insights-topology-card`)||window.customCards.push({type:he,name:Sc(`card.name`),description:Sc(`card.description`),preview:!0,documentationURL:`https://github.com/tinkerologie/ha-unifi-insights#network-topology-card`});