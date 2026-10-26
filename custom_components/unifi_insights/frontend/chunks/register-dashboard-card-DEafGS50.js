var e=[`wan`,`gateway`,`switch`,`access_point`,`client`,`other`],t=`unifi_insights/topology/sources`,n=`unifi_insights/topology/subscribe`,r=`unifi_insights/topology/node_details`,i=`entry_unloaded`,a=`site_unavailable`,o=`devices_unavailable`,s=`legacy_uplink_missing`,c=`parents_unresolved`,l=`clients_truncated`,u=`entry_not_found`,d=`entry_not_loaded`,ee=`site_not_selected`,te=class extends Error{received;constructor(e){super(`Unsupported topology schema_version ${String(e)}`),this.name=`IncompatibleSchemaError`,this.received=e}},ne=e=>typeof e==`object`&&!!e&&!Array.isArray(e);function re(e){if(!ne(e))throw TypeError(`Topology snapshot is not an object`);if(typeof e.schema_version!=`number`)throw TypeError(`Topology snapshot field schema_version is not a number`);if(e.schema_version!==2)throw new te(e.schema_version);for(let t of[`nodes`,`edges`,`issues`,`unresolved`])if(!Array.isArray(e[t]))throw TypeError(`Topology snapshot field ${t} is not a list`);for(let t of[`entry_id`,`site_id`,`site_name`,`revision`,`status`])if(typeof e[t]!=`string`)throw TypeError(`Topology snapshot field ${t} is not a string`);return e}var ie=`unifi-insights-topology-card`,ae=`unifi-insights-topology-card-editor`,oe=`custom:${ie}`,se=[`graph`,`list`],ce=[`collapsed`,`expanded`,`hidden`],le=[`comfortable`,`compact`],ue=[`vertical`,`horizontal`],f=[`tree`,`tiered`];function p(e,t,n){if(e!==void 0&&!(typeof e==`string`&&t.includes(e)))throw Error(`${n} must be one of: ${t.join(`, `)}`)}function de(t){if(typeof t!=`object`||!t||Array.isArray(t))throw Error(`Card configuration must be an object`);let n=t;if(typeof n.type!=`string`)throw Error(`type is required`);for(let e of[`entry_id`,`site_id`,`title`]){let t=n[e];if(t!==void 0&&(typeof t!=`string`||t===``))throw Error(`${e} must be a non-empty string`)}if(n.entry_id===void 0!=(n.site_id===void 0))throw Error(`entry_id and site_id must be set together`);p(n.view,se,`view`),p(n.clients,ce,`clients`),p(n.density,le,`density`),p(n.orientation,ue,`orientation`),p(n.layout,f,`layout`);for(let e of[`show_site_selector`,`show_labels`,`show_wan`])if(n[e]!==void 0&&typeof n[e]!=`boolean`)throw Error(`${e} must be true or false`);if(n.kinds!==void 0&&(!Array.isArray(n.kinds)||n.kinds.length===0||!n.kinds.every(t=>e.includes(t))))throw Error(`kinds must be a non-empty list of: ${e.join(`, `)}`);let r=n.max_clients;if(r!==void 0&&(typeof r!=`number`||!Number.isInteger(r)||r<1||r>500))throw Error(`max_clients must be a whole number from 1 to 500`);return n}function fe(t){let n=t.show_wan??!0;return{entry_id:t.entry_id,site_id:t.site_id,title:t.title,view:t.view??`graph`,show_site_selector:t.show_site_selector??!1,clients:t.clients??`collapsed`,kinds:(t.kinds??e).filter(e=>n||e!==`wan`),density:t.density,orientation:t.orientation??`vertical`,layout:t.layout??`tree`,show_labels:t.show_labels??!0,show_wan:n,max_clients:t.max_clients??500}}function m(e){return e.flatMap(e=>e.sites.map(t=>({binding:{entry_id:e.entry_id,site_id:t.id},label:`${e.title} — ${t.name}`})))}function pe(e,t){if(e.entry_id!==void 0&&e.site_id!==void 0)return{entry_id:e.entry_id,site_id:e.site_id};let n=m(t??[]);return n.length===1?n[0].binding:void 0}function me(e,t){return e?.entry_id===t?.entry_id&&e?.site_id===t?.site_id}function h(e,t){customElements.get(e)||customElements.define(e,t)}var he=`M4.93,4.93C3.12,6.74 2,9.24 2,12C2,14.76 3.12,17.26 4.93,19.07L6.34,17.66C4.89,16.22 4,14.22 4,12C4,9.79 4.89,7.78 6.34,6.34L4.93,4.93M19.07,4.93L17.66,6.34C19.11,7.78 20,9.79 20,12C20,14.22 19.11,16.22 17.66,17.66L19.07,19.07C20.88,17.26 22,14.76 22,12C22,9.24 20.88,6.74 19.07,4.93M7.76,7.76C6.67,8.85 6,10.35 6,12C6,13.65 6.67,15.15 7.76,16.24L9.17,14.83C8.45,14.11 8,13.11 8,12C8,10.89 8.45,9.89 9.17,9.17L7.76,7.76M16.24,7.76L14.83,9.17C15.55,9.89 16,10.89 16,12C16,13.11 15.55,14.11 14.83,14.83L16.24,16.24C17.33,15.15 18,13.65 18,12C18,10.35 17.33,8.85 16.24,7.76M12,10A2,2 0 0,0 10,12A2,2 0 0,0 12,14A2,2 0 0,0 14,12A2,2 0 0,0 12,10Z`,ge=`M12,4A4,4 0 0,1 16,8A4,4 0 0,1 12,12A4,4 0 0,1 8,8A4,4 0 0,1 12,4M12,14C16.42,14 20,15.79 20,18V20H4V18C4,15.79 7.58,14 12,14Z`,g=`M11,15H13V17H11V15M11,7H13V13H11V7M12,2C6.47,2 2,6.5 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M12,20A8,8 0 0,1 4,12A8,8 0 0,1 12,4A8,8 0 0,1 20,12A8,8 0 0,1 12,20Z`,_e=`M9,4H15V12H19.84L12,19.84L4.16,12H9V4Z`,ve=`M15,20H9V12H4.16L12,4.16L19.84,12H15V20Z`,ye=`M10,21H14A2,2 0 0,1 12,23A2,2 0 0,1 10,21M21,19V20H3V19L5,17V11C5,7.9 7.03,5.17 10,4.29C10,4.19 10,4.1 10,4A2,2 0 0,1 12,2A2,2 0 0,1 14,4C14,4.1 14,4.19 14,4.29C16.97,5.17 19,7.9 19,11V17L21,19M17,11A5,5 0 0,0 12,6A5,5 0 0,0 7,11V18H17V11M19.75,3.19L18.33,4.61C20.04,6.3 21,8.6 21,11H23C23,8.07 21.84,5.25 19.75,3.19M1,11H3C3,8.6 3.96,6.3 5.67,4.61L4.25,3.19C2.16,5.25 1,8.07 1,11Z`,be=`M5,11L6.5,6.5H17.5L19,11M17.5,16A1.5,1.5 0 0,1 16,14.5A1.5,1.5 0 0,1 17.5,13A1.5,1.5 0 0,1 19,14.5A1.5,1.5 0 0,1 17.5,16M6.5,16A1.5,1.5 0 0,1 5,14.5A1.5,1.5 0 0,1 6.5,13A1.5,1.5 0 0,1 8,14.5A1.5,1.5 0 0,1 6.5,16M18.92,6C18.72,5.42 18.16,5 17.5,5H6.5C5.84,5 5.28,5.42 5.08,6L3,12V20A1,1 0 0,0 4,21H5A1,1 0 0,0 6,20V19H18V20A1,1 0 0,0 19,21H20A1,1 0 0,0 21,20V12L18.92,6Z`,xe=`M6.03 12.03L8.03 15.5L5.5 18.68L2 12.62L6.03 12.03M17 18V15.29C17.88 14.9 18.5 14.03 18.5 13C18.5 12.43 18.3 11.9 17.97 11.5L19.94 10.35C20.95 9.76 21.3 8.47 20.71 7.46L19.33 5.06C18.74 4.05 17.45 3.7 16.44 4.28L8.31 9C7.36 9.53 7.03 10.75 7.58 11.71L9.08 14.31C9.63 15.26 10.86 15.59 11.81 15.04L13.69 13.96C13.94 14.55 14.41 15.03 15 15.29V18C15 19.1 15.9 20 17 20H22V18H17Z`,Se=`M12 2C6.5 2 2 6.5 2 12S6.5 22 12 22 22 17.5 22 12 17.5 2 12 2M12 20C7.59 20 4 16.41 4 12S7.59 4 12 4 20 7.59 20 12 16.41 20 12 20M16.59 7.58L10 14.17L7.41 11.59L6 13L10 17L18 9L16.59 7.58Z`,Ce=`M7.41,8.58L12,13.17L16.59,8.58L18,10L12,16L6,10L7.41,8.58Z`,we=`M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z`,Te=`M6,4H18V5H21V7H18V9H21V11H18V13H21V15H18V17H21V19H18V20H6V19H3V17H6V15H3V13H6V11H3V9H6V7H3V5H6V4M11,15V18H12V15H11M13,15V18H14V15H13M15,15V18H16V15H15Z`,Ee=`M19,6.41L17.59,5L12,10.59L6.41,5L5,6.41L10.59,12L5,17.59L6.41,19L12,13.41L17.59,19L19,17.59L13.41,12L19,6.41Z`,De=`M19,21H8V7H19M19,5H8A2,2 0 0,0 6,7V21A2,2 0 0,0 8,23H19A2,2 0 0,0 21,21V7A2,2 0 0,0 19,5M16,1H4A2,2 0 0,0 2,3V17H4V3H16V1Z`,_=`M3 6H21V4H3C1.9 4 1 4.9 1 6V18C1 19.1 1.9 20 3 20H7V18H3V6M13 12H9V13.78C8.39 14.33 8 15.11 8 16C8 16.89 8.39 17.67 9 18.22V20H13V18.22C13.61 17.67 14 16.88 14 16S13.61 14.33 13 13.78V12M11 17.5C10.17 17.5 9.5 16.83 9.5 16S10.17 14.5 11 14.5 12.5 15.17 12.5 16 11.83 17.5 11 17.5M22 8H16C15.5 8 15 8.5 15 9V19C15 19.5 15.5 20 16 20H22C22.5 20 23 19.5 23 19V9C23 8.5 22.5 8 22 8M21 18H17V10H21V18Z`,Oe=`M14 15C14 16.11 13.11 17 12 17S10 16.11 10 15 10.9 13 12 13 14 13.9 14 15M18 4V20C18 21.1 17.11 22 16 22H8C6.9 22 6 21.11 6 20V4C6 2.9 6.9 2 8 2H16C17.11 2 18 2.9 18 4M10.5 7C10.5 7.83 11.17 8.5 12 8.5S13.5 7.83 13.5 7 12.83 5.5 12 5.5 10.5 6.17 10.5 7M16 10H8V20H16V10Z`,ke=`M12,16A2,2 0 0,1 14,18A2,2 0 0,1 12,20A2,2 0 0,1 10,18A2,2 0 0,1 12,16M12,10A2,2 0 0,1 14,12A2,2 0 0,1 12,14A2,2 0 0,1 10,12A2,2 0 0,1 12,10M12,4A2,2 0 0,1 14,6A2,2 0 0,1 12,8A2,2 0 0,1 10,6A2,2 0 0,1 12,4Z`,Ae=`M17.9,17.39C17.64,16.59 16.89,16 16,16H15V13A1,1 0 0,0 14,12H8V10H10A1,1 0 0,0 11,9V7H13A2,2 0 0,0 15,5V4.59C17.93,5.77 20,8.64 20,12C20,14.08 19.2,15.97 17.9,17.39M11,19.93C7.05,19.44 4,16.08 4,12C4,11.38 4.08,10.78 4.21,10.21L9,15V16A2,2 0 0,0 11,18M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2Z`,je=`M17 4H20C21.1 4 22 4.9 22 6V8H20V6H17V4M4 8V6H7V4H4C2.9 4 2 4.9 2 6V8H4M20 16V18H17V20H20C21.1 20 22 19.1 22 18V16H20M7 18H4V16H2V18C2 19.1 2.9 20 4 20H7V18M16 10V14H8V10H16M18 8H6V16H18V8Z`,Me=`M11,18H13V16H11V18M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M12,20C7.59,20 4,16.41 4,12C4,7.59 7.59,4 12,4C16.41,4 20,7.59 20,12C20,16.41 16.41,20 12,20M12,6A4,4 0 0,0 8,10H10A2,2 0 0,1 12,8A2,2 0 0,1 14,10C14,12 11,11.75 11,15H13C13,12.75 16,12.5 16,10A4,4 0 0,0 12,6Z`,Ne=`M11,9H13V7H11M12,20C7.59,20 4,16.41 4,12C4,7.59 7.59,4 12,4C16.41,4 20,7.59 20,12C20,16.41 16.41,20 12,20M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M11,17H13V11H11V17Z`,Pe=`M4,1C2.89,1 2,1.89 2,3V7C2,8.11 2.89,9 4,9H1V11H13V9H10C11.11,9 12,8.11 12,7V3C12,1.89 11.11,1 10,1H4M4,3H10V7H4V3M3,13V18L3,20H10V18H5V13H3M14,13C12.89,13 12,13.89 12,15V19C12,20.11 12.89,21 14,21H11V23H23V21H20C21.11,21 22,20.11 22,19V15C22,13.89 21.11,13 20,13H14M14,15H20V19H14V15Z`,Fe=`M15.5,14H14.71L14.43,13.73C15.41,12.59 16,11.11 16,9.5A6.5,6.5 0 0,0 9.5,3A6.5,6.5 0 0,0 3,9.5A6.5,6.5 0 0,0 9.5,16C11.11,16 12.59,15.41 13.73,14.43L14,14.71V15.5L19,20.5L20.5,19L15.5,14M9.5,14C7,14 5,12 5,9.5C5,7 7,5 9.5,5C12,5 14,7 14,9.5C14,12 12,14 9.5,14M7,9H12V10H7V9Z`,Ie=`M15.5,14L20.5,19L19,20.5L14,15.5V14.71L13.73,14.43C12.59,15.41 11.11,16 9.5,16A6.5,6.5 0 0,1 3,9.5A6.5,6.5 0 0,1 9.5,3A6.5,6.5 0 0,1 16,9.5C16,11.11 15.41,12.59 14.43,13.73L14.71,14H15.5M9.5,14C12,14 14,12 14,9.5C14,7 12,5 9.5,5C7,5 5,7 5,9.5C5,12 7,14 9.5,14M12,10H10V12H9V10H7V9H9V7H10V9H12V10Z`,Le=`M10,0.2C9,0.2 8.2,1 8.2,2C8.2,3 9,3.8 10,3.8C11,3.8 11.8,3 11.8,2C11.8,1 11,0.2 10,0.2M15.67,1A7.33,7.33 0 0,0 23,8.33V7A6,6 0 0,1 17,1H15.67M18.33,1C18.33,3.58 20.42,5.67 23,5.67V4.33C21.16,4.33 19.67,2.84 19.67,1H18.33M21,1A2,2 0 0,0 23,3V1H21M7.92,4.03C7.75,4.03 7.58,4.06 7.42,4.11L2,5.8V11H3.8V7.33L5.91,6.67L2,22H3.8L6.67,13.89L9,17V22H10.8V15.59L8.31,11.05L9.04,8.18L10.12,10H15V8.2H11.38L9.38,4.87C9.08,4.37 8.54,4.03 7.92,4.03Z`,Re=`M14,3V5H17.59L7.76,14.83L9.17,16.24L19,6.41V10H21V3M19,19H5V5H12V3H5C3.89,3 3,3.9 3,5V19A2,2 0 0,0 5,21H19A2,2 0 0,0 21,19V12H19V19Z`,ze=`M8.35,3C9.53,2.83 10.78,4.12 11.14,5.9C11.5,7.67 10.85,9.25 9.67,9.43C8.5,9.61 7.24,8.32 6.87,6.54C6.5,4.77 7.17,3.19 8.35,3M15.5,3C16.69,3.19 17.35,4.77 17,6.54C16.62,8.32 15.37,9.61 14.19,9.43C13,9.25 12.35,7.67 12.72,5.9C13.08,4.12 14.33,2.83 15.5,3M3,7.6C4.14,7.11 5.69,8 6.5,9.55C7.26,11.13 7,12.79 5.87,13.28C4.74,13.77 3.2,12.89 2.41,11.32C1.62,9.75 1.9,8.08 3,7.6M21,7.6C22.1,8.08 22.38,9.75 21.59,11.32C20.8,12.89 19.26,13.77 18.13,13.28C17,12.79 16.74,11.13 17.5,9.55C18.31,8 19.86,7.11 21,7.6M19.33,18.38C19.37,19.32 18.65,20.36 17.79,20.75C16,21.57 13.88,19.87 11.89,19.87C9.9,19.87 7.76,21.64 6,20.75C5,20.26 4.31,18.96 4.44,17.88C4.62,16.39 6.41,15.59 7.47,14.5C8.88,13.09 9.88,10.44 11.89,10.44C13.89,10.44 14.95,13.05 16.3,14.5C17.41,15.72 19.26,16.75 19.33,18.38Z`,v=`M5 9C3.9 9 3 9.9 3 11V15C3 16.11 3.9 17 5 17H11V19H10C9.45 19 9 19.45 9 20H2V22H9C9 22.55 9.45 23 10 23H14C14.55 23 15 22.55 15 22H22V20H15C15 19.45 14.55 19 14 19H13V17H19C20.11 17 21 16.11 21 15V11C21 9.9 20.11 9 19 9H5M6 12H8V14H6V12M9.5 12H11.5V14H9.5V12M13 12H15V14H13V12Z`,Be=`M21,11C21,16.55 17.16,21.74 12,23C6.84,21.74 3,16.55 3,11V5L12,1L21,5V11M12,21C15.75,20 19,15.54 19,11.22V6.3L12,3.18L5,6.3V11.22C5,15.54 8.25,20 12,21M10,17L6,13L7.41,11.59L10,14.17L16.59,7.58L18,9`,Ve=`M12,16A3,3 0 0,1 9,13C9,11.88 9.61,10.9 10.5,10.39L20.21,4.77L14.68,14.35C14.18,15.33 13.17,16 12,16M12,3C13.81,3 15.5,3.5 16.97,4.32L14.87,5.53C14,5.19 13,5 12,5A8,8 0 0,0 4,13C4,15.21 4.89,17.21 6.34,18.65H6.35C6.74,19.04 6.74,19.67 6.35,20.06C5.96,20.45 5.32,20.45 4.93,20.07V20.07C3.12,18.26 2,15.76 2,13A10,10 0 0,1 12,3M22,13C22,15.76 20.88,18.26 19.07,20.07V20.07C18.68,20.45 18.05,20.45 17.66,20.06C17.27,19.67 17.27,19.04 17.66,18.65V18.65C19.11,17.2 20,15.21 20,13C20,12 19.81,11 19.46,10.1L20.67,8C21.5,9.5 22,11.18 22,13Z`,He=`M13,18H14A1,1 0 0,1 15,19H22V21H15A1,1 0 0,1 14,22H10A1,1 0 0,1 9,21H2V19H9A1,1 0 0,1 10,18H11V16H8A1,1 0 0,1 7,15V3A1,1 0 0,1 8,2H16A1,1 0 0,1 17,3V15A1,1 0 0,1 16,16H13V18M13,6H14V4H13V6M9,4V6H11V4H9M9,8V10H11V8H9M9,12V14H11V12H9Z`,Ue=`M4 2V8H2V2H4M2 22V16H4V22H2M5 12C5 13.11 4.11 14 3 14C1.9 14 1 13.11 1 12C1 10.9 1.9 10 3 10C4.11 10 5 10.9 5 12M16 4C20.42 4 24 7.58 24 12C24 16.42 20.42 20 16 20C12.4 20 9.36 17.62 8.35 14.35L6 12L8.35 9.65C9.36 6.38 12.4 4 16 4M16 6C12.69 6 10 8.69 10 12C10 15.31 12.69 18 16 18C19.31 18 22 15.31 22 12C22 8.69 19.31 6 16 6M15 13V8H16.5V12.2L19.5 14L18.68 15.26L15 13Z`,We=`M16.36,14C16.44,13.34 16.5,12.68 16.5,12C16.5,11.32 16.44,10.66 16.36,10H19.74C19.9,10.64 20,11.31 20,12C20,12.69 19.9,13.36 19.74,14M14.59,19.56C15.19,18.45 15.65,17.25 15.97,16H18.92C17.96,17.65 16.43,18.93 14.59,19.56M14.34,14H9.66C9.56,13.34 9.5,12.68 9.5,12C9.5,11.32 9.56,10.65 9.66,10H14.34C14.43,10.65 14.5,11.32 14.5,12C14.5,12.68 14.43,13.34 14.34,14M12,19.96C11.17,18.76 10.5,17.43 10.09,16H13.91C13.5,17.43 12.83,18.76 12,19.96M8,8H5.08C6.03,6.34 7.57,5.06 9.4,4.44C8.8,5.55 8.35,6.75 8,8M5.08,16H8C8.35,17.25 8.8,18.45 9.4,19.56C7.57,18.93 6.03,17.65 5.08,16M4.26,14C4.1,13.36 4,12.69 4,12C4,11.31 4.1,10.64 4.26,10H7.64C7.56,10.66 7.5,11.32 7.5,12C7.5,12.68 7.56,13.34 7.64,14M12,4.03C12.83,5.23 13.5,6.57 13.91,8H10.09C10.5,6.57 11.17,5.23 12,4.03M18.92,8H15.97C15.65,6.75 15.19,5.55 14.59,4.44C16.43,5.07 17.96,6.34 18.92,8M12,2C6.47,2 2,6.5 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2Z`,Ge=`M12,21L15.6,16.2C14.6,15.45 13.35,15 12,15C10.65,15 9.4,15.45 8.4,16.2L12,21M12,3C7.95,3 4.21,4.34 1.2,6.6L3,9C5.5,7.12 8.62,6 12,6C15.38,6 18.5,7.12 21,9L22.8,6.6C19.79,4.34 16.05,3 12,3M12,9C9.3,9 6.81,9.89 4.8,11.4L6.6,13.8C8.1,12.67 9.97,12 12,12C14.03,12 15.9,12.67 17.4,13.8L19.2,11.4C17.19,9.89 14.7,9 12,9Z`,y=globalThis,b=y.ShadowRoot&&(y.ShadyCSS===void 0||y.ShadyCSS.nativeShadow)&&`adoptedStyleSheets`in Document.prototype&&`replace`in CSSStyleSheet.prototype,x=Symbol(),Ke=new WeakMap,qe=class{constructor(e,t,n){if(this._$cssResult$=!0,n!==x)throw Error("CSSResult is not constructable. Use `unsafeCSS` or `css` instead.");this.cssText=e,this.t=t}get styleSheet(){let e=this.o,t=this.t;if(b&&e===void 0){let n=t!==void 0&&t.length===1;n&&(e=Ke.get(t)),e===void 0&&((this.o=e=new CSSStyleSheet).replaceSync(this.cssText),n&&Ke.set(t,e))}return e}toString(){return this.cssText}},Je=e=>new qe(typeof e==`string`?e:e+``,void 0,x),S=(e,...t)=>new qe(e.length===1?e[0]:t.reduce((t,n,r)=>t+(e=>{if(!0===e._$cssResult$)return e.cssText;if(typeof e==`number`)return e;throw Error(`Value passed to 'css' function must be a 'css' function result: `+e+`. Use 'unsafeCSS' to pass non-literal values, but take care to ensure page security.`)})(n)+e[r+1],e[0]),e,x),Ye=(e,t)=>{if(b)e.adoptedStyleSheets=t.map(e=>e instanceof CSSStyleSheet?e:e.styleSheet);else for(let n of t){let t=document.createElement(`style`),r=y.litNonce;r!==void 0&&t.setAttribute(`nonce`,r),t.textContent=n.cssText,e.appendChild(t)}},Xe=b?e=>e:e=>e instanceof CSSStyleSheet?(e=>{let t=``;for(let n of e.cssRules)t+=n.cssText;return Je(t)})(e):e,{is:Ze,defineProperty:Qe,getOwnPropertyDescriptor:$e,getOwnPropertyNames:et,getOwnPropertySymbols:tt,getPrototypeOf:nt}=Object,C=globalThis,rt=C.trustedTypes,it=rt?rt.emptyScript:``,at=C.reactiveElementPolyfillSupport,w=(e,t)=>e,T={toAttribute(e,t){switch(t){case Boolean:e=e?it:null;break;case Object:case Array:e=e==null?e:JSON.stringify(e)}return e},fromAttribute(e,t){let n=e;switch(t){case Boolean:n=e!==null;break;case Number:n=e===null?null:Number(e);break;case Object:case Array:try{n=JSON.parse(e)}catch{n=null}}return n}},ot=(e,t)=>!Ze(e,t),st={attribute:!0,type:String,converter:T,reflect:!1,useDefault:!1,hasChanged:ot};Symbol.metadata??=Symbol(`metadata`),C.litPropertyMetadata??=new WeakMap;var E=class extends HTMLElement{static addInitializer(e){this._$Ei(),(this.l??=[]).push(e)}static get observedAttributes(){return this.finalize(),this._$Eh&&[...this._$Eh.keys()]}static createProperty(e,t=st){if(t.state&&(t.attribute=!1),this._$Ei(),this.prototype.hasOwnProperty(e)&&((t=Object.create(t)).wrapped=!0),this.elementProperties.set(e,t),!t.noAccessor){let n=Symbol(),r=this.getPropertyDescriptor(e,n,t);r!==void 0&&Qe(this.prototype,e,r)}}static getPropertyDescriptor(e,t,n){let{get:r,set:i}=$e(this.prototype,e)??{get(){return this[t]},set(e){this[t]=e}};return{get:r,set(t){let a=r?.call(this);i?.call(this,t),this.requestUpdate(e,a,n)},configurable:!0,enumerable:!0}}static getPropertyOptions(e){return this.elementProperties.get(e)??st}static _$Ei(){if(this.hasOwnProperty(w(`elementProperties`)))return;let e=nt(this);e.finalize(),e.l!==void 0&&(this.l=[...e.l]),this.elementProperties=new Map(e.elementProperties)}static finalize(){if(this.hasOwnProperty(w(`finalized`)))return;if(this.finalized=!0,this._$Ei(),this.hasOwnProperty(w(`properties`))){let e=this.properties,t=[...et(e),...tt(e)];for(let n of t)this.createProperty(n,e[n])}let e=this[Symbol.metadata];if(e!==null){let t=litPropertyMetadata.get(e);if(t!==void 0)for(let[e,n]of t)this.elementProperties.set(e,n)}this._$Eh=new Map;for(let[e,t]of this.elementProperties){let n=this._$Eu(e,t);n!==void 0&&this._$Eh.set(n,e)}this.elementStyles=this.finalizeStyles(this.styles)}static finalizeStyles(e){let t=[];if(Array.isArray(e)){let n=new Set(e.flat(1/0).reverse());for(let e of n)t.unshift(Xe(e))}else e!==void 0&&t.push(Xe(e));return t}static _$Eu(e,t){let n=t.attribute;return!1===n?void 0:typeof n==`string`?n:typeof e==`string`?e.toLowerCase():void 0}constructor(){super(),this._$Ep=void 0,this.isUpdatePending=!1,this.hasUpdated=!1,this._$Em=null,this._$Ev()}_$Ev(){this._$ES=new Promise(e=>this.enableUpdating=e),this._$AL=new Map,this._$E_(),this.requestUpdate(),this.constructor.l?.forEach(e=>e(this))}addController(e){(this._$EO??=new Set).add(e),this.renderRoot!==void 0&&this.isConnected&&e.hostConnected?.()}removeController(e){this._$EO?.delete(e)}_$E_(){let e=new Map,t=this.constructor.elementProperties;for(let n of t.keys())this.hasOwnProperty(n)&&(e.set(n,this[n]),delete this[n]);e.size>0&&(this._$Ep=e)}createRenderRoot(){let e=this.shadowRoot??this.attachShadow(this.constructor.shadowRootOptions);return Ye(e,this.constructor.elementStyles),e}connectedCallback(){this.renderRoot??=this.createRenderRoot(),this.enableUpdating(!0),this._$EO?.forEach(e=>e.hostConnected?.())}enableUpdating(e){}disconnectedCallback(){this._$EO?.forEach(e=>e.hostDisconnected?.())}attributeChangedCallback(e,t,n){this._$AK(e,n)}_$ET(e,t){let n=this.constructor.elementProperties.get(e),r=this.constructor._$Eu(e,n);if(r!==void 0&&!0===n.reflect){let i=(n.converter?.toAttribute===void 0?T:n.converter).toAttribute(t,n.type);this._$Em=e,i==null?this.removeAttribute(r):this.setAttribute(r,i),this._$Em=null}}_$AK(e,t){let n=this.constructor,r=n._$Eh.get(e);if(r!==void 0&&this._$Em!==r){let e=n.getPropertyOptions(r),i=typeof e.converter==`function`?{fromAttribute:e.converter}:e.converter?.fromAttribute===void 0?T:e.converter;this._$Em=r;let a=i.fromAttribute(t,e.type);this[r]=a??this._$Ej?.get(r)??a,this._$Em=null}}requestUpdate(e,t,n,r=!1,i){if(e!==void 0){let a=this.constructor;if(!1===r&&(i=this[e]),n??=a.getPropertyOptions(e),!((n.hasChanged??ot)(i,t)||n.useDefault&&n.reflect&&i===this._$Ej?.get(e)&&!this.hasAttribute(a._$Eu(e,n))))return;this.C(e,t,n)}!1===this.isUpdatePending&&(this._$ES=this._$EP())}C(e,t,{useDefault:n,reflect:r,wrapped:i},a){n&&!(this._$Ej??=new Map).has(e)&&(this._$Ej.set(e,a??t??this[e]),!0!==i||a!==void 0)||(this._$AL.has(e)||(this.hasUpdated||n||(t=void 0),this._$AL.set(e,t)),!0===r&&this._$Em!==e&&(this._$Eq??=new Set).add(e))}async _$EP(){this.isUpdatePending=!0;try{await this._$ES}catch(e){Promise.reject(e)}let e=this.scheduleUpdate();return e!=null&&await e,!this.isUpdatePending}scheduleUpdate(){return this.performUpdate()}performUpdate(){if(!this.isUpdatePending)return;if(!this.hasUpdated){if(this.renderRoot??=this.createRenderRoot(),this._$Ep){for(let[e,t]of this._$Ep)this[e]=t;this._$Ep=void 0}let e=this.constructor.elementProperties;if(e.size>0)for(let[t,n]of e){let{wrapped:e}=n,r=this[t];!0!==e||this._$AL.has(t)||r===void 0||this.C(t,void 0,n,r)}}let e=!1,t=this._$AL;try{e=this.shouldUpdate(t),e?(this.willUpdate(t),this._$EO?.forEach(e=>e.hostUpdate?.()),this.update(t)):this._$EM()}catch(t){throw e=!1,this._$EM(),t}e&&this._$AE(t)}willUpdate(e){}_$AE(e){this._$EO?.forEach(e=>e.hostUpdated?.()),this.hasUpdated||(this.hasUpdated=!0,this.firstUpdated(e)),this.updated(e)}_$EM(){this._$AL=new Map,this.isUpdatePending=!1}get updateComplete(){return this.getUpdateComplete()}getUpdateComplete(){return this._$ES}shouldUpdate(e){return!0}update(e){this._$Eq&&=this._$Eq.forEach(e=>this._$ET(e,this[e])),this._$EM()}updated(e){}firstUpdated(e){}};E.elementStyles=[],E.shadowRootOptions={mode:`open`},E[w(`elementProperties`)]=new Map,E[w(`finalized`)]=new Map,at?.({ReactiveElement:E}),(C.reactiveElementVersions??=[]).push(`2.1.2`);var D=globalThis,ct=e=>e,O=D.trustedTypes,lt=O?O.createPolicy(`lit-html`,{createHTML:e=>e}):void 0,k=`$lit$`,A=`lit$${Math.random().toFixed(9).slice(2)}$`,j=`?`+A,ut=`<${j}>`,M=document,N=()=>M.createComment(``),P=e=>e===null||typeof e!=`object`&&typeof e!=`function`,F=Array.isArray,dt=e=>F(e)||typeof e?.[Symbol.iterator]==`function`,I=`[ 	
\f\r]`,L=/<(?:(!--|\/[^a-zA-Z])|(\/?[a-zA-Z][^>\s]*)|(\/?$))/g,ft=/-->/g,pt=/>/g,R=RegExp(`>|${I}(?:([^\\s"'>=/]+)(${I}*=${I}*(?:[^ \t\n\f\r"'\`<>=]|("|')|))|$)`,`g`),mt=/'/g,ht=/"/g,gt=/^(?:script|style|textarea|title)$/i,_t=e=>(t,...n)=>({_$litType$:e,strings:t,values:n}),z=_t(1),vt=_t(2),B=Symbol.for(`lit-noChange`),V=Symbol.for(`lit-nothing`),yt=new WeakMap,H=M.createTreeWalker(M,129);function bt(e,t){if(!F(e)||!e.hasOwnProperty(`raw`))throw Error(`invalid template strings array`);return lt===void 0?t:lt.createHTML(t)}var xt=(e,t)=>{let n=e.length-1,r=[],i,a=t===2?`<svg>`:t===3?`<math>`:``,o=L;for(let t=0;t<n;t++){let n=e[t],s,c,l=-1,u=0;for(;u<n.length&&(o.lastIndex=u,c=o.exec(n),c!==null);)u=o.lastIndex,o===L?c[1]===`!--`?o=ft:c[1]===void 0?c[2]===void 0?c[3]!==void 0&&(o=R):(gt.test(c[2])&&(i=RegExp(`</`+c[2],`g`)),o=R):o=pt:o===R?c[0]===`>`?(o=i??L,l=-1):c[1]===void 0?l=-2:(l=o.lastIndex-c[2].length,s=c[1],o=c[3]===void 0?R:c[3]===`"`?ht:mt):o===ht||o===mt?o=R:o===ft||o===pt?o=L:(o=R,i=void 0);let d=o===R&&e[t+1].startsWith(`/>`)?` `:``;a+=o===L?n+ut:l>=0?(r.push(s),n.slice(0,l)+k+n.slice(l)+A+d):n+A+(l===-2?t:d)}return[bt(e,a+(e[n]||`<?>`)+(t===2?`</svg>`:t===3?`</math>`:``)),r]},U=class e{constructor({strings:t,_$litType$:n},r){let i;this.parts=[];let a=0,o=0,s=t.length-1,c=this.parts,[l,u]=xt(t,n);if(this.el=e.createElement(l,r),H.currentNode=this.el.content,n===2||n===3){let e=this.el.content.firstChild;e.replaceWith(...e.childNodes)}for(;(i=H.nextNode())!==null&&c.length<s;){if(i.nodeType===1){if(i.hasAttributes())for(let e of i.getAttributeNames())if(e.endsWith(k)){let t=u[o++],n=i.getAttribute(e).split(A),r=/([.?@])?(.*)/.exec(t);c.push({type:1,index:a,name:r[2],strings:n,ctor:r[1]===`.`?Ct:r[1]===`?`?wt:r[1]===`@`?Tt:K}),i.removeAttribute(e)}else e.startsWith(A)&&(c.push({type:6,index:a}),i.removeAttribute(e));if(gt.test(i.tagName)){let e=i.textContent.split(A),t=e.length-1;if(t>0){i.textContent=O?O.emptyScript:``;for(let n=0;n<t;n++)i.append(e[n],N()),H.nextNode(),c.push({type:2,index:++a});i.append(e[t],N())}}}else if(i.nodeType===8){if(i.data===j)c.push({type:2,index:a});else{let e=-1;for(;(e=i.data.indexOf(A,e+1))!==-1;)c.push({type:7,index:a}),e+=A.length-1}}a++}}static createElement(e,t){let n=M.createElement(`template`);return n.innerHTML=e,n}};function W(e,t,n=e,r){if(t===B)return t;let i=r===void 0?n._$Cl:n._$Co?.[r],a=P(t)?void 0:t._$litDirective$;return i?.constructor!==a&&(i?._$AO?.(!1),a===void 0?i=void 0:(i=new a(e),i._$AT(e,n,r)),r===void 0?n._$Cl=i:(n._$Co??=[])[r]=i),i!==void 0&&(t=W(e,i._$AS(e,t.values),i,r)),t}var St=class{constructor(e,t){this._$AV=[],this._$AN=void 0,this._$AD=e,this._$AM=t}get parentNode(){return this._$AM.parentNode}get _$AU(){return this._$AM._$AU}u(e){let{el:{content:t},parts:n}=this._$AD,r=(e?.creationScope??M).importNode(t,!0);H.currentNode=r;let i=H.nextNode(),a=0,o=0,s=n[0];for(;s!==void 0;){if(a===s.index){let t;s.type===2?t=new G(i,i.nextSibling,this,e):s.type===1?t=new s.ctor(i,s.name,s.strings,this,e):s.type===6&&(t=new Et(i,this,e)),this._$AV.push(t),s=n[++o]}a!==s?.index&&(i=H.nextNode(),a++)}return H.currentNode=M,r}p(e){let t=0;for(let n of this._$AV)n!==void 0&&(n.strings===void 0?n._$AI(e[t]):(n._$AI(e,n,t),t+=n.strings.length-2)),t++}},G=class e{get _$AU(){return this._$AM?._$AU??this._$Cv}constructor(e,t,n,r){this.type=2,this._$AH=V,this._$AN=void 0,this._$AA=e,this._$AB=t,this._$AM=n,this.options=r,this._$Cv=r?.isConnected??!0}get parentNode(){let e=this._$AA.parentNode,t=this._$AM;return t!==void 0&&e?.nodeType===11&&(e=t.parentNode),e}get startNode(){return this._$AA}get endNode(){return this._$AB}_$AI(e,t=this){e=W(this,e,t),P(e)?e===V||e==null||e===``?(this._$AH!==V&&this._$AR(),this._$AH=V):e!==this._$AH&&e!==B&&this._(e):e._$litType$===void 0?e.nodeType===void 0?dt(e)?this.k(e):this._(e):this.T(e):this.$(e)}O(e){return this._$AA.parentNode.insertBefore(e,this._$AB)}T(e){this._$AH!==e&&(this._$AR(),this._$AH=this.O(e))}_(e){this._$AH!==V&&P(this._$AH)?this._$AA.nextSibling.data=e:this.T(M.createTextNode(e)),this._$AH=e}$(e){let{values:t,_$litType$:n}=e,r=typeof n==`number`?this._$AC(e):(n.el===void 0&&(n.el=U.createElement(bt(n.h,n.h[0]),this.options)),n);if(this._$AH?._$AD===r)this._$AH.p(t);else{let e=new St(r,this),n=e.u(this.options);e.p(t),this.T(n),this._$AH=e}}_$AC(e){let t=yt.get(e.strings);return t===void 0&&yt.set(e.strings,t=new U(e)),t}k(t){F(this._$AH)||(this._$AH=[],this._$AR());let n=this._$AH,r,i=0;for(let a of t)i===n.length?n.push(r=new e(this.O(N()),this.O(N()),this,this.options)):r=n[i],r._$AI(a),i++;i<n.length&&(this._$AR(r&&r._$AB.nextSibling,i),n.length=i)}_$AR(e=this._$AA.nextSibling,t){for(this._$AP?.(!1,!0,t);e!==this._$AB;){let t=ct(e).nextSibling;ct(e).remove(),e=t}}setConnected(e){this._$AM===void 0&&(this._$Cv=e,this._$AP?.(e))}},K=class{get tagName(){return this.element.tagName}get _$AU(){return this._$AM._$AU}constructor(e,t,n,r,i){this.type=1,this._$AH=V,this._$AN=void 0,this.element=e,this.name=t,this._$AM=r,this.options=i,n.length>2||n[0]!==``||n[1]!==``?(this._$AH=Array(n.length-1).fill(new String),this.strings=n):this._$AH=V}_$AI(e,t=this,n,r){let i=this.strings,a=!1;if(i===void 0)e=W(this,e,t,0),a=!P(e)||e!==this._$AH&&e!==B,a&&(this._$AH=e);else{let r=e,o,s;for(e=i[0],o=0;o<i.length-1;o++)s=W(this,r[n+o],t,o),s===B&&(s=this._$AH[o]),a||=!P(s)||s!==this._$AH[o],s===V?e=V:e!==V&&(e+=(s??``)+i[o+1]),this._$AH[o]=s}a&&!r&&this.j(e)}j(e){e===V?this.element.removeAttribute(this.name):this.element.setAttribute(this.name,e??``)}},Ct=class extends K{constructor(){super(...arguments),this.type=3}j(e){this.element[this.name]=e===V?void 0:e}},wt=class extends K{constructor(){super(...arguments),this.type=4}j(e){this.element.toggleAttribute(this.name,!!e&&e!==V)}},Tt=class extends K{constructor(e,t,n,r,i){super(e,t,n,r,i),this.type=5}_$AI(e,t=this){if((e=W(this,e,t,0)??V)===B)return;let n=this._$AH,r=e===V&&n!==V||e.capture!==n.capture||e.once!==n.once||e.passive!==n.passive,i=e!==V&&(n===V||r);r&&this.element.removeEventListener(this.name,this,n),i&&this.element.addEventListener(this.name,this,e),this._$AH=e}handleEvent(e){typeof this._$AH==`function`?this._$AH.call(this.options?.host??this.element,e):this._$AH.handleEvent(e)}},Et=class{constructor(e,t,n){this.element=e,this.type=6,this._$AN=void 0,this._$AM=t,this.options=n}get _$AU(){return this._$AM._$AU}_$AI(e){W(this,e)}},Dt={M:k,P:A,A:j,C:1,L:xt,R:St,D:dt,V:W,I:G,H:K,N:wt,U:Tt,B:Ct,F:Et},Ot=D.litHtmlPolyfillSupport;Ot?.(U,G),(D.litHtmlVersions??=[]).push(`3.3.3`);var kt=(e,t,n)=>{let r=n?.renderBefore??t,i=r._$litPart$;if(i===void 0){let e=n?.renderBefore??null;r._$litPart$=i=new G(t.insertBefore(N(),e),e,void 0,n??{})}return i._$AI(e),i},q=globalThis,J=class extends E{constructor(){super(...arguments),this.renderOptions={host:this},this._$Do=void 0}createRenderRoot(){let e=super.createRenderRoot();return this.renderOptions.renderBefore??=e.firstChild,e}update(e){let t=this.render();this.hasUpdated||(this.renderOptions.isConnected=this.isConnected),super.update(e),this._$Do=kt(t,this.renderRoot,this.renderOptions)}connectedCallback(){super.connectedCallback(),this._$Do?.setConnected(!0)}disconnectedCallback(){super.disconnectedCallback(),this._$Do?.setConnected(!1)}render(){return B}};J._$litElement$=!0,J.finalized=!0,q.litElementHydrateSupport?.({LitElement:J});var At=q.litElementPolyfillSupport;At?.({LitElement:J}),(q.litElementVersions??=[]).push(`4.2.2`);function jt(e){return e?.user?.is_admin===!0}function Mt(e){return typeof e==`object`&&!!e&&typeof e.code==`string`}function Y(e){return Mt(e)?{code:e.code,message:typeof e.message==`string`?e.message:``}:{code:`unknown_error`,message:String(e)}}function X(e,t,n){e.dispatchEvent(new CustomEvent(t,{detail:n,bubbles:!0,composed:!0}))}function Nt(e,t){X(e,`hass-more-info`,{entityId:t})}function Pt(e){history.pushState(null,``,e),X(window,`location-changed`,{replace:!1})}var Z=S`
  :host {
    --uit-online: var(--success-color, #43a047);
    --uit-offline: var(--error-color, #db4437);
    --uit-warning: var(--warning-color, #ffa600);
    --uit-unknown: var(--disabled-text-color, #bdbdbd);
    --uit-focus: var(--ha-color-focus, var(--primary-color, #03a9f4));
    --uit-line: var(--divider-color, rgba(0, 0, 0, 0.12));
    color: var(--ha-color-text-primary, var(--primary-text-color));
    font-family: var(--ha-font-family-body, sans-serif);
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
`,Q=S`
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
    background: var(
      --ha-color-fill-primary-quiet-resting,
      color-mix(in srgb, var(--primary-color) 16%, transparent)
    );
    border-color: var(--primary-color);
    color: var(--ha-color-text-primary, var(--primary-text-color));
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
`,Ft=S`
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
`,It=_;function Lt(e){switch(e.kind){case`wan`:return We;case`gateway`:return v;case`switch`:return He;case`access_point`:return he;case`client`:return e.connection===`wireless`?Ge:Pe;default:return _}}function $(e){return z`<svg
    class="icon"
    viewBox="0 0 24 24"
    aria-hidden="true"
    focusable="false"
  >
    <path d=${e}></path>
  </svg>`}var Rt=`unifi-insights-site-health-card`,zt=`unifi-insights-site-health-card-editor`,Bt=`unifi-insights-internet-activity-card`,Vt=`unifi-insights-internet-activity-card-editor`,Ht=`unifi-insights-performance-card`,Ut=`unifi-insights-performance-card-editor`,Wt=`unifi-insights-protect-status-card`,Gt=`unifi-insights-protect-status-card-editor`,Kt=`unifi-insights-timeline-card`,qt=`unifi-insights-timeline-card-editor`;function Jt(e){let t=Math.round(e/1e6);return e>=1e9?`${(e/1e9).toFixed(1)} GB`:`${t} MB`}function Yt(e){if(!(typeof e!=`number`||!Number.isFinite(e)||e<0))return e>=1e9?`${(e/1e9).toFixed(1)} Gbps`:e>=1e6?`${(e/1e6).toFixed(1)} Mbps`:e>=1e3?`${Math.round(e/1e3)} Kbps`:`${Math.round(e)} bps`}function Xt(e){if(typeof e!=`number`||!Number.isFinite(e)||e<=0)return;let t=Math.floor(e/86400),n=Math.floor(e%86400/3600);if(t>0)return`${t}d ${n}h`;let r=Math.max(1,Math.floor(e%3600/60));return n>0?`${n}h ${r}m`:`${r}m`}function Zt(e){if(typeof e!=`string`||!e)return``;let t=new Date(e);if(Number.isNaN(t.getTime()))return``;let n=Math.max(0,Math.round((Date.now()-t.getTime())/6e4));if(n<1)return`Just now`;if(n<60)return`${n}m ago`;let r=Math.floor(n/60);return r<24?`${r}h ago`:t.toLocaleTimeString([],{hour:`2-digit`,minute:`2-digit`})}function Qt(e){switch(String(e??``)){case`gateway`:return v;case`switch`:return He;case`access_point`:return he;case`camera`:return xe;case`doorbell`:return Oe;case`chime`:case`ring`:return ye;default:return _}}function $t(e){return e>=85?`critical`:e>=60?`warning`:`ok`}var en=class extends J{static properties={hass:{attribute:!1},config:{state:!0}};cardType;sourceCommand;options;optionsRequested=!1;constructor(e,t){super(),this.cardType=e,this.sourceCommand=t,this.config={type:`custom:${e}`},this.options=[]}setConfig(e){this.config={...e}}willUpdate(){this.hass&&!this.optionsRequested&&(this.optionsRequested=!0,this.loadOptions())}async loadOptions(){if(this.hass)try{let e=await this.hass.callWS({type:this.sourceCommand});this.options=this.sourceCommand===`unifi_insights/protect/sources`?e.map(e=>({entryId:e.entry_id,siteId:`*`,label:e.title})):m(e).map(e=>({entryId:e.binding.entry_id,siteId:e.binding.site_id,label:e.label})),this.requestUpdate()}catch{this.options=[],this.requestUpdate()}}applySite(e){let t={...this.config,type:this.config.type||`custom:${this.cardType}`};if(!e)delete t.entry_id,delete t.site_id;else{let n=this.options[Number.parseInt(e,10)];n&&(t.entry_id=n.entryId,t.site_id=n.siteId)}this.config=t,X(this,`config-changed`,{config:t})}applyTitle(e){let t={...this.config};e.trim()?t.title=e.trim():delete t.title,this.config=t,X(this,`config-changed`,{config:t})}render(){let e=this.config.entry_id&&this.config.site_id?String(this.options.findIndex(e=>e.entryId===this.config.entry_id&&e.siteId===this.config.site_id)):``;return z`
      <div class="editor">
        <label for="source"
          >${this.sourceCommand===`unifi_insights/protect/sources`?`Integration`:`Site`}</label
        >
        <select
          id="source"
          @change=${e=>this.applySite(e.target.value)}
        >
          <option value="">Auto</option>
          ${this.options.map((t,n)=>{let r=String(n);return z`<option value=${r} ?selected=${r===e}>
              ${t.label}
            </option>`})}
        </select>
        <label for="title">Title</label>
        <input
          id="title"
          .value=${this.config.title??``}
          @input=${e=>this.applyTitle(e.target.value)}
        />
      </div>
    `}static styles=[Z,Q,S`
      :host {
        display: block;
        min-width: 0;
      }
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
        min-height: 44px;
        width: 100%;
        min-width: 0;
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        padding: 4px 8px;
        background: var(--card-background-color);
        color: var(--primary-text-color);
      }
    `]},tn=[Z,Q,S`
    :host {
      display: block;
      height: 100%;
      min-width: 0;
      --uit-text-secondary: var(
        --ha-color-text-secondary,
        var(--primary-text-color)
      );
    }
    ha-card {
      height: 100%;
      box-sizing: border-box;
      padding: var(--ha-space-4, 16px);
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-3, 12px);
      overflow: hidden;
      container-type: inline-size;
      min-height: 0;
    }
    .header {
      flex-shrink: 0;
      flex-wrap: wrap;
    }
    .content {
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-3, 12px);
      flex: 1 1 auto;
      min-height: 0;
      overflow: auto;
      padding: var(--ha-space-1, 4px);
      margin: calc(-1 * var(--ha-space-1, 4px));
    }
    .content > * {
      flex-shrink: 0;
    }
    .content > .list {
      flex: 1 1 auto;
      min-height: 44px;
      overflow: auto;
      padding: var(--ha-space-1, 4px);
      margin: calc(-1 * var(--ha-space-1, 4px));
      align-content: start;
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
      border-radius: var(--ha-border-radius-lg, 12px);
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
      font-weight: var(--ha-font-weight-medium, 600);
    }
    .header-title {
      font-size: var(--ha-font-size-l, 1.02rem);
      line-height: var(--ha-line-height-condensed, 1.25);
      margin: 0;
    }
    .header-subtitle,
    .hero-meta,
    .kpi-sub,
    .item-meta,
    .empty-sub {
      font-size: var(--ha-font-size-s, 0.75rem);
      color: var(--uit-text-secondary);
    }
    .header-actions,
    .kpi-top,
    .item-meta,
    .empty-hero,
    .chip {
      gap: var(--ha-space-2, 6px);
    }
    .state {
      flex-shrink: 0;
      color: var(--uit-text-secondary);
      font-size: var(--ha-font-size-m, 0.88rem);
      padding: var(--ha-space-3, 12px);
      border-radius: var(--ha-border-radius-lg, 12px);
      background: color-mix(in srgb, var(--primary-text-color) 4%, transparent);
      display: flex;
      align-items: center;
      gap: var(--ha-space-2, 8px);
    }
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
      font-size: var(--ha-font-size-s, 0.74rem);
      text-transform: capitalize;
      background: color-mix(in srgb, var(--primary-text-color) 8%, transparent);
      color: var(--primary-text-color);
    }
    .item-icon.online,
    .empty-hero .icon-badge {
      background: color-mix(in srgb, var(--uit-online) 16%, transparent);
      color: var(--uit-online);
    }
    .hero-banner {
      padding: 10px 12px;
      border-radius: var(--ha-border-radius-lg, 12px);
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
      font-size: var(--ha-font-size-m, 0.9rem);
    }
    .kpi-grid,
    .ring-strip,
    .list {
      display: grid;
      gap: var(--ha-space-2, 8px);
    }
    .kpi-grid {
      grid-template-columns: repeat(auto-fit, minmax(92px, 1fr));
    }
    .ring-strip {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .kpi-tile,
    .ring-card,
    .item-card {
      border-radius: var(--ha-border-radius-lg, 12px);
      background: color-mix(in srgb, var(--primary-text-color) 4%, transparent);
      border: 1px solid var(--uit-line);
    }
    .kpi-tile {
      appearance: none;
      align-items: stretch;
      padding: 10px 12px;
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-1, 4px);
      min-width: 0;
      text-align: start;
      font: inherit;
      color: inherit;
    }
    .kpi-tile:disabled {
      cursor: default;
      opacity: 1;
    }
    .kpi-tile.clickable {
      cursor: pointer;
      transition: background var(--ha-animation-duration-fast, 150ms) ease;
    }
    .kpi-tile.clickable:hover {
      background: color-mix(in srgb, var(--primary-color) 10%, transparent);
    }
    .kpi-top {
      font-size: var(--ha-font-size-s, 0.73rem);
      text-transform: uppercase;
      letter-spacing: 0.03em;
      color: var(--uit-text-secondary);
    }
    .kpi-value {
      font-size: var(--ha-font-size-xl, 1.18rem);
      font-weight: var(--ha-font-weight-bold, 700);
      font-variant-numeric: tabular-nums;
      line-height: var(--ha-line-height-condensed, 1.2);
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
      transition: width var(--ha-animation-duration-normal, 250ms) ease;
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
      background: var(--purple-color, var(--primary-color));
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
      font-size: var(--ha-font-size-s, 0.7rem);
      font-weight: var(--ha-font-weight-bold, 700);
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
      gap: var(--ha-space-1, 4px);
    }
    .item-name {
      font-size: var(--ha-font-size-m, 0.88rem);
    }
    .item-value {
      font-size: var(--ha-font-size-m, 0.85rem);
      font-weight: var(--ha-font-weight-bold, 700);
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
      color: var(--uit-text-secondary);
      font: inherit;
      font-size: var(--ha-font-size-s, 0.7rem);
      padding: 3px 8px;
      border-radius: 999px;
      cursor: pointer;
      text-transform: uppercase;
      min-width: 44px;
      min-height: 44px;
      justify-content: center;
    }
    .pill-tab[aria-pressed="true"] {
      background: var(
        --ha-color-fill-primary-quiet-resting,
        color-mix(in srgb, var(--primary-color) 16%, transparent)
      );
      color: var(--ha-color-text-primary, var(--primary-text-color));
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
      font-size: var(--ha-font-size-l, 0.92rem);
    }
    button.link {
      border: 0;
      background: transparent;
      color: var(--ha-color-text-primary, var(--primary-text-color));
      cursor: pointer;
      padding: 0;
      text-align: start;
      font: inherit;
      min-width: 44px;
      min-height: 44px;
      text-decoration: underline;
      text-underline-offset: 3px;
      white-space: normal;
      overflow-wrap: anywhere;
    }
    .chip.ok,
    .chip.healthy,
    .chip.online {
      color: var(--ha-color-text-primary, var(--primary-text-color));
      background: var(
        --ha-color-fill-success-quiet-resting,
        color-mix(in srgb, var(--uit-online) 16%, transparent)
      );
    }
    .chip.warning,
    .chip.degraded,
    .chip.partial {
      color: var(--ha-color-text-primary, var(--primary-text-color));
      background: var(
        --ha-color-fill-warning-quiet-resting,
        color-mix(in srgb, var(--uit-warning) 18%, transparent)
      );
    }
    .chip.critical,
    .chip.offline,
    .state.error {
      color: var(--ha-color-text-primary, var(--primary-text-color));
      background: var(
        --ha-color-fill-danger-quiet-resting,
        color-mix(in srgb, var(--uit-offline) 14%, transparent)
      );
    }
    .state-detail {
      font-size: var(--ha-font-size-s, 12px);
      display: block;
    }
    .header-main {
      flex: 1 1 140px;
    }
    .header-actions {
      flex-wrap: wrap;
    }
    .hero-left > div,
    .ring-card > div,
    .item-top {
      min-width: 0;
    }
    .hero-meta,
    .item-meta {
      flex-wrap: wrap;
      overflow-wrap: anywhere;
    }
    @container (max-width: 360px) {
      .header-actions {
        max-width: 100%;
      }
      .hero-banner,
      .item-top {
        flex-wrap: wrap;
      }
      .hero-title,
      .item-name {
        white-space: normal;
        overflow-wrap: anywhere;
      }
    }
    @container (max-width: 260px) {
      .ring-strip,
      .kpi-grid {
        grid-template-columns: minmax(0, 1fr);
      }
      .item-card {
        align-items: flex-start;
      }
    }
  `],nn={"timeline.person":`Person`,"timeline.animal":`Animal`,"timeline.car":`Car`,"timeline.motion":`Motion`,"timeline.doorbell":`Doorbell`,"timeline.unknown_object":`Unknown object ({value})`,"dashboard.loading":`Loading {title}…`,"dashboard.error":`Could not load this card. Check the integration and connection.`,"dashboard.unavailable":`Data is temporarily unavailable. Check the UniFi Insights integration and connection.`,"dashboard.no_protect":`UniFi Protect is not configured for this integration.`,"dashboard.partial":`Some data is unavailable or needs attention. Available readings are shown below.`,"dashboard.stale":`Updates are delayed. Showing the last available readings.`,"dashboard.no_events":`No recent events are available.`,"card.name":`UniFi Topology`,"card.description":`Interactive network topology of a UniFi site, from UniFi Insights.`,"header.summary":`{devices} devices · {clients} clients`,"state.loading":`Loading network topology…`,"state.no_sources":`No UniFi Insights integration is loaded.`,"state.unconfigured":`Choose a site to show.`,"state.empty":`No devices reported for {site}.`,"state.incompatible":`Card and integration versions don't match. Refresh the browser (clear cache) after updating.`,"state.stale":`Stale`,"state.reconnecting":`Reconnecting…`,"state.online":`Online`,"state.offline":`Offline`,"state.unknown":`Unknown`,"state.connected":`Connected`,"state.disconnected":`Disconnected`,"issue.site_unavailable":`Site data temporarily unavailable; will recover automatically.`,"issue.devices_unavailable":`Device data unavailable; showing last known layout.`,"issue.legacy_uplink_missing":`Uplink details unavailable; device links may be missing.`,"issue.parents_unresolved":`{count} nodes couldn't be placed under a parent.`,"issue.clients_truncated":`Showing {included} of {total} clients (limit {max}).`,"issue.entry_unloaded":`Integration is reloading.`,"issue.unknown":`Topology problem: {code}.`,"error.entry_not_found":`The configured site no longer exists or is disabled.`,"error.site_not_selected":`The configured site no longer exists or is disabled.`,"error.entry_not_loaded":`Integration isn't loaded (retrying).`,"error.unknown":`Could not load the topology ({code}).`,"action.integration":`Integration`,"action.edit":`Edit card`,"view.graph":`Graph`,"view.list":`List`,"toolbar.view":`View`,"toolbar.filters":`Show`,"toolbar.zoom":`Zoom`,"toolbar.options":`Options`,"toolbar.site":`Site`,"zoom.in":`Zoom in`,"zoom.out":`Zoom out`,"zoom.fit":`Fit to view`,"zoom.hint":`Use Ctrl + scroll to zoom`,"kind.wan":`WAN`,"kind.gateway":`Gateway`,"kind.switch":`Switch`,"kind.access_point":`Access point`,"kind.client":`Client`,"kind.other":`Other`,"medium.wired":`Wired`,"medium.wireless":`Wireless`,"medium.unknown":`Unknown`,"group.clients":`{count} clients`,"group.client_one":`1 client`,"group.unconnected":`Unconnected clients`,"group.root":`Clients`,"group.summary":`{wireless} wireless, {offline} offline`,"graph.label":`{site} topology, {devices} devices, {clients} clients`,"graph.roledescription":`network topology`,"node.label":`{kind} {name}, {state}`,"node.clients":`{count} clients`,"node.client_one":`1 client`,"node.uplink":`uplink port {port}`,"node.uplink_speed":`uplink port {port} at {speed}`,"node.wan_of":`internet connection of {device}`,"list.label":`{site} devices and clients`,"list.search":`Search`,"list.no_matches":`No matches`,"detail.close":`Close`,"detail.kind":`Type`,"detail.model":`Model`,"detail.state":`State`,"detail.parent":`Connected to`,"detail.port":`Port`,"detail.speed":`Speed`,"detail.medium":`Link`,"detail.poe":`PoE`,"detail.clients":`Clients`,"detail.clients_value":`{total} ({wired} wired, {wireless} wireless)`,"detail.connection":`Connection`,"detail.vlan":`VLAN`,"detail.network":`Network`,"detail.via_hidden":`Via hidden`,"detail.open_device":`Open device`,"detail.search_members":`Search clients`,"menu.label":`Actions`,"menu.copy_ip":`Copy IP address`,"menu.copy_mac":`Copy MAC address`,"menu.open_entity":`Open entity`,"menu.copied_ip":`IP address copied to the clipboard.`,"menu.copied_mac":`MAC address copied to the clipboard.`,"menu.copy_failed":`Couldn't copy. Select the value below to copy it by hand.`,"announce.updated":`Topology updated.`,"announce.offline":`{count} devices offline.`,"editor.site":`Site`,"editor.site_unavailable":`{site} (unavailable)`,"editor.title":`Title`,"editor.view":`Default view`,"editor.clients":`Clients`,"editor.kinds":`Show node types`,"editor.density":`Density`,"editor.orientation":`Orientation`,"editor.layout":`Layout`,"editor.show_site_selector":`Show site selector`,"editor.show_labels":`Show labels`,"editor.show_wan":`Show internet connections (WAN)`,"editor.max_clients":`Maximum clients`,"clients.collapsed":`Grouped`,"clients.expanded":`Expanded`,"clients.hidden":`Hidden`,"density.auto":`Automatic`,"density.comfortable":`Comfortable`,"density.compact":`Compact`,"orientation.vertical":`Top to bottom`,"orientation.horizontal":`Left to right`,"layout.tree":`Tree`,"layout.tiered":`Tiered by role`},rn={en:nn};function an(e){let t=(e??`en`).toLowerCase(),n=rn[t]??rn[t.split(`-`)[0]??`en`]??{};return(e,t)=>{let r=n[e]??nn[e];if(t)for(let[e,n]of Object.entries(t))r=r.replaceAll(`{${e}}`,String(n));return r}}var on=class extends J{static editorTag=``;static async getConfigElement(){return document.createElement(this.editorTag)}static properties={hass:{attribute:!1},config:{state:!0},snapshot:{state:!0},sources:{state:!0},loading:{state:!0},error:{state:!0}};unsubscribe;bindingKey;syncGeneration=0;failedBindingKey;retryAfterMs=0;retryTimer;constructor(){super(),this.config={type:``},this.snapshot=void 0,this.sources=[],this.loading=!1,this.error=void 0}get headerAccent(){return`var(--primary-color, #03a9f4)`}renderHeaderBadge(){return V}get loadingLabel(){return`Loading`}get includeSiteInSubscribeMessage(){return!0}connectedCallback(){super.connectedCallback(),this.sync()}disconnectedCallback(){super.disconnectedCallback(),this.syncGeneration+=1,this.retryTimer&&=(clearTimeout(this.retryTimer),void 0),this.unsubscribe?.(),this.unsubscribe=void 0}willUpdate(e){let t=e.has(`hass`),n=e.has(`config`),r=e.get(`hass`),i=t&&r?.connection!==this.hass?.connection;(n||i)&&this.sync()}setConfig(e){if(!e||typeof e.type!=`string`)throw Error(`Invalid card config`);this.config={...e}}getCardSize(){return 4}getGridOptions(){return{columns:6,rows:4,min_columns:3,min_rows:3}}getBinding(){return this.config.entry_id&&this.config.site_id?{entry_id:this.config.entry_id,site_id:this.config.site_id}:pe({entry_id:this.config.entry_id,site_id:this.config.site_id,title:this.config.title,view:`graph`,show_site_selector:!1,clients:`collapsed`,kinds:[`gateway`,`switch`,`access_point`,`client`,`other`],density:void 0,orientation:`vertical`,layout:`tree`,show_labels:!0,show_wan:!0,max_clients:500},this.sources)}async sync(){let e=++this.syncGeneration;if(!this.hass)return;this.loading=!0;try{if(this.sources=await this.hass.callWS({type:this.sourceCommand}),e!==this.syncGeneration||!this.isConnected){this.loading=!1;return}this.error=void 0}catch(t){if(e!==this.syncGeneration||!this.isConnected){this.loading=!1;return}this.error=Y(t),this.loading=!1;return}let t=this.getBinding(),n=t?`${t.entry_id}:${t.site_id}`:void 0;if(n===this.bindingKey&&this.unsubscribe||(this.bindingKey=n,n!==this.failedBindingKey&&(this.failedBindingKey=void 0,this.retryAfterMs=0),this.snapshot=void 0,await this.unsubscribe?.(),this.unsubscribe=void 0,!t)){this.loading=!1;return}if(n!==void 0&&n===this.failedBindingKey&&Date.now()<this.retryAfterMs){this.loading=!1,this.retryTimer&&clearTimeout(this.retryTimer),this.retryTimer=setTimeout(()=>{e===this.syncGeneration&&this.isConnected&&this.sync()},Math.max(0,this.retryAfterMs-Date.now()));return}let r={type:this.subscribeCommand,entry_id:t.entry_id};this.includeSiteInSubscribeMessage&&(r.site_id=t.site_id);try{let t=await this.hass.connection.subscribeMessage(t=>{e===this.syncGeneration&&this.isConnected&&((Array.isArray(t.issues)?t.issues:[]).some(e=>e?.code===`entry_unloaded`)?(this.unsubscribe?.().catch(()=>void 0),this.unsubscribe=void 0,this.bindingKey=void 0,this.retryTimer&&clearTimeout(this.retryTimer),this.retryTimer=setTimeout(()=>{e===this.syncGeneration&&this.isConnected&&this.sync()},1e3)):(this.failedBindingKey=void 0,this.retryAfterMs=0,this.error=void 0,this.snapshot=t,this.loading=!1))},r,{resubscribe:!1});if(e!==this.syncGeneration||!this.isConnected){await t(),this.loading=!1;return}this.unsubscribe=t}catch(t){if(e!==this.syncGeneration||!this.isConnected){this.loading=!1;return}this.failedBindingKey=n,this.retryAfterMs=Date.now()+5e3,this.error=Y(t),this.loading=!1,this.retryTimer=setTimeout(()=>{e===this.syncGeneration&&this.isConnected&&this.sync()},5e3)}}openMoreInfo(e){X(this,`hass-more-info`,{entityId:e})}renderStatus(){let e=an(this.hass?.language??this.hass?.locale?.language);if(this.loading)return z`<div
        class="state loading-box"
        role="status"
        aria-live="polite"
      >
        <span class="pulse-dot" aria-hidden="true"></span>
        ${e(`dashboard.loading`,{title:this.defaultTitle})}
      </div>`;if(this.error)return z`<div class="state error" role="alert">
        ${$(g)}
        <div>
          ${e(`dashboard.error`)}
          <span class="state-detail">${this.error.code}</span>
        </div>
      </div>`;let t=this.snapshot?.status;return t===`unavailable`?z`<div class="state error" role="alert">
        ${$(g)}
        ${e(`dashboard.unavailable`)}
      </div>`:t===`no_protect`?z`<div class="state" role="status" aria-live="polite">
        ${e(`dashboard.no_protect`)}
      </div>`:t===`partial`||t===`degraded`||this.snapshot?.freshness===`stale`?z`<div class="state" role="status" aria-live="polite">
        ${$(g)}
        <div>
          ${e(this.snapshot?.freshness===`stale`?`dashboard.stale`:`dashboard.partial`)}
          ${Array.isArray(this.snapshot?.warnings)&&this.snapshot.warnings.length?z`<span class="state-detail"
                >${this.snapshot.warnings.map(e=>String(e).replaceAll(`_`,` `)).join(` · `)}</span
              >`:V}
        </div>
      </div>`:V}render(){let e=!this.loading&&!this.error&&this.snapshot?.status!==`unavailable`&&this.snapshot?.status!==`no_protect`,t=typeof this.snapshot?.site_name==`string`&&this.snapshot.site_name.trim()?this.snapshot.site_name:typeof this.snapshot?.entry_title==`string`&&this.snapshot.entry_title.trim()?this.snapshot.entry_title:void 0;return z`
      <ha-card style=${`--card-accent: ${this.headerAccent}`}>
        <div class="header">
          <div class="header-main">
            <div class="header-icon">${$(this.headerIcon)}</div>
            <div class="header-titles">
              <h2 class="header-title" id="card-title">
                ${this.config.title??this.defaultTitle}
              </h2>
              ${t?z`<div class="header-subtitle">${t}</div>`:V}
            </div>
          </div>
          <div class="header-actions">
            ${e?this.renderHeaderBadge():V}
          </div>
        </div>
        <div
          class="content"
          tabindex="0"
          role="region"
          aria-labelledby="card-title"
        >
          ${this.renderStatus()}
          ${e?this.renderContent():V}
        </div>
      </ha-card>
    `}static styles=tn};function sn(e){h(e.tag,e.card),h(e.editorTag,e.editor),window.customCards??=[],window.customCards.some(t=>t.type===e.tag)||window.customCards.push({type:e.tag,name:e.name,description:e.description,preview:!0,documentationURL:`https://github.com/tinkerologie/ha-unifi-insights#dashboard-cards`})}export{ke as $,Pt as A,ee as At,g as B,t as Bt,$ as C,m as Ct,X as D,de as Dt,Z as E,me as Et,z as F,c as Ft,xe as G,ve as H,re as Ht,Dt as I,a as It,we as J,Se as K,vt as L,te as Lt,J as M,o as Mt,V as N,i as Nt,Nt as O,u as Ot,B as P,s as Pt,_ as Q,S as R,e as Rt,Lt as S,se as St,Ft as T,fe as Tt,ye as U,_e as V,n as Vt,be as W,Ee as X,Te as Y,De as Z,Zt as _,ce as _t,Bt as a,Ie as at,$t as b,f as bt,Ut as c,ze as ct,Rt as d,Ve as dt,Ae as et,zt as f,Ue as ft,Jt as g,oe as gt,Yt as h,ie as ht,en as i,Fe as it,Y as j,l as jt,jt as k,d as kt,Wt as l,v as lt,qt as m,h as mt,on as n,Me as nt,Vt as o,Le as ot,Kt as p,Ge as pt,Ce as q,an as r,Ne as rt,Ht as s,Re as st,sn as t,je as tt,Gt as u,Be as ut,Xt as v,le as vt,Q as w,pe as wt,It as x,ue as xt,Qt as y,ae as yt,ge as z,r as zt};