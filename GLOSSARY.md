# UniFi Insights

UniFi Insights connects UniFi applications to Home Assistant. This glossary
gives the meaning of the terms in the integration.

## Language

### Integration and connections

#### UniFi Insights

The Home Assistant integration that shows data from UniFi applications. It
also provides controls for UniFi equipment.

Use UniFi Network only for the Network application.

#### UniFi application

A UniFi product with its own functions and access requirements. Network,
Protect, Access, and InnerSpace are examples.

Give the name of the application when it is known.

#### Console

A UniFi system that runs applications. A Dream Machine, a Cloud Key, and a
Protect NVR are examples. A console can also route network traffic.

Use gateway for the device that sends traffic between networks.

#### Console entry

A Home Assistant config entry for a console and its selected functions. The
entry can use a local connection or a remote connection.

Use Network site for a group in the Network application.

#### Local connection

A direct connection to a console. It uses credentials that the console
accepts.

Use remote connection for access through the UniFi cloud connector.

#### Remote connection

A connection to a console through the UniFi cloud connector. It uses a cloud
API key and a console ID.

Use Site Manager for the cloud service that gives console and site data.

#### Carrier Fabric entry

A Home Assistant config entry for Carrier Fabric. It uses the credentials for
its administrative group.

Use console entry for a connection to a console.

#### Site Manager

The UniFi cloud service that finds consoles that the API key can access. It
reports site and connection data.

Use Network application for the application on a console.

#### Site Manager host

A console in Site Manager. The integration can use a local console without a
Site Manager host record.

Use hostname for the name used to find a system on the network.

#### Site Manager site

A site that Site Manager reports for a Site Manager host.

Use Network site for the site record from the Network application.

#### API client

Software that the integration uses to send requests to a UniFi application.

Use Network client for a device that uses the network.

### Network

#### Network site

An administrative group in the Network application on one console. The site ID
has meaning only on that console.

Use Site Manager site for the cloud site record.

#### Network device

Equipment that the Network application manages. Access points, switches,
gateways, and power distribution units are examples.

Use Network client for a device that uses the network. Use Home Assistant
device for a device registry entry.

#### Gateway

A Network device that sends traffic between networks. One of those networks is
usually the internet.

Use console for a system that runs UniFi applications.

#### Site gateway

The gateway that the integration uses to represent a Network site. The site
can have more than one gateway.

#### Network client

A device that uses the network. A phone or a computer is an example.

Use API client for the software that sends requests to a UniFi application.

#### Connected Network client

A Network client that the Network application reports as connected.

Use online Network device for the equipment that the Network application
manages.

#### Online Network device

A Network device with a state that shows that it has a network connection.

Use serviceable Network device for equipment that the integration can monitor
and control.

#### Serviceable Network device

A Network device that the integration can monitor and control. The device can
be in the process of applying its configuration.

Use online Network device when its state shows that it has a network
connection.

#### Available entity

A Home Assistant entity that can give a reading or accept a command. Its data
source and device must meet the conditions for that entity.

Use online Network device for the device state.

#### Wi-Fi network

A wireless network that Network clients can connect to.

Use SSID for the name of the network.

#### SSID

A service set identifier. It gives the name of a Wi-Fi network.

Use Wi-Fi network for the full network configuration.

#### WAN

A wide area network connection from a gateway. It usually connects to an
internet service provider.

Use Network site for the administrative group.

#### Uplink

A connection from a device to the network upstream of it.

Use WAN for the gateway's connection to the upstream network.

#### Port

A physical network connection on a Network device.

Use outlet for a power connection on a power distribution unit.

#### PoE

Power over Ethernet. A network port supplies power to the device through the
Ethernet cable.

Use outlet power for power from an outlet.

#### Network rule

A Network setting that controls traffic, access, routing, or name resolution.

Use firewall rule only for a rule that controls traffic.

#### Rule-change event

An addition, change, or removal of a Network rule that the integration finds
when it compares two polls. The event can show only changes that the integration can
see in the poll results.

#### Classic API

An older Network API. The integration uses it for functions that the Network
Integration API does not supply.

Use Network Integration API for the interface with versions.

### Protect

#### NVR

A network video recorder. It records video and manages Protect devices. An NVR
can be separate equipment or part of a console.

Use camera for the equipment that captures video.

#### Smart detection

A type of activity that a Protect camera identifies. A person, an animal, or a
vehicle can cause a smart detection.

Use motion detection when the camera reports movement without a specific type.

#### Protect doorbell ring

A doorbell ring that a Protect camera reports.

Use Access doorbell ring for events from UniFi Access.

### Access

#### Access door

A door or gate that UniFi Access controls. UniFi Access can report its
position.

Use hub or reader for the equipment that controls entry.

#### Lock relay

The control that locks or unlocks an Access door.

An unlocked door or gate can stay closed.

#### Door or gate position

The physical position that UniFi Access reports for a door or gate. The
position can be open or closed.

Use lock relay state for the locked or unlocked state.

#### Access doorbell ring

A doorbell ring that UniFi Access reports for a door or gate.

Use Protect doorbell ring for events from a Protect camera.

#### Access result event

An event that shows if UniFi Access gave or refused access at a door or gate.

Access permission alone does not show that the door is open.

### InnerSpace

#### Floor plan

A drawing of a physical space in InnerSpace. InnerSpace can show the positions
of UniFi products on this drawing.

Use Network site for the group in the Network application.

#### InnerSpace placement

The relation between a UniFi product and a floor plan. The product can be
placed, unplaced, or have an unknown position.

An unknown position gives no information about whether the product is placed.

### Mobility

#### Mobility workspace

An administrative group for mobile routers and their administrators in UniFi
Mobility.

Use Carrier Fabric organization for the administrative group in Carrier
Fabric.

#### Mobile router

A router that UniFi Mobility manages. Mobility reports the router's
connections and cellular service.

Use gateway for a Network device that sends traffic between networks.

### Carrier Fabric

#### Carrier Fabric organization

An administrative group for subscribers and service plans in Carrier Fabric.

A fabric is a separate resource in Carrier Fabric.

#### Subscriber

A customer or service subscription with a subscriber ID in Carrier Fabric.

Use Network client for a device that uses the network.

#### Service plan

A Carrier Fabric service that an administrator can give to a subscriber.

Use subscriber for the customer or service subscription.

#### Unassigned subscriber

A subscriber without a service plan.

Pending assignment and suspended are different subscriber states.

### Home Assistant representation

#### Entity

A Home Assistant object for one reading, event stream, or control.

Use Home Assistant device for a group of related entities.

#### Home Assistant device

A device registry entry that groups related entities. The entry can represent
equipment or an object such as a site or subscriber.

#### UniFi Site device

The Home Assistant device that groups entities for a Network site.

Use site gateway for the gateway that represents a Network site.

#### Device tracker

A Home Assistant entity that shows if a Network client is present.

Some device trackers have a separate Home Assistant device.

#### Event entity

A Home Assistant entity that reports events. A detection, a doorbell ring, and
a rule change are examples.

Use sensor for an entity that gives a measured value.

#### Action

A Home Assistant operation that reads or changes UniFi data. A device restart
and a request for vouchers are examples.

Use event for an occurrence that the integration reports.
