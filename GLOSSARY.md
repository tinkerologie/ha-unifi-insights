# UniFi Insights

UniFi Insights connects UniFi applications to Home Assistant for monitoring and
control. This glossary defines the product concepts used in that integration.

## Language

### Integration and connections

**UniFi Insights**:
The Home Assistant integration that represents UniFi infrastructure, activity,
and controls.
_Avoid_: UniFi Network integration when referring to all supported applications.

**UniFi application**:
A UniFi product with its own capabilities and access requirements, such as
Network, Protect, Access, or InnerSpace.
_Avoid_: domain without a qualifier.

**Console**:
A UniFi system that hosts applications, such as a Dream Machine, Cloud Key,
or Protect NVR. Hosting applications and routing network traffic are distinct
roles, even when one system performs both.
_Avoid_: controller or gateway as a synonym for every console.

**Console entry**:
A saved Home Assistant connection to a console and its selected capabilities.
It can use a local connection or a remote connection.
_Avoid_: site or application as a synonym for the connection.

**Local connection**:
A console connection made directly to the console using its local credentials.
_Avoid_: cloud connection.

**Remote connection**:
A console connection made through UniFi's cloud connector using a cloud key
and a console identity.
_Avoid_: Site Manager as a synonym for every remote operation.

**Carrier Fabric entry**:
A saved Home Assistant connection to Carrier Fabric using credentials for its
administrative scope, separate from a console entry.
_Avoid_: console entry or Network site.

**Site Manager**:
UniFi's cloud service for discovering accessible consoles and reporting cloud
site and connectivity information.
_Avoid_: Network application or Network site.

**Site Manager host**:
A console represented in Site Manager. A local console does not need a Site
Manager host record to be used by the integration.
_Avoid_: Network client, hostname, or physical location.

**Site Manager site**:
A site reported by Site Manager in association with a cloud host.
_Avoid_: treating its identity as interchangeable with a Network site identity.

**API client**:
Software that communicates with a UniFi application's interface on behalf of
the integration.
_Avoid_: client without a qualifier.

### Network

**Network site**:
An administrative grouping within a console's Network application. Its identity
is scoped to that console rather than being a unique physical location.
_Avoid_: console, VLAN, Site Manager site, or location as a synonym.

**Network device**:
Managed Network infrastructure, such as an access point, switch, gateway, or
power distribution unit.
_Avoid_: Network client or Home Assistant device as an interchangeable term.

**Gateway**:
A Network device that routes traffic between networks, commonly including the
internet connection.
_Avoid_: console when only the routing role is meant.

**Site gateway**:
The gateway associated with a Network site for the integration's site-level
representation. This does not mean the site can contain only one gateway.
_Avoid_: console or sole gateway.

**Network client**:
An endpoint using the network, such as a phone or computer, rather than the
infrastructure managing it.
_Avoid_: client without a qualifier, API client, or Network device.

**Connected Network client**:
A Network client currently reported as connected by the Network application.
_Avoid_: online Network device when describing client presence.

**Online Network device**:
A Network device whose reported state confirms network connectivity.
_Avoid_: serviceable or available as an interchangeable state.

**Serviceable Network device**:
A Network device eligible for its integration controls and readings, including
a managed device applying configuration before connectivity is confirmed again.
_Avoid_: online as a synonym.

**Available entity**:
A Home Assistant entity whose source is healthy and whose product-specific
conditions allow its current reading or control to be used.
_Avoid_: online without naming the device or source being described.

**Wi-Fi network**:
A configured wireless network through which Network clients can connect.
_Avoid_: VLAN or SSID when the complete configuration is meant.

**SSID**:
The name identifying a Wi-Fi network to wireless clients.
_Avoid_: Network site or the entire Wi-Fi configuration.

**WAN**:
A gateway's connection to an upstream network, commonly an internet provider.
_Avoid_: the whole Network site.

**Uplink**:
The connection from a device or client toward its upstream network attachment.
_Avoid_: WAN when the attachment is another local device.

**Port**:
A physical network interface on managed infrastructure.
_Avoid_: outlet when referring to an Ethernet interface.

**PoE**:
Power over Ethernet supplied through a network port to an attached device.
_Avoid_: outlet power as a synonym.

**Network rule**:
A Network configuration governing traffic, access, routing, or name resolution.
_Avoid_: firewall rule when referring to all supported rule families.

**Rule-change event**:
An observed addition, modification, or removal of a Network rule between
successive observations.
_Avoid_: a record of every action performed on the console.

**Classic API**:
The older Network controller interface used for supported information and
operations that the Network Integration API does not supply.
_Avoid_: Network Integration API as an interchangeable name.

### Protect

**NVR**:
The system hosting Protect's video recording and device management, whether
dedicated hardware or part of a console with other roles.
_Avoid_: camera or Network gateway as a universal synonym.

**Smart detection**:
A Protect camera's classification of activity, such as a person, animal, or
vehicle detection.
_Avoid_: motion detection when a specific classification is meant.

**Protect doorbell ring**:
A ring reported by a Protect doorbell camera.
_Avoid_: Access doorbell ring; the applications report separate events.

### Access

**Access door**:
A managed entry point in UniFi Access, with access control and optional
position information.
_Avoid_: hub or reader as a synonym for the entry point.

**Lock relay**:
The Access door control whose locked or unlocked state governs the lock.
_Avoid_: door or gate position; an unlocked entry point can remain closed.

**Door or gate position**:
The physical open or closed position reported for an Access entry point.
_Avoid_: lock relay state.

**Access doorbell ring**:
A ring reported by UniFi Access for an entry point.
_Avoid_: Protect doorbell ring.

**Access result event**:
An access grant or denial reported by UniFi Access for an entry point.
_Avoid_: treating a grant as confirmation that the door physically opened.

### InnerSpace

**Floor plan**:
An InnerSpace representation of a physical space on which UniFi products can
be located.
_Avoid_: Network site or Home Assistant area as an interchangeable grouping.

**InnerSpace placement**:
The relationship between a UniFi product and a floor plan. A placement is
placed, unplaced, or unknown when the available information cannot decide.
_Avoid_: treating an unknown placement as confirmed unplaced.

### Mobility

**Mobility workspace**:
A UniFi Mobility administrative grouping for mobile routers and their
administrators.
_Avoid_: Network site or Carrier Fabric organization.

**Mobile router**:
A router managed by UniFi Mobility, with its own connectivity and cellular
service information.
_Avoid_: Network client or treating every Network gateway as a Mobility router.

### Carrier Fabric

**Carrier Fabric organization**:
The administrative scope for Carrier Fabric subscribers and service plans.
_Avoid_: Network site or fabric as an interchangeable name.

**Subscriber**:
A Carrier Fabric customer or service subscription represented by its own
subscriber identity.
_Avoid_: device, Network client, or Network site.

**Service plan**:
A Carrier Fabric service offering that can be assigned to subscribers.
_Avoid_: subscriber or floor plan.

**Unassigned subscriber**:
A subscriber without a service-plan assignment.
_Avoid_: pending assignment or suspended as a synonym; those are separate states.

### Home Assistant representation

**Entity**:
A Home Assistant representation of one reading, event stream, or control
exposed by the integration.
_Avoid_: device when referring to an individual reading or control.

**Home Assistant device**:
A registry grouping for related entities, representing either equipment or a
logical object such as a site or subscriber.
_Avoid_: physical hardware as a universal interpretation.

**UniFi Site device**:
The logical Home Assistant device grouping the integration's entities for a
Network site.
_Avoid_: gateway or physical console.

**Device tracker**:
A Home Assistant entity representing a Network client's reported presence.
_Avoid_: assuming every tracker has a separate Home Assistant device.

**Event entity**:
A Home Assistant entity representing discrete occurrences such as a detection,
doorbell ring, or rule change.
_Avoid_: a continuously measured sensor value.

**Action**:
A Home Assistant operation exposed by UniFi Insights to read or change UniFi
state, such as restarting a device or generating vouchers.
_Avoid_: event when referring to an operation being requested.
