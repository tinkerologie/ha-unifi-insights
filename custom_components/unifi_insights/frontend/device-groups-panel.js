import{$ as e,A as t,D as n,P as r,V as i,X as a,a as o,b as s,d as c,f as l,g as u,i as d,k as f,m as p,p as m,q as h,s as g,t as _,y as v}from"./chunks/localize-DuXs5QH_.js";import{o as y,t as b}from"./chunks/repeat-5Yw3wr94.js";var x=`unifi_insights/device_groups/sources`,S=`unifi_insights/device_groups/subscribe`,C=`entry_not_loaded`,w=class extends Error{received;constructor(e){super(`Unsupported device_groups schema_version ${String(e)}`),this.name=`IncompatibleDeviceGroupsSchemaError`,this.received=e}},T=e=>typeof e==`object`&&!!e&&!Array.isArray(e),E=e=>e===null||typeof e==`string`;function D(e,t,n){if(!T(e))throw TypeError(`Device groups row ${n}[${t}] is not an object`);for(let r of[`key`,`name`,`kind`])if(typeof e[r]!=`string`)throw TypeError(`Device groups row ${n}[${t}].${r} is not a string`);for(let r of[`ha_device_id`,`parent_key`,`reason`])if(!E(e[r]))throw TypeError(`Device groups row ${n}[${t}].${r} is not string | null`);if(e.port!==null&&(typeof e.port!=`number`||!Number.isFinite(e.port)))throw TypeError(`Device groups row ${n}[${t}].port is not number | null`)}function O(e,t){if(!T(e))throw TypeError(`Device groups section[${t}] is not an object`);for(let n of[`key`,`kind`])if(typeof e[n]!=`string`)throw TypeError(`Device groups section[${t}].${n} is not a string`);if(!E(e.title))throw TypeError(`Device groups section[${t}].title is not string | null`);if(!Array.isArray(e.rows))throw TypeError(`Device groups section[${t}].rows is not a list`);let n=e.key;e.rows.forEach((e,t)=>D(e,t,n))}function k(e,t){if(typeof e.schema_version!=`number`)throw TypeError(`${t} field schema_version is not a number`);if(e.schema_version!==1)throw new w(e.schema_version)}function A(e){if(!T(e))throw TypeError(`Device groups sources response is not an object`);if(k(e,`Device groups sources`),!Array.isArray(e.entries))throw TypeError(`Device groups sources field entries is not a list`);return e.entries.forEach((e,t)=>{if(!T(e))throw TypeError(`Device groups source entry[${t}] is not an object`);if(typeof e.entry_id!=`string`)throw TypeError(`Device groups source entry[${t}].entry_id is not a string`);if(typeof e.title!=`string`)throw TypeError(`Device groups source entry[${t}].title is not a string`)}),e}function j(e){if(!T(e))throw TypeError(`Device groups snapshot is not an object`);k(e,`Device groups snapshot`);for(let t of[`entry_id`,`status`,`revision`])if(typeof e[t]!=`string`)throw TypeError(`Device groups snapshot field ${t} is not a string`);if(typeof e.stale!=`boolean`)throw TypeError(`Device groups snapshot field stale is not a boolean`);if(!Array.isArray(e.sections))throw TypeError(`Device groups snapshot field sections is not a list`);return e.sections.forEach((e,t)=>O(e,t)),e}var M=new Set([C,`unknown_command`]),N=class{generation=0;connection;entryId;unsubscribe;retryTimer;attempt=0;lastRevision;isLive=!1;terminalDisabled=!1;handlers;random;constructor(e,t=Math.random){this.handlers=e,this.random=t}get live(){return this.isLive}update(e,t){(e!==this.connection||t!==this.entryId)&&(this.stop(),this.connection=e,this.entryId=t,e&&(e.addEventListener(`ready`,this.onReady),e.addEventListener(`disconnected`,this.onDisconnected)),e&&t&&this.open())}stop(){this.generation++,this.clearRetry(),this.attempt=0,this.terminalDisabled=!1,this.release(),this.connection?.removeEventListener(`ready`,this.onReady),this.connection?.removeEventListener(`disconnected`,this.onDisconnected),this.connection=void 0,this.entryId=void 0}clearRetry(){this.retryTimer!==void 0&&clearTimeout(this.retryTimer),this.retryTimer=void 0}drop(){this.generation++,this.clearRetry(),this.unsubscribe=void 0,this.isLive=!1,this.lastRevision=void 0}onDisconnected=()=>{this.drop(),this.handlers.onDisconnected()};onReady=()=>{this.drop(),this.attempt=0,this.entryId&&!this.terminalDisabled&&this.open(),this.handlers.onReconnected()};release(){let e=this.unsubscribe;this.unsubscribe=void 0,this.isLive=!1,this.lastRevision=void 0,e&&e().catch(()=>void 0)}open(){let e=++this.generation,{connection:t,entryId:n}=this;t&&n&&t.subscribeMessage(t=>{e===this.generation&&this.receive(t)},{type:S,entry_id:n},{resubscribe:!1}).then(t=>{e===this.generation?this.unsubscribe=t:t().catch(()=>void 0)},t=>{if(e!==this.generation)return;let r=l(t);this.handlers.onError(r),r.code===`grouping_disabled`?(this.generation++,this.terminalDisabled=!0,this.clearRetry(),this.release(),this.handlers.onGroupingDisabled(n)):M.has(r.code)&&this.scheduleRetry()})}receive(e){let t;try{t=j(e)}catch(e){e instanceof w?(this.generation++,this.clearRetry(),this.release(),this.handlers.onIncompatible()):this.handlers.onError({code:`invalid_payload`,message:String(e)});return}let n=t.status===`entry_unloaded`,r=t.status===`grouping_disabled`;!n&&!r&&(this.isLive=!0,this.attempt=0),(t.revision===``||t.revision!==this.lastRevision)&&(this.lastRevision=t.revision,this.handlers.onSnapshot(t),n?(this.generation++,this.release(),this.scheduleRetry()):r&&(this.generation++,this.terminalDisabled=!0,this.clearRetry(),this.release(),this.handlers.onGroupingDisabled(t.entry_id)))}scheduleRetry(){let e=this.generation,t=y(this.attempt++,this.random);this.retryTimer=setTimeout(()=>{this.retryTimer=void 0,e===this.generation&&this.open()},t)}};function P(e){let t=new Map,n=[];for(let r of e)t.has(r.key)||(t.set(r.key,r),n.push(r));let r=new Set,i=new Set;for(let e of n){if(r.has(e.key)||i.has(e.key))continue;let n=[],a=new Map,o=e.key;for(;o!==null&&t.has(o)&&!r.has(o)&&!i.has(o);){let e=a.get(o);if(e!==void 0){for(let t=e;t<n.length;t++)r.add(n[t]);for(let t=0;t<e;t++)i.add(n[t]);n.length=0;break}a.set(o,n.length),n.push(o),o=t.get(o).parent_key}for(let e of n)i.add(e)}let a=new Map;for(let e of n)a.set(e.key,{row:e,children:[]});let o=[];for(let e of n){let t=a.get(e.key),n=e.parent_key;n!==null&&!r.has(e.key)&&a.has(n)?a.get(n).children.push(t):o.push(t)}return o}function F(e,t){return`${e}\u0000${t}`}function I(e,t,n){let r=[],i=(e,a,o)=>{e.forEach((s,c)=>{let l=s.children.length>0,u=l&&!t.has(F(n,s.row.key));r.push({key:s.row.key,row:s.row,level:a,posinset:c+1,setsize:e.length,hasChildren:l,expanded:u,parentKey:o}),u&&i(s.children,a+1,s.row.key)})};return i(e,1,void 0),r}var L=`unifi-insights-devices-panel`,R=new Set([C,`unknown_command`]),z={no_uplink:`panel.reason.no_uplink`,historical:`panel.reason.historical`,ambiguous:`panel.reason.ambiguous`,parent_not_found:`panel.reason.parent_not_found`,cycle:`panel.reason.cycle`,stale:`panel.reason.stale`};function B(e){switch(e){case`gateway`:return h;case`switch`:return a;case`access_point`:return s;case`network_device`:return i;case`camera`:return n;default:return r}}function V(e){return z[e]??`panel.reason.unknown`}function H(e){return`/config/devices/device/${encodeURIComponent(e)}`}function U(e,t){return e.kind===`unassigned`?t(`panel.section.unassigned`):e.kind===`other`?t(`panel.section.other`):e.title||e.key}var W=class extends m{static properties={hass:{attribute:!1},narrow:{type:Boolean,reflect:!0},route:{attribute:!1},panel:{attribute:!1},entries:{state:!0},selectedEntryId:{state:!0},snapshot:{state:!0},lastGood:{state:!0},error:{state:!0},sourcesError:{state:!0},incompatible:{state:!0},disconnected:{state:!0},groupingDisabled:{state:!0},collapsed:{state:!0},focusBySection:{state:!0}};subscription=new N({onSnapshot:e=>this.applySnapshot(e),onError:e=>{this.error=e},onGroupingDisabled:e=>{this.groupingDisabled=!0,this.hass&&this.loadSources(this.hass,e)},onIncompatible:()=>{this.incompatible=!0},onDisconnected:()=>{this.disconnected=!0},onReconnected:()=>{this.disconnected=!1,this.hass&&this.loadSources(this.hass)}});sourcesFor;sourcesPending=!1;sourcesRetry;sourcesAttempt=0;boundEntryId;localizeLang;localizeFn;typeahead=``;typeaheadTimer;constructor(){super(),this.narrow=!1,this.incompatible=!1,this.disconnected=!1,this.groupingDisabled=!1,this.collapsed=new Set,this.focusBySection=new Map}connectedCallback(){super.connectedCallback(),this.sync()}disconnectedCallback(){super.disconnectedCallback(),this.subscription.stop(),this.sourcesFor=void 0,this.clearSourcesRetry(),this.typeaheadTimer!==void 0&&clearTimeout(this.typeaheadTimer),this.typeaheadTimer=void 0}shouldUpdate(e){if(e.size!==1||!e.has(`hass`))return!0;let t=e.get(`hass`),n=this.hass;return!t||!n||t.connection!==n.connection||(t.locale?.language??t.language)!==(n.locale?.language??n.language)}willUpdate(e){(e.has(`hass`)||e.has(`selectedEntryId`))&&this.sync()}get localize(){let e=this.hass?.locale?.language??this.hass?.language??`en`;return e===this.localizeLang&&this.localizeFn||(this.localizeLang=e,this.localizeFn=_(e)),this.localizeFn}sync(){let{hass:e}=this;this.isConnected&&e&&(this.sourcesFor!==e.connection&&(this.sourcesFor=e.connection,this.disconnected=!1,this.incompatible=!1,this.groupingDisabled=!1,this.sourcesError=void 0,this.clearSourcesRetry(),this.sourcesAttempt=0,this.loadSources(e)),this.selectedEntryId!==this.boundEntryId&&(this.boundEntryId=this.selectedEntryId,this.snapshot=void 0,this.lastGood=void 0,this.error=void 0,this.incompatible=!1,this.selectedEntryId!==void 0&&(this.groupingDisabled=!1)),this.subscription.update(e.connection,this.groupingDisabled?void 0:this.selectedEntryId))}async loadSources(e,t){if(this.sourcesPending)return;this.sourcesPending=!0,this.clearSourcesRetry();let n=e.connection;try{let r=await e.callWS({type:x});if(this.sourcesFor!==n)return;let i=A(r);this.entries=i.entries,this.sourcesError=void 0,this.sourcesAttempt=0,this.reconcileSelectedEntry(i.entries,t)}catch(e){if(this.sourcesFor!==n)return;if(e instanceof w){this.incompatible=!0;return}if(e instanceof TypeError){this.sourcesError={code:`invalid_payload`,message:String(e)};return}let t=l(e);this.sourcesError=t,R.has(t.code)&&this.scheduleSourcesRetry()}finally{this.sourcesPending=!1,this.isConnected&&this.sourcesFor!==void 0&&this.sourcesFor!==n&&this.hass&&this.loadSources(this.hass)}}reconcileSelectedEntry(e,t){if(t!==void 0){let n=e.find(e=>e.entry_id!==t);if(n){this.groupingDisabled=!1,this.selectedEntryId=n.entry_id;return}e.length===0&&(this.selectedEntryId=void 0)}else this.selectedEntryId!==void 0&&e.some(e=>e.entry_id===this.selectedEntryId)||(this.selectedEntryId=e[0]?.entry_id)}scheduleSourcesRetry(){this.isConnected&&(this.sourcesRetry=setTimeout(()=>{this.sourcesRetry=void 0,this.hass&&this.loadSources(this.hass)},y(this.sourcesAttempt++)))}clearSourcesRetry(){this.sourcesRetry!==void 0&&clearTimeout(this.sourcesRetry),this.sourcesRetry=void 0}applySnapshot(e){this.snapshot=e,this.incompatible=!1,this.disconnected=!1,e.status===`ok`&&(this.lastGood=e,this.error=void 0,this.groupingDisabled=!1)}onEntryChange(e){let t=e.target.value;t&&t!==this.selectedEntryId&&(this.groupingDisabled=!1,this.selectedEntryId=t)}toggleCollapse(e){let t=new Set(this.collapsed);t.has(e)?t.delete(e):t.add(e),this.collapsed=t}onLinkClick(e,t){e.metaKey||e.ctrlKey||e.shiftKey||e.altKey||e.button!==0?e.stopPropagation():(e.preventDefault(),e.stopPropagation(),c(t))}activateRow(e,t,n){e.ha_device_id===null?t&&this.toggleCollapse(n):c(H(e.ha_device_id))}onTreeKeydown(e,t,n){if(n.length===0)return;let r=this.focusKeyFor(t,n),i=n.findIndex(e=>e.key===r),a=n[i];if(!a)return;let o=F(t,a.key),s;switch(e.key){case`ArrowDown`:s=n[i+1]?.key;break;case`ArrowUp`:s=n[i-1]?.key;break;case`Home`:s=n[0]?.key;break;case`End`:s=n.at(-1)?.key;break;case`ArrowRight`:a.hasChildren&&!a.expanded?this.toggleCollapse(o):a.expanded&&(s=n[i+1]?.key);break;case`ArrowLeft`:a.expanded?this.toggleCollapse(o):s=a.parentKey;break;case`Enter`:case` `:this.activateRow(a.row,a.hasChildren,o);break;default:e.key.length!==1||e.ctrlKey||e.metaKey||e.altKey||(e.preventDefault(),this.typeAhead(e.key,t,n,i));return}e.preventDefault(),s!==void 0&&this.focusRow(t,s)}typeAhead(e,t,n,r){this.typeahead+=e.toLowerCase(),this.typeaheadTimer!==void 0&&clearTimeout(this.typeaheadTimer),this.typeaheadTimer=setTimeout(()=>{this.typeahead=``},500);for(let e=1;e<=n.length;e++){let i=n[(r+e)%n.length];if(i.row.name.toLowerCase().startsWith(this.typeahead)){this.focusRow(t,i.key);return}}}focusKeyFor(e,t){let n=this.focusBySection.get(e);return n!==void 0&&t.some(e=>e.key===n)?n:t[0]?.key}setFocusKey(e,t){if(this.focusBySection.get(e)===t)return;let n=new Map(this.focusBySection);n.set(e,t),this.focusBySection=n}async focusRow(e,t){this.setFocusKey(e,t),await this.updateComplete;let n=this.renderRoot.querySelectorAll(`[role="tree"]`);for(let r of n)if(r.getAttribute(`data-section`)===e)for(let e of r.querySelectorAll(`[role="treeitem"]`))e.getAttribute(`data-key`)===t&&e.focus()}render(){let e=this.localize,t=this.entries,n=t!==void 0&&t.length>1;return u`
      <div class="panel">
        <header class="header">
          <div class="title-wrap">
            <span class="header-icon" aria-hidden="true">
              ${d(i)}
            </span>
            <h1 class="title">${e(`panel.title`)}</h1>
          </div>
          ${n?this.renderEntryPicker(t,e):p}
        </header>
        ${this.renderNotices(e)} ${this.renderBody(e)}
      </div>
    `}renderEntryPicker(e,t){return u`
      <label class="entry-picker">
        <span class="picker-label">${t(`panel.entry_label`)}</span>
        <select
          .value=${this.selectedEntryId??``}
          @change=${this.onEntryChange}
        >
          ${e.map(e=>u`<option
                value=${e.entry_id}
                ?selected=${e.entry_id===this.selectedEntryId}
              >
                ${e.title}
              </option>`)}
        </select>
      </label>
    `}renderNotices(e){let t=[],n=this.snapshot?.status===`ok`?this.snapshot:this.lastGood;return this.incompatible?t.push({kind:`error`,text:e(`panel.incompatible`)}):this.groupingDisabled||this.error?.code===`grouping_disabled`?t.push({kind:`warning`,text:e(`panel.grouping_disabled`)}):(this.disconnected&&t.push({kind:`warning`,text:e(`panel.disconnected`)}),this.snapshot?.status===`entry_unloaded`||this.error!==void 0&&R.has(this.error.code)?t.push({kind:`info`,text:e(`panel.reconnecting`)}):this.error&&n&&t.push({kind:`error`,text:this.formatError(this.error,e)}),n?.stale&&t.push({kind:`warning`,text:e(`panel.stale`)})),t.length===0?p:u`
      <div class="notices" role="status" aria-live="polite">
        ${t.map(e=>u`<div class="notice ${e.kind}">${e.text}</div>`)}
      </div>
    `}formatError(e,t){return e.code===`entry_not_found`?t(`panel.error_not_found`):t(`panel.error`,{code:e.code})}renderBody(e){if(this.incompatible||this.groupingDisabled||this.error?.code===`grouping_disabled`)return u``;if(this.sourcesError&&this.entries===void 0)return R.has(this.sourcesError.code)?u`<p class="state-card">
          ${e(`panel.reconnecting`)}
        </p>`:u`<p class="state-card error">
        ${this.formatError(this.sourcesError,e)}
      </p>`;if(this.entries===void 0)return u`<p class="state-card">${e(`panel.loading`)}</p>`;if(this.entries.length===0)return u`<p class="state-card">${e(`panel.empty_entries`)}</p>`;let t=this.snapshot?.status===`ok`?this.snapshot:this.lastGood;if(!t)return this.error&&!R.has(this.error.code)&&this.snapshot?.status!==`entry_unloaded`?u`<p class="state-card error">
          ${this.formatError(this.error,e)}
        </p>`:this.snapshot?.status===`entry_unloaded`||this.error&&R.has(this.error.code)?u``:u`<p class="state-card">${e(`panel.loading`)}</p>`;let n=t.sections.filter(e=>e.rows.length>0);return n.length===0?u`<p class="state-card">${e(`panel.empty_devices`)}</p>`:u`
      <div class="sections">
        ${n.map((t,n)=>this.renderSection(t,n,e))}
      </div>
    `}renderSection(e,t,n){let r=U(e,n),i=`section-heading-${t}`,a=I(P(e.rows),this.collapsed,e.key),o=this.focusKeyFor(e.key,a);return u`
      <section class="section" aria-labelledby=${i}>
        <h2 id=${i} class="section-title">${r}</h2>
        <div
          class="tree"
          role="tree"
          aria-labelledby=${i}
          data-section=${e.key}
          @keydown=${t=>this.onTreeKeydown(t,e.key,a)}
        >
          ${b(a,e=>e.key,t=>this.renderRow(e.key,t,o,n))}
        </div>
      </section>
    `}renderRow(e,n,r,i){let{row:a}=n,o=F(e,n.key),s=a.ha_device_id===null?void 0:H(a.ha_device_id);return u`
      <div
        class="row ${s?`linked`:`plain`}"
        role="treeitem"
        data-key=${n.key}
        aria-level=${n.level}
        aria-setsize=${n.setsize}
        aria-posinset=${n.posinset}
        aria-expanded=${n.hasChildren?String(n.expanded):p}
        tabindex=${n.key===r?0:-1}
        style=${`--level: ${n.level}`}
        @click=${()=>this.activateRow(a,n.hasChildren,o)}
        @focus=${()=>this.setFocusKey(e,n.key)}
      >
        <span
          class="chevron"
          aria-hidden="true"
          @click=${e=>{e.stopPropagation(),n.hasChildren&&this.toggleCollapse(o)}}
        >
          ${n.hasChildren?d(n.expanded?f:t):p}
        </span>
        <span class="kind-icon" aria-hidden="true">
          ${d(B(a.kind))}
        </span>
        <div class="row-main">
          ${s?u`<a
                class="name device-link"
                href=${s}
                title=${a.name}
                tabindex="-1"
                @click=${e=>this.onLinkClick(e,s)}
                >${a.name}</a
              >`:u`<span class="name" title=${a.name}>${a.name}</span>`}
          ${a.port!==null||a.reason!==null?u`<div class="meta">
                ${a.port===null?p:u`<span class="badge port">
                      ${i(`panel.port`,{port:a.port})}
                    </span>`}
                ${a.reason===null?p:u`<span class="badge reason">
                      ${i(V(a.reason))}
                    </span>`}
              </div>`:p}
        </div>
      </div>
    `}static styles=[g,o,v`
      :host {
        display: block;
        min-height: 100%;
        box-sizing: border-box;
        padding: 16px;
        background: var(
          --primary-background-color,
          var(--card-background-color, transparent)
        );
        --indent: 24px;
      }
      :host([narrow]) {
        padding: 12px;
        --indent: 16px;
      }
      .panel {
        max-width: 960px;
        margin: 0 auto;
        display: flex;
        flex-direction: column;
        gap: 16px;
        min-width: 0;
      }
      .header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 12px;
      }
      .title-wrap {
        display: flex;
        align-items: center;
        gap: 12px;
        min-width: 0;
      }
      .header-icon {
        width: 40px;
        height: 40px;
        border-radius: 12px;
        display: grid;
        place-items: center;
        flex: none;
        background: color-mix(in srgb, var(--primary-color) 15%, transparent);
        color: var(--primary-color);
      }
      .title {
        margin: 0;
        font-size: 1.35rem;
        font-weight: 600;
        line-height: 1.25;
      }
      .entry-picker {
        display: flex;
        align-items: center;
        gap: 8px;
        min-width: 0;
        max-width: 100%;
      }
      .picker-label {
        font-size: 0.9rem;
        color: var(--ha-color-text-secondary, var(--secondary-text-color));
      }
      .entry-picker select {
        max-width: 100%;
      }
      .notices {
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .notice {
        padding: 10px 14px;
        border-radius: 10px;
        border: 1px solid var(--uit-line);
        border-inline-start: 4px solid var(--uit-warning);
        background: var(
          --ha-color-fill-warning-quiet-resting,
          color-mix(in srgb, var(--uit-warning) 14%, transparent)
        );
        color: var(--ha-color-text-primary, var(--primary-text-color));
      }
      .notice.info {
        border-inline-start-color: var(--uit-focus);
        background: var(
          --ha-color-fill-primary-quiet-resting,
          color-mix(in srgb, var(--uit-focus) 12%, transparent)
        );
      }
      .notice.error {
        border-inline-start-color: var(--uit-offline);
        background: var(
          --ha-color-fill-danger-quiet-resting,
          color-mix(in srgb, var(--uit-offline) 14%, transparent)
        );
      }
      .state-card {
        margin: 0;
        padding: 16px;
        border-radius: 12px;
        background: var(--card-background-color);
        border: 1px solid var(--uit-line);
        color: var(--ha-color-text-secondary, var(--secondary-text-color));
      }
      .state-card.error {
        color: var(--ha-color-text-primary, var(--primary-text-color));
        border-inline-start: 4px solid var(--uit-offline);
      }
      .sections {
        display: flex;
        flex-direction: column;
        gap: 16px;
      }
      .section {
        background: var(--card-background-color);
        border: 1px solid var(--uit-line);
        border-radius: 12px;
        padding: 12px;
        min-width: 0;
      }
      .section-title {
        margin: 0 0 8px;
        font-size: 1rem;
        font-weight: 600;
        color: var(--ha-color-text-primary, var(--primary-text-color));
      }
      .tree {
        display: flex;
        flex-direction: column;
        gap: 2px;
      }
      .row {
        display: flex;
        align-items: center;
        gap: 8px;
        min-height: 44px;
        padding-block: 6px;
        padding-inline-start: calc(
          min(var(--level) - 1, 6) * var(--indent) + 6px
        );
        padding-inline-end: 10px;
        border-radius: 8px;
        min-width: 0;
        box-sizing: border-box;
      }
      .row.linked {
        cursor: pointer;
      }
      .row.linked:hover,
      .row:focus-visible {
        background: color-mix(in srgb, var(--uit-focus) 10%, transparent);
      }
      .chevron {
        width: 24px;
        height: 24px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        flex: none;
        cursor: pointer;
      }
      .kind-icon {
        display: inline-flex;
        align-items: center;
        flex: none;
        color: var(--ha-color-text-secondary, var(--secondary-text-color));
      }
      .row-main {
        flex: 1;
        min-width: 0;
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 6px 10px;
      }
      .name {
        min-width: 0;
        overflow-wrap: anywhere;
        font-weight: 500;
      }
      .device-link {
        color: var(--ha-color-text-primary, var(--primary-text-color));
        text-decoration: underline;
        text-underline-offset: 2px;
      }
      .meta {
        display: inline-flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 6px;
        margin-inline-start: auto;
      }
      .badge {
        display: inline-flex;
        align-items: center;
        padding: 2px 8px;
        border-radius: 999px;
        font-size: 0.78rem;
        line-height: 1.4;
        background: color-mix(
          in srgb,
          var(--primary-text-color) 8%,
          transparent
        );
        color: var(--ha-color-text-primary, var(--primary-text-color));
      }
      .badge.reason {
        background: var(
          --ha-color-fill-warning-quiet-resting,
          color-mix(in srgb, var(--uit-warning) 18%, transparent)
        );
      }
      @media (max-width: 480px) {
        :host {
          padding: 12px;
          --indent: 16px;
        }
        .meta {
          margin-inline-start: 0;
        }
      }
    `]};e(L,W);