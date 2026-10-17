var e=[`wan`,`gateway`,`switch`,`access_point`,`client`,`other`],t=`unifi_insights/topology/sources`,n=`unifi_insights/topology/subscribe`,r=`unifi_insights/topology/node_details`,i=`entry_unloaded`,a=`site_unavailable`,o=`devices_unavailable`,s=`legacy_uplink_missing`,c=`parents_unresolved`,l=`clients_truncated`,u=`entry_not_found`,d=`entry_not_loaded`,ee=`site_not_selected`,te=class extends Error{received;constructor(e){super(`Unsupported topology schema_version ${String(e)}`),this.name=`IncompatibleSchemaError`,this.received=e}},ne=e=>typeof e==`object`&&!!e&&!Array.isArray(e);function re(e){if(!ne(e))throw TypeError(`Topology snapshot is not an object`);if(typeof e.schema_version!=`number`)throw TypeError(`Topology snapshot field schema_version is not a number`);if(e.schema_version!==2)throw new te(e.schema_version);for(let t of[`nodes`,`edges`,`issues`,`unresolved`])if(!Array.isArray(e[t]))throw TypeError(`Topology snapshot field ${t} is not a list`);for(let t of[`entry_id`,`site_id`,`site_name`,`revision`,`status`])if(typeof e[t]!=`string`)throw TypeError(`Topology snapshot field ${t} is not a string`);return e}var ie=`unifi-insights-topology-card`,ae=`unifi-insights-topology-card-editor`,oe=`custom:${ie}`,se=[`graph`,`list`],ce=[`collapsed`,`expanded`,`hidden`],le=[`comfortable`,`compact`],ue=[`vertical`,`horizontal`],f=[`tree`,`tiered`];function p(e,t,n){if(e!==void 0&&!(typeof e==`string`&&t.includes(e)))throw Error(`${n} must be one of: ${t.join(`, `)}`)}function de(t){if(typeof t!=`object`||!t||Array.isArray(t))throw Error(`Card configuration must be an object`);let n=t;if(typeof n.type!=`string`)throw Error(`type is required`);for(let e of[`entry_id`,`site_id`,`title`]){let t=n[e];if(t!==void 0&&(typeof t!=`string`||t===``))throw Error(`${e} must be a non-empty string`)}if(n.entry_id===void 0!=(n.site_id===void 0))throw Error(`entry_id and site_id must be set together`);p(n.view,se,`view`),p(n.clients,ce,`clients`),p(n.density,le,`density`),p(n.orientation,ue,`orientation`),p(n.layout,f,`layout`);for(let e of[`show_site_selector`,`show_labels`,`show_wan`])if(n[e]!==void 0&&typeof n[e]!=`boolean`)throw Error(`${e} must be true or false`);if(n.kinds!==void 0&&(!Array.isArray(n.kinds)||n.kinds.length===0||!n.kinds.every(t=>e.includes(t))))throw Error(`kinds must be a non-empty list of: ${e.join(`, `)}`);let r=n.max_clients;if(r!==void 0&&(typeof r!=`number`||!Number.isInteger(r)||r<1||r>500))throw Error(`max_clients must be a whole number from 1 to 500`);return n}function fe(t){let n=t.show_wan??!0;return{entry_id:t.entry_id,site_id:t.site_id,title:t.title,view:t.view??`graph`,show_site_selector:t.show_site_selector??!1,clients:t.clients??`collapsed`,kinds:(t.kinds??e).filter(e=>n||e!==`wan`),density:t.density,orientation:t.orientation??`vertical`,layout:t.layout??`tree`,show_labels:t.show_labels??!0,show_wan:n,max_clients:t.max_clients??500}}function m(e){return e.flatMap(e=>e.sites.map(t=>({binding:{entry_id:e.entry_id,site_id:t.id},label:`${e.title} — ${t.name}`})))}function h(e,t){if(e.entry_id!==void 0&&e.site_id!==void 0)return{entry_id:e.entry_id,site_id:e.site_id};let n=m(t??[]);return n.length===1?n[0].binding:void 0}function pe(e,t){return e?.entry_id===t?.entry_id&&e?.site_id===t?.site_id}function g(e,t){customElements.get(e)||customElements.define(e,t)}var me=`M4.93,4.93C3.12,6.74 2,9.24 2,12C2,14.76 3.12,17.26 4.93,19.07L6.34,17.66C4.89,16.22 4,14.22 4,12C4,9.79 4.89,7.78 6.34,6.34L4.93,4.93M19.07,4.93L17.66,6.34C19.11,7.78 20,9.79 20,12C20,14.22 19.11,16.22 17.66,17.66L19.07,19.07C20.88,17.26 22,14.76 22,12C22,9.24 20.88,6.74 19.07,4.93M7.76,7.76C6.67,8.85 6,10.35 6,12C6,13.65 6.67,15.15 7.76,16.24L9.17,14.83C8.45,14.11 8,13.11 8,12C8,10.89 8.45,9.89 9.17,9.17L7.76,7.76M16.24,7.76L14.83,9.17C15.55,9.89 16,10.89 16,12C16,13.11 15.55,14.11 14.83,14.83L16.24,16.24C17.33,15.15 18,13.65 18,12C18,10.35 17.33,8.85 16.24,7.76M12,10A2,2 0 0,0 10,12A2,2 0 0,0 12,14A2,2 0 0,0 14,12A2,2 0 0,0 12,10Z`,he=`M11,15H13V17H11V15M11,7H13V13H11V7M12,2C6.47,2 2,6.5 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M12,20A8,8 0 0,1 4,12A8,8 0 0,1 12,4A8,8 0 0,1 20,12A8,8 0 0,1 12,20Z`,ge=`M9,4H15V12H19.84L12,19.84L4.16,12H9V4Z`,_e=`M15,20H9V12H4.16L12,4.16L19.84,12H15V20Z`,ve=`M10,21H14A2,2 0 0,1 12,23A2,2 0 0,1 10,21M21,19V20H3V19L5,17V11C5,7.9 7.03,5.17 10,4.29C10,4.19 10,4.1 10,4A2,2 0 0,1 12,2A2,2 0 0,1 14,4C14,4.1 14,4.19 14,4.29C16.97,5.17 19,7.9 19,11V17L21,19M17,11A5,5 0 0,0 12,6A5,5 0 0,0 7,11V18H17V11M19.75,3.19L18.33,4.61C20.04,6.3 21,8.6 21,11H23C23,8.07 21.84,5.25 19.75,3.19M1,11H3C3,8.6 3.96,6.3 5.67,4.61L4.25,3.19C2.16,5.25 1,8.07 1,11Z`,ye=`M6.03 12.03L8.03 15.5L5.5 18.68L2 12.62L6.03 12.03M17 18V15.29C17.88 14.9 18.5 14.03 18.5 13C18.5 12.43 18.3 11.9 17.97 11.5L19.94 10.35C20.95 9.76 21.3 8.47 20.71 7.46L19.33 5.06C18.74 4.05 17.45 3.7 16.44 4.28L8.31 9C7.36 9.53 7.03 10.75 7.58 11.71L9.08 14.31C9.63 15.26 10.86 15.59 11.81 15.04L13.69 13.96C13.94 14.55 14.41 15.03 15 15.29V18C15 19.1 15.9 20 17 20H22V18H17Z`,be=`M12 2C6.5 2 2 6.5 2 12S6.5 22 12 22 22 17.5 22 12 17.5 2 12 2M12 20C7.59 20 4 16.41 4 12S7.59 4 12 4 20 7.59 20 12 16.41 20 12 20M16.59 7.58L10 14.17L7.41 11.59L6 13L10 17L18 9L16.59 7.58Z`,xe=`M7.41,8.58L12,13.17L16.59,8.58L18,10L12,16L6,10L7.41,8.58Z`,Se=`M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z`,Ce=`M6,4H18V5H21V7H18V9H21V11H18V13H21V15H18V17H21V19H18V20H6V19H3V17H6V15H3V13H6V11H3V9H6V7H3V5H6V4M11,15V18H12V15H11M13,15V18H14V15H13M15,15V18H16V15H15Z`,we=`M19,6.41L17.59,5L12,10.59L6.41,5L5,6.41L10.59,12L5,17.59L6.41,19L12,13.41L17.59,19L19,17.59L13.41,12L19,6.41Z`,Te=`M19,21H8V7H19M19,5H8A2,2 0 0,0 6,7V21A2,2 0 0,0 8,23H19A2,2 0 0,0 21,21V7A2,2 0 0,0 19,5M16,1H4A2,2 0 0,0 2,3V17H4V3H16V1Z`,_=`M3 6H21V4H3C1.9 4 1 4.9 1 6V18C1 19.1 1.9 20 3 20H7V18H3V6M13 12H9V13.78C8.39 14.33 8 15.11 8 16C8 16.89 8.39 17.67 9 18.22V20H13V18.22C13.61 17.67 14 16.88 14 16S13.61 14.33 13 13.78V12M11 17.5C10.17 17.5 9.5 16.83 9.5 16S10.17 14.5 11 14.5 12.5 15.17 12.5 16 11.83 17.5 11 17.5M22 8H16C15.5 8 15 8.5 15 9V19C15 19.5 15.5 20 16 20H22C22.5 20 23 19.5 23 19V9C23 8.5 22.5 8 22 8M21 18H17V10H21V18Z`,Ee=`M14 15C14 16.11 13.11 17 12 17S10 16.11 10 15 10.9 13 12 13 14 13.9 14 15M18 4V20C18 21.1 17.11 22 16 22H8C6.9 22 6 21.11 6 20V4C6 2.9 6.9 2 8 2H16C17.11 2 18 2.9 18 4M10.5 7C10.5 7.83 11.17 8.5 12 8.5S13.5 7.83 13.5 7 12.83 5.5 12 5.5 10.5 6.17 10.5 7M16 10H8V20H16V10Z`,De=`M12,16A2,2 0 0,1 14,18A2,2 0 0,1 12,20A2,2 0 0,1 10,18A2,2 0 0,1 12,16M12,10A2,2 0 0,1 14,12A2,2 0 0,1 12,14A2,2 0 0,1 10,12A2,2 0 0,1 12,10M12,4A2,2 0 0,1 14,6A2,2 0 0,1 12,8A2,2 0 0,1 10,6A2,2 0 0,1 12,4Z`,Oe=`M17.9,17.39C17.64,16.59 16.89,16 16,16H15V13A1,1 0 0,0 14,12H8V10H10A1,1 0 0,0 11,9V7H13A2,2 0 0,0 15,5V4.59C17.93,5.77 20,8.64 20,12C20,14.08 19.2,15.97 17.9,17.39M11,19.93C7.05,19.44 4,16.08 4,12C4,11.38 4.08,10.78 4.21,10.21L9,15V16A2,2 0 0,0 11,18M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2Z`,ke=`M17 4H20C21.1 4 22 4.9 22 6V8H20V6H17V4M4 8V6H7V4H4C2.9 4 2 4.9 2 6V8H4M20 16V18H17V20H20C21.1 20 22 19.1 22 18V16H20M7 18H4V16H2V18C2 19.1 2.9 20 4 20H7V18M16 10V14H8V10H16M18 8H6V16H18V8Z`,Ae=`M11,9H13V7H11M12,20C7.59,20 4,16.41 4,12C4,7.59 7.59,4 12,4C16.41,4 20,7.59 20,12C20,16.41 16.41,20 12,20M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M11,17H13V11H11V17Z`,je=`M4,1C2.89,1 2,1.89 2,3V7C2,8.11 2.89,9 4,9H1V11H13V9H10C11.11,9 12,8.11 12,7V3C12,1.89 11.11,1 10,1H4M4,3H10V7H4V3M3,13V18L3,20H10V18H5V13H3M14,13C12.89,13 12,13.89 12,15V19C12,20.11 12.89,21 14,21H11V23H23V21H20C21.11,21 22,20.11 22,19V15C22,13.89 21.11,13 20,13H14M14,15H20V19H14V15Z`,Me=`M15.5,14H14.71L14.43,13.73C15.41,12.59 16,11.11 16,9.5A6.5,6.5 0 0,0 9.5,3A6.5,6.5 0 0,0 3,9.5A6.5,6.5 0 0,0 9.5,16C11.11,16 12.59,15.41 13.73,14.43L14,14.71V15.5L19,20.5L20.5,19L15.5,14M9.5,14C7,14 5,12 5,9.5C5,7 7,5 9.5,5C12,5 14,7 14,9.5C14,12 12,14 9.5,14M7,9H12V10H7V9Z`,Ne=`M15.5,14L20.5,19L19,20.5L14,15.5V14.71L13.73,14.43C12.59,15.41 11.11,16 9.5,16A6.5,6.5 0 0,1 3,9.5A6.5,6.5 0 0,1 9.5,3A6.5,6.5 0 0,1 16,9.5C16,11.11 15.41,12.59 14.43,13.73L14.71,14H15.5M9.5,14C12,14 14,12 14,9.5C14,7 12,5 9.5,5C7,5 5,7 5,9.5C5,12 7,14 9.5,14M12,10H10V12H9V10H7V9H9V7H10V9H12V10Z`,Pe=`M10,0.2C9,0.2 8.2,1 8.2,2C8.2,3 9,3.8 10,3.8C11,3.8 11.8,3 11.8,2C11.8,1 11,0.2 10,0.2M15.67,1A7.33,7.33 0 0,0 23,8.33V7A6,6 0 0,1 17,1H15.67M18.33,1C18.33,3.58 20.42,5.67 23,5.67V4.33C21.16,4.33 19.67,2.84 19.67,1H18.33M21,1A2,2 0 0,0 23,3V1H21M7.92,4.03C7.75,4.03 7.58,4.06 7.42,4.11L2,5.8V11H3.8V7.33L5.91,6.67L2,22H3.8L6.67,13.89L9,17V22H10.8V15.59L8.31,11.05L9.04,8.18L10.12,10H15V8.2H11.38L9.38,4.87C9.08,4.37 8.54,4.03 7.92,4.03Z`,Fe=`M14,3V5H17.59L7.76,14.83L9.17,16.24L19,6.41V10H21V3M19,19H5V5H12V3H5C3.89,3 3,3.9 3,5V19A2,2 0 0,0 5,21H19A2,2 0 0,0 21,19V12H19V19Z`,v=`M5 9C3.9 9 3 9.9 3 11V15C3 16.11 3.9 17 5 17H11V19H10C9.45 19 9 19.45 9 20H2V22H9C9 22.55 9.45 23 10 23H14C14.55 23 15 22.55 15 22H22V20H15C15 19.45 14.55 19 14 19H13V17H19C20.11 17 21 16.11 21 15V11C21 9.9 20.11 9 19 9H5M6 12H8V14H6V12M9.5 12H11.5V14H9.5V12M13 12H15V14H13V12Z`,Ie=`M21,11C21,16.55 17.16,21.74 12,23C6.84,21.74 3,16.55 3,11V5L12,1L21,5V11M12,21C15.75,20 19,15.54 19,11.22V6.3L12,3.18L5,6.3V11.22C5,15.54 8.25,20 12,21M10,17L6,13L7.41,11.59L10,14.17L16.59,7.58L18,9`,Le=`M12,16A3,3 0 0,1 9,13C9,11.88 9.61,10.9 10.5,10.39L20.21,4.77L14.68,14.35C14.18,15.33 13.17,16 12,16M12,3C13.81,3 15.5,3.5 16.97,4.32L14.87,5.53C14,5.19 13,5 12,5A8,8 0 0,0 4,13C4,15.21 4.89,17.21 6.34,18.65H6.35C6.74,19.04 6.74,19.67 6.35,20.06C5.96,20.45 5.32,20.45 4.93,20.07V20.07C3.12,18.26 2,15.76 2,13A10,10 0 0,1 12,3M22,13C22,15.76 20.88,18.26 19.07,20.07V20.07C18.68,20.45 18.05,20.45 17.66,20.06C17.27,19.67 17.27,19.04 17.66,18.65V18.65C19.11,17.2 20,15.21 20,13C20,12 19.81,11 19.46,10.1L20.67,8C21.5,9.5 22,11.18 22,13Z`,y=`M13,18H14A1,1 0 0,1 15,19H22V21H15A1,1 0 0,1 14,22H10A1,1 0 0,1 9,21H2V19H9A1,1 0 0,1 10,18H11V16H8A1,1 0 0,1 7,15V3A1,1 0 0,1 8,2H16A1,1 0 0,1 17,3V15A1,1 0 0,1 16,16H13V18M13,6H14V4H13V6M9,4V6H11V4H9M9,8V10H11V8H9M9,12V14H11V12H9Z`,Re=`M4 2V8H2V2H4M2 22V16H4V22H2M5 12C5 13.11 4.11 14 3 14C1.9 14 1 13.11 1 12C1 10.9 1.9 10 3 10C4.11 10 5 10.9 5 12M16 4C20.42 4 24 7.58 24 12C24 16.42 20.42 20 16 20C12.4 20 9.36 17.62 8.35 14.35L6 12L8.35 9.65C9.36 6.38 12.4 4 16 4M16 6C12.69 6 10 8.69 10 12C10 15.31 12.69 18 16 18C19.31 18 22 15.31 22 12C22 8.69 19.31 6 16 6M15 13V8H16.5V12.2L19.5 14L18.68 15.26L15 13Z`,ze=`M16.36,14C16.44,13.34 16.5,12.68 16.5,12C16.5,11.32 16.44,10.66 16.36,10H19.74C19.9,10.64 20,11.31 20,12C20,12.69 19.9,13.36 19.74,14M14.59,19.56C15.19,18.45 15.65,17.25 15.97,16H18.92C17.96,17.65 16.43,18.93 14.59,19.56M14.34,14H9.66C9.56,13.34 9.5,12.68 9.5,12C9.5,11.32 9.56,10.65 9.66,10H14.34C14.43,10.65 14.5,11.32 14.5,12C14.5,12.68 14.43,13.34 14.34,14M12,19.96C11.17,18.76 10.5,17.43 10.09,16H13.91C13.5,17.43 12.83,18.76 12,19.96M8,8H5.08C6.03,6.34 7.57,5.06 9.4,4.44C8.8,5.55 8.35,6.75 8,8M5.08,16H8C8.35,17.25 8.8,18.45 9.4,19.56C7.57,18.93 6.03,17.65 5.08,16M4.26,14C4.1,13.36 4,12.69 4,12C4,11.31 4.1,10.64 4.26,10H7.64C7.56,10.66 7.5,11.32 7.5,12C7.5,12.68 7.56,13.34 7.64,14M12,4.03C12.83,5.23 13.5,6.57 13.91,8H10.09C10.5,6.57 11.17,5.23 12,4.03M18.92,8H15.97C15.65,6.75 15.19,5.55 14.59,4.44C16.43,5.07 17.96,6.34 18.92,8M12,2C6.47,2 2,6.5 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2Z`,b=`M12,21L15.6,16.2C14.6,15.45 13.35,15 12,15C10.65,15 9.4,15.45 8.4,16.2L12,21M12,3C7.95,3 4.21,4.34 1.2,6.6L3,9C5.5,7.12 8.62,6 12,6C15.38,6 18.5,7.12 21,9L22.8,6.6C19.79,4.34 16.05,3 12,3M12,9C9.3,9 6.81,9.89 4.8,11.4L6.6,13.8C8.1,12.67 9.97,12 12,12C14.03,12 15.9,12.67 17.4,13.8L19.2,11.4C17.19,9.89 14.7,9 12,9Z`,x=globalThis,S=x.ShadowRoot&&(x.ShadyCSS===void 0||x.ShadyCSS.nativeShadow)&&`adoptedStyleSheets`in Document.prototype&&`replace`in CSSStyleSheet.prototype,C=Symbol(),Be=new WeakMap,Ve=class{constructor(e,t,n){if(this._$cssResult$=!0,n!==C)throw Error("CSSResult is not constructable. Use `unsafeCSS` or `css` instead.");this.cssText=e,this.t=t}get styleSheet(){let e=this.o,t=this.t;if(S&&e===void 0){let n=t!==void 0&&t.length===1;n&&(e=Be.get(t)),e===void 0&&((this.o=e=new CSSStyleSheet).replaceSync(this.cssText),n&&Be.set(t,e))}return e}toString(){return this.cssText}},He=e=>new Ve(typeof e==`string`?e:e+``,void 0,C),w=(e,...t)=>new Ve(e.length===1?e[0]:t.reduce((t,n,r)=>t+(e=>{if(!0===e._$cssResult$)return e.cssText;if(typeof e==`number`)return e;throw Error(`Value passed to 'css' function must be a 'css' function result: `+e+`. Use 'unsafeCSS' to pass non-literal values, but take care to ensure page security.`)})(n)+e[r+1],e[0]),e,C),Ue=(e,t)=>{if(S)e.adoptedStyleSheets=t.map(e=>e instanceof CSSStyleSheet?e:e.styleSheet);else for(let n of t){let t=document.createElement(`style`),r=x.litNonce;r!==void 0&&t.setAttribute(`nonce`,r),t.textContent=n.cssText,e.appendChild(t)}},We=S?e=>e:e=>e instanceof CSSStyleSheet?(e=>{let t=``;for(let n of e.cssRules)t+=n.cssText;return He(t)})(e):e,{is:Ge,defineProperty:Ke,getOwnPropertyDescriptor:qe,getOwnPropertyNames:Je,getOwnPropertySymbols:Ye,getPrototypeOf:Xe}=Object,T=globalThis,Ze=T.trustedTypes,Qe=Ze?Ze.emptyScript:``,$e=T.reactiveElementPolyfillSupport,E=(e,t)=>e,D={toAttribute(e,t){switch(t){case Boolean:e=e?Qe:null;break;case Object:case Array:e=e==null?e:JSON.stringify(e)}return e},fromAttribute(e,t){let n=e;switch(t){case Boolean:n=e!==null;break;case Number:n=e===null?null:Number(e);break;case Object:case Array:try{n=JSON.parse(e)}catch{n=null}}return n}},et=(e,t)=>!Ge(e,t),tt={attribute:!0,type:String,converter:D,reflect:!1,useDefault:!1,hasChanged:et};Symbol.metadata??=Symbol(`metadata`),T.litPropertyMetadata??=new WeakMap;var O=class extends HTMLElement{static addInitializer(e){this._$Ei(),(this.l??=[]).push(e)}static get observedAttributes(){return this.finalize(),this._$Eh&&[...this._$Eh.keys()]}static createProperty(e,t=tt){if(t.state&&(t.attribute=!1),this._$Ei(),this.prototype.hasOwnProperty(e)&&((t=Object.create(t)).wrapped=!0),this.elementProperties.set(e,t),!t.noAccessor){let n=Symbol(),r=this.getPropertyDescriptor(e,n,t);r!==void 0&&Ke(this.prototype,e,r)}}static getPropertyDescriptor(e,t,n){let{get:r,set:i}=qe(this.prototype,e)??{get(){return this[t]},set(e){this[t]=e}};return{get:r,set(t){let a=r?.call(this);i?.call(this,t),this.requestUpdate(e,a,n)},configurable:!0,enumerable:!0}}static getPropertyOptions(e){return this.elementProperties.get(e)??tt}static _$Ei(){if(this.hasOwnProperty(E(`elementProperties`)))return;let e=Xe(this);e.finalize(),e.l!==void 0&&(this.l=[...e.l]),this.elementProperties=new Map(e.elementProperties)}static finalize(){if(this.hasOwnProperty(E(`finalized`)))return;if(this.finalized=!0,this._$Ei(),this.hasOwnProperty(E(`properties`))){let e=this.properties,t=[...Je(e),...Ye(e)];for(let n of t)this.createProperty(n,e[n])}let e=this[Symbol.metadata];if(e!==null){let t=litPropertyMetadata.get(e);if(t!==void 0)for(let[e,n]of t)this.elementProperties.set(e,n)}this._$Eh=new Map;for(let[e,t]of this.elementProperties){let n=this._$Eu(e,t);n!==void 0&&this._$Eh.set(n,e)}this.elementStyles=this.finalizeStyles(this.styles)}static finalizeStyles(e){let t=[];if(Array.isArray(e)){let n=new Set(e.flat(1/0).reverse());for(let e of n)t.unshift(We(e))}else e!==void 0&&t.push(We(e));return t}static _$Eu(e,t){let n=t.attribute;return!1===n?void 0:typeof n==`string`?n:typeof e==`string`?e.toLowerCase():void 0}constructor(){super(),this._$Ep=void 0,this.isUpdatePending=!1,this.hasUpdated=!1,this._$Em=null,this._$Ev()}_$Ev(){this._$ES=new Promise(e=>this.enableUpdating=e),this._$AL=new Map,this._$E_(),this.requestUpdate(),this.constructor.l?.forEach(e=>e(this))}addController(e){(this._$EO??=new Set).add(e),this.renderRoot!==void 0&&this.isConnected&&e.hostConnected?.()}removeController(e){this._$EO?.delete(e)}_$E_(){let e=new Map,t=this.constructor.elementProperties;for(let n of t.keys())this.hasOwnProperty(n)&&(e.set(n,this[n]),delete this[n]);e.size>0&&(this._$Ep=e)}createRenderRoot(){let e=this.shadowRoot??this.attachShadow(this.constructor.shadowRootOptions);return Ue(e,this.constructor.elementStyles),e}connectedCallback(){this.renderRoot??=this.createRenderRoot(),this.enableUpdating(!0),this._$EO?.forEach(e=>e.hostConnected?.())}enableUpdating(e){}disconnectedCallback(){this._$EO?.forEach(e=>e.hostDisconnected?.())}attributeChangedCallback(e,t,n){this._$AK(e,n)}_$ET(e,t){let n=this.constructor.elementProperties.get(e),r=this.constructor._$Eu(e,n);if(r!==void 0&&!0===n.reflect){let i=(n.converter?.toAttribute===void 0?D:n.converter).toAttribute(t,n.type);this._$Em=e,i==null?this.removeAttribute(r):this.setAttribute(r,i),this._$Em=null}}_$AK(e,t){let n=this.constructor,r=n._$Eh.get(e);if(r!==void 0&&this._$Em!==r){let e=n.getPropertyOptions(r),i=typeof e.converter==`function`?{fromAttribute:e.converter}:e.converter?.fromAttribute===void 0?D:e.converter;this._$Em=r;let a=i.fromAttribute(t,e.type);this[r]=a??this._$Ej?.get(r)??a,this._$Em=null}}requestUpdate(e,t,n,r=!1,i){if(e!==void 0){let a=this.constructor;if(!1===r&&(i=this[e]),n??=a.getPropertyOptions(e),!((n.hasChanged??et)(i,t)||n.useDefault&&n.reflect&&i===this._$Ej?.get(e)&&!this.hasAttribute(a._$Eu(e,n))))return;this.C(e,t,n)}!1===this.isUpdatePending&&(this._$ES=this._$EP())}C(e,t,{useDefault:n,reflect:r,wrapped:i},a){n&&!(this._$Ej??=new Map).has(e)&&(this._$Ej.set(e,a??t??this[e]),!0!==i||a!==void 0)||(this._$AL.has(e)||(this.hasUpdated||n||(t=void 0),this._$AL.set(e,t)),!0===r&&this._$Em!==e&&(this._$Eq??=new Set).add(e))}async _$EP(){this.isUpdatePending=!0;try{await this._$ES}catch(e){Promise.reject(e)}let e=this.scheduleUpdate();return e!=null&&await e,!this.isUpdatePending}scheduleUpdate(){return this.performUpdate()}performUpdate(){if(!this.isUpdatePending)return;if(!this.hasUpdated){if(this.renderRoot??=this.createRenderRoot(),this._$Ep){for(let[e,t]of this._$Ep)this[e]=t;this._$Ep=void 0}let e=this.constructor.elementProperties;if(e.size>0)for(let[t,n]of e){let{wrapped:e}=n,r=this[t];!0!==e||this._$AL.has(t)||r===void 0||this.C(t,void 0,n,r)}}let e=!1,t=this._$AL;try{e=this.shouldUpdate(t),e?(this.willUpdate(t),this._$EO?.forEach(e=>e.hostUpdate?.()),this.update(t)):this._$EM()}catch(t){throw e=!1,this._$EM(),t}e&&this._$AE(t)}willUpdate(e){}_$AE(e){this._$EO?.forEach(e=>e.hostUpdated?.()),this.hasUpdated||(this.hasUpdated=!0,this.firstUpdated(e)),this.updated(e)}_$EM(){this._$AL=new Map,this.isUpdatePending=!1}get updateComplete(){return this.getUpdateComplete()}getUpdateComplete(){return this._$ES}shouldUpdate(e){return!0}update(e){this._$Eq&&=this._$Eq.forEach(e=>this._$ET(e,this[e])),this._$EM()}updated(e){}firstUpdated(e){}};O.elementStyles=[],O.shadowRootOptions={mode:`open`},O[E(`elementProperties`)]=new Map,O[E(`finalized`)]=new Map,$e?.({ReactiveElement:O}),(T.reactiveElementVersions??=[]).push(`2.1.2`);var k=globalThis,nt=e=>e,A=k.trustedTypes,rt=A?A.createPolicy(`lit-html`,{createHTML:e=>e}):void 0,j=`$lit$`,M=`lit$${Math.random().toFixed(9).slice(2)}$`,N=`?`+M,it=`<${N}>`,P=document,F=()=>P.createComment(``),I=e=>e===null||typeof e!=`object`&&typeof e!=`function`,L=Array.isArray,at=e=>L(e)||typeof e?.[Symbol.iterator]==`function`,R=`[ 	
\f\r]`,z=/<(?:(!--|\/[^a-zA-Z])|(\/?[a-zA-Z][^>\s]*)|(\/?$))/g,ot=/-->/g,st=/>/g,B=RegExp(`>|${R}(?:([^\\s"'>=/]+)(${R}*=${R}*(?:[^ \t\n\f\r"'\`<>=]|("|')|))|$)`,`g`),ct=/'/g,lt=/"/g,ut=/^(?:script|style|textarea|title)$/i,dt=e=>(t,...n)=>({_$litType$:e,strings:t,values:n}),V=dt(1),ft=dt(2),H=Symbol.for(`lit-noChange`),U=Symbol.for(`lit-nothing`),pt=new WeakMap,W=P.createTreeWalker(P,129);function mt(e,t){if(!L(e)||!e.hasOwnProperty(`raw`))throw Error(`invalid template strings array`);return rt===void 0?t:rt.createHTML(t)}var ht=(e,t)=>{let n=e.length-1,r=[],i,a=t===2?`<svg>`:t===3?`<math>`:``,o=z;for(let t=0;t<n;t++){let n=e[t],s,c,l=-1,u=0;for(;u<n.length&&(o.lastIndex=u,c=o.exec(n),c!==null);)u=o.lastIndex,o===z?c[1]===`!--`?o=ot:c[1]===void 0?c[2]===void 0?c[3]!==void 0&&(o=B):(ut.test(c[2])&&(i=RegExp(`</`+c[2],`g`)),o=B):o=st:o===B?c[0]===`>`?(o=i??z,l=-1):c[1]===void 0?l=-2:(l=o.lastIndex-c[2].length,s=c[1],o=c[3]===void 0?B:c[3]===`"`?lt:ct):o===lt||o===ct?o=B:o===ot||o===st?o=z:(o=B,i=void 0);let d=o===B&&e[t+1].startsWith(`/>`)?` `:``;a+=o===z?n+it:l>=0?(r.push(s),n.slice(0,l)+j+n.slice(l)+M+d):n+M+(l===-2?t:d)}return[mt(e,a+(e[n]||`<?>`)+(t===2?`</svg>`:t===3?`</math>`:``)),r]},G=class e{constructor({strings:t,_$litType$:n},r){let i;this.parts=[];let a=0,o=0,s=t.length-1,c=this.parts,[l,u]=ht(t,n);if(this.el=e.createElement(l,r),W.currentNode=this.el.content,n===2||n===3){let e=this.el.content.firstChild;e.replaceWith(...e.childNodes)}for(;(i=W.nextNode())!==null&&c.length<s;){if(i.nodeType===1){if(i.hasAttributes())for(let e of i.getAttributeNames())if(e.endsWith(j)){let t=u[o++],n=i.getAttribute(e).split(M),r=/([.?@])?(.*)/.exec(t);c.push({type:1,index:a,name:r[2],strings:n,ctor:r[1]===`.`?_t:r[1]===`?`?vt:r[1]===`@`?yt:J}),i.removeAttribute(e)}else e.startsWith(M)&&(c.push({type:6,index:a}),i.removeAttribute(e));if(ut.test(i.tagName)){let e=i.textContent.split(M),t=e.length-1;if(t>0){i.textContent=A?A.emptyScript:``;for(let n=0;n<t;n++)i.append(e[n],F()),W.nextNode(),c.push({type:2,index:++a});i.append(e[t],F())}}}else if(i.nodeType===8){if(i.data===N)c.push({type:2,index:a});else{let e=-1;for(;(e=i.data.indexOf(M,e+1))!==-1;)c.push({type:7,index:a}),e+=M.length-1}}a++}}static createElement(e,t){let n=P.createElement(`template`);return n.innerHTML=e,n}};function K(e,t,n=e,r){if(t===H)return t;let i=r===void 0?n._$Cl:n._$Co?.[r],a=I(t)?void 0:t._$litDirective$;return i?.constructor!==a&&(i?._$AO?.(!1),a===void 0?i=void 0:(i=new a(e),i._$AT(e,n,r)),r===void 0?n._$Cl=i:(n._$Co??=[])[r]=i),i!==void 0&&(t=K(e,i._$AS(e,t.values),i,r)),t}var gt=class{constructor(e,t){this._$AV=[],this._$AN=void 0,this._$AD=e,this._$AM=t}get parentNode(){return this._$AM.parentNode}get _$AU(){return this._$AM._$AU}u(e){let{el:{content:t},parts:n}=this._$AD,r=(e?.creationScope??P).importNode(t,!0);W.currentNode=r;let i=W.nextNode(),a=0,o=0,s=n[0];for(;s!==void 0;){if(a===s.index){let t;s.type===2?t=new q(i,i.nextSibling,this,e):s.type===1?t=new s.ctor(i,s.name,s.strings,this,e):s.type===6&&(t=new bt(i,this,e)),this._$AV.push(t),s=n[++o]}a!==s?.index&&(i=W.nextNode(),a++)}return W.currentNode=P,r}p(e){let t=0;for(let n of this._$AV)n!==void 0&&(n.strings===void 0?n._$AI(e[t]):(n._$AI(e,n,t),t+=n.strings.length-2)),t++}},q=class e{get _$AU(){return this._$AM?._$AU??this._$Cv}constructor(e,t,n,r){this.type=2,this._$AH=U,this._$AN=void 0,this._$AA=e,this._$AB=t,this._$AM=n,this.options=r,this._$Cv=r?.isConnected??!0}get parentNode(){let e=this._$AA.parentNode,t=this._$AM;return t!==void 0&&e?.nodeType===11&&(e=t.parentNode),e}get startNode(){return this._$AA}get endNode(){return this._$AB}_$AI(e,t=this){e=K(this,e,t),I(e)?e===U||e==null||e===``?(this._$AH!==U&&this._$AR(),this._$AH=U):e!==this._$AH&&e!==H&&this._(e):e._$litType$===void 0?e.nodeType===void 0?at(e)?this.k(e):this._(e):this.T(e):this.$(e)}O(e){return this._$AA.parentNode.insertBefore(e,this._$AB)}T(e){this._$AH!==e&&(this._$AR(),this._$AH=this.O(e))}_(e){this._$AH!==U&&I(this._$AH)?this._$AA.nextSibling.data=e:this.T(P.createTextNode(e)),this._$AH=e}$(e){let{values:t,_$litType$:n}=e,r=typeof n==`number`?this._$AC(e):(n.el===void 0&&(n.el=G.createElement(mt(n.h,n.h[0]),this.options)),n);if(this._$AH?._$AD===r)this._$AH.p(t);else{let e=new gt(r,this),n=e.u(this.options);e.p(t),this.T(n),this._$AH=e}}_$AC(e){let t=pt.get(e.strings);return t===void 0&&pt.set(e.strings,t=new G(e)),t}k(t){L(this._$AH)||(this._$AH=[],this._$AR());let n=this._$AH,r,i=0;for(let a of t)i===n.length?n.push(r=new e(this.O(F()),this.O(F()),this,this.options)):r=n[i],r._$AI(a),i++;i<n.length&&(this._$AR(r&&r._$AB.nextSibling,i),n.length=i)}_$AR(e=this._$AA.nextSibling,t){for(this._$AP?.(!1,!0,t);e!==this._$AB;){let t=nt(e).nextSibling;nt(e).remove(),e=t}}setConnected(e){this._$AM===void 0&&(this._$Cv=e,this._$AP?.(e))}},J=class{get tagName(){return this.element.tagName}get _$AU(){return this._$AM._$AU}constructor(e,t,n,r,i){this.type=1,this._$AH=U,this._$AN=void 0,this.element=e,this.name=t,this._$AM=r,this.options=i,n.length>2||n[0]!==``||n[1]!==``?(this._$AH=Array(n.length-1).fill(new String),this.strings=n):this._$AH=U}_$AI(e,t=this,n,r){let i=this.strings,a=!1;if(i===void 0)e=K(this,e,t,0),a=!I(e)||e!==this._$AH&&e!==H,a&&(this._$AH=e);else{let r=e,o,s;for(e=i[0],o=0;o<i.length-1;o++)s=K(this,r[n+o],t,o),s===H&&(s=this._$AH[o]),a||=!I(s)||s!==this._$AH[o],s===U?e=U:e!==U&&(e+=(s??``)+i[o+1]),this._$AH[o]=s}a&&!r&&this.j(e)}j(e){e===U?this.element.removeAttribute(this.name):this.element.setAttribute(this.name,e??``)}},_t=class extends J{constructor(){super(...arguments),this.type=3}j(e){this.element[this.name]=e===U?void 0:e}},vt=class extends J{constructor(){super(...arguments),this.type=4}j(e){this.element.toggleAttribute(this.name,!!e&&e!==U)}},yt=class extends J{constructor(e,t,n,r,i){super(e,t,n,r,i),this.type=5}_$AI(e,t=this){if((e=K(this,e,t,0)??U)===H)return;let n=this._$AH,r=e===U&&n!==U||e.capture!==n.capture||e.once!==n.once||e.passive!==n.passive,i=e!==U&&(n===U||r);r&&this.element.removeEventListener(this.name,this,n),i&&this.element.addEventListener(this.name,this,e),this._$AH=e}handleEvent(e){typeof this._$AH==`function`?this._$AH.call(this.options?.host??this.element,e):this._$AH.handleEvent(e)}},bt=class{constructor(e,t,n){this.element=e,this.type=6,this._$AN=void 0,this._$AM=t,this.options=n}get _$AU(){return this._$AM._$AU}_$AI(e){K(this,e)}},xt={M:j,P:M,A:N,C:1,L:ht,R:gt,D:at,V:K,I:q,H:J,N:vt,U:yt,B:_t,F:bt},St=k.litHtmlPolyfillSupport;St?.(G,q),(k.litHtmlVersions??=[]).push(`3.3.3`);var Ct=(e,t,n)=>{let r=n?.renderBefore??t,i=r._$litPart$;if(i===void 0){let e=n?.renderBefore??null;r._$litPart$=i=new q(t.insertBefore(F(),e),e,void 0,n??{})}return i._$AI(e),i},Y=globalThis,X=class extends O{constructor(){super(...arguments),this.renderOptions={host:this},this._$Do=void 0}createRenderRoot(){let e=super.createRenderRoot();return this.renderOptions.renderBefore??=e.firstChild,e}update(e){let t=this.render();this.hasUpdated||(this.renderOptions.isConnected=this.isConnected),super.update(e),this._$Do=Ct(t,this.renderRoot,this.renderOptions)}connectedCallback(){super.connectedCallback(),this._$Do?.setConnected(!0)}disconnectedCallback(){super.disconnectedCallback(),this._$Do?.setConnected(!1)}render(){return H}};X._$litElement$=!0,X.finalized=!0,Y.litElementHydrateSupport?.({LitElement:X});var wt=Y.litElementPolyfillSupport;wt?.({LitElement:X}),(Y.litElementVersions??=[]).push(`4.2.2`);function Tt(e){return e?.user?.is_admin===!0}function Et(e){return typeof e==`object`&&!!e&&typeof e.code==`string`}function Z(e){return Et(e)?{code:e.code,message:typeof e.message==`string`?e.message:``}:{code:`unknown_error`,message:String(e)}}function Q(e,t,n){e.dispatchEvent(new CustomEvent(t,{detail:n,bubbles:!0,composed:!0}))}function Dt(e,t){Q(e,`hass-more-info`,{entityId:t})}function Ot(e){history.pushState(null,``,e),Q(window,`location-changed`,{replace:!1})}var kt=_;function At(e){switch(e.kind){case`wan`:return ze;case`gateway`:return v;case`switch`:return y;case`access_point`:return me;case`client`:return e.connection===`wireless`?b:je;default:return _}}function $(e){return V`<svg
    class="icon"
    viewBox="0 0 24 24"
    aria-hidden="true"
    focusable="false"
  >
    <path d=${e}></path>
  </svg>`}var jt=`unifi-insights-site-health-card`,Mt=`unifi-insights-site-health-card-editor`,Nt=`unifi-insights-internet-activity-card`,Pt=`unifi-insights-internet-activity-card-editor`,Ft=`unifi-insights-performance-card`,It=`unifi-insights-performance-card-editor`,Lt=`unifi-insights-protect-status-card`,Rt=`unifi-insights-protect-status-card-editor`,zt=`unifi-insights-timeline-card`,Bt=`unifi-insights-timeline-card-editor`;function Vt(e){let t=Math.round(e/1e6);return e>=1e9?`${(e/1e9).toFixed(1)} GB`:`${t} MB`}function Ht(e){if(!(typeof e!=`number`||!Number.isFinite(e)||e<0))return e>=1e9?`${(e/1e9).toFixed(1)} Gbps`:e>=1e6?`${(e/1e6).toFixed(1)} Mbps`:e>=1e3?`${Math.round(e/1e3)} Kbps`:`${Math.round(e)} bps`}function Ut(e){if(typeof e!=`number`||!Number.isFinite(e)||e<=0)return;let t=Math.floor(e/86400),n=Math.floor(e%86400/3600);if(t>0)return`${t}d ${n}h`;let r=Math.max(1,Math.floor(e%3600/60));return n>0?`${n}h ${r}m`:`${r}m`}function Wt(e){if(typeof e!=`string`||!e)return``;let t=new Date(e);if(Number.isNaN(t.getTime()))return``;let n=Math.max(0,Math.round((Date.now()-t.getTime())/6e4));if(n<1)return`Just now`;if(n<60)return`${n}m ago`;let r=Math.floor(n/60);return r<24?`${r}h ago`:t.toLocaleTimeString([],{hour:`2-digit`,minute:`2-digit`})}function Gt(e){switch(String(e??``)){case`gateway`:return v;case`switch`:return y;case`access_point`:return me;case`camera`:return ye;case`doorbell`:return Ee;case`chime`:case`ring`:return ve;default:return _}}function Kt(e){return e>=85?`critical`:e>=60?`warning`:`ok`}var qt=class extends X{static properties={hass:{attribute:!1},config:{state:!0}};cardType;sourceCommand;options;optionsRequested=!1;constructor(e,t){super(),this.cardType=e,this.sourceCommand=t,this.config={type:`custom:${e}`},this.options=[]}setConfig(e){this.config={...e}}willUpdate(){this.hass&&!this.optionsRequested&&(this.optionsRequested=!0,this.loadOptions())}async loadOptions(){if(this.hass)try{let e=await this.hass.callWS({type:this.sourceCommand});this.options=this.sourceCommand===`unifi_insights/protect/sources`?e.map(e=>({entryId:e.entry_id,siteId:`*`,label:e.title})):m(e).map(e=>({entryId:e.binding.entry_id,siteId:e.binding.site_id,label:e.label})),this.requestUpdate()}catch{this.options=[],this.requestUpdate()}}applySite(e){let t={...this.config,type:this.config.type||`custom:${this.cardType}`};if(!e)delete t.entry_id,delete t.site_id;else{let n=this.options[Number.parseInt(e,10)];n&&(t.entry_id=n.entryId,t.site_id=n.siteId)}this.config=t,Q(this,`config-changed`,{config:t})}applyTitle(e){let t={...this.config};e.trim()?t.title=e.trim():delete t.title,this.config=t,Q(this,`config-changed`,{config:t})}render(){let e=this.config.entry_id&&this.config.site_id?String(this.options.findIndex(e=>e.entryId===this.config.entry_id&&e.siteId===this.config.site_id)):``;return V`
      <div class="editor">
        <label>Site</label>
        <select
          @change=${e=>this.applySite(e.target.value)}
        >
          <option value="">Auto</option>
          ${this.options.map((t,n)=>{let r=String(n);return V`<option value=${r} ?selected=${r===e}>
              ${t.label}
            </option>`})}
        </select>
        <label>Title</label>
        <input
          .value=${this.config.title??``}
          @input=${e=>this.applyTitle(e.target.value)}
        />
      </div>
    `}static styles=w`
    .editor {
      display: grid;
      gap: 8px;
    }
    label {
      font-size: 0.85rem;
      color: var(--secondary-text-color);
    }
    select,
    input {
      min-height: 36px;
      border: 1px solid var(--divider-color);
      border-radius: 8px;
      padding: 4px 8px;
      background: var(--card-background-color);
      color: var(--primary-text-color);
    }
  `},Jt=w`
  :host {
    --uit-online: var(--success-color, #43a047);
    --uit-offline: var(--error-color, #db4437);
    --uit-warning: var(--warning-color, #ffa600);
    --uit-unknown: var(--disabled-text-color, #bdbdbd);
    --uit-focus: var(--primary-color, #03a9f4);
    --uit-line: var(--divider-color, rgba(0, 0, 0, 0.12));
    color: var(--primary-text-color);
    font-family: var(
      --ha-font-family-body,
      var(--paper-font-body1_-_font-family, sans-serif)
    );
  }
  @media (forced-colors: active) {
    :host {
      --uit-online: CanvasText;
      --uit-offline: CanvasText;
      --uit-warning: CanvasText;
      --uit-unknown: GrayText;
      --uit-focus: Highlight;
    }
  }
`,Yt=w`
  button {
    font: inherit;
    color: var(--primary-text-color);
    background: none;
    border: 1px solid var(--uit-line);
    border-radius: 18px;
    min-height: 44px;
    min-width: 44px;
    padding: 0 14px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  button[aria-pressed="true"] {
    background: var(--primary-color);
    border-color: var(--primary-color);
    color: var(--text-primary-color, #fff);
  }
  input,
  select {
    font: inherit;
    color: var(--primary-text-color);
    background: var(--card-background-color);
    border: 1px solid var(--uit-line);
    border-radius: 8px;
    min-height: 44px;
    padding: 0 12px;
    box-sizing: border-box;
  }
  :focus-visible {
    outline: 2px solid var(--uit-focus);
    outline-offset: 2px;
  }
  .icon {
    width: 20px;
    height: 20px;
    fill: currentColor;
    flex: none;
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }
  @media (prefers-reduced-motion: reduce) {
    * {
      transition: none !important;
      animation: none !important;
    }
  }
`,Xt=w`
  :host {
    position: relative;
    display: inline-flex;
  }
  .trigger {
    border: none;
    padding: 0;
  }
  .menu {
    position: absolute;
    inset-block-start: calc(100% + 4px);
    inset-inline-start: 0;
    z-index: 3;
    box-sizing: border-box;
    width: max-content;
    min-width: 13em;
    max-width: min(22em, 80vw);
    padding: 4px;
    background: var(--card-background-color);
    border: 1px solid var(--uit-line);
    border-radius: 12px;
    box-shadow: var(--ha-card-box-shadow, 0 4px 14px rgba(0, 0, 0, 0.18));
  }
  [role="menuitem"] {
    display: flex;
    align-items: center;
    gap: 10px;
    box-sizing: border-box;
    width: 100%;
    min-height: 44px;
    padding: 0 12px;
    border: none;
    border-radius: 8px;
    font: inherit;
    color: var(--primary-text-color);
    background: none;
    text-align: start;
    cursor: pointer;
  }
  button[role="menuitem"]:hover,
  button[role="menuitem"]:focus {
    background: color-mix(in srgb, var(--uit-focus) 14%, transparent);
  }
`,Zt=[Jt,w`
    :host {
      display: block;
      height: 100%;
    }
    ha-card {
      height: 100%;
      box-sizing: border-box;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      overflow: hidden;
    }
    .icon {
      width: 20px;
      height: 20px;
      fill: currentColor;
      flex: none;
    }
    .header,
    .header-main,
    .header-actions,
    .hero-banner,
    .hero-left,
    .kpi-top,
    .item-card,
    .item-top,
    .item-meta,
    .ring-card {
      display: flex;
      align-items: center;
    }
    .header,
    .hero-banner,
    .kpi-top,
    .item-top {
      justify-content: space-between;
      gap: 10px;
    }
    .header-main,
    .hero-left,
    .item-card,
    .ring-card {
      gap: 10px;
      min-width: 0;
    }
    .header-icon,
    .item-icon,
    .ring-gauge,
    .empty-hero .icon-badge {
      display: grid;
      place-items: center;
      flex: none;
    }
    .header-icon {
      width: 38px;
      height: 38px;
      border-radius: 12px;
      background: color-mix(
        in srgb,
        var(--card-accent, var(--primary-color)) 15%,
        transparent
      );
      color: var(--card-accent, var(--primary-color));
    }
    .header-titles {
      min-width: 0;
    }
    .header-title,
    .hero-title,
    .item-name,
    .kpi-sub {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .header-title,
    .hero-title,
    .item-name,
    .empty-title,
    .chip,
    .kpi-top,
    .pill-tab,
    button.link {
      font-weight: 600;
    }
    .header-title {
      font-size: 1.02rem;
      line-height: 1.25;
    }
    .header-subtitle,
    .hero-meta,
    .kpi-sub,
    .item-meta,
    .empty-sub {
      font-size: 0.75rem;
      color: var(--secondary-text-color);
    }
    .header-actions,
    .kpi-top,
    .item-meta,
    .empty-hero,
    .chip {
      gap: 6px;
    }
    .state {
      color: var(--secondary-text-color);
      font-size: 0.88rem;
      padding: 12px;
      border-radius: 12px;
      background: color-mix(in srgb, var(--primary-text-color) 4%, transparent);
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .error,
    .chip.critical,
    .chip.offline,
    .item-icon.offline {
      color: var(--uit-offline);
      background: color-mix(in srgb, var(--uit-offline) 14%, transparent);
    }
    .pulse-dot,
    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: currentColor;
      display: inline-block;
      flex: none;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 0.74rem;
      text-transform: capitalize;
      background: color-mix(in srgb, var(--primary-text-color) 8%, transparent);
      color: var(--primary-text-color);
    }
    .chip.ok,
    .chip.healthy,
    .chip.online,
    .item-icon.online,
    .empty-hero .icon-badge {
      background: color-mix(in srgb, var(--uit-online) 16%, transparent);
      color: var(--uit-online);
    }
    .chip.warning,
    .chip.degraded,
    .chip.partial {
      background: color-mix(in srgb, var(--uit-warning) 18%, transparent);
      color: var(--uit-warning);
    }
    .hero-banner {
      padding: 10px 12px;
      border-radius: 12px;
      background: color-mix(
        in srgb,
        var(--card-accent, var(--primary-color)) 8%,
        transparent
      );
      border: 1px solid
        color-mix(
          in srgb,
          var(--card-accent, var(--primary-color)) 20%,
          transparent
        );
    }
    .hero-left .icon {
      color: var(--card-accent, var(--primary-color));
    }
    .hero-title {
      font-size: 0.9rem;
    }
    .kpi-grid,
    .ring-strip,
    .list {
      display: grid;
      gap: 8px;
    }
    .kpi-grid {
      grid-template-columns: repeat(auto-fit, minmax(92px, 1fr));
    }
    .ring-strip {
      grid-template-columns: repeat(2, 1fr);
    }
    .kpi-tile,
    .ring-card,
    .item-card {
      border-radius: 12px;
      background: color-mix(in srgb, var(--primary-text-color) 4%, transparent);
      border: 1px solid var(--uit-line);
    }
    .kpi-tile {
      appearance: none;
      padding: 10px 12px;
      display: flex;
      flex-direction: column;
      gap: 4px;
      min-width: 0;
      text-align: left;
      font: inherit;
      color: inherit;
    }
    .kpi-tile:disabled {
      cursor: default;
      opacity: 1;
    }
    .kpi-tile.clickable {
      cursor: pointer;
      transition: background 150ms ease;
    }
    .kpi-tile.clickable:hover {
      background: color-mix(in srgb, var(--primary-color) 10%, transparent);
    }
    .kpi-top {
      font-size: 0.73rem;
      text-transform: uppercase;
      letter-spacing: 0.03em;
      color: var(--secondary-text-color);
    }
    .kpi-value {
      font-size: 1.18rem;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
      line-height: 1.2;
      text-transform: capitalize;
    }
    .bar-track {
      width: 100%;
      height: 6px;
      border-radius: 999px;
      background: color-mix(
        in srgb,
        var(--primary-text-color) 10%,
        transparent
      );
      overflow: hidden;
      display: flex;
    }
    .bar-fill {
      height: 100%;
      border-radius: 999px;
      background: var(--primary-color);
      transition: width 250ms ease;
    }
    .bar-fill.ok {
      background: var(--uit-online);
    }
    .bar-fill.warning {
      background: var(--uit-warning);
    }
    .bar-fill.critical {
      background: var(--uit-offline);
    }
    .bar-fill.secondary {
      background: #8b5cf6;
    }
    .ring-card,
    .item-card {
      padding: 8px 10px;
    }
    .ring-gauge {
      width: 40px;
      height: 40px;
      border-radius: 50%;
      background: conic-gradient(
        var(--ring-color, var(--uit-online)) calc(var(--pct, 0) * 1%),
        color-mix(in srgb, var(--primary-text-color) 10%, transparent) 0
      );
      position: relative;
    }
    .ring-gauge::before {
      content: "";
      position: absolute;
      inset: 5px;
      border-radius: 50%;
      background: var(--card-background-color, #fff);
    }
    .ring-gauge span {
      position: relative;
      font-size: 0.7rem;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
    }
    .item-icon {
      width: 34px;
      height: 34px;
      border-radius: 10px;
      background: color-mix(in srgb, var(--primary-color) 12%, transparent);
      color: var(--primary-color);
    }
    .item-body {
      flex: 1;
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .item-name {
      font-size: 0.88rem;
    }
    .item-value {
      font-size: 0.85rem;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
      flex-shrink: 0;
    }
    .pill-tabs {
      display: inline-flex;
      background: color-mix(in srgb, var(--primary-text-color) 6%, transparent);
      border-radius: 999px;
      padding: 2px;
      gap: 2px;
    }
    .pill-tab {
      border: 0;
      background: transparent;
      color: var(--secondary-text-color);
      font: inherit;
      font-size: 0.7rem;
      padding: 3px 8px;
      border-radius: 999px;
      cursor: pointer;
      text-transform: uppercase;
    }
    .pill-tab[aria-pressed="true"] {
      background: var(--primary-color);
      color: var(--text-primary-color, #fff);
    }
    .empty-hero {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      padding: 18px 12px;
      border-radius: 14px;
      background: color-mix(in srgb, var(--uit-online) 7%, transparent);
      border: 1px dashed color-mix(in srgb, var(--uit-online) 30%, transparent);
    }
    .empty-hero .icon-badge {
      width: 42px;
      height: 42px;
      border-radius: 50%;
    }
    .empty-title {
      font-size: 0.92rem;
    }
    button.link {
      border: 0;
      background: transparent;
      color: var(--primary-color);
      cursor: pointer;
      padding: 0;
      text-align: left;
      font: inherit;
    }
    button.link:hover {
      text-decoration: underline;
    }
  `],Qt=class extends X{static editorTag=``;static async getConfigElement(){return document.createElement(this.editorTag)}static properties={hass:{attribute:!1},config:{state:!0},snapshot:{state:!0},sources:{state:!0},loading:{state:!0},error:{state:!0}};unsubscribe;bindingKey;syncGeneration=0;failedBindingKey;retryAfterMs=0;retryTimer;constructor(){super(),this.config={type:``},this.snapshot=void 0,this.sources=[],this.loading=!1,this.error=void 0}get headerAccent(){return`var(--primary-color, #03a9f4)`}renderHeaderBadge(){return U}get loadingLabel(){return`Loading`}get includeSiteInSubscribeMessage(){return!0}connectedCallback(){super.connectedCallback(),this.sync()}disconnectedCallback(){super.disconnectedCallback(),this.syncGeneration+=1,this.retryTimer&&=(clearTimeout(this.retryTimer),void 0),this.unsubscribe?.(),this.unsubscribe=void 0}willUpdate(e){let t=e.has(`hass`),n=e.has(`config`),r=e.get(`hass`),i=t&&r?.connection!==this.hass?.connection;(n||i)&&this.sync()}setConfig(e){if(!e||typeof e.type!=`string`)throw Error(`Invalid card config`);this.config={...e}}getCardSize(){return 4}getGridOptions(){return{columns:6,rows:4,min_columns:3,min_rows:3}}getBinding(){return this.config.entry_id&&this.config.site_id?{entry_id:this.config.entry_id,site_id:this.config.site_id}:h({entry_id:this.config.entry_id,site_id:this.config.site_id,title:this.config.title,view:`graph`,show_site_selector:!1,clients:`collapsed`,kinds:[`gateway`,`switch`,`access_point`,`client`,`other`],density:void 0,orientation:`vertical`,layout:`tree`,show_labels:!0,show_wan:!0,max_clients:500},this.sources)}async sync(){let e=++this.syncGeneration;if(!this.hass)return;this.loading=!0;try{if(this.sources=await this.hass.callWS({type:this.sourceCommand}),e!==this.syncGeneration||!this.isConnected){this.loading=!1;return}this.error=void 0}catch(t){if(e!==this.syncGeneration||!this.isConnected){this.loading=!1;return}this.error=Z(t),this.loading=!1;return}let t=this.getBinding(),n=t?`${t.entry_id}:${t.site_id}`:void 0;if(n===this.bindingKey&&this.unsubscribe||(this.bindingKey=n,n!==this.failedBindingKey&&(this.failedBindingKey=void 0,this.retryAfterMs=0),this.snapshot=void 0,await this.unsubscribe?.(),this.unsubscribe=void 0,!t)){this.loading=!1;return}if(n!==void 0&&n===this.failedBindingKey&&Date.now()<this.retryAfterMs){this.loading=!1,this.retryTimer&&clearTimeout(this.retryTimer),this.retryTimer=setTimeout(()=>{e===this.syncGeneration&&this.isConnected&&this.sync()},Math.max(0,this.retryAfterMs-Date.now()));return}let r={type:this.subscribeCommand,entry_id:t.entry_id};this.includeSiteInSubscribeMessage&&(r.site_id=t.site_id);try{let t=await this.hass.connection.subscribeMessage(t=>{e===this.syncGeneration&&this.isConnected&&((Array.isArray(t.issues)?t.issues:[]).some(e=>e?.code===`entry_unloaded`)?(this.unsubscribe?.().catch(()=>void 0),this.unsubscribe=void 0,this.bindingKey=void 0,this.retryTimer&&clearTimeout(this.retryTimer),this.retryTimer=setTimeout(()=>{e===this.syncGeneration&&this.isConnected&&this.sync()},1e3)):(this.failedBindingKey=void 0,this.retryAfterMs=0,this.error=void 0,this.snapshot=t,this.loading=!1))},r,{resubscribe:!1});if(e!==this.syncGeneration||!this.isConnected){await t(),this.loading=!1;return}this.unsubscribe=t}catch(t){if(e!==this.syncGeneration||!this.isConnected){this.loading=!1;return}this.failedBindingKey=n,this.retryAfterMs=Date.now()+5e3,this.error=Z(t),this.loading=!1,this.retryTimer=setTimeout(()=>{e===this.syncGeneration&&this.isConnected&&this.sync()},5e3)}}openMoreInfo(e){Q(this,`hass-more-info`,{entityId:e})}render(){let e=typeof this.snapshot?.site_name==`string`&&this.snapshot.site_name.trim()?this.snapshot.site_name:typeof this.snapshot?.entry_title==`string`&&this.snapshot.entry_title.trim()?this.snapshot.entry_title:void 0;return V`
      <ha-card style=${`--card-accent: ${this.headerAccent}`}>
        <div class="header">
          <div class="header-main">
            <div class="header-icon">${$(this.headerIcon)}</div>
            <div class="header-titles">
              <div class="header-title">
                ${this.config.title??this.defaultTitle}
              </div>
              ${e?V`<div class="header-subtitle">${e}</div>`:U}
            </div>
          </div>
          <div class="header-actions">${this.renderHeaderBadge()}</div>
        </div>
        ${this.loading?V`<div class="state loading-box">
              <span class="pulse-dot"></span>
              <span>${this.loadingLabel}</span>
            </div>`:U}
        ${this.error?V`<div class="state error">
              ${$(he)}
              <span>${this.error.code}</span>
            </div>`:U}
        ${!this.loading&&!this.error?this.renderContent():U}
      </ha-card>
    `}static styles=Zt};function $t(e){g(e.tag,e.card),g(e.editorTag,e.editor),window.customCards??=[],window.customCards.some(t=>t.type===e.tag)||window.customCards.push({type:e.tag,name:e.name,description:e.description,preview:!0})}export{Ae as $,Z as A,c as At,_e as B,kt as C,u as Ct,Dt as D,o as Dt,Q as E,l as Et,xt as F,t as Ft,Se as G,ye as H,ft as I,n as It,Te as J,Ce as K,w as L,re as Lt,U as M,te as Mt,H as N,e as Nt,Tt as O,i as Ot,V as P,r as Pt,ke as Q,he as R,Kt as S,de as St,$ as T,ee as Tt,be as U,ve as V,xe as W,De as X,_ as Y,Oe as Z,Ht as _,se as _t,Jt as a,Ie as at,Ut as b,fe as bt,Pt as c,b as ct,Lt as d,oe as dt,Me as et,Rt as f,ce as ft,Bt as g,ue as gt,zt as h,f as ht,Xt as i,v as it,X as j,a as jt,Ot as k,s as kt,Ft as l,g as lt,Mt as m,ae as mt,Qt as n,Pe as nt,qt as o,Le as ot,jt as p,le as pt,we as q,Yt as r,Fe as rt,Nt as s,Re as st,$t as t,Ne as tt,It as u,ie as ut,Vt as v,m as vt,At as w,d as wt,Gt as x,pe as xt,Wt as y,h as yt,ge as z};