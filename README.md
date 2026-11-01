# UniFi Insights integration for Home Assistant

<!-- BADGES:START -->

[![Marketplace and HACS](https://img.shields.io/badge/Marketplace%20%7C%20HACS-Default-orange.svg)](https://www.hacs.xyz/docs/marketplace/)
[![GitHub Last Commit](https://img.shields.io/github/last-commit/tinkerologie/ha-unifi-insights)](https://github.com/tinkerologie/ha-unifi-insights/commits/main)
[![GitHub Release](https://img.shields.io/github/v/release/tinkerologie/ha-unifi-insights)](https://github.com/tinkerologie/ha-unifi-insights/releases)
[![codecov](https://codecov.io/gh/tinkerologie/ha-unifi-insights/graph/badge.svg)](https://codecov.io/gh/tinkerologie/ha-unifi-insights)
[![License](https://img.shields.io/github/license/tinkerologie/ha-unifi-insights)](./LICENSE)

<!-- BADGES:END -->

Monitor and control your UniFi system from Home Assistant: UniFi Network, UniFi Protect, UniFi Access, UniFi InnerSpace, UniFi Mobility, UniFi Site Manager and UniFi Carrier Fabric, in one integration.

## Contents

- [Overview](#overview)
- [Supported UniFi products](#supported-unifi-products)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- Products: [Network](#unifi-network), [Protect](#unifi-protect), [Access](#unifi-access), [InnerSpace](#unifi-innerspace), [Mobility](#unifi-mobility), [Site Manager](#unifi-site-manager), [Carrier Fabric (ISP)](#unifi-carrier-fabric-isp)
- [Automation blueprints](#automation-blueprints)
- [Dashboard cards](#dashboard-cards)
- [Troubleshooting](#troubleshooting)

## Overview

UniFi Insights is a custom integration that you install from the Home Assistant Marketplace (Home Assistant 2026.11 and newer) or from HACS (Home Assistant 2026.9 and 2026.10). It connects with an API key, not a user name and password. It mainly uses the official UniFi APIs that Ubiquiti publishes at [developer.ui.com](https://developer.ui.com/). For some UniFi Network features that the official API doesn't cover yet, such as Wi-Fi QR codes, VPN, route, port forward, traffic rule, NAT rule and static route switches, and internet traffic history, it uses the console's classic Network endpoints with the same API key.

- **One integration for your UniFi system.** One console entry covers that console's Network, Protect and InnerSpace applications. Local entries can also connect to Access using its separate token. A cloud API key adds Mobility and Site Manager data. ISPs can add a Carrier Fabric entry.
- **API key setup, local or remote.** Connect to a console on your network, or choose any console on your UniFi account through UniFi Cloud. You don't need a local user account.
- **Standard Home Assistant entities.** Sensors, binary sensors, switches, buttons, cameras, events, lights, selects, numbers, images, device trackers and update entities, grouped under their UniFi devices.
- **Actions** for tasks that are not entities, such as guest authorization, hotspot vouchers, PTZ moves and Protect live views.
- **Six dashboard cards**, installed with the integration: network topology, site health, internet activity, device performance, Protect status and an event timeline.
- **Diagnostics that protect your privacy.** API keys, addresses, client names and other personal data are redacted.

## Supported UniFi products

| Product                                                 | Connection            | Highlights                                                                                                                          |
| ------------------------------------------------------- | --------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| [UniFi Network](#unifi-network)                         | Local or Remote       | Device, port, power, WAN and client monitoring; Wi-Fi, firewall, route, VPN and client controls; guest access and vouchers          |
| [UniFi Protect](#unifi-protect)                         | Local or Remote       | Cameras, smart detection and doorbell events, lights, chimes, sirens, PTZ, sensors, NVR storage, Alarm Manager, alarm hub status    |
| [UniFi Access](#unifi-access)                           | Local, optional token | Door locks and unlock buttons, Gate Hub covers, emergency controls, doorbell and signed access-result events, administrator actions |
| [UniFi InnerSpace](#unifi-innerspace)                   | Local or Remote       | Floor-plan placement of your UniFi devices                                                                                          |
| [UniFi Mobility](#unifi-mobility)                       | Remote                | Mobile router status, cellular data use and GPS location                                                                            |
| [UniFi Site Manager](#unifi-site-manager)               | Remote                | Account-wide host, site, ISP and SD-WAN data in diagnostics                                                                         |
| [UniFi Carrier Fabric (ISP)](#unifi-carrier-fabric-isp) | Carrier Fabric (ISP)  | Subscriber and service plan counts, with optional suspend and resume                                                                |

The integration only creates entities for the applications your console runs. A console with only Network, only Protect or only InnerSpace works.

## Requirements

- Home Assistant 2026.9.0 or newer.
- For Local and Remote entries: a UniFi OS console (for example a UDM, UDR, UCG or Cloud Key Gen2+) with UniFi Network, UniFi Protect or UniFi InnerSpace. The console must provide the official UniFi Integration API. Self-hosted UniFi Network Application installs (Linux, Docker, Proxmox) don't provide it, so they can't be added as Local entries.
- For Remote entries, Mobility and Site Manager: a cloud API key from [UniFi Site Manager](https://unifi.ui.com).
- For Carrier Fabric: an ISP API key.
- Keep UniFi OS and its applications up to date. Some features need recent releases. For example, the Thread and keypad fob entities, and the alarm hub's reported tamper status, need UniFi Protect 7.3.70 or newer.

## Installation

### Marketplace (Home Assistant 2026.11 and newer, recommended)

Starting with Home Assistant 2026.11, HACS is built into Home Assistant as the Marketplace, so there is nothing else to install first.

[![Open your Home Assistant instance and open a repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=tinkerologie&repository=ha-unifi-insights&category=integration)

1. Select the button above, or go to **Settings** → **Marketplace** and search for "UniFi Insights".
2. Install it. The integration loads right away. If Home Assistant shows a repair that asks for a restart, restart Home Assistant.

If you installed UniFi Insights with HACS before you upgraded to Home Assistant 2026.11, you don't need to do anything. The Marketplace takes over HACS installs, and UniFi Insights keeps getting updates.

### HACS (Home Assistant 2026.9 and 2026.10)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=tinkerologie&repository=ha-unifi-insights&category=integration)

1. Select the button above, or open HACS and search for "UniFi Insights".
2. Select **Download**.
3. Restart Home Assistant.

### Manual installation

1. Download `unifi_insights.zip` from the latest release on the [Releases page](https://github.com/tinkerologie/ha-unifi-insights/releases).
2. Create the folder `config/custom_components/unifi_insights/` and extract the zip into it. `manifest.json` must end up directly in that folder.
3. Restart Home Assistant.

Each release since the first one that ships `unifi_insights.zip` carries the zip, a `SHA256SUMS` file and a signed build provenance attestation. The Marketplace and HACS install the same zip. The "Downloaded N times" figure they show is the download count of the latest release's zip, so it starts again with each release. To check that a zip you downloaded is the one the release workflow built:

```bash
gh attestation verify unifi_insights.zip -R tinkerologie/ha-unifi-insights
sha256sum -c SHA256SUMS
```

Keep `SHA256SUMS` in the same folder as the zip. On macOS, use `shasum -a 256 -c SHA256SUMS`.

## Configuration

### Choose a connection type

| Connection type           | Use it for                                                        | API key                                                        |
| ------------------------- | ----------------------------------------------------------------- | -------------------------------------------------------------- |
| Local (Direct connection) | A console on your network: Network, Protect and InnerSpace        | Created on the console                                         |
| Remote (UniFi Cloud)      | Any console on your UniFi account, plus Mobility and Site Manager | Created in [UniFi Site Manager](https://unifi.ui.com)          |
| Carrier Fabric (ISP)      | An ISP's Carrier Fabric subscribers and service plans             | ISP API key, see [Carrier Fabric setup](#carrier-fabric-setup) |

You can add more than one entry, for example one per console.

### Add the integration

1. In Home Assistant, go to **Settings** → **Devices & services**.
2. Select **Add integration** and search for "UniFi Insights".
3. Choose the connection type, then follow the steps for it:
   - **Local:** create an API key on the console in **Settings** → **Control Plane** → **API Keys**. Enter the console URL (for example `https://192.168.1.1`) and the key. Leave **Verify SSL Certificate** off if the console uses its default self-signed certificate.
   - **Remote:** in [UniFi Site Manager](https://unifi.ui.com), go to **Settings** → **API Keys** and select **Create New API Key**. Copy the key, enter it, then choose the console from the list of consoles that the key can access.
   - **Carrier Fabric (ISP):** enter an ISP API key. See [Carrier Fabric setup](#carrier-fabric-setup).

### Options

To change these settings after setup, go to **Settings** → **Devices & services** → **UniFi Insights** → **Configure**:

| Option                | Default | Description                                                                                                                                                                        |
| --------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Track WiFi Clients    | Off     | Creates a device tracker for each wireless client. This can add many entities on busy networks.                                                                                    |
| Track Wired Clients   | Off     | Creates a device tracker for each wired client.                                                                                                                                    |
| Enable Client Control | On      | Creates client allow/block switches, reconnect buttons and disabled-by-default Wake-on-LAN buttons for wired clients. Turn off for read-only monitoring.                           |
| Sites                 | All     | Only shown when the console has more than one site. Choose the sites to poll. Sites you don't choose are not queried at all, which reduces API traffic. Leave empty for all sites. |

Client trackers are created enabled, also on Home Assistant 2026.11 and later, which otherwise creates network-scanner trackers disabled. A tracker you disable stays disabled. When a client is renamed on the controller, the integration follows the new name on the next poll. It renames the client's own device when the tracker has one; a name you gave that device in Home Assistant is kept. When the tracker sits under its access point or switch, only the tracker is renamed.

Carrier Fabric entries have their own options. See [Carrier Fabric setup](#carrier-fabric-setup).

## UniFi Network

- **Device health:** status, CPU, memory, uptime, temperature and uplink rates for gateways, switches and access points.
- **Ports:** PoE power, link speed, traffic counters and rates, and SFP module details.
- **Power:** PDU outlet switches with power, voltage, current and power factor, and AC power consumption and budget.
- **Storage:** used percentage, used space and total space for each filesystem a console reports (for example eMMC and backup storage on a UDM Pro SE), and a Storage Nearly Full alert. Per-drive SMART, temperature, RAID and Drive/UNAS data require supported API fields. Protect NVR aggregate diagnostics are conditional on reported storage fields; the current official `/v1/nvrs` response lacks them.
- **Internet and WAN:** gateway and per-WAN connection status, WAN IP address, site-to-site VPN tunnel status, and internet download and upload totals for the last hour, day, week and month.
- **Clients:** client counts per site, device and Wi-Fi network; optional presence tracking; allow/block switches and reconnect buttons, and Wake-on-LAN buttons for wired clients.
- **Controls:** switches for Wi-Fi networks, firewall policies, policy-based routes, VPN clients, port forwards, traffic rules, NAT rules and static routes; device restart buttons; QR code images to join each Wi-Fi network.
- **Firmware:** update entities show the installed firmware and whether a newer version is available. They don't install firmware. Use the UniFi app for that.
- **Guest access:** authorize guests and create or delete hotspot vouchers with actions.

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

NAT rule and static route switches use the console's private API. They were tested on one UniFi gateway through the cloud connector; other consoles and Network versions may behave differently. If the console rejects a change, the switch reports an error and the rule is left as it was. A console can refuse requests for a few seconds while it applies a change (seen after static route changes). The rule switches of that site are then unavailable for about 15 seconds, until the integration reads the rules again.

### Access point radios

Each radio of an access point gets sensors named after its band: **Wi-Fi 2.4 GHz TX retries**, **Wi-Fi 5 GHz TX retries**, and so on for 900 MHz, 6 GHz and 60 GHz. The retry rate is a diagnostic measurement in percent, and it is polled every 30 seconds with the rest of the device statistics.

The radio settings - TX power mode, Wi-Fi standard, channel and channel width - come from the console's per-device details, which the integration requests for access points only and at most once every 5 minutes. Their sensors are diagnostic and disabled by default, because these values rarely change. A request that fails does not affect the retry rate: the settings sensors become unavailable and keep the last values until a request succeeds again, and the request is retried on the next 5-minute check rather than on every poll.

### Network rule change events

Each site has one event entity, **Network rule changes**. It fires `added`, `modified` or `deleted` when a firewall policy, policy-based route, port forward, traffic rule, NAT rule or static route changes on the console. Changes made from Home Assistant switches count too.

It does not fire for: rule reordering, system-defined firewall policies, console-managed NAT rules, VPN clients.

Changes are found when the configuration is polled, every 5 minutes. A change that is made and undone between two polls is not seen.

The first poll after Home Assistant starts or the integration reloads only records the current rules. Changes made while Home Assistant was stopped or reloading are not reported.

If the console cannot be reached, nothing fires; changes made meanwhile are reported after the next successful poll.

Deleting the last rule of a type on a site is reported one poll later (the integration waits for a second empty answer before it believes that every rule is gone).

Known limits: a rule the integration cannot parse is reported as `deleted`; a rule the console takes over (it becomes system-defined) is reported as `deleted`, and `added` if it is handed back; at most 100 changes per site are kept from one poll.

| Property         | Description                                                                                                        |
| ---------------- | ------------------------------------------------------------------------------------------------------------------ |
| `event_type`     | `added`, `modified` or `deleted`                                                                                   |
| `site_id`        | Site identifier                                                                                                    |
| `rule_type`      | Family of rule (`firewall_rule`, `policy_based_route`, `port_forward`, `traffic_rule`, `nat_rule`, `static_route`) |
| `rule_id`        | Unique ID of the rule                                                                                              |
| `rule_name`      | Name or description of the rule                                                                                    |
| `enabled`        | Whether the rule is enabled (`true`, `false`, or `null`)                                                           |
| `changed_fields` | List of field names that changed                                                                                   |

`rule_name` is the name or description typed on the console. No other field value (addresses, ports, domains) is included; `changed_fields` lists field names only.

Do not use a bare state trigger: Home Assistant re-sends the last event when the entity returns from unavailable (a failed poll, a reload, a restart).

```yaml
triggers:
  - trigger: state
    entity_id: event.<site>_network_rule_changes
    not_from: unavailable
    not_to: unavailable
mode: queued
```

Do not add `unknown` to `not_from`: the entity is `unknown` before its first event, so that would suppress the first event.

#### Hotspot voucher management

The integration creates entities to configure, generate, and monitor UniFi Hotspot guest vouchers:

- **Generation parameters (Number entities):**
  - **Voucher Duration:** Validity in minutes (1 to 1,000,000 minutes, default 480 / 8 hours). Forwarded directly as minutes.
  - **Voucher Guest Limit:** Allowed distinct guest count per voucher (0 to 1000, default 1; 0 = not set; UniFi applies its own default).
  - **Voucher Download Limit / Upload Limit:** Speed limit in Mbit/s (0 to 100 Mbit/s in 0.1 steps, multiplied by 1000 to kbps for UniFi; 0 = not set; UniFi applies its own default).
  - **Voucher Data Limit:** Byte transfer quota in MB (0 to 1,048,576 MB, default 0; 0 = not set; UniFi applies its own default).
  - Settings are Home Assistant-only (`RestoreNumber`) and restored across restarts; setting a number makes no API call.
  - **Generate guest voucher (Button entity):** Generates a single voucher in the site named "Home Assistant" using current input settings.
- **Inventory polling & targeted refresh:**
  - Voucher inventory is polled on the config coordinator schedule (every five minutes).
  - Generating or deleting a voucher triggers an immediate targeted per-site voucher refresh (`async_refresh_vouchers`).
- **Voucher status (Sensor entities):**
  - **Active Vouchers:** Count of unexpired vouchers that can still admit a guest (future or unactivated expiration, authorized guest count below limit).
  - **Latest Voucher Code:** Plain activation code of the most recent voucher generated in this Home Assistant session. In-memory only (not restored across restarts).
  - **Latest Voucher Expiration:** Expiration timestamp of the latest generated voucher. Unknown until first guest authorization.
- **Externally deleted record retention:**
  - If a voucher is deleted externally on the controller, Home Assistant retains the latest generated in-memory record until a restart or subsequent generation, but the QR image and entity become unavailable once the expiration timestamp passes.
- **Latest voucher QR code (Image entity):**
  - Displays a QR code containing the plain activation code text for entry on the captive portal web page.
  - **Note:** This QR code contains only the activation code text. It does not configure or join a Wi-Fi network (use the Wi-Fi QR code entity for Wi-Fi credentials).
  - Automatically becomes unavailable when expired or when the expiration deadline is reached.
- **Recorder history disclosure and privacy:**
  - **Security:** Voucher codes grant network access. The "Latest Voucher Code" sensor stores the active credential in Home Assistant's recorder history database by default.
  - To prevent credentials from persisting in the database, exclude the sensor in `configuration.yaml`:
    ```yaml
    recorder:
      exclude:
        entities:
          - sensor.your_site_latest_voucher_code
    ```
  - You can also disable the code sensor entity in Home Assistant entity settings. Note that disabling the entity after use does not erase previously recorded database history.

### Wake-on-LAN buttons

UniFi has no Wake-on-LAN function, so the **Wake** buttons send the packet from Home Assistant, using Home Assistant's built-in Wake on LAN integration. You don't set that integration up: this integration loads it for you. Wake buttons sit on the site's gateway device, or on the site device when the site has no gateway.

- **Which clients:** every wired client gets a **Wake _Client name_** button, disabled by default. Enable the buttons you want in Home Assistant. A button you have enabled is always restored after a restart, even if its client is offline, unnamed or absent from history, or history cannot be fetched. At startup, a background task also seeds buttons for wired clients with a name or hostname on the console that were seen in the last 30 days. Turning off **Enable Client Control** removes the client controls, including Wake buttons, for read-only monitoring.
- **Same network:** Home Assistant must be on the same network (VLAN) as the device. UniFi gateways usually don't forward wake packets between VLANs. The packet goes to the broadcast address of the client's network when the console reports it, and to 255.255.255.255 otherwise.
- **The device:** it needs Wake-on-LAN enabled in its BIOS/UEFI and in its operating system or network adapter settings. The button can't tell whether the device woke up.
- **Home Assistant:** it must use host networking (Home Assistant OS does). In a Docker bridge network the packet doesn't reach your network.
- **Availability:** Wake and reconnect buttons are unavailable while the console is unreachable and return when it recovers. A Wake button stays available for an offline client while the console is reachable.
- **Debug logging:** turn on debug logging for `homeassistant.components.wake_on_lan` to see each packet.
- Remote (cloud) entries work the same way: the packet is sent from Home Assistant, not through the console.

### Network actions

| Action                                | Description                                   |
| ------------------------------------- | --------------------------------------------- |
| `unifi_insights.restart_device`       | Restart a network device                      |
| `unifi_insights.authorize_guest`      | Authorize a guest client on a hotspot network |
| `unifi_insights.reconnect_client`     | Reconnect a wireless client to Wi-Fi          |
| `unifi_insights.remove_clients`       | Remove short-lived unnamed clients from sites |
| `unifi_insights.generate_voucher`     | Create one or more hotspot vouchers           |
| `unifi_insights.delete_voucher`       | Delete a hotspot voucher                      |
| `unifi_insights.power_cycle_port`     | Power cycle a switch port with PoE enabled    |
| `unifi_insights.export_network_rules` | Export the network rules of a console         |

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

The export is a snapshot of six rule types: firewall policies, policy-based routes, port forwards, traffic
rules, NAT rules and static routes. Use it to compare, version and manually recover rules. It is not a complete
console backup: the networks, firewall zones, traffic matching lists, groups and VPN definitions that rules
refer to are not included, and there is no import action.

- **Status:** the rules are read from the console when the action runs. Each rule type of each site has a
  `status`. Only `ok` is complete. `failed` means the answer could not be read, was not JSON, was incomplete
  or held a record that could not be parsed; no rules of that type are returned. `unsupported` means the
  console refused the request (403, 404 or another client error), or the site has no verified classic name,
  which the five rule types other than firewall policies need.
- **Redacted (default, `redact: true`):** keeps each rule's ID, whether it is enabled, its type, action and
  protocol, the port settings of port forwards and NAT rules, the WAN interface name of a port forward, the
  order index, the logging flag, the kill switch and matching target of a policy-based route, the connection
  state of a firewall policy, and the distance and gateway type of a static route. It replaces names,
  descriptions, addresses, domains, the source and destination of firewall policies (their ports included),
  network and client references and anything it does not recognise with `**REDACTED**`. Site IDs become
  `site-1`, `site-2`, and so on: the label is the site's rank in sorted order among the exported sites, so it
  can change when sites are added or when you export a single site. The site names and the console's title
  and entry ID are removed.
- **Unredacted (`redact: false`):** returns the fields the console sent for each rule, with the real site
  IDs, names and addresses. Store it like a secret.
- **Stable output:** `generated_at` changes on every call. For an unchanged console everything else is
  identical, because sites and rules are listed in a fixed order.

`authorize_guest` doesn't send a duration or limits yet, so the `duration_minutes` and limit fields are ignored and a warning is logged. The guest network's default access time and limits from UniFi apply.

## UniFi Protect

- **Cameras:** live view, snapshots and RTSPS streams, with switches and selects for the status light, HDR and video mode.
- **Detection:** motion, person, vehicle, animal and package binary sensors, and smart detection and doorbell event entities.
- **Camera digital inputs:** an event entity for each digital (alarm) input of a camera, created on the first `cameraDigitalInputChanged` event for that input and restored after a restart. It fires `circuit_open` and `circuit_closed`; with normally-closed wiring an open circuit can mean the input is idle, so the names describe the circuit, not what it is wired to. Needs Protect 7.2.105 or newer.
- **Lights:** floodlight on/off and brightness, and a light mode action.
- **Chimes:** volume, repeat count and ringtone, and which doorbells ring the chime.
- **Sirens:** sound a siren for 5, 10, 20 or 30 seconds, and set its volume.
- **PTZ:** move to a preset, and start or stop a patrol.
- **Sensors:** temperature, humidity, light level, battery, motion, door/window, tamper and leak.
- **NVR:** storage used, total, available and percentage, plus diagnostic Storage degraded and Storage nearly full alerts, when the existing Protect data supplies each signal. Missing health or capacity stays unavailable; no extra requests are made. The current official `/v1/nvrs` response does not expose storage, so these entities are conditional compatibility support, not guaranteed telemetry. Per-drive SMART, temperature, RAID and Drive/UNAS monitoring require supported API fields. Nearly full uses the displayed one-decimal percentage at 90% or more, matching console mounts.
- **Alarm Manager:** an alarm control panel to arm (away) and disarm the Protect alarm, when the NVR reports an arm mode.
- **Security devices:** alarm hub tamper detection, plus Thread network status for Thread gateways and read-only keypad fob settings (both need Protect 7.3.70 or newer). These devices are read-only: the Protect API only allows integrations to rename them.
- **Live views:** create live views and choose the live view on a viewer.

### Protect entities

| Entity                                                                                                 | Type                  | Notes                                                                           |
| ------------------------------------------------------------------------------------------------------ | --------------------- | ------------------------------------------------------------------------------- |
| Camera                                                                                                 | Camera                | Live view, snapshots, RTSPS stream                                              |
| Motion Detection, Person Detection, Vehicle Detection, Animal Detection, Package Detection, Microphone | Binary sensor         | Cameras (Microphone is read-only; skipped if the camera reports no mic)         |
| Ring                                                                                                   | Binary sensor         | Doorbells                                                                       |
| Doorbell, Smart Detection, Door/Window, Digital input                                                  | Event                 | Doorbell rings, smart detections, sensor open/close and digital input events    |
| Status Light, High FPS Mode                                                                            | Switch                | Camera settings                                                                 |
| HDR Mode, Video Mode, PTZ Preset                                                                       | Select                | Camera settings                                                                 |
| Microphone Volume                                                                                      | Number                | Camera microphone volume                                                        |
| Start PTZ Patrol, Stop PTZ Patrol                                                                      | Button                | PTZ cameras                                                                     |
| Floodlight                                                                                             | Light                 | On/off and brightness                                                           |
| Brightness Level                                                                                       | Number                | Floodlight brightness level                                                     |
| Play                                                                                                   | Button                | Always raises a not supported error; sends no request                           |
| Chime Volume, Repeat Times                                                                             | Number                | Chime settings                                                                  |
| Ringtone                                                                                               | Select                | Protect ringtone IDs already used by this console's chimes                      |
| Liveview                                                                                               | Select                | Live view shown on a viewer                                                     |
| Temperature, Humidity, Light, Battery                                                                  | Sensor                | Protect sensors (°C, %, lux, %)                                                 |
| Motion Detection, Door/Window Status, Tamper Detection, Leak Detection, External Leak Detection        | Binary sensor         | Protect sensors                                                                 |
| Storage Used, Storage Total, Storage Available, Storage Used Percentage                                | Sensor                | NVR storage (GB, %), when the console reports it                                |
| Tamper Detection                                                                                       | Binary sensor         | Alarm hubs, with the time and user name of the last tamper event                |
| Thread Network                                                                                         | Binary sensor         | On when a gateway's Thread network reports a problem                            |
| Thread Role, Thread Joined Devices                                                                     | Sensor                | Thread gateways, diagnostic                                                     |
| Keypad Beep, Keypad Beep Volume, Arm Control                                                           | Binary sensor, sensor | Keypad fobs, diagnostic, disabled by default                                    |
| Siren (named after the device)                                                                         | Siren                 | Protect sirens: sound for 5, 10, 20 or 30 seconds, with volume                  |
| Alarm                                                                                                  | Alarm control panel   | NVRs that report an arm mode: arm away and disarm with the selected arm profile |

> **Alarm panel and the UniFi global alarm manager:** Protect's own arm state (`armMode`) does not follow the UniFi global alarm manager. While the global alarm manager is enabled, the panel is not created, or becomes unavailable, instead of showing a state that may be wrong. Whether it is enabled is checked with a read-only request when the NVR reports an arm mode. The check is repeated about every 10 minutes, so switching the global alarm manager on or off in the UniFi app takes up to that long to show, and reloading the integration entry checks immediately. If Protect gives no answer, the check is retried every 5 minutes, and Home Assistant logs a warning after three tries in a row. If Protect refuses an arm or disarm because the global alarm manager is on, Home Assistant says so.

### Protect actions

The camera **Video Mode** dropdown uses plain-English labels. Its stored select keys and the values accepted by `unifi_insights.set_video_mode` are listed below. The `video_mode` attribute keeps the raw API value.

| Label                           | Select state / `select.select_option` | `unifi_insights.set_video_mode` value |
| ------------------------------- | ------------------------------------- | ------------------------------------- |
| Normal                          | `default`                             | `default`                             |
| High frame rate                 | `high_fps`                            | `highFps`                             |
| Fast motion                     | `sport`                               | `sport`                               |
| Low light                       | `slow_shutter`                        | `slowShutter`                         |
| License plates (reflective)     | `lpr_reflex`                          | `lprReflex`                           |
| License plates (non-reflective) | `lpr_none_reflex`                     | `lprNoneReflex`                       |

**Migration:** Update state comparisons and `select.select_option` calls that use `highFps`, `slowShutter`, `lprReflex` or `lprNoneReflex` to the select keys above. Entity IDs and `unifi_insights.set_video_mode` calls are unchanged. Recorder history retains the old values; the migration applies to future states and calls.

For example, a native select action now uses the normalized key:

```yaml
action: select.select_option
target:
  entity_id: select.my_camera_video_mode
data:
  option: lpr_none_reflex
```

A `unifi_insights.set_video_mode` action continues to use `mode: lprNoneReflex`.

| Action                                      | Description                                                                                            |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `unifi_insights.set_hdr_mode`               | Set a camera's HDR mode (`auto`, `on`, `off`)                                                          |
| `unifi_insights.set_video_mode`             | Set a camera's video mode (`default`, `highFps`, `sport`, `slowShutter`, `lprReflex`, `lprNoneReflex`) |
| `unifi_insights.set_mic_volume`             | Set a camera's microphone volume (1–100)                                                               |
| `unifi_insights.ptz_move`                   | Move a PTZ camera to a preset (0–15)                                                                   |
| `unifi_insights.ptz_patrol`                 | Start or stop a PTZ patrol                                                                             |
| `unifi_insights.set_light_mode`             | Set a light's mode (`always`, `motion`, `off`)                                                         |
| `unifi_insights.set_light_level`            | Set a light's brightness (0–100)                                                                       |
| `unifi_insights.play_chime_ringtone`        | Always raises a not supported error; sends no request                                                  |
| `unifi_insights.set_chime_volume`           | Set a chime's volume (0–100)                                                                           |
| `unifi_insights.set_chime_ringtone`         | Set an opaque Protect ringtone ID for every paired doorbell                                            |
| `unifi_insights.set_chime_repeat_times`     | Set how many times a chime repeats (1–10)                                                              |
| `unifi_insights.set_chime_paired_doorbells` | Choose which doorbells ring a chime (replaces its current pairing)                                     |
| `unifi_insights.trigger_alarm`              | Trigger an Alarm Manager webhook alarm                                                                 |
| `unifi_insights.create_liveview`            | Create a live view                                                                                     |
| `unifi_insights.set_liveview`               | Set the live view shown on a viewer                                                                    |

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
console's chimes because the official API has no ringtone catalogue or
name-to-ID mapping. The action also accepts IDs not currently in use; the console
validates them. Changing the ringtone applies to every paired doorbell while
preserving its own volume and repeat count. A chime with no paired doorbells has
no ringtone setting to change.

The **Play** button and `play_chime_ringtone` action always raise a clear
not supported error and send no request because the official Protect API has
no chime playback endpoint. The action's optional `ringtone_id` field is kept
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

1. In the Access application, open **Settings → General → Advanced → API Token**
   and create a token.
2. Grant `view:space` for door state and `edit:space` for door, gate and emergency
   controls. Add `view:device` for device discovery and doorbell notifications,
   `edit:user` for user actions, and `view:webhook` and `edit:webhook` for access-result
   events.
3. In Home Assistant, reconfigure your Local UniFi Insights entry and enter the
   **Access token** and **Access port**. The default port is **12445**. This is
   separate from the Network/Protect API key and uses the same console host and
   SSL verification setting. Use the port specified by your console's Access API
   reference; some UniFi documentation calls port **12455** “OpenAPI”, but the
   reference used for this integration documents **12445**.
4. Save. To remove Access, select **Remove the Access token** during
   reconfiguration. Leaving the token field empty keeps the stored token.

Access needs version **1.20.11+** for doorbell notifications, **1.24.6+** for lock
rules and emergency settings, and **2.2.10+** for webhooks. Gate motor commands
require **4.2.16+** and a **UA Hub Gate in three-button mode**, as documented by
current community Access SDKs. These gate contracts have not
been verified on gate hardware for this change. Only hubs reported as `UGT` with
an exact door location link get a cover; other types retain ordinary door controls.
Verify the reported hub type and linkage before automating gate operations.
Gate close and stop controls are exposed only for recognized UA Hub Gate devices.
If your token lacks permission for a feature, that feature becomes unavailable.
Door and gate controls also require device-read permission.
An expired Access token affects Access entities; replace it through reconfiguration.
If Access is temporarily unreachable during startup, polling continues and its
entities are discovered automatically when connectivity returns. Network and
Protect remain available during the outage.

### Access entities and actions

- **Door locks** read the lock relay. **Unlock buttons** are available for doors
  bound to an Access hub. The lock action sends the `reset` temporary lock rule:
  it restores the door's configured rule and does not override an active unlock
  schedule. It does not enable emergency lockdown. Access Enterprise hubs that
  do not support temporary lock rules reject this action.
  Locks also expose relay data for unbound doors; remote unlock is refused until
  a hub is bound. Unlock buttons are only created for bound doors.
- **Gate Hub covers** offer open, close and stop. Their position comes from the
  physical position sensor, separately from the lock relay. An absent or unknown
  sensor reports unknown position.
- **Lockdown** keeps doors locked; **Evacuation** keeps doors unlocked. Enabling
  either disables the other. State is read back from Access after each action.
- **Doorbell events** arrive through Access notifications. **Access granted** and
  **Access denied** events require the optional webhook below.

The following actions require a Home Assistant administrator. Supply `user_id`
from Access; when more than one Access console is configured, also supply
`console_id` using the integration entry ID or title.

| Action                                  | Description                                                                   |
| --------------------------------------- | ----------------------------------------------------------------------------- |
| `unifi_insights.set_access_user_pin`    | Assign a digits-only PIN; Access validates its configured length requirements |
| `unifi_insights.remove_access_user_pin` | Remove a user's assigned PIN                                                  |
| `unifi_insights.set_access_user_status` | Set the user account to `ACTIVE` or `DEACTIVATED`                             |

User status controls the entire user's access, rather than one credential.
Deactivation is reversible and does not delete NFC credentials.

### Access-result webhooks

Enable **Receive Access door events (granted and denied)** in the entry's options
to register a signed `access.door.unlock` webhook. The console must be able to
reach Home Assistant's internal URL. The receiver accepts local POST requests,
verifies the signature and timestamp, and rejects repeated deliveries across
entry reloads. The replay cache is held in memory and resets when Home Assistant
restarts; the signature's timestamp window still applies. Without a reachable URL,
sufficient token permissions and a returned signing secret, access-result events
remain unavailable.
Doorbell notifications continue independently.

Reloading the entry preserves its remote webhook registration. Disabling the
option or removing the entry deletes that registration when Access is reachable.
If the option is disabled while Access is offline, the receiver is retired and
the endpoint metadata is kept so a later entry reload can retry deletion.
Changing the host or port while keeping Access configured first deletes the old
webhook endpoint. If deletion fails, reconfiguration stops and retains the old
receiver and connection settings so you can retry after restoring connectivity.
If an identity conflict or save error occurs after the receiver is retired,
the integration schedules an entry reload to rebuild its webhook.
Check the old token's permissions if the console rejects deletion. If cleanup is
no longer possible, clear the Access token first, remove the remaining endpoint
in Access, then reconfigure the host or port.
If Access is offline when removing the entry or clearing its token, delete the
remaining endpoint in Access. The old receiver is retired; the integration does
not retain obsolete credentials to retry that cleanup.
Webhook registration runs during entry setup. If initial registration fails,
reload the entry after addressing the connection, URL or permission problem.
Access-result deliveries are ignored until a successful poll discovers their
door, including deliveries received during a startup outage.
If Access accepts a webhook registration but returns an unusable response,
remove the orphaned endpoint in Access before retrying setup.
Tokens, PINs, signing secrets and credential values are excluded from diagnostics
and event attributes.

Home Assistant handles service-call inputs before this integration receives them.
A PIN supplied in a script or automation can appear in its trace, and service-call
events can reach Recorder or other event listeners. Home Assistant's core debug
logging also records service inputs, so do not enable `homeassistant.core` DEBUG
when invoking PIN actions. Avoid storing a reusable PIN in automation YAML;
protect access to Home Assistant's traces, logs and database. Supply PINs as
strings, and quote them in YAML (for example, `pin: "0123"`) to preserve leading
zeros. Numeric inputs are rejected.

## UniFi InnerSpace

InnerSpace places your UniFi devices on floor plans. For each device that InnerSpace reports, the integration creates a diagnostic **InnerSpace Placement** sensor:

- State: `placed`, `unplaced` or `unknown`.
- Attributes: floor plan name and ID, position (`x`, `y`, `height`, `azimuth`, `mount`), model and status.
- The sensor is added to the matching Network or Protect device. If no device matches, it gets its own InnerSpace device.

InnerSpace works with Local and Remote entries, and data is refreshed every 5 minutes.

## UniFi Mobility

Remote entries also read [UniFi Mobility](https://unifi.ui.com) workspaces and mobile routers (UMR, UMR Industrial, UMR Ultra). Mobility is only available through the UniFi cloud, so Local entries can't use it.

### Mobility setup

Create the cloud API key at [unifi.ui.com](https://unifi.ui.com) with Mobility read access (the `mobility` scope with `read:mobility`).

- Mobility data is refreshed every 5 minutes. Accounts with many routers are polled less often, so the integration uses no more than half of the key's 100 requests per minute on average.
- A key without Mobility access is normal: the integration logs one message, creates no Mobility entities, does not ask you to re-authenticate, and checks again every hour. Accounts without routers are also checked every hour, so a new router can take up to an hour to appear (reload the entry to add it now).
- If several remote entries use the same API key, only one of them polls Mobility and creates its devices, so they are not duplicated: the oldest enabled entry when Home Assistant starts. If that entry is disabled or deleted, the next one takes over. If it is enabled but cannot load (for example, its console is offline), Mobility is not polled until it does.
- Mobility is read-only: router names, LAN and Wi-Fi settings can't be changed from Home Assistant, and the per-router client list is not read.

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
| Location                               | GPS device tracker; unknown while the router has no GPS fix         |
| Memory usage, Firmware                 | Diagnostic                                                          |
| Cellular data limit, Subscription plan | Diagnostic; the limit is unknown for unlimited plans                |
| Uptime, WAN IP address, ISP            | Diagnostic, disabled by default                                     |

A router that Mobility stops reporting becomes unavailable. You can then delete its device from the device page.

## UniFi Site Manager

Remote entries use the same cloud API key to read account-wide data from UniFi Site Manager every 10 minutes:

- Host, site and device inventory.
- Five-minute ISP metrics.
- SD-WAN configuration types.

Site Manager data does not create entities. A limited summary is included in the diagnostics report. Entries that use the same key share one account refresh. A Site Manager error does not stop the console connection, and the diagnostics report shows whether collection works.

## UniFi Carrier Fabric (ISP)

Carrier Fabric entries let ISPs monitor their UniFi Carrier Fabric subscribers and service plans, and optionally suspend or resume subscribers.

### Carrier Fabric setup

Carrier Fabric needs an ISP API key (type ISP) with the `read:subscribers` and `read:plans` scopes. To suspend or resume subscribers, the key also needs the `suspend:service` and `resume:service` scopes. See the [Carrier Fabric developer documentation](https://developer.ui.com/carrier-fabric/v1.0.0/getting-started) to create the key. Console and Site Manager API keys don't work for Carrier Fabric.

Add the integration, choose **Carrier Fabric (ISP)** and enter the ISP API key. Data is refreshed every 5 minutes.

Carrier Fabric entries have two options, both off by default:

- **Track subscribers:** creates a device for each subscriber, with a service state sensor and a service plan sensor. Each subscriber device is named after the subscriber's name, or their subscriber number when no name is set. These names are customer data that anyone with access to your Home Assistant can see.
- **Enable service actions:** allows the `carrier_suspend_subscriber` and `carrier_resume_subscriber` actions, which suspend or resume a subscriber's internet access.

### Carrier Fabric entities

By default, the integration creates:

- **Sensors:** total subscribers and suspended subscribers.
- **Diagnostic sensors:** subscribers pending assignment, provisioned, installed and unassigned; active service plans; and the number of subscribers on each service plan (archived plans are disabled by default).

When **Track subscribers** is on, each subscriber device has:

- **Sensor:** service state (`pending_assignment`, `provisioned`, `installed`, `suspended`, or `unknown` when the API reports a state that this integration does not recognize).
- **Diagnostic sensor:** service plan (the name of the subscriber's assigned service plan).

### Carrier Fabric actions

These actions need **Enable service actions** and an API key with the required scope:

- `unifi_insights.carrier_suspend_subscriber`: stops a subscriber's internet service (optional `reason`, maximum 1024 characters). The target must be a single Carrier Fabric subscriber device or entity. Needs the `suspend:service` scope.
- `unifi_insights.carrier_resume_subscriber`: restores a subscriber's internet service. The target must be a single Carrier Fabric subscriber device or entity. Needs the `resume:service` scope.

### Carrier Fabric privacy

- Personal data such as email addresses, service addresses, customer notes, custom metadata and suspension reasons is never shown in Home Assistant entities or state attributes.
- The diagnostics report contains only an allowlist of fields. Subscriber names and subscriber numbers are redacted.

The integration does not assign plans, create or change subscribers, or attach or detach hosts.

## Automation blueprints

This repository includes three automation blueprints for network health alerts. They are native Home Assistant YAML and need no external service or API. They use the entities that UniFi Insights creates, and they need Home Assistant 2026.9.0 or newer, like the integration. Each one runs the actions you choose.

| Blueprint                      | Runs your actions when                                                                                      | Needs                                                                                                                            |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Infrastructure offline alert   | A gateway, switch or access point stays offline for a set time, and optionally when it comes back           | The **Status** connectivity sensors in [Network entities](#network-entities)                                                     |
| AP overload alert              | An access point has too many wireless clients, or optionally too high a channel utilization, for a set time | The **Wireless Clients** sensors in [Network entities](#network-entities); an external utilization sensor for utilization alerts |
| Secure subnet new client alert | A new tracked client matches a secure subnet prefix                                                         | Client tracking and IP prefixes for secure subnets                                                                               |

### Install the blueprints

The Marketplace and HACS install the integration only. They don't install blueprints, so add them yourself in one of two ways:

- **Import from GitHub.** Select an **Import Blueprint** badge below, or go to **Settings** → **Automations & scenes** → **Blueprints**, select **Import blueprint** and paste the URL.
- **Copy the files.** Download the YAML files from [`blueprints/automation/unifi_insights`](https://github.com/tinkerologie/ha-unifi-insights/tree/main/blueprints/automation/unifi_insights) and put them in `<config>/blueprints/automation/unifi_insights/`. `<config>` is your Home Assistant configuration folder, the one that contains `configuration.yaml`.

| Blueprint                      | Import                                                                                                                                                                                                                                                                                                      | URL                                                                                                                              |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Infrastructure offline alert   | [![Import Blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ftinkerologie%2Fha-unifi-insights%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Funifi_insights%2Finfrastructure_offline.yaml)   | `https://github.com/tinkerologie/ha-unifi-insights/blob/main/blueprints/automation/unifi_insights/infrastructure_offline.yaml`   |
| AP overload alert              | [![Import Blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ftinkerologie%2Fha-unifi-insights%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Funifi_insights%2Fap_overload.yaml)              | `https://github.com/tinkerologie/ha-unifi-insights/blob/main/blueprints/automation/unifi_insights/ap_overload.yaml`              |
| Secure subnet new client alert | [![Import Blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ftinkerologie%2Fha-unifi-insights%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Funifi_insights%2Fsecure_subnet_new_client.yaml) | `https://github.com/tinkerologie/ha-unifi-insights/blob/main/blueprints/automation/unifi_insights/secure_subnet_new_client.yaml` |

To check the install, open the Blueprints page. It lists three blueprints named "UniFi Insights - ...". To use one, create an automation from it and fill in its inputs.

### Blueprint inputs

- **Infrastructure offline alert:** **Connectivity sensors**, **Offline delay** (default 5 minutes), **Treat unavailable as offline** (default on), **Offline actions** and, optionally, **Recovery actions**. The selected sensors are the **Status** entities in [Network entities](#network-entities). Actions can use `trigger.id` (`offline`, `unavailable` or `recovery`), `trigger.entity_id` and `trigger.to_state`.
- **AP overload alert:** **Access point client sensors**, **Client threshold** (default 30; range 1–500), **Channel utilisation sensors (optional)**, **Utilisation threshold** (default 80%; range 1–100%), **Duration** (default 5 minutes) and **Actions**. The client sensors are **Wireless Clients** entities in [Network entities](#network-entities). Actions can use `trigger.id` (`client_overload` or `utilisation`), `trigger.entity_id` and `trigger.to_state`.
- **Secure subnet new client alert:** **Secure subnet prefixes** (enter each IP prefix, for example `10.0.50.`), **Allowed trackers (optional)** from [Network entities](#network-entities) and **Actions**. Actions can use `tracker` (the device tracker's entity ID), `ip_address` and `matched_prefix`. Trackers listed under **Allowed trackers (optional)** never alert; the blueprint does not pass the MAC address to the actions as a variable.

### Blueprint prerequisites

- **Secure subnet new client alert:** turn on **Track WiFi Clients** or **Track Wired Clients** in [Options](#options). Client tracking is off by default. The blueprint reacts to a newly created device tracker, so it does nothing until tracking is on.
- **AP overload alert, channel utilization:** UniFi Insights has no channel utilization sensor. To alert on utilization, select a sensor from another source, in percent, under **Channel utilisation sensors (optional)**. Leave it empty to alert on the client count only.

### Blueprint limits

- **Run modes:** the offline and AP overload blueprints queue runs, with a maximum of 100. The secure subnet blueprint runs in parallel, with a maximum of 100 concurrent runs.
- **Offline and controller loss look the same.** A device that the controller reports as offline and a failed poll of the controller both make the **Status** sensor `unavailable`. The offline alert can't tell them apart, so one controller outage alerts for every device you selected. **Treat unavailable as offline** is on by default because an offline device reports `unavailable`. If you turn it off, you are alerted only when a sensor is `off`, and you miss offline devices.
- The previous state is judged alone, so a device that passes briefly through another state (for example `off`, a short `unavailable`, then `on`) can produce an offline alert without a recovery, or a recovery without an offline alert.
- **The subnet is inferred from the IP address.** Device trackers carry no VLAN or network name. Give each secure subnet its own IP prefix, such as `10.0.50.`. A client matches when its IP address starts with that prefix. The blueprint waits 10 seconds after tracker creation before checking its IP; a missing IP does not match.
- **A client alerts once.** Only creation of a tracker triggers the blueprint. A client that reconnects or changes address does not alert again.
- **Turning on client tracking creates a burst.** The first time you turn on **Track WiFi Clients** or **Track Wired Clients**, a tracker is created for every connected client at once. The blueprint runs at most 100 instances concurrently; additional runs are dropped and Home Assistant logs a warning. Turn tracking on first and create the automation afterwards if you do not want that burst.
- **Client counts can be stale, then unknown.** If an access point's statistics request fails, its **Wireless Clients** sensor keeps the last good count for up to three polls in a row (polls run every 30 seconds), then shows `unknown`. The AP overload alert ignores a reading that follows `unknown` or `unavailable`. An access point that is already over the threshold when its sensor recovers therefore alerts only after its count has dropped to the threshold or lower and risen above it again.
- **Cached presence stays during a controller outage.** Client trackers don't become unavailable when the controller can't be reached. They keep their last known home or away state until the integration reaches the controller again, so `person` entities don't drop to `unknown`. Automations that read tracker states see this cached state.

## Dashboard cards

The integration installs six dashboard cards. There is no separate download and no dashboard resource to add. To add a card, edit a dashboard, select **Add card** and search for its name. The cards use the integration's existing data and don't create entities.

| Card                     | Shows                                                                   |
| ------------------------ | ----------------------------------------------------------------------- |
| UniFi Topology           | Interactive network topology of a UniFi site                            |
| UniFi Site Health        | Compact site health summary with WAN, gateway, device and client status |
| UniFi Internet Activity  | Historical WAN download/upload activity and live gateway throughput     |
| UniFi Device Performance | Infrastructure CPU, memory, PoE, client load and throughput summary     |
| UniFi Protect Status     | Live UniFi Protect camera, doorbell, chime and NVR status summary       |
| UniFi Event Timeline     | Recent UniFi Protect security and device activity timeline              |

### Network topology card

The **UniFi Topology** card draws each site's network: the gateway's internet connections (WANs), the gateway, switches and access points, and the clients connected to them. With a single UniFi site the card works without any configuration. With several sites, choose the site in the card editor.

**Use it:**

- **Graph** view: drag to pan, pinch or Ctrl + scroll to zoom (plain scroll zooms in panel view), and use the zoom buttons or `+` / `-` / `0` keys. Select a device to see its uplink port, link speed, PoE draw and client counts, with a link to its Home Assistant device page.
- **List** view: the same network as an indented list with search. It is the recommended view for screen readers.
- Clients are grouped under their switch or access point ("12 clients"). Select a group to expand it.
- Each WAN the gateway reports (the same WANs as its **Connection** sensors) is drawn above the gateway with its name and state. A disconnected WAN is dimmed, marked **Disconnected** and linked with a red dashed line. WANs follow the gateway: hiding gateways hides them too.
- The filter buttons hide WANs, gateways, switches, access points, clients or other devices. Devices under a hidden one stay visible, linked with a dashed line.
- Select a device or client and use the **Actions** menu in its details, or use a context-menu gesture. See **Actions menu** below.

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

**Layout:** `tree` places every device one level below the device it connects through, and each gateway's WANs one level above it. `tiered` gives each device role its own row: WANs at the top, then gateways, switches, access points, other devices and, at the bottom, the clients. A device whose own row is at or above its parent's row, such as a switch behind an access point, goes on a row just below the parent. Empty rows take no space. With `orientation: horizontal` the rows become columns. Only the graph view uses `layout`; the list view ignores it.

### Actions menu

The **Actions** menu is for administrators and its button appears in a node's details when at least one action is available. Right-click a node, long-press it on a touch screen in browsers that send a context-menu event on a long-press, or focus it and press the ContextMenu key or Shift+F10 to select it and open the menu. Otherwise, use the **Actions** button in the node details. These gestures work in graph and list views. Client groups ("12 clients") have no menu. Non-administrators may still see the separate **Open device** link in node details when a device page is available.

| Action           | Who can use it | What it does                                                                                                            |
| ---------------- | -------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Copy IP address  | Administrators | Copies the node's IP address to the clipboard                                                                           |
| Copy MAC address | Administrators | Copies the node's MAC address to the clipboard                                                                          |
| Open device      | Administrators | Opens the node's Home Assistant device page                                                                             |
| Open entity      | Administrators | Opens the more-info dialog of a device's **Status** sensor, a WAN's **Connection** sensor, or a client's device tracker |

An action is omitted when no target is available, for example when there is no IP address or a client has no tracker (see **Track WiFi Clients** and **Track Wired Clients** in [Options](#options)).

Use the Up and Down arrow keys, Home and End to move through the menu, Enter to choose an action and Escape to close it. Escape closes only the menu and restores focus to its button. The Up and Down arrow keys on the button open the menu. If copying fails, the card shows the value as selectable text for manual copying.

**Privacy:** the topology data that every user receives contains names, models, states, ports and VLANs. MAC addresses, IP addresses, hostnames and firmware versions are never included. For an administrator, the card requests one selected node's details through the admin-only `unifi_insights/topology/node_details` command. The response includes IP and MAC addresses (for a WAN, its WAN IP address) plus Home Assistant's `device_id` and `entity_id` when available. Non-administrators cannot call the command. Hostnames and firmware versions are not sent, and the card does not keep an address after the selection changes.

## Troubleshooting

### Enable debug logging

Add the following to your `configuration.yaml` and restart Home Assistant:

```yaml
logger:
  default: info
  logs:
    custom_components.unifi_insights: debug
```

### Force a refresh

To refresh all data now, for example in an automation:

```yaml
action: unifi_insights.refresh_data
```

### Common problems

| Problem                       | Solution                                                                                                                                                                                                           |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Cannot connect                | Check that Home Assistant can reach the console URL. For self-signed certificates, turn off **Verify SSL Certificate** (reconfigure the entry to change it).                                                       |
| Authentication failed         | Check that the API key is valid and was not revoked on the console or in UniFi Site Manager.                                                                                                                       |
| Integration API not available | Self-hosted UniFi Network Application installs don't provide the official Integration API. Use a UniFi OS console.                                                                                                 |
| No Protect entities           | UniFi Protect must run on the same console, and the API key must have access to it.                                                                                                                                |
| Entities missing              | Check that the devices are adopted and online in UniFi.                                                                                                                                                            |
| Storage sensors unavailable   | The public Protect API does not report NVR storage on all firmware versions.                                                                                                                                       |
| Many orphaned client entities | Turn off the **Enable Client Control** option.                                                                                                                                                                     |
| Wake button does nothing      | Check Wake-on-LAN in the device settings, Home Assistant host networking and VLAN. See the Wake-on-LAN section for details and debug logging.                                                                      |
| Duplicate entities            | If you also use the official UniFi Network or UniFi Protect integration, both create entities for the same devices. Disable the duplicates you don't need so automations don't act on two entities for one device. |

### Diagnostics

To download a diagnostics report for troubleshooting:

1. Go to **Settings** → **Devices & services**.
2. Select **UniFi Insights**.
3. Select the three-dot menu and choose **Download diagnostics**.

The report is cleaned before it is written: API keys, passwords and Wi-Fi secrets, host names and IP addresses, SSIDs, and the names of your clients are redacted. Every MAC address is replaced with a placeholder that stays the same within one report. Device, site and camera names are kept so the report stays readable. UniFi Mobility is summarized with counts only (for example routers by state and model). No workspace or router names, addresses or locations are included.

## Contributing

Contributions are welcome. Please open an issue before you submit significant changes. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file for details.

## Disclaimer

This integration is not affiliated with or endorsed by Ubiquiti Inc. Use at your own risk.
