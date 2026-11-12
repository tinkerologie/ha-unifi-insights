# UniFi Insights integration for Home Assistant

Monitor and control UniFi devices from Home Assistant. UniFi Insights adds
entities, actions, and six dashboard cards.

<!-- BADGES:START -->

[![Marketplace and HACS](https://img.shields.io/badge/Marketplace%20%7C%20HACS-Default-orange.svg)](https://www.hacs.xyz/docs/marketplace/)
[![Last commit on main](https://img.shields.io/github/last-commit/tinkerologie/ha-unifi-insights)](https://github.com/tinkerologie/ha-unifi-insights/commits/main)
[![Latest release](https://img.shields.io/github/v/release/tinkerologie/ha-unifi-insights)](https://github.com/tinkerologie/ha-unifi-insights/releases)
[![Test coverage](https://codecov.io/gh/tinkerologie/ha-unifi-insights/graph/badge.svg)](https://codecov.io/gh/tinkerologie/ha-unifi-insights)
[![Apache License 2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

<!-- BADGES:END -->

## Contents

- **Setup** · [Requirements](#requirements) · [Installation](#installation) · [Configuration](#configuration)
- **Products** · [Overview](#overview) · [Supported UniFi products](#supported-unifi-products)
- **Home Assistant** · [Automation blueprints](#automation-blueprints) · [Dashboard cards](#dashboard-cards)
- **Help** · [Troubleshooting](#troubleshooting) · [Contributing](#contributing) · [License](#license)

## Overview

One console entry can connect to Network, Protect, and InnerSpace. A local
entry can also connect to Access with an Access token. A cloud API key can add
Mobility and Site Manager data. Internet service providers can add a Carrier
Fabric entry.

The dashboard cards show topology, site health, internet activity, performance,
Protect status, and events. The integration removes API keys and personal
data from diagnostics.

The integration uses the official [UniFi APIs](https://developer.ui.com/).
It also uses the Classic API for some Network functions. Examples include
Wi-Fi QR codes, VPN controls, some Network rule switches, and internet
traffic history. Both APIs use the same key.

## Supported UniFi products

| Product                                                 | Connection            | Highlights                                                                                                                          |
| ------------------------------------------------------- | --------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| [UniFi Network](#unifi-network)                         | Local or Remote       | Device, port, power, WAN, and client monitoring. Network controls, guest access, and vouchers.                                      |
| [UniFi Protect](#unifi-protect)                         | Local or Remote       | Cameras, smart detection and doorbell events, lights, chimes, sirens, PTZ, sensors, NVR storage, Alarm Manager, alarm hub status    |
| [UniFi Access](#unifi-access)                           | Local, optional token | Door locks and unlock buttons, Gate Hub covers, emergency controls, doorbell and signed access-result events, administrator actions |
| [UniFi InnerSpace](#unifi-innerspace)                   | Local or Remote       | Floor-plan placement of your UniFi devices                                                                                          |
| [UniFi Mobility](#unifi-mobility)                       | Remote                | Mobile router status, cellular data use and GPS location                                                                            |
| [UniFi Site Manager](#unifi-site-manager)               | Remote                | Account-wide host, site, ISP and SD-WAN data in diagnostics                                                                         |
| [UniFi Carrier Fabric (ISP)](#unifi-carrier-fabric-isp) | Carrier Fabric (ISP)  | Subscriber and service plan counts, with optional suspend and resume                                                                |

The integration creates entities for the applications that the console runs.
The integration also works if the console runs only Network, Protect, or InnerSpace.

## Requirements

- Home Assistant 2026.9.0 or later.
- For Local or Remote, use a UniFi OS console with Network, Protect, or
  InnerSpace. The console must provide the UniFi Integration API. A
  self-hosted Network application does not provide this API for a Local
  connection. Examples include installations on Linux, Docker, and Proxmox.
- For Remote, use a cloud API key from [UniFi Site Manager](https://unifi.ui.com).
  Mobility and Site Manager use this key.
- For Carrier Fabric, use an ISP API key.
- Keep UniFi OS and its applications up to date. Thread and keypad fob entities
  need Protect 7.3.70 or later. Alarm hub tamper status also needs this version.
  Sensor mounting orientation, anti-flicker video mode, and external alarm manager
  panel gating require Protect 7.4.96 or later.

## Installation

### Marketplace (Home Assistant 2026.11 and newer, recommended)

[![Open your Home Assistant instance and open a repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=tinkerologie&repository=ha-unifi-insights&category=integration)

1. Open **Settings** → **Marketplace**.
2. Search for **UniFi Insights**.
3. Install the integration.
4. If Home Assistant shows a repair that requests a restart, restart Home Assistant.

The Marketplace updates existing HACS installations of UniFi Insights after
you upgrade to Home Assistant 2026.11.

### HACS (Home Assistant 2026.9 and 2026.10)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=tinkerologie&repository=ha-unifi-insights&category=integration)

1. Open HACS.
2. Search for **UniFi Insights**.
3. Select **Download**.
4. Restart Home Assistant.

### Manual installation

1. Download `unifi_insights.zip` from the latest [release](https://github.com/tinkerologie/ha-unifi-insights/releases).
2. Create the `config/custom_components/unifi_insights/` folder.
3. Extract the ZIP file into that folder.
4. Make sure that `manifest.json` is directly in the folder.
5. Restart Home Assistant.

Releases with a ZIP file also include `SHA256SUMS` and a signed build
provenance attestation. The Marketplace and HACS use the same ZIP file.
The download count applies to the ZIP file for the latest release.

Put `SHA256SUMS` in the same folder as the ZIP file.
Use these commands to check the ZIP file.

```bash
gh attestation verify unifi_insights.zip -R tinkerologie/ha-unifi-insights
sha256sum -c SHA256SUMS
```

On macOS, use `shasum -a 256 -c SHA256SUMS` for the checksum check.

## Configuration

### Choose a connection type

| Connection type           | Use it for                                                        | API key                                                        |
| ------------------------- | ----------------------------------------------------------------- | -------------------------------------------------------------- |
| Local (Direct connection) | A console on your network: Network, Protect and InnerSpace        | Created on the console                                         |
| Remote (UniFi Cloud)      | Any console on your UniFi account, plus Mobility and Site Manager | Created in [UniFi Site Manager](https://unifi.ui.com)          |
| Carrier Fabric (ISP)      | An ISP's Carrier Fabric subscribers and service plans             | ISP API key, see [Carrier Fabric setup](#carrier-fabric-setup) |

You can add an entry for each console.

### Add the integration

1. In Home Assistant, open **Settings** → **Devices & services**.
2. Select **Add integration**.
3. Search for **UniFi Insights**.
4. Select the connection type.

#### Local connection

1. On the console, open **Settings** → **Control Plane** → **API Keys**.
2. Create an API key.
3. Enter the console URL in Home Assistant. An example is `https://192.168.1.1`.
4. Enter the API key.
5. If the console uses its default self-signed certificate, leave **Verify SSL Certificate** off.

#### Remote connection

1. In [UniFi Site Manager](https://unifi.ui.com), open **Settings** → **API Keys**.
2. Select **Create New API Key**.
3. Copy the key.
4. Enter the key in Home Assistant.
5. Select the console from the list.

A console in the list does not prove that the key has Cloud Connector access.
Personal Site Manager API keys work only with consoles that the key's account
owns. Use a key from the console owner, an organization API key, or a
[Local connection](#local-connection) with a key created on the console.

For Carrier Fabric, enter the ISP API key. See
[Carrier Fabric setup](#carrier-fabric-setup).

### Options

To change these settings, open **Settings** → **Devices & services** →
**UniFi Insights** → **Configure**.

| Option                | Default | Description                                                                                                                                                                                             |
| --------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Track WiFi Clients    | Off     | Creates a device tracker for each wireless client. This can add many entities on busy networks.                                                                                                         |
| Track Wired Clients   | Off     | Creates a device tracker for each wired client.                                                                                                                                                         |
| Enable Client Control | On      | Creates client allow/block switches, reconnect buttons and disabled-by-default Wake-on-LAN buttons for wired clients. Turn off for read-only monitoring.                                                |
| Sites                 | All     | Appears if the console lists sites and either condition applies. It lists more than one site, or the entry has a saved site filter. Select the sites to poll. Leave this field empty to poll all sites. |

The integration enables new client trackers. This also applies to Home
Assistant 2026.11 and later, which disable network-scanner trackers by default.
A tracker that you disable stays disabled.

If you change a client name in UniFi, the integration updates the tracker name
on the next poll. If the tracker has its own Home Assistant device, the
integration also updates that device name. It keeps a name that you set in
Home Assistant. If the tracker belongs to an access point or switch device,
the integration changes only the tracker name.

Carrier Fabric entries have their own options. See [Carrier Fabric setup](#carrier-fabric-setup).

## UniFi Network

- **Device health.** Sensors show the status, CPU, memory, uptime, temperature,
  and uplink rates of gateways, switches, and access points.
- **Ports.** Sensors show PoE power, link speed, traffic counters, traffic
  rates, and SFP module details.
- **Power.** Switches control PDU outlets. Sensors show power, voltage,
  current, and power factor. Devices can also report AC consumption and budget.
- **Storage.** Sensors show used space, total space, and percent used for each
  filesystem that the console reports. Examples include eMMC and backup storage
  on a UDM Pro SE. The **Storage Nearly Full** sensor turns on if a persistent
  filesystem is at least 90% full. Per-drive SMART, temperature, RAID, and
  Drive/UNAS data need the applicable API fields. Protect NVR storage needs
  fields that the official `/v1/nvrs` response can omit.
- **Internet and WAN.** Sensors show the gateway and WAN connection status.
  They also show WAN IP addresses and site-to-site VPN status. The integration
  reports download and upload totals for the last hour, day, week, and month.
- **Clients.** Sensors show client counts per site, device, and Wi-Fi network.
  Optional features include presence tracking, allow/block switches,
  reconnect buttons, and Wake-on-LAN for wired clients.
- **Controls.** The integration has switches for Wi-Fi networks and Network rules.
  Buttons restart devices. Wi-Fi QR images help you connect to a network.
- **Firmware.** Update entities show the firmware version and indicate if a
  newer version is available. The integration does not install firmware.
- **Guest access.** Actions give access to guests and create or delete hotspot
  vouchers.

Use the UniFi app to install firmware.

### Network entities

| Entity                                                       | Type           | Notes                                                                                  |
| ------------------------------------------------------------ | -------------- | -------------------------------------------------------------------------------------- |
| Status                                                       | Binary sensor  | Device online state                                                                    |
| CPU Usage, Memory Usage                                      | Sensor         | Per device (%)                                                                         |
| Uplink Transmit Rate, Uplink Receive Rate                    | Sensor         | Per device (Mbit/s)                                                                    |
| Uptime, Temperature, Firmware Version                        | Sensor         | Per device, disabled by default                                                        |
| Wired Clients, Wireless Clients                              | Sensor         | Per switch or access point                                                             |
| Total Clients, Wired Clients, Wireless Clients               | Sensor         | Per site                                                                               |
| Connected clients                                            | Sensor         | Per Wi-Fi network, on its `WiFi: <SSID>` device                                        |
| Internet Download, Internet Upload                           | Sensor         | Per site, for the last hour, 24 hours, 7 days and 30 days                              |
| Total PoE Power, _port_ PoE Power                            | Sensor         | Per switch and per port (W)                                                            |
| _Port_ TX, RX, TX Rate, RX Rate, Speed                       | Sensor         | Per port: traffic (bytes), rates (Mbit/s) and link speed (Mbps, disabled by default)   |
| _Port_ SFP Module, SFP Vendor, SFP Type, SFP Serial          | Sensor         | Per SFP port, disabled by default                                                      |
| _Port_ SFP Module                                            | Binary sensor  | On when an SFP module is inserted                                                      |
| Wi-Fi _band_ TX retries                                      | Sensor         | Per access point radio (%), diagnostic                                                 |
| Wi-Fi _band_ TX power mode, Standard, Channel, Channel width | Sensor         | Per access point radio, diagnostic, disabled by default                                |
| AC Power Consumption, AC Power Budget                        | Sensor         | Devices that report power                                                              |
| Storage _mount_ Used Percentage                              | Sensor         | Consoles that report storage, per filesystem (%). /tmp-style mounts start disabled     |
| Storage _mount_ Used, Storage _mount_ Total                  | Sensor         | Per filesystem (GiB), disabled by default                                              |
| Storage Nearly Full                                          | Binary sensor  | On when a persistent filesystem is 90% full or more                                    |
| _Outlet name_ Power, Voltage, Current, Power Factor          | Sensor         | Per PDU outlet                                                                         |
| WAN Status                                                   | Binary sensor  | Gateway online state (for the link state, use the WAN connection)                      |
| _WAN name_ Connection                                        | Binary sensor  | Internet connection state of each WAN (DHCP, static or PPPoE)                          |
| _Tunnel name_ Site-to-Site VPN                               | Binary sensor  | Connection state of each site-to-site VPN tunnel                                       |
| WAN IP Address, WAN Uptime                                   | Sensor         | Gateway, disabled by default                                                           |
| LED                                                          | Light          | Turn a Network device status LED on or off. The site default shows as unknown.         |
| WiFi _network name_                                          | Switch         | Turn a Wi-Fi network on or off                                                         |
| _Policy name_                                                | Switch         | Turn a user-defined firewall policy on or off                                          |
| _Route name_                                                 | Switch         | Turn a policy-based route on or off                                                    |
| _VPN client name_                                            | Switch         | Turn a VPN client (WireGuard, OpenVPN, Privado VPN) on or off                          |
| Port forward _rule name_                                     | Switch         | Turn a port forwarding rule on or off                                                  |
| Traffic rule _rule description_                              | Switch         | Turn a traffic rule on or off                                                          |
| NAT rule _rule description_                                  | Switch         | Turn a custom NAT rule on or off (console-managed rules get no switch)                 |
| Static route _route name_                                    | Switch         | Turn a static route on or off, disabled by default                                     |
| Network rule changes                                         | Event          | A firewall policy, route, static route, port forward, traffic rule or NAT rule changed |
| _Outlet name_                                                | Switch         | Turn a PDU outlet on or off                                                            |
| _Outlet name_ Power Cycle                                    | Switch         | Automatic modem power cycling for an outlet, disabled by default                       |
| _Client name_ Allow                                          | Switch         | Block or allow a client (needs **Enable Client Control**)                              |
| _Client name_ Reconnect                                      | Button         | Reconnect a client (needs **Enable Client Control**)                                   |
| Wake _Client name_                                           | Button         | Send a Wake-on-LAN packet, disabled by default (needs **Enable Client Control**)       |
| Restart                                                      | Button         | Restart a device                                                                       |
| _Port_ PoE Power Cycle                                       | Button         | Cut and restore power on a port with PoE enabled, disabled by default                  |
| WiFi QR code                                                 | Image          | Scan to join a Wi-Fi network                                                           |
| Client tracker                                               | Device tracker | Presence of each client (needs **Track WiFi Clients** or **Track Wired Clients**)      |
| Firmware                                                     | Update         | Installed and available firmware version (no installation)                             |
| Voucher Duration                                             | Number         | Duration in minutes for generated vouchers (1–1,000,000, default 480)                  |
| Voucher Guest Limit                                          | Number         | Authorized guest limit (0–1000, default 1; 0 = not set; UniFi applies its own default) |
| Voucher Download Limit                                       | Number         | Download rate limit in Mbit/s (0–100, step 0.1, ×1000 to kbps; 0 = not set)            |
| Voucher Upload Limit                                         | Number         | Upload rate limit in Mbit/s (0–100, step 0.1, ×1000 to kbps; 0 = not set)              |
| Voucher Data Limit                                           | Number         | Data transfer quota in MB (0–1,048,576; 0 = not set; UniFi applies its own default)    |
| Generate guest voucher                                       | Button         | Generate a new hotspot voucher using current input settings                            |
| Active Vouchers                                              | Sensor         | Count of active, unexpired vouchers for the site                                       |
| Latest Voucher Code                                          | Sensor         | Secret activation code of the most recent generated voucher                            |
| Latest Voucher Expiration                                    | Sensor         | Expiration timestamp of the most recent generated voucher                              |
| Latest voucher QR code                                       | Image          | Captive portal activation code QR code (not for joining Wi-Fi)                         |

NAT rule and static route switches use the console's private API.
Testing used one UniFi gateway through the cloud connector. Other
consoles or Network versions can behave differently. If the console
rejects a change, the switch reports an error and the rule stays the same.

The console can refuse requests for a few seconds while it applies a
change. This occurred in testing after static route changes. The rule
switches for that site stay unavailable until the next read, usually
after 15 seconds.

### Access point radios

Each access point radio has a sensor for its band. Examples are
**Wi-Fi 2.4 GHz TX retries** and **Wi-Fi 5 GHz TX retries**. The other bands
are 900 MHz, 6 GHz, and 60 GHz. The sensor shows the retry rate in percent.
The integration reads this rate with the device statistics every 30 seconds.

The integration reads TX power mode, Wi-Fi standard, channel, and channel
width from access point details at most once every 5 minutes. These
diagnostic sensors are off by default. If the request fails, they keep their
last values and become unavailable. The integration retries at the next
5-minute check. The retry-rate sensor uses a separate request.

### Network rule change events

Each site has one **Network rule changes** event entity. It reports `added`,
`modified`, or `deleted`. It monitors firewall policies, policy-based
routes, port forwards, traffic rules, NAT rules, and static routes.
Changes from Home Assistant switches also count.

The entity does not report rule order changes, system-defined firewall
policies, console-managed NAT rules, or VPN clients.

The integration checks for changes every 5 minutes. If a rule changes and
returns to its previous state between polls, the integration reports no change.

The first poll after a start or reload records the current rules. The
integration does not report changes from the time between a stop or reload
and that first poll.

If the console is unavailable, the entity reports no events. It reports
changes after the next successful poll.

If the last rule of a type disappears, the integration waits for a second
empty result. It reports the deletion after that poll.

If the integration cannot parse a rule, it reports `deleted`. It also
reports `deleted` if the console takes control of a rule. If the rule
returns to user control, the integration reports `added`. The integration
keeps up to 100 changes per site from each poll.

| Property         | Description                                                                                                        |
| ---------------- | ------------------------------------------------------------------------------------------------------------------ |
| `event_type`     | `added`, `modified` or `deleted`                                                                                   |
| `site_id`        | Site identifier                                                                                                    |
| `rule_type`      | Family of rule (`firewall_rule`, `policy_based_route`, `port_forward`, `traffic_rule`, `nat_rule`, `static_route`) |
| `rule_id`        | Unique ID of the rule                                                                                              |
| `rule_name`      | Name or description of the rule                                                                                    |
| `enabled`        | Whether the rule is enabled (`true`, `false`, or `null`)                                                           |
| `changed_fields` | List of field names that changed                                                                                   |

`rule_name` gives the name or description from the console.
`changed_fields` lists field names. The event omits other field values,
including addresses, ports, and domains.

Use a state trigger that excludes `unavailable`. Home Assistant can send
the last event again when the entity returns after a failure or restart.

```yaml
triggers:
  - trigger: state
    entity_id: event.<site>_network_rule_changes
    not_from: unavailable
    not_to: unavailable
mode: queued
```

Keep `unknown` out of `not_from`. The entity starts in `unknown`.
The trigger needs that state to report the first event.

### Hotspot voucher management

The integration has entities to configure, generate, and monitor vouchers.

- **Voucher Duration** gives the validity in minutes (1 to 1,000,000).
  The default is 480 minutes (8 hours). The integration sends minutes to UniFi.
- **Voucher Guest Limit** gives the number of distinct guests (0 to 1000).
  The default is 1. If the value is 0, UniFi uses its default.
- **Voucher Download Limit / Upload Limit** gives the speed in Mbit/s
  (0 to 100 in steps of 0.1). The integration sends the value in kbps.
  If the value is 0, UniFi uses its default.
- **Voucher Data Limit** gives the amount of data in MB (0 to 1,048,576).
  The default is 0. If the value is 0, UniFi uses its default.
- Home Assistant restores these settings with `RestoreNumber` after a restart.
  A change to a setting sends no API request.
- **Generate guest voucher** creates one voucher named "Home Assistant" with
  the current settings.
- The Config coordinator polls the vouchers every 5 minutes. A create or
  delete action requests a site refresh with `async_refresh_vouchers`.
- **Active Vouchers** counts unexpired vouchers that can still admit a guest.
- **Latest Voucher Code** shows the last code that Home Assistant generated.
  Home Assistant stores it in memory until a restart or the next voucher.
- **Latest Voucher Expiration** shows the expiration time. The time stays
  unknown until the first guest receives access.
- If someone deletes a voucher in UniFi, Home Assistant keeps its last record
  until a restart or a new voucher. Its QR image and entity become unavailable
  after the voucher expires.
- **Latest voucher QR code** contains the activation code for the captive portal.
  Use the Wi-Fi QR code entity for Wi-Fi credentials. The voucher image
  becomes unavailable at expiration.

#### Voucher codes in Recorder

> **Warning.** To keep new voucher codes out of Recorder, exclude the sensor
> in `configuration.yaml`. Voucher codes give network access. By default,
> Recorder stores the active code from **Latest Voucher Code**.

```yaml
recorder:
  exclude:
    entities:
      - sensor.your_site_latest_voucher_code
```

You can also disable the code sensor in Home Assistant. Recorder keeps the
codes that it already stored.

### Wake-on-LAN buttons

Home Assistant sends the wake packet with its Wake on LAN integration.
UniFi Insights loads that integration. The **Wake** buttons are on the site
gateway, or on the site device if there is no gateway.

- Each wired client gets a **Wake _Client name_** button. The button is off
  by default. Home Assistant restores enabled buttons after a restart.
  This also applies if a client is offline or history is unavailable.
- During startup, the integration can create buttons for named wired clients
  that UniFi reported in the last 30 days. **Enable Client Control** controls
  these buttons and other client controls. It is not an access permission.
- Connect Home Assistant and the device to the same VLAN. UniFi gateways
  usually do not forward wake packets between VLANs. Home Assistant uses the
  client's broadcast address if the console supplies it. Otherwise, it sends
  the packet to 255.255.255.255.
- Enable Wake-on-LAN in the device BIOS/UEFI. Enable it in the operating
  system or network adapter settings. The button cannot confirm wake-up.
- Use host networking for Home Assistant. Home Assistant OS uses it.
  A Docker bridge can prevent the wake packet from reaching the network.
- A console outage makes Wake and reconnect buttons unavailable. If the
  console is available, a Wake button stays available for an offline client.
- Enable debug logging for `homeassistant.components.wake_on_lan` to see
  the packets. Home Assistant also sends the packets for remote entries.

### Network actions

| Action                                | Description                                                         |
| ------------------------------------- | ------------------------------------------------------------------- |
| `unifi_insights.restart_device`       | Restart a network device                                            |
| `unifi_insights.authorize_guest`      | Authorize a guest client on a hotspot network                       |
| `unifi_insights.reconnect_client`     | Reconnect a wireless client to Wi-Fi                                |
| `unifi_insights.remove_clients`       | Remove short-lived unnamed clients from sites                       |
| `unifi_insights.generate_voucher`     | Create one or more hotspot vouchers                                 |
| `unifi_insights.delete_voucher`       | Delete a hotspot voucher                                            |
| `unifi_insights.power_cycle_port`     | Power cycle a switch port with PoE enabled. `port_idx` starts at 1. |
| `unifi_insights.export_network_rules` | Export the network rules of a console                               |

```yaml
# Restart a network device
action: unifi_insights.restart_device
data:
  site_id: "your-site-id"
  device_id: "device-id"
```

```yaml
# Authorize a guest client
action: unifi_insights.authorize_guest
data:
  site_id: "your-site-id"
  client_id: "client-id"
```

```yaml
# Create a hotspot voucher
action: unifi_insights.generate_voucher
data:
  site_id: "your-site-id"
  count: 1
  duration_minutes: 480
```

```yaml
# Power cycle the device on PoE port 5 of a switch
action: unifi_insights.power_cycle_port
data:
  site_id: "your-site-id"
  device_id: "device-id"
  port_idx: 5
```

```yaml
# Export the network rules of a console (admin only; returns data, writes no file)
action: unifi_insights.export_network_rules
data:
  redact: true
response_variable: rules
```

The export gives a snapshot of six rule types. These are firewall policies,
policy-based routes, port forwards, traffic rules, NAT rules, and static routes.
Use the export to compare rules or recover them manually. The export omits
networks, zones, matching lists, groups, and VPN definitions. There is no
import action.

- **Status.** The action reads each rule type from the console.
  Only `ok` indicates a complete result. `failed` indicates an unreadable,
  incomplete, or invalid result. It returns no rules of that type.
  `unsupported` indicates a client error or a missing verified classic site name.
  Five rule types need that name. Firewall policies do not.
- **Redacted output.** The default is `redact: true`. It keeps rule IDs,
  enabled state, type, action, protocol, order, and the logging flag.
  It also keeps port settings, WAN interface names, and route and policy settings.
  The export replaces sensitive fields and unrecognized values with `**REDACTED**`.
  It removes site names, console title, and entry ID.
- Site IDs use labels such as `site-1` and `site-2`. The label depends on
  the site's sorted position in this export. A change to the selected sites
  can change the label. Redacted output still contains rule IDs and settings.
- **Unredacted output.** `redact: false` returns the console's rule fields,
  including site IDs, names, and addresses. Keep the output private.
- **Stable order.** `generated_at` changes on every call. The action keeps
  sites and rules in the same order when their data does not change.

`authorize_guest` ignores `duration_minutes` and the limit fields.
It records a warning. The guest network uses the time and limits from UniFi.

## UniFi Protect

- **Cameras.** Cameras provide live view, snapshots, and RTSPS streams.
  The status light, HDR, and video mode have controls. The **Medium resolution**
  and **Low resolution** camera entities are off by default.
  Each uses the same snapshots as the primary entity and streams its own
  channel. If Protect cannot supply that channel, the entity has no stream.
  The existing entity keeps its high channel, name, and IDs.
- **Detection.** Binary sensors show motion, people, vehicles, animals,
  and packages. Event entities report smart detections and doorbell rings.
- **Camera digital inputs.** The first `cameraDigitalInputChanged` event creates
  an entity for that input. Home Assistant restores it after a restart.
  It reports `circuit_open` or `circuit_closed`. With normally closed
  wiring, an open circuit can mean that the input is idle. This feature
  needs Protect 7.2.105 or later.
- **Lights.** Controls set power, brightness, and light mode.
- **Chimes.** Controls set volume, repeat count, ringtone, and doorbell pairing.
- **Sirens.** Controls set volume and sound the siren for 5, 10, 20, or 30 seconds.
- **PTZ.** Actions move to a preset or control a patrol.
- **Sensors.** Sensors show temperature, humidity, light, battery, motion,
  door or window position, tamper, leaks, and incorrect mounting orientation
  (Protect 7.4.96+). Protect sensors reflect tamper status from device state;
  real-hardware event payload behaviour for sensor tamper remains unconfirmed.
- **NVR.** Storage entities show used space, total space, available space,
  and percent used when Protect supplies the fields. Health and capacity
  stay unavailable without data. The integration sends no extra requests
  for these fields. The official `/v1/nvrs` response can omit them.
- **NVR alerts.** **Storage degraded** needs health data. **Storage nearly full**
  uses the displayed percentage at 90% or more. Per-drive SMART, temperature,
  RAID, and Drive/UNAS monitoring require the applicable API fields.
- **Alarm Manager & External Profiles.** The local panel arms away or disarms
  the alarm if the NVR reports an arm mode. When an NVR enables
  `featureFlags.useExternalAlarmManager` (Protect 7.4.96+), the local NVR alarm
  panel is hidden or made unavailable, and individual `alarm_control_panel`
  entities are created for each external arm profile (supporting arm away and
  disarm; profile CRUD and PIN management stay in UniFi OS).
- **Speakers.** Protect speakers provide a **Stop siren** button to stop an
  active siren (Protect 7.4.96+).
- **Security devices.** The integration shows alarm hub tamper, Thread
  network status, and keypad fob settings. Thread and keypad fob data
  need Protect 7.3.70 or later. These entities are read-only.
- **Live views.** Actions create a live view or select one on a viewer.

### Protect entities

| Entity                                                                                                               | Type                  | Notes                                                                                                       |
| -------------------------------------------------------------------------------------------------------------------- | --------------------- | ----------------------------------------------------------------------------------------------------------- |
| Camera                                                                                                               | Camera                | Live view, snapshots, RTSPS stream; Medium/Low are disabled by default                                      |
| Motion Detection, Person Detection, Vehicle Detection, Animal Detection, Package Detection, Microphone               | Binary sensor         | Cameras (Microphone is read-only; skipped if the camera reports no mic)                                     |
| Ring                                                                                                                 | Binary sensor         | Doorbells                                                                                                   |
| Doorbell, Smart Detection, Door/Window, Digital input                                                                | Event                 | Doorbell rings, smart detections, sensor open/close and digital input events                                |
| Status Light, High FPS Mode                                                                                          | Switch                | Camera settings                                                                                             |
| HDR Mode, Video Mode, PTZ Preset                                                                                     | Select                | Camera settings                                                                                             |
| Microphone Volume                                                                                                    | Number                | Camera microphone volume                                                                                    |
| Start PTZ Patrol, Stop PTZ Patrol                                                                                    | Button                | PTZ cameras                                                                                                 |
| Floodlight                                                                                                           | Light                 | On/off and brightness                                                                                       |
| Brightness Level                                                                                                     | Number                | Floodlight brightness level                                                                                 |
| Play                                                                                                                 | Button                | Raises an unsupported-operation error without a request                                                     |
| Chime Volume, Repeat Times                                                                                           | Number                | Chime settings                                                                                              |
| Ringtone                                                                                                             | Select                | Protect ringtone IDs already used by this console's chimes                                                  |
| Liveview                                                                                                             | Select                | Live view shown on a viewer                                                                                 |
| Temperature, Humidity, Light, Battery                                                                                | Sensor                | Protect sensors (°C, %, lux, %)                                                                             |
| Motion Detection, Door/Window Status, Tamper Detection, Leak Detection, External Leak Detection, Mounted Incorrectly | Binary sensor         | Protect sensors                                                                                             |
| Storage Used, Storage Total, Storage Available, Storage Used Percentage                                              | Sensor                | NVR storage (GB, %), when the console reports it                                                            |
| Tamper Detection                                                                                                     | Binary sensor         | Alarm hubs, with the time and user name of the last tamper event                                            |
| Thread Network                                                                                                       | Binary sensor         | On when a gateway's Thread network reports a problem                                                        |
| Thread Role, Thread Joined Devices                                                                                   | Sensor                | Thread gateways, diagnostic                                                                                 |
| Keypad Beep, Keypad Beep Volume, Arm Control                                                                         | Binary sensor, sensor | Keypad fobs, diagnostic, disabled by default                                                                |
| Siren (named after the device)                                                                                       | Siren                 | Protect sirens: sound for 5, 10, 20 or 30 seconds, with volume                                              |
| Stop siren                                                                                                           | Button                | Protect speakers: stop an active siren (Protect 7.4.96+)                                                    |
| Alarm                                                                                                                | Alarm control panel   | NVRs that report an arm mode: arm away and disarm with the selected arm profile                             |
| External arm profile                                                                                                 | Alarm control panel   | Protect external arm profiles when external alarm manager is enabled (profile management stays in UniFi OS) |

> **Alarm panel and the UniFi global alarm manager.** Protect's `armMode`
> is separate from the global alarm manager. While the global alarm manager
> is on, the panel is absent or unavailable. When the NVR reports an arm
> mode, the integration uses a read-only request to find the global state.
> The normal check interval is about 10 minutes. A reload checks immediately.
> If Protect gives no answer, the integration retries after 5 minutes.
> Three consecutive failures cause a warning. If the global alarm manager
> causes an arm or disarm failure, Home Assistant reports the cause.

### Protect actions

The camera **Video Mode** select shows the labels below. Its stored select keys and the values accepted by `unifi_insights.set_video_mode` are listed below. The `video_mode` attribute keeps the raw API value.

| Label                           | Select state / `select.select_option` | `unifi_insights.set_video_mode` value |
| ------------------------------- | ------------------------------------- | ------------------------------------- |
| Normal                          | `default`                             | `default`                             |
| High frame rate                 | `high_fps`                            | `highFps`                             |
| Fast motion                     | `sport`                               | `sport`                               |
| Low light                       | `slow_shutter`                        | `slowShutter`                         |
| Anti-flicker                    | `anti_flicker`                        | `antiFlicker`                         |
| License plates (reflective)     | `lpr_reflex`                          | `lprReflex`                           |
| License plates (non-reflective) | `lpr_none_reflex`                     | `lprNoneReflex`                       |

**Migration.** Use the select keys above in state comparisons. Use the
same keys in `select.select_option` calls. This applies to `highFps`,
`slowShutter`, `lprReflex`, and `lprNoneReflex`. Keep the existing entity
IDs and `unifi_insights.set_video_mode` values. Recorder keeps the old
values in its history.

For example, a native select action now uses the normalized key:

```yaml
action: select.select_option
target:
  entity_id: select.my_camera_video_mode
data:
  option: lpr_none_reflex
```

A `unifi_insights.set_video_mode` action continues to use `mode: lprNoneReflex`.

| Action                                      | Description                                                                                                           |
| ------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `unifi_insights.set_hdr_mode`               | Set a camera's HDR mode (`auto`, `on`, `off`)                                                                         |
| `unifi_insights.set_video_mode`             | Set a camera's video mode (`default`, `highFps`, `sport`, `slowShutter`, `antiFlicker`, `lprReflex`, `lprNoneReflex`) |
| `unifi_insights.set_mic_volume`             | Set a camera's microphone volume (1–100)                                                                              |
| `unifi_insights.ptz_move`                   | Move a PTZ camera to a preset (0–15)                                                                                  |
| `unifi_insights.ptz_patrol`                 | Start or stop a PTZ patrol                                                                                            |
| `unifi_insights.set_light_mode`             | Set a light's mode (`always`, `motion`, `off`)                                                                        |
| `unifi_insights.set_light_level`            | Set a light's brightness (0–100)                                                                                      |
| `unifi_insights.play_chime_ringtone`        | Raises an unsupported-operation error without a request                                                               |
| `unifi_insights.set_chime_volume`           | Set a chime's volume (0–100)                                                                                          |
| `unifi_insights.set_chime_ringtone`         | Set an opaque Protect ringtone ID for every paired doorbell                                                           |
| `unifi_insights.set_chime_repeat_times`     | Set how many times a chime repeats (1–10)                                                                             |
| `unifi_insights.set_chime_paired_doorbells` | Choose which doorbells ring a chime (replaces its current pairing)                                                    |
| `unifi_insights.trigger_alarm`              | Trigger an Alarm Manager webhook alarm                                                                                |
| `unifi_insights.create_liveview`            | Create a live view                                                                                                    |
| `unifi_insights.set_liveview`               | Set the live view shown on a viewer                                                                                   |

```yaml
# Move a PTZ camera to a preset position
action: unifi_insights.ptz_move
data:
  camera_id: "camera-id"
  preset: 0 # 0–15
```

```yaml
# Start or stop a PTZ patrol
action: unifi_insights.ptz_patrol
data:
  camera_id: "camera-id"
  action: "start" # start, stop
  slot: 0 # 0–15
```

```yaml
# Set a Protect light's mode
action: unifi_insights.set_light_mode
data:
  light_id: "light-id"
  mode: "motion" # always, motion, off
```

```yaml
# Set the same ringtone for every doorbell paired to a chime
action: unifi_insights.set_chime_ringtone
data:
  chime_id: "chime-id"
  ringtone_id: "example-ringtone-id"
```

Use the Protect ringtone ID shown by the **Ringtone** select or its
`chime_ringtone_id` attribute. The select lists only IDs already used by this
console's chimes because the official API has no ringtone catalog or
name-to-ID mapping. The action also accepts IDs not currently in use. The console
validates them. Changing the ringtone applies to every paired doorbell while
preserving its own volume and repeat count. A chime with no paired doorbells has
no ringtone setting to change.

The **Play** button and `play_chime_ringtone` action report an unsupported
operation. They send no request because the official API has no playback endpoint. The action's optional `ringtone_id` field is kept
only so existing automations stay valid. Calls with and without the field
raise the same error.

```yaml
# Choose which doorbells ring a chime (replaces its current pairing).
# Pick doorbell ring sensors of the chime's console, or their devices or areas.
# Leave doorbells empty to unpair every doorbell.
action: unifi_insights.set_chime_paired_doorbells
data:
  chime_id: "chime-id"
  doorbells:
    entity_id:
      - binary_sensor.front_doorbell_doorbell_ring
```

## UniFi Access

Access is an optional application on an existing **Local** console entry. The
console must also provide Network, Protect or InnerSpace for onboarding.

### Access setup

1. In Access, open **Settings → General → Advanced → API Token**.
2. Create a token.
3. Grant `view:space` for door state.
4. Grant `edit:space` for door, gate, and emergency controls.
5. For device discovery and doorbell notifications, grant `view:device`.
6. For user actions, grant `edit:user`.
7. For access-result events, grant `view:webhook` and `edit:webhook`.
8. Reconfigure the Local entry in Home Assistant.
9. Enter the **Access token**.
10. Enter the **Access port** from your console's Access API reference.
11. Save the entry.

The default port is **12445**. Some UniFi documents identify **12455** as
“OpenAPI”. This integration uses the reference that specifies **12445**.
Access uses its own token. It uses the same console host and certificate
verification setting as the other applications.

To remove Access, select **Remove the Access token** during reconfiguration.
An empty token field keeps the stored token.

Doorbell notifications need **1.20.11+**. Lock rules and emergency settings
need **1.24.6+**. Webhooks need **2.2.10+**. Community Access SDKs document
gate motor commands for **4.2.16+** and a **UA Hub Gate in three-button mode**.

> **Warning.** Before you automate gate commands, check the hub type and door
> link. Tests did not use gate hardware.

The integration creates a cover only for a hub that reports `UGT` with an
exact door location link. Other hub types keep their door controls.

If the token lacks permission, the related feature becomes unavailable.
Door and gate controls also need device-read permission. If the Access
token expires, replace it during reconfiguration. If Access is offline
at startup, the integration continues to poll. It adds the Access entities
after the connection returns. Network and Protect can continue to run.

### Access entities and actions

- **Door locks** show the lock relay state, including for unbound doors.
  The lock action sends the temporary `reset` rule. It restores the
  configured rule. An active unlock schedule still applies.
  This action does not start emergency lockdown. An Access Enterprise hub
  that lacks temporary lock rules can reject it.
- **Unlock buttons** appear only for doors with an Access hub. An unbound
  door cannot unlock remotely.
- **Gate Hub covers** offer open, close, and stop. The sensor reports the
  gate position separately from the lock relay. If the sensor has no
  position, Home Assistant shows unknown.
- **Lockdown** keeps doors locked. **Evacuation** keeps doors unlocked.
  If you enable one, Access disables the other. The integration reads
  the state from Access after each action.
- **Doorbell events** come from Access notifications. **Access granted**
  and **Access denied** require the optional webhook.

The actions below require a Home Assistant administrator. Use the Access
`user_id`. If more than one Access console is configured, enter `console_id`
as the entry ID or title.

| Action                                  | Description                                                         |
| --------------------------------------- | ------------------------------------------------------------------- |
| `unifi_insights.set_access_user_pin`    | Assign a PIN with digits only. Access checks the configured length. |
| `unifi_insights.remove_access_user_pin` | Remove a user's assigned PIN                                        |
| `unifi_insights.set_access_user_status` | Set the user account to `ACTIVE` or `DEACTIVATED`                   |

User status applies to all credentials for that user. You can activate
the user again after deactivation. Access keeps the NFC credentials.

### Access-result webhooks

Enable **Receive Access door events (granted and denied)** in the entry
options. The integration registers a signed `access.door.unlock` webhook.
The console must reach Home Assistant's internal URL. The receiver accepts
local POST requests and verifies their signature and timestamp. It rejects
repeated deliveries across entry reloads. A restart clears the replay cache.
A replay within the timestamp window can then pass.

Access-result events need a reachable URL, token permissions, and a
signing secret. Doorbell notifications operate separately.

An entry reload keeps its webhook registration on the console.
If Access is reachable, removal of the entry or the option deletes that
registration. If you disable the option while Access is offline, the
receiver stops. The entry keeps the endpoint details for a retry on reload.

A host or port change first deletes the old webhook endpoint. If deletion
fails, reconfiguration stops. The old receiver and settings stay active.
If a save error or identity conflict stops the change after receiver
shutdown, the integration requests a reload.

If the console refuses deletion, examine the old token's permissions.
If cleanup is impossible, clear the token. Remove the old endpoint in
Access. Then reconfigure the host or port. If Access is offline, the old endpoint can remain after entry or token removal.
When Access returns, remove the old endpoint. The integration stops the receiver and discards the
old credentials.

Setup registers the webhook. If registration fails, correct the connection,
URL, or permissions. Then reload the entry. The receiver ignores access
results for a door until a poll finds that door. If Access registers an
endpoint but returns an unusable response, remove it in Access before retrying.
Diagnostics and events omit tokens, PINs, signing secrets, and other credentials.

> **Warning.** Do not put PINs that you use again in automation YAML. Traces,
> event listeners, Recorder, and core debug logs can contain action inputs.
> Before you use a PIN action, turn off `homeassistant.core` DEBUG.
> Limit access to traces, logs, and the database.

Give PINs as strings. In YAML, put the PIN in quotation marks (for example,
`pin: "0123"`). This keeps the initial zeros. The integration rejects
numeric inputs.

## UniFi InnerSpace

InnerSpace places your UniFi devices on floor plans. For each device that InnerSpace reports, the integration creates a diagnostic **InnerSpace Placement** sensor:

- State: `placed`, `unplaced` or `unknown`.
- Attributes: floor plan name and ID, position (`x`, `y`, `height`, `azimuth`, `mount`), model and status.
- The sensor is added to the matching Network or Protect device. If no device matches, it gets its own InnerSpace device.

InnerSpace works with Local and Remote entries, and data is refreshed every 5 minutes.

## UniFi Mobility

Remote entries read [UniFi Mobility](https://unifi.ui.com) workspaces and
mobile routers (UMR, UMR Industrial, UMR Ultra). Mobility needs the
UniFi cloud.

### Mobility setup

Create the cloud API key at [unifi.ui.com](https://unifi.ui.com) with Mobility read access (the `mobility` scope with `read:mobility`).

- The integration usually polls Mobility every 5 minutes. For many
  routers, it polls less often. This keeps average use within half of
  the key's 100 requests per minute.
- Without Mobility access, the integration logs one message and creates
  no Mobility entities. It does not request reauthentication. It polls
  again after one hour. It also uses this interval for accounts without
  routers. Reload the entry to look for a new router immediately.
- Remote entries that share one key use one entry to poll Mobility and
  create devices. At startup, this is normally the oldest enabled entry.
  If it is disabled or removed, another entry takes over. If the selected
  entry cannot load, Mobility can wait until that entry loads.
- The integration reads Mobility data. It cannot change router names,
  LAN settings, or Wi-Fi settings. It does not read per-router client lists.

### Mobility entities

Each active Mobility workspace is a service device with **Routers** and **Online routers** counts. Each mobile router is its own device under its workspace, with these entities:

| Entity                                 | Notes                                                               |
| -------------------------------------- | ------------------------------------------------------------------- |
| Connectivity                           | On while the router is connected to the UniFi cloud                 |
| State                                  | Connected, disconnected, adopting, upgrading, restarting, and so on |
| Clients                                | Connected client count                                              |
| WAN source                             | LTE, Ethernet WAN or Wi-Fi WAN                                      |
| LTE signal                             | No signal, poor, fair or strong                                     |
| Cellular data usage                    | Data used in the current billing cycle (resets each cycle)          |
| VPN status, Subscription status        | Unknown when no VPN or subscription is configured                   |
| Location                               | GPS device tracker. It shows unknown without a GPS fix.             |
| Memory usage, Firmware                 | Diagnostic                                                          |
| Cellular data limit, Subscription plan | Diagnostic. The limit is unknown for unlimited plans.               |
| Uptime, WAN IP address, ISP            | Diagnostic, disabled by default                                     |

A router that Mobility stops reporting becomes unavailable. You can then delete its device from the device page.

## UniFi Site Manager

Remote entries use the same cloud API key to read account-wide data from UniFi Site Manager every 10 minutes:

- Host, site and device inventory.
- Five-minute ISP metrics.
- SD-WAN configuration types.

Site Manager data appears in diagnostics. Entries with the same API key
share one account refresh. A Site Manager failure does not stop the console
connection. The diagnostics report shows the collection status.

## UniFi Carrier Fabric (ISP)

Carrier Fabric entries let ISPs monitor their UniFi Carrier Fabric subscribers and service plans, and optionally suspend or resume subscribers.

### Carrier Fabric setup

Use an ISP API key with `read:subscribers` and `read:plans`.
For suspend and resume actions, add `suspend:service` and `resume:service`.
See the [Carrier Fabric developer documentation](https://developer.ui.com/carrier-fabric/v1.0.0/getting-started)
for key setup. Console and Site Manager keys cannot access Carrier Fabric.

1. Add the integration.
2. Select **Carrier Fabric (ISP)**.
3. Enter the ISP API key.

The integration polls Carrier Fabric every 5 minutes.

Carrier Fabric entries have two options, both off by default:

- **Track subscribers** creates a device for each subscriber. It has a
  service state sensor and a service plan sensor. The device name uses
  the subscriber name, or the subscriber number if there is no name.
  Home Assistant users can see this customer data.
- **Enable service actions** enables `carrier_suspend_subscriber` and
  `carrier_resume_subscriber`. These actions change internet access.

### Carrier Fabric entities

By default, the integration creates:

- **Sensors:** total subscribers and suspended subscribers.
- **Diagnostic sensors:** subscriber counts by state, plus counts for active service plans. Each service plan also has a subscriber count. Archived plan sensors are off by default.

When **Track subscribers** is on, each subscriber device has:

- **Sensor:** service state (`pending_assignment`, `provisioned`, `installed`, `suspended`, or `unknown` when the API reports a state that this integration does not recognize).
- **Diagnostic sensor:** service plan (the name of the subscriber's assigned service plan).

### Carrier Fabric actions

These actions need **Enable service actions** and an API key with the required scope:

- `unifi_insights.carrier_suspend_subscriber`: stops a subscriber's internet service (optional `reason`, maximum 1024 characters). The target must be a single Carrier Fabric subscriber device or entity. Needs the `suspend:service` scope.
- `unifi_insights.carrier_resume_subscriber`: restores a subscriber's internet service. The target must be a single Carrier Fabric subscriber device or entity. Needs the `resume:service` scope.

### Carrier Fabric privacy

- Entities and state attributes omit email addresses, service addresses,
  customer notes, custom metadata, and suspension reasons.
- Diagnostics include only selected fields. The report removes subscriber
  names and numbers. It can keep identifiers.

The integration does not assign plans, create or change subscribers, or attach or detach hosts.

## Automation blueprints

The repository includes three YAML blueprints for network health alerts.
They use UniFi Insights entities and need Home Assistant 2026.9.0 or later.
Each blueprint runs the actions you select.

| Blueprint                      | Runs your actions when                                                                                     | Needs                                                                                                                  |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Infrastructure offline alert   | A gateway, switch, or access point stays offline for the set time. The blueprint can also report recovery. | The **Status** sensors in [Network entities](#network-entities)                                                        |
| AP overload alert              | The client count or channel utilization stays above its limit for the set time.                            | The **Wireless Clients** sensors in [Network entities](#network-entities). Utilization alerts need an external sensor. |
| Secure subnet new client alert | A new tracked client matches a secure subnet prefix                                                        | Client tracking and IP prefixes for secure subnets                                                                     |

### Install the blueprints

Install the blueprints separately after the integration.
Select an import button to open the blueprint in Home Assistant.

| Blueprint source                                            | Home Assistant                                                                                  |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| [Infrastructure offline alert][yaml-infrastructure-offline] | [![Import Infrastructure offline alert][blueprint-import-badge]][import-infrastructure-offline] |
| [AP overload alert][yaml-ap-overload]                       | [![Import AP overload alert][blueprint-import-badge]][import-ap-overload]                       |
| [Secure subnet new client alert][yaml-secure-subnet]        | [![Import Secure subnet new client alert][blueprint-import-badge]][import-secure-subnet]        |

[blueprint-import-badge]: https://my.home-assistant.io/badges/blueprint_import.svg
[import-infrastructure-offline]: https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ftinkerologie%2Fha-unifi-insights%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Funifi_insights%2Finfrastructure_offline.yaml
[yaml-infrastructure-offline]: https://github.com/tinkerologie/ha-unifi-insights/blob/main/blueprints/automation/unifi_insights/infrastructure_offline.yaml
[import-ap-overload]: https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ftinkerologie%2Fha-unifi-insights%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Funifi_insights%2Fap_overload.yaml
[yaml-ap-overload]: https://github.com/tinkerologie/ha-unifi-insights/blob/main/blueprints/automation/unifi_insights/ap_overload.yaml
[import-secure-subnet]: https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ftinkerologie%2Fha-unifi-insights%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Funifi_insights%2Fsecure_subnet_new_client.yaml
[yaml-secure-subnet]: https://github.com/tinkerologie/ha-unifi-insights/blob/main/blueprints/automation/unifi_insights/secure_subnet_new_client.yaml

#### Import with a URL

1. Copy the link from the **Blueprint source** column.
2. Open **Settings** → **Automations & scenes** → **Blueprints**.
3. Select **Import blueprint**.
4. Paste the URL.

To install from a file, download it from the
[blueprints folder](blueprints/automation/unifi_insights).
Put it in `<config>/blueprints/automation/unifi_insights/`.
`<config>` is the folder that contains `configuration.yaml`.

#### Create an automation

1. Open the **Blueprints** page.
2. Find a blueprint with a name that starts with "UniFi Insights -".
3. Create an automation from that blueprint.
4. Enter its inputs.

### Blueprint inputs

- **Infrastructure offline alert.** Select **Connectivity sensors**.
  These are the **Status** entities in [Network entities](#network-entities).
  **Offline delay** defaults to 5 minutes. **Treat unavailable as offline**
  is on by default. Select **Offline actions**. You can also select
  **Recovery actions (optional)**. The actions can use `trigger.id`
  (`offline`, `unavailable`, or `recovery`), `trigger.entity_id`, and `trigger.to_state`.
- **AP overload alert.** Select **Access point client sensors**. These are
  the **Wireless Clients** entities in [Network entities](#network-entities).
  **Client threshold** defaults to 30 (1–500). **Utilisation threshold**
  defaults to 80% (1–100%). **Duration** defaults to 5 minutes.
  Select **Actions**. You can also select **Channel utilisation sensors (optional)**.
  The actions can use `trigger.id` (`client_overload` or `utilisation`),
  `trigger.entity_id`, and `trigger.to_state`.
- **Secure subnet new client alert.** Enter **Secure subnet prefixes**.
  An example is `10.0.50.`. You can select **Allowed trackers (optional)**
  from [Network entities](#network-entities). Select **Actions**.
  The actions can use `tracker`, `ip_address`, and `matched_prefix`.
  `tracker` is the tracker entity ID. Allowed trackers cause no alert.
  The blueprint does not give actions the MAC address.

### Blueprint prerequisites

- For the secure subnet alert, turn on **Track WiFi Clients** or
  **Track Wired Clients** in [Options](#options). These options are off
  by default. The blueprint needs a new device tracker to start a run.
- For channel utilization, select a sensor from another source. It must
  report percent. Put it under **Channel utilisation sensors (optional)**.
  If the field is empty, the blueprint uses client count only.

### Blueprint limits

- The offline and AP overload blueprints queue up to 100 runs. The secure
  subnet blueprint can run up to 100 instances at the same time.
- A device outage and a console outage can both make **Status** show
  `unavailable`. One console outage can alert for all selected devices.
  **Treat unavailable as offline** is on by default. If you turn it off,
  only `off` causes an alert.
- The offline blueprint uses the previous state. A short intermediate
  state can cause an offline alert without recovery, or recovery without
  an offline alert.
- The secure subnet blueprint compares the text at the start of the IP
  address. Device trackers have no VLAN or network name. Give each secure
  subnet a different prefix, such as `10.0.50.`. The blueprint checks the
  IP address 10 seconds after tracker creation. A missing address cannot match.
- The secure subnet blueprint runs when Home Assistant creates the
  tracker. Reconnection or a change of address does not start a run.
- When you first turn on tracking, Home Assistant creates trackers for
  all connected clients. The blueprint can run only 100 at once. Home
  Assistant logs a warning for extra runs. To prevent these alerts,
  turn on tracking before you create the automation.
- If an access point statistics request fails, **Wireless Clients** keeps
  the last count for up to three polls (30 seconds each). Then it shows
  `unknown`. AP overload ignores readings after `unknown` or `unavailable`.
  After recovery above the threshold, the count must drop to the threshold
  or lower. The next increase above it can cause an alert.
- During a console outage, client trackers keep their last home or away
  state. Automations and `person` entities can still read that state.

## Dashboard cards

The integration installs six dashboard cards. The cards use existing data
and do not create entities.

1. Edit a dashboard.
2. Select **Add card**.
3. Search for the card name.

| Card                     | Type                                           | Shows                                                                   |
| ------------------------ | ---------------------------------------------- | ----------------------------------------------------------------------- |
| UniFi Topology           | `custom:unifi-insights-topology-card`          | Interactive network topology of a UniFi site                            |
| UniFi Site Health        | `custom:unifi-insights-site-health-card`       | Compact site health summary with WAN, gateway, device and client status |
| UniFi Internet Activity  | `custom:unifi-insights-internet-activity-card` | Historical WAN download/upload activity and live gateway throughput     |
| UniFi Device Performance | `custom:unifi-insights-performance-card`       | Infrastructure CPU, memory, PoE, client load and throughput summary     |
| UniFi Protect Status     | `custom:unifi-insights-protect-status-card`    | Live UniFi Protect camera, doorbell, chime and NVR status summary       |
| UniFi Event Timeline     | `custom:unifi-insights-timeline-card`          | Recent UniFi Protect security and device activity timeline              |

Home Assistant keeps dashboard resources in storage by default.
In this mode, the integration adds the card resources automatically.
To see the resources, open **Settings → Dashboards → Resources**.

After you install or update the integration, refresh the browser page.
If you use the Home Assistant app, close it. Then open it again.

### Dashboard resources in YAML

If you use YAML for dashboard resources, the integration loads the cards
as extra frontend modules. If the dashboard loads first, it can show
**Custom element doesn't exist**.

To prevent this error, add the resource below to `configuration.yaml`.
Set the text after `v=` to the installed version. Then restart Home Assistant.
This resource loads all six cards.

```yaml
lovelace:
  resources:
    - url: /unifi_insights/topology-card.js?v=2026.11.1
      type: module
```

The browser uses the text after `v=` to decide when to get a new copy.
If the value stays the same, the browser can load old cards or fail to load them.

After each integration update, follow these steps.

1. Change the text after `v=` to the new integration version.
2. Restart Home Assistant.
3. Refresh the browser page.

### Dashboard cards in YAML

You can also add a card in the dashboard code editor. Use the type from
the table above. All other options are optional.

#### Topology card

Use this example for the default settings.

```yaml
type: custom:unifi-insights-topology-card
```

Use this example to set options for one site.

```yaml
type: custom:unifi-insights-topology-card
entry_id: 01JABCDEFGHJKMNPQRSTVWXYZ0
site_id: 88f7af54-98f8-306a-a1c7-c9349722b1f6
title: Home network
view: graph
layout: tiered
clients: expanded
kinds:
  - gateway
  - switch
  - access_point
```

For all topology options, see [Network topology card](#network-topology-card).

#### Site cards

The **UniFi Site Health**, **UniFi Internet Activity**, **UniFi Device Performance**,
and **UniFi Event Timeline** cards use the same options.

**UniFi Site Health**

```yaml
type: custom:unifi-insights-site-health-card
title: Home network health
entry_id: 01JABCDEFGHJKMNPQRSTVWXYZ0
site_id: 88f7af54-98f8-306a-a1c7-c9349722b1f6
```

**UniFi Internet Activity**

```yaml
type: custom:unifi-insights-internet-activity-card
```

**UniFi Device Performance**

```yaml
type: custom:unifi-insights-performance-card
```

**UniFi Event Timeline**

```yaml
type: custom:unifi-insights-timeline-card
```

#### Protect Status card

The **UniFi Protect Status** card uses only the config entry.

```yaml
type: custom:unifi-insights-protect-status-card
title: Cameras
entry_id: 01JABCDEFGHJKMNPQRSTVWXYZ0
```

#### Options for site and Protect cards

| Option     | Values                           | Default                         |
| ---------- | -------------------------------- | ------------------------------- |
| `title`    | text                             | the card name                   |
| `entry_id` | the ID of the UniFi config entry | the only entry, if there is one |
| `site_id`  | the ID of the UniFi site         | the only site, if there is one  |

- Set `entry_id` and `site_id` together. The Protect Status card needs
  only `entry_id`.
- With no options, a site card selects the site only if there is one site.
- The example IDs are not real. To get your IDs, select the site in the
  card editor. Then select **Show code editor**.

### Network topology card

The **UniFi Topology** card shows a site's gateways, WANs, switches,
access points, and clients. For one site, the card works with the default
settings. If you have more than one site, select one in the card editor.

- In **Graph**, drag to pan. Pinch or use Ctrl + scroll to zoom.
  In panel view, scroll to zoom. The zoom buttons and `+`, `-`, and `0` also work.
- Select a device to see its uplink port, link speed, PoE draw, and client
  counts. Its details can link to its Home Assistant device page.
- For screen readers, use **List**. It shows an indented list with search.
- Select a client group, for example "12 clients", to expand it.
- A gateway's WANs appear above it with their names and states.
  A disconnected WAN is dim. It shows **Disconnected** and a red dashed line.
  The WANs disappear when you hide their gateway.
- Use the filters to hide WANs, gateways, switches, access points, clients,
  or other devices. Devices below a hidden device stay visible with a
  dashed line.
- Select a device or client to see its details. See **Actions menu** below.

**Options** (all optional, all available in the card editor):

| Option                 | Values                                                               | Default                             |
| ---------------------- | -------------------------------------------------------------------- | ----------------------------------- |
| `entry_id` + `site_id` | the site to show                                                     | the only site, if there is one      |
| `title`                | text                                                                 | the site name                       |
| `view`                 | `graph`, `list`                                                      | `graph`                             |
| `show_site_selector`   | `true`, `false`                                                      | `false`                             |
| `clients`              | `collapsed`, `expanded`, `hidden`                                    | `collapsed`                         |
| `kinds`                | any of `wan`, `gateway`, `switch`, `access_point`, `client`, `other` | all                                 |
| `density`              | `comfortable`, `compact`                                             | automatic (compact on narrow cards) |
| `orientation`          | `vertical`, `horizontal`                                             | `vertical`                          |
| `layout`               | `tree`, `tiered`                                                     | `tree`                              |
| `show_labels`          | `true`, `false`                                                      | `true`                              |
| `show_wan`             | `true`, `false`; `false` removes WAN nodes and their filter button   | `true`                              |
| `max_clients`          | 1–500                                                                | 500                                 |

The graph uses `layout`. In `tree`, a device goes below its parent and a
WAN goes above its gateway. In `tiered`, the rows show WANs, gateways,
switches, access points, other devices, and clients in that order.
If the normal row is at or above the parent, the device goes just below
the parent. The graph omits empty rows. With `orientation: horizontal`,
these rows become columns.

### Actions menu

Administrators can open **Actions** from the node details if an action is
available. In **Graph** or **List**, right-click the node to open the menu.
On a touch screen, press and hold if the browser supports this gesture.
With the node in focus, use ContextMenu or Shift+F10.

A client group has no **Actions** menu. Other users can still see
**Open device** if the details have a device page.

| Action           | Who can use it | What it does                                                                                                            |
| ---------------- | -------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Copy IP address  | Administrators | Copies the node's IP address to the clipboard                                                                           |
| Copy MAC address | Administrators | Copies the node's MAC address to the clipboard                                                                          |
| Open device      | Administrators | Opens the node's Home Assistant device page                                                                             |
| Open entity      | Administrators | Opens the more-info dialog of a device's **Status** sensor, a WAN's **Connection** sensor, or a client's device tracker |

The menu shows an action only if its target exists. For example, a client
needs a tracker to open its entity. See [Options](#options) for
**Track WiFi Clients** and **Track Wired Clients**.

Use the Up and Down arrow keys, Home, and End to move through the menu.
Press Enter to select an action. Press Escape to close the menu and
return focus to the button. On the button, press Up or Down to open the
menu. If the copy fails, the card shows text that you can select.

**Privacy.** The topology snapshot can include names, models, states,
ports, VLANs, and `ha_device_id`. It omits MAC addresses, IP addresses,
hostnames, and firmware versions.

Only administrators can use `unifi_insights/topology/node_details`.
The result can contain the selected node's IP and MAC addresses, plus
`device_id` and `entity_id`. A WAN can give its WAN IP address. The result
omits hostnames and firmware versions. When the selection changes, the
card discards the address.

## Troubleshooting

### Enable debug logging

1. Add this configuration to `configuration.yaml`.

   ```yaml
   logger:
     default: info
     logs:
       custom_components.unifi_insights: debug
   ```

2. Restart Home Assistant.

### Force a refresh

Use the `unifi_insights.refresh_data` action to refresh all data.

```yaml
action: unifi_insights.refresh_data
```

### Common problems

#### The console does not connect

- **Cannot connect.** Make sure that Home Assistant can reach the console URL.
  If the console uses a self-signed certificate, reconfigure the entry.
  Turn off **Verify SSL Certificate**.
- **Authentication failed.** Make sure that the API key is valid. If necessary,
  get a new key from the console or UniFi Site Manager.
- **Integration API not available.** Use a UniFi OS console. A self-hosted
  Network application does not provide the UniFi Integration API.

#### Entities are missing or unavailable

- **No Protect entities.** Make sure that Protect runs on the same console.
  Make sure that the API key can access Protect.
- **Entities are missing.** Make sure that the devices are adopted and online in UniFi.
- **Storage sensors are unavailable.** Some firmware versions do not report
  NVR storage through the public Protect API.
- **Too many unused client entities.** Turn off **Enable Client Control**.
- **Duplicate entities.** UniFi Insights and the official UniFi integrations
  can create entities for the same device. Disable the duplicate entities
  that you do not use.
- **Wake button does nothing.** Make sure that Wake-on-LAN is on for the
  device. Check the Home Assistant host network and VLAN.
  See [Wake-on-LAN buttons](#wake-on-lan-buttons).

#### Dashboard cards do not load

If **Add card** does not show a card, refresh the browser page. In the Home
Assistant app, close the app. Then open it again.

If you use the default storage mode, check **Settings → Dashboards → Resources**.
Make sure that the list includes the `/unifi_insights/` entries.

If a card shows **Custom element doesn't exist**, refresh the browser page.
If you use YAML resources, add the resource from [Dashboard resources in YAML](#dashboard-resources-in-yaml).
After an update, change its `v=` value to the installed integration version.

### Remote console access denied (HTTP 403)

Site Manager can list consoles that an API key cannot access through the
Cloud Connector. Personal Site Manager API keys work only with consoles that
the key's account owns. Delegated accounts, including view-only accounts and
accounts with reported Super Admin access, can receive HTTP 403.

Use a key from the console owner, an organization API key, or a
[Local connection](#local-connection) with a key created on the console.
UniFi Insights cannot grant access to the console's applications.

### Diagnostics

1. Open **Settings** → **Devices & services**.
2. Select **UniFi Insights**.
3. Open the three-dot menu.
4. Select **Download diagnostics**.

The integration removes API keys, passwords, Wi-Fi secrets, and network
addresses from the report. It also removes hostnames, SSIDs, and Network
client names. It replaces each MAC address with a value that stays the same
within one report. The report keeps device, site, and camera names.

The Mobility section gives counts, for example router counts by state and
model. It omits workspace names, router names, addresses, and locations.

## Contributing

Read the [contributor guide](CONTRIBUTING.md). Open an issue before you submit a change.

The [domain glossary](GLOSSARY.md) and [architecture decisions](docs/development/DECISIONS.md)
give the project terms and describe the design of the integration.

## License

This project uses the Apache License 2.0. See the [license](LICENSE).

## Disclaimer

This integration is not affiliated with or endorsed by Ubiquiti Inc. Use at your own risk.
