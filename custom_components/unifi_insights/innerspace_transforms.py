# Copyright 2026 UniFi Insights contributors
"""Data transformation and MAC correlation helpers for UniFi InnerSpace."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, Final
from urllib.parse import unquote, urlsplit, urlunsplit

from unifi_official_api.innerspace.client import is_valid_asset_segment

_MAC_HEX_LEN = 12

# The Protect collections correlated with InnerSpace records, with the device
# type each match reports.
PROTECT_CORRELATION_COLLECTIONS: Final = (
    ("cameras", "camera"),
    ("lights", "light"),
    ("sensors", "sensor"),
    ("nvrs", "nvr"),
    ("viewers", "viewer"),
    ("chimes", "chime"),
    ("doorlocks", "doorlock"),
    ("viewports", "viewport"),
)


def _to_mapping(record: Any) -> dict[str, Any]:
    """Convert a Pydantic model or mapping to a plain dictionary."""
    if isinstance(record, dict):
        return dict(record)
    if hasattr(record, "model_dump"):
        dumped = record.model_dump(by_alias=False, exclude_none=False)
        return dict(dumped) if isinstance(dumped, dict) else {}
    return {}


_VALID_HEX = frozenset("0123456789abcdef")


def _normalize_innerspace_mac(mac: Any) -> str | None:
    """
    Normalize a MAC address to lowercase colon-separated form (aa:bb:cc:dd:ee:ff).

    Identity policy: InnerSpace correlation. Unlike the canonical
    ``data_transforms.normalize_mac`` it also accepts dotted (``aabb.ccdd.eeff``)
    and mixed separators, because InnerSpace records spell MACs that way.
    """
    if not isinstance(mac, str):
        return None
    cleaned = mac.strip().lower().replace("-", "").replace(":", "").replace(".", "")
    if len(cleaned) != _MAC_HEX_LEN or not set(cleaned).issubset(_VALID_HEX):
        return None
    return (
        f"{cleaned[0:2]}:{cleaned[2:4]}:{cleaned[4:6]}:"
        f"{cleaned[6:8]}:{cleaned[8:10]}:{cleaned[10:12]}"
    )


def _valid_number(value: Any) -> float | int | None:
    """Return value if it is an int or float (excluding bool), else None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def _valid_int(value: Any) -> int | None:
    """Return value as int if it is an integer (excluding bool), else None."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def transform_innerspace_project(project_data: Any) -> dict[str, Any] | None:
    """
    Normalize a UniFi InnerSpace project response.

    Preserves project identity and plan metadata (including siteId), while
    excluding raw shapes, image downloads, and image URLs.
    """
    raw = _to_mapping(project_data)
    if not raw:
        return None

    if "data" in raw and isinstance(raw["data"], dict):
        raw = _to_mapping(raw["data"])

    project_obj = _to_mapping(raw.get("project")) if "project" in raw else raw
    project_id = project_obj.get("id") if isinstance(project_obj, dict) else None
    project_id = (
        project_id if isinstance(project_id, str) and project_id.strip() else None
    )

    raw_plans = raw.get("plans")
    plans: list[dict[str, Any]] = []
    if isinstance(raw_plans, list):
        for plan_item in raw_plans:
            plan_dict = _to_mapping(plan_item)
            plan_id = plan_dict.get("id")
            if not isinstance(plan_id, str) or not plan_id:
                continue
            plans.append(
                {
                    "id": plan_id,
                    "name": plan_dict.get("name") or plan_dict.get("title"),
                    "ppm": _valid_number(plan_dict.get("ppm")),
                    "project_id": (
                        plan_dict.get("project_id")
                        or plan_dict.get("projectId")
                        or project_id
                    ),
                    "site_id": plan_dict.get("site_id") or plan_dict.get("siteId"),
                    "ordering": _valid_number(plan_dict.get("ordering")),
                    "attenuation": _valid_number(plan_dict.get("attenuation")),
                    "created_at": (
                        plan_dict.get("created_at") or plan_dict.get("createdAt")
                    ),
                    "updated_at": (
                        plan_dict.get("updated_at") or plan_dict.get("updatedAt")
                    ),
                }
            )

    raw_products = raw.get("products")
    products: list[dict[str, Any]] = []
    if isinstance(raw_products, list):
        for prod_item in raw_products:
            prod_dict = _to_mapping(prod_item)
            prod_id = prod_dict.get("id")
            if not isinstance(prod_id, str) or not prod_id:
                continue
            products.append(
                {
                    "id": prod_id,
                    "sku": prod_dict.get("sku"),
                }
            )

    if not project_id and not plans and not products:
        return None

    wall_types = raw.get("wall_types") or raw.get("wallTypes")
    attenuation_types = raw.get("attenuation_object_types") or raw.get(
        "attenuationObjectTypes"
    )

    return {
        "id": project_id,
        "title": (
            project_obj.get("title") or project_obj.get("name") or raw.get("title")
        ),
        "model": project_obj.get("model")
        if isinstance(project_obj, dict)
        else raw.get("model"),
        "environment": project_obj.get("environment")
        if isinstance(project_obj, dict)
        else raw.get("environment"),
        "plans": plans,
        "products": products,
        "plan_count": len(plans),
        "product_count": len(products),
        "wall_type_count": len(wall_types) if isinstance(wall_types, list) else 0,
        "attenuation_type_count": (
            len(attenuation_types) if isinstance(attenuation_types, list) else 0
        ),
    }


_MIN_ASSET_PATH_PARTS = 4


def strip_url_query(url: str | None) -> str | None:
    """
    Return `url` without its query string, fragment, and userinfo.

    Floor plan image URLs are shown in entity attributes and logs, where a
    signed or token-bearing query must not appear. Scheme, host, and path are
    kept; only the parts that can carry credentials are dropped.
    """
    if not isinstance(url, str) or not url:
        return None
    parts = urlsplit(url)
    host = parts.netloc.rpartition("@")[2]
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def parse_floor_plan_asset_path(
    image_url: str | None,
) -> tuple[str, str] | None:
    """
    Parse the floor plan ID and asset filename from an image URL.

    Accepts only paths ending in `/assets/<plan_id>/<filename>` (ignoring
    scheme, host, query string, and fragment). Each segment is percent-decoded
    (the URL path carries names such as `floor%20plan.png` encoded) and must
    then pass `is_valid_asset_segment`.

    Returns:
        Tuple of the decoded (plan_id, filename) if valid, or None.

    """
    if not isinstance(image_url, str) or not image_url.strip():
        return None

    path = urlsplit(image_url).path
    if not path:
        return None

    parts = path.split("/")
    if len(parts) < _MIN_ASSET_PATH_PARTS or parts[-3] != "assets":
        return None

    plan_id = unquote(parts[-2])
    filename = unquote(parts[-1])

    if not is_valid_asset_segment(plan_id) or not is_valid_asset_segment(filename):
        return None

    return plan_id, filename


def transform_innerspace_floor_plan(
    floor_plan_data: Any,
    *,
    project_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Normalize a UniFi InnerSpace floor plan record.

    Merges project plan metadata (such as `siteId`, `ordering`, `attenuation`,
    `projectId`) and keeps the plan's own asset `image_url`. Project plans carry
    no image, so `project_plan` never supplies one.
    """
    raw = _to_mapping(floor_plan_data)
    proj = project_plan if isinstance(project_plan, dict) else {}

    plan_id = raw.get("id") or proj.get("id")
    site_id = (
        raw.get("site_id")
        or raw.get("siteId")
        or proj.get("site_id")
        or proj.get("siteId")
    )
    project_id = (
        raw.get("project_id")
        or raw.get("projectId")
        or proj.get("project_id")
        or proj.get("projectId")
    )

    return {
        "id": str(plan_id) if plan_id is not None else None,
        "name": (
            raw.get("name") or raw.get("title") or proj.get("name") or proj.get("title")
        ),
        "floor_number": _valid_int(raw.get("floor_number") or raw.get("floorNumber")),
        "site_id": site_id if isinstance(site_id, str) and site_id else None,
        "image_url": raw.get("image_url") or raw.get("imageUrl"),
        "project_id": (
            project_id if isinstance(project_id, str) and project_id else None
        ),
        "ppm": _valid_number(raw.get("ppm") if "ppm" in raw else proj.get("ppm")),
        "width": _valid_int(raw.get("width")),
        "height": _valid_int(raw.get("height")),
        "origin_x": _valid_number(raw.get("origin_x") or raw.get("originX")),
        "origin_y": _valid_number(raw.get("origin_y") or raw.get("originY")),
        "ordering": _valid_number(
            raw.get("ordering") if "ordering" in raw else proj.get("ordering")
        ),
        "attenuation": _valid_number(
            raw.get("attenuation") if "attenuation" in raw else proj.get("attenuation")
        ),
    }


def transform_innerspace_device(
    device_data: Any,
    *,
    device_kind: str | None = None,
    placed: bool = False,
    floor_plans: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Normalize an InnerSpace placed device or unplaced inventory record.

    For `/v1/inventory` (`placed=False`, `device_kind=None`), `device_type` is
    never inferred from `model` and defaults to `None` unless explicitly
    provided by the API payload.
    """
    raw = _to_mapping(device_data)
    record_id = raw.get("id")
    record_id = (
        str(record_id).strip()
        if isinstance(record_id, str) and record_id.strip()
        else None
    )

    raw_mac = raw.get("mac") or raw.get("macAddress") or raw.get("mac_address")
    norm_mac = _normalize_innerspace_mac(raw_mac)
    raw_status = raw.get("status")
    status_str = (
        raw_status.strip().lower()
        if isinstance(raw_status, str) and raw_status.strip()
        else None
    )

    if device_kind in ("access_point", "switch"):
        device_type: str | None = device_kind
    else:
        explicit_type = raw.get("device_type") or raw.get("deviceType")
        device_type = (
            explicit_type
            if isinstance(explicit_type, str) and explicit_type.strip()
            else None
        )

    floor_plan_id = raw.get("floor_plan_id") or raw.get("floorPlanId")
    floor_plan_id = (
        floor_plan_id
        if isinstance(floor_plan_id, str) and floor_plan_id.strip()
        else None
    )
    x_coord = _valid_number(raw.get("x"))
    y_coord = _valid_number(raw.get("y"))

    floor_plan_name: str | None = None
    site_id = raw.get("site_id") or raw.get("siteId")
    if floor_plan_id and floor_plans and floor_plan_id in floor_plans:
        plan_info = floor_plans[floor_plan_id]
        floor_plan_name = plan_info.get("name")
        if not site_id:
            site_id = plan_info.get("site_id")

    if placed:
        has_valid_plan = floor_plan_id is not None and (
            not floor_plans or floor_plan_id in floor_plans
        )
        if has_valid_plan and x_coord is not None and y_coord is not None:
            placement_state = "placed"
        else:
            placement_state = "unknown"
    elif (
        isinstance(record_id, str)
        and record_id
        and floor_plan_id is None
        and x_coord is None
        and y_coord is None
    ):
        placement_state = "unplaced"
    else:
        placement_state = "unknown"

    return {
        "id": record_id,
        "name": raw.get("name"),
        "model": raw.get("model"),
        "device_type": device_type,
        "mac": norm_mac or (raw_mac if isinstance(raw_mac, str) and raw_mac else None),
        "normalized_mac": norm_mac,
        "serial": raw.get("serial"),
        "status": status_str,
        "placed": placement_state == "placed",
        "placement_state": placement_state,
        "floor_plan_id": floor_plan_id,
        "floor_plan_name": floor_plan_name,
        "site_id": site_id if isinstance(site_id, str) and site_id else None,
        "x": x_coord,
        "y": y_coord,
        "height": _valid_number(raw.get("height")),
        "azimuth": _valid_number(raw.get("azimuth")),
        "mount": raw.get("mount") if isinstance(raw.get("mount"), str) else None,
        "correlation": None,
    }


def _index_network_devices_by_mac(
    net_by_site: dict[str, Any],
) -> tuple[
    dict[str, dict[str, list[dict[str, Any]]]],
    dict[str, list[dict[str, Any]]],
]:
    """Index Network devices by site_id and normalized MAC address."""
    net_index_by_site: dict[str, dict[str, list[dict[str, Any]]]] = {}
    net_index_all: dict[str, list[dict[str, Any]]] = {}
    for site_id, site_devices in net_by_site.items():
        if not isinstance(site_devices, dict):
            continue
        site_map: dict[str, list[dict[str, Any]]] = {}
        for dev_id, dev_data in site_devices.items():
            if not isinstance(dev_data, dict):
                continue
            raw_mac = (
                dev_data.get("macAddress")
                or dev_data.get("mac_address")
                or dev_data.get("mac")
            )
            norm = _normalize_innerspace_mac(raw_mac)
            if not norm:
                continue
            candidate = {
                "application": "network",
                "device_id": str(dev_data.get("id") or dev_id),
                "site_id": str(site_id),
                "device_type": dev_data.get("type") or dev_data.get("model"),
                "mac": norm,
            }
            site_map.setdefault(norm, []).append(candidate)
            net_index_all.setdefault(norm, []).append(candidate)
        net_index_by_site[str(site_id)] = site_map
    return net_index_by_site, net_index_all


def _index_protect_devices_by_mac(
    prot: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Index Protect devices across all collections by normalized MAC address."""
    prot_index_all: dict[str, list[dict[str, Any]]] = {}
    for collection_key, dtype in PROTECT_CORRELATION_COLLECTIONS:
        col = prot.get(collection_key)
        if not isinstance(col, dict):
            continue
        for dev_id, dev_data in col.items():
            if not isinstance(dev_data, dict):
                continue
            raw_mac = (
                dev_data.get("mac")
                or dev_data.get("macAddress")
                or dev_data.get("mac_address")
            )
            norm = _normalize_innerspace_mac(raw_mac)
            if not norm:
                continue
            prot_index_all.setdefault(norm, []).append(
                {
                    "application": "protect",
                    "device_id": str(dev_data.get("id") or dev_id),
                    "site_id": None,
                    "device_type": dtype,
                    "mac": norm,
                }
            )
    return prot_index_all


def correlate_innerspace_devices(
    placed_or_snapshot: dict[str, Any],
    inventory_devices: dict[str, dict[str, Any]] | None = None,
    *,
    network_devices_by_site: dict[str, Any] | None = None,
    network_devices: dict[str, Any] | None = None,
    protect_data: dict[str, Any] | None = None,
    protect_devices: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any] | None]:
    """
    Correlate InnerSpace records with Network and Protect devices by normalized MAC.

    Rules:
    - Correlate only when a normalized MAC identifies exactly one device unambiguously.
    - If a floor-plan (or record) `site_id` is available and matches a polled site in
      `network_devices_by_site`, narrow Network candidate matching to that site.
    - Store correlations as metadata (`record["correlation"]` and `matched_*` keys)
      without altering existing Network or Protect unique IDs, device identifiers,
      or areas.
    """
    raw_net = (
        network_devices_by_site
        if network_devices_by_site is not None
        else network_devices
    )
    raw_prot = protect_data if protect_data is not None else protect_devices
    net_by_site = raw_net if isinstance(raw_net, dict) else {}
    prot = raw_prot if isinstance(raw_prot, dict) else {}

    net_index_by_site, net_index_all = _index_network_devices_by_mac(net_by_site)
    prot_index_all = _index_protect_devices_by_mac(prot)

    collections_to_update: list[dict[str, Any]] = []
    if inventory_devices is None and (
        "access_points" in placed_or_snapshot
        or "switches" in placed_or_snapshot
        or "inventory" in placed_or_snapshot
        or "devices" in placed_or_snapshot
    ):
        for section_name in ("access_points", "switches", "inventory", "devices"):
            sec = placed_or_snapshot.get(section_name)
            if isinstance(sec, dict):
                collections_to_update.append(sec)
    else:
        collections_to_update.append(placed_or_snapshot)
        if isinstance(inventory_devices, dict):
            collections_to_update.append(inventory_devices)

    correlations: dict[str, dict[str, Any] | None] = {}
    for collection in collections_to_update:
        for record_id, record in collection.items():
            if not isinstance(record, dict):
                continue
            norm_mac = record.get("normalized_mac") or _normalize_innerspace_mac(
                record.get("mac")
            )
            if not norm_mac:
                record["correlation"] = None
                record["matched_domain"] = None
                record["matched_site_id"] = None
                record["matched_device_id"] = None
                record["matched_protect_type"] = None
                correlations[record_id] = None
                continue

            record_site_id = record.get("site_id")
            if (
                isinstance(record_site_id, str)
                and record_site_id
                and record_site_id in net_index_by_site
            ):
                net_candidates = net_index_by_site[record_site_id].get(norm_mac, [])
            else:
                net_candidates = net_index_all.get(norm_mac, [])

            prot_candidates = prot_index_all.get(norm_mac, [])
            all_candidates = [*net_candidates, *prot_candidates]

            if len(all_candidates) == 1:
                match = dict(all_candidates[0])
                record["correlation"] = match
                record["matched_domain"] = match["application"]
                record["matched_site_id"] = match["site_id"]
                record["matched_device_id"] = match["device_id"]
                record["matched_protect_type"] = (
                    match["device_type"] if match["application"] == "protect" else None
                )
                correlations[record_id] = match
            else:
                record["correlation"] = None
                record["matched_domain"] = None
                record["matched_site_id"] = None
                record["matched_device_id"] = None
                record["matched_protect_type"] = None
                correlations[record_id] = None

    return correlations


def innerspace_view_inputs(
    raw: Mapping[str, Any] | None, network_devices: Any, protect_data: Any
) -> tuple[object, ...]:
    """
    Return the objects ``build_innerspace_view`` reads, for an identity memo.

    The owners publish copy-on-write, so the same objects mean the same view.
    Without InnerSpace data the view reads nothing else.
    """
    if not raw:
        return (raw,)
    collections = (
        tuple(
            protect_data.get(collection)
            for collection, _ in PROTECT_CORRELATION_COLLECTIONS
        )
        if isinstance(protect_data, Mapping)
        else (protect_data,)
    )
    return (raw, network_devices, *collections)


def innerspace_correlation_key(
    network_devices: Any, protect_data: Any
) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    """
    Return every device field the MAC correlation reads, as a comparable key.

    Two calls that return equal keys correlate the same InnerSpace records to
    the same Network and Protect devices.
    """
    network: tuple[Any, ...] = ()
    if isinstance(network_devices, dict):
        network = tuple(
            (
                site_id,
                dev_id,
                dev.get("macAddress") or dev.get("mac_address") or dev.get("mac"),
                dev.get("id"),
                dev.get("type") or dev.get("model"),
            )
            for site_id, site_devices in network_devices.items()
            if isinstance(site_devices, dict)
            for dev_id, dev in site_devices.items()
            if isinstance(dev, dict)
        )
    protect: tuple[Any, ...] = ()
    if isinstance(protect_data, dict):
        protect = tuple(
            (
                collection,
                dev_id,
                dev.get("mac") or dev.get("macAddress") or dev.get("mac_address"),
                dev.get("id"),
            )
            for collection, _ in PROTECT_CORRELATION_COLLECTIONS
            if isinstance(devices := protect_data.get(collection), dict)
            for dev_id, dev in devices.items()
            if isinstance(dev, dict)
        )
    return network, protect


def build_innerspace_view(
    raw: Mapping[str, Any] | None, network_devices: Any, protect_data: Any
) -> dict[str, Any]:
    """
    Build the InnerSpace view the facade publishes, correlated by MAC address.

    ``raw`` is the InnerSpace coordinator's data, ``network_devices`` the
    device coordinator's ``devices`` section and ``protect_data`` the Protect
    data. The inputs are never changed: each placed and inventory record is
    copied before ``correlate_innerspace_devices`` writes its match into it.
    """
    if not raw:
        return {**normalize_innerspace_snapshot(), "last_update": None}
    access_points = {
        str(rec_id): dict(rec)
        for rec_id, rec in raw.get("access_points", {}).items()
        if isinstance(rec, dict)
    }
    switches = {
        str(rec_id): dict(rec)
        for rec_id, rec in raw.get("switches", {}).items()
        if isinstance(rec, dict)
    }
    inventory = {
        str(rec_id): dict(rec)
        for rec_id, rec in raw.get("inventory", {}).items()
        if isinstance(rec, dict)
    }
    view: dict[str, Any] = {
        "project": raw.get("project"),
        "floor_plans": dict(raw.get("floor_plans", {})),
        "access_points": access_points,
        "switches": switches,
        "inventory": inventory,
        "placed_devices": {**access_points, **switches},
        "devices": {**access_points, **switches, **inventory},
        "last_update": raw.get("last_update"),
    }
    view["correlations"] = correlate_innerspace_devices(
        view,
        network_devices=network_devices if isinstance(network_devices, dict) else None,
        protect_devices=protect_data if isinstance(protect_data, dict) else None,
    )
    return view


def normalize_innerspace_snapshot(
    *,
    project: Any = None,
    floor_plans: list[Any] | None = None,
    access_points: list[Any] | None = None,
    switches: list[Any] | None = None,
    inventory: list[Any] | None = None,
    network_devices_by_site: dict[str, Any] | None = None,
    network_devices: dict[str, Any] | None = None,
    protect_data: dict[str, Any] | None = None,
    protect_devices: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build the normalized InnerSpace coordinator snapshot.

    Keeps unplaced inventory distinct from placed access points and switches,
    keeps floor plan image URLs (but not raw shapes), and computes unambiguous
    MAC correlations.
    """
    normalized_project = transform_innerspace_project(project)
    project_plans_by_id: dict[str, dict[str, Any]] = {}
    if normalized_project and isinstance(normalized_project.get("plans"), list):
        for p_plan in normalized_project["plans"]:
            project_plans_by_id[p_plan["id"]] = p_plan

    floor_plans_dict: dict[str, dict[str, Any]] = {}
    for fp_item in floor_plans or []:
        fp_raw = _to_mapping(fp_item)
        fp_id = fp_raw.get("id")
        if not isinstance(fp_id, str) or not fp_id:
            continue
        floor_plans_dict[fp_id] = transform_innerspace_floor_plan(
            fp_raw,
            project_plan=project_plans_by_id.get(fp_id),
        )

    for fp_id, proj_plan in project_plans_by_id.items():
        if fp_id not in floor_plans_dict:
            floor_plans_dict[fp_id] = transform_innerspace_floor_plan(
                proj_plan,
                project_plan=proj_plan,
            )

    access_points_dict: dict[str, dict[str, Any]] = {}
    placed_devices_dict: dict[str, dict[str, Any]] = {}
    for ap_item in access_points or []:
        ap_norm = transform_innerspace_device(
            ap_item,
            device_kind="access_point",
            placed=True,
            floor_plans=floor_plans_dict,
        )
        ap_id = ap_norm.get("id")
        if isinstance(ap_id, str) and ap_id:
            access_points_dict[ap_id] = ap_norm
            placed_devices_dict[ap_id] = ap_norm

    switches_dict: dict[str, dict[str, Any]] = {}
    for sw_item in switches or []:
        sw_norm = transform_innerspace_device(
            sw_item,
            device_kind="switch",
            placed=True,
            floor_plans=floor_plans_dict,
        )
        sw_id = sw_norm.get("id")
        if isinstance(sw_id, str) and sw_id:
            switches_dict[sw_id] = sw_norm
            placed_devices_dict[sw_id] = sw_norm

    inventory_dict: dict[str, dict[str, Any]] = {}
    for inv_item in inventory or []:
        inv_norm = transform_innerspace_device(
            inv_item,
            device_kind=None,
            placed=False,
            floor_plans=floor_plans_dict,
        )
        inv_id = inv_norm.get("id")
        if isinstance(inv_id, str) and inv_id and inv_id not in placed_devices_dict:
            inventory_dict[inv_id] = inv_norm

    correlations = correlate_innerspace_devices(
        placed_devices_dict,
        inventory_dict,
        network_devices_by_site=network_devices_by_site,
        network_devices=network_devices,
        protect_data=protect_data,
        protect_devices=protect_devices,
    )

    devices_dict: dict[str, dict[str, Any]] = {
        **inventory_dict,
        **placed_devices_dict,
    }

    return {
        "project": normalized_project,
        "floor_plans": floor_plans_dict,
        "access_points": access_points_dict,
        "switches": switches_dict,
        "placed_devices": placed_devices_dict,
        "inventory": inventory_dict,
        "devices": devices_dict,
        "correlations": correlations,
    }


def build_innerspace_diagnostics_summary(
    snapshot: Mapping[str, Any],
    *,
    available: bool,
    redact_fn: Callable[[Any, Iterable[Any]], Any],
    to_redact: Iterable[Any],
) -> dict[str, Any]:
    """Build a bounded, redacted diagnostic view of InnerSpace state."""
    project = snapshot.get("project")
    floor_plans = snapshot.get("floor_plans") or {}
    access_points = snapshot.get("access_points") or {}
    switches = snapshot.get("switches") or {}
    inventory = snapshot.get("inventory") or {}
    devices = snapshot.get("devices") or {}

    redacted_project = (
        redact_fn(
            {
                "id": project.get("id"),
                "plan_count": project.get("plan_count"),
                "product_count": project.get("product_count"),
                "wall_type_count": project.get("wall_type_count"),
                "attenuation_type_count": project.get("attenuation_type_count"),
            },
            to_redact,
        )
        if isinstance(project, Mapping)
        else None
    )

    redacted_plans = [
        redact_fn(
            {
                "id": plan.get("id"),
                "name": plan.get("name"),
                "floor_number": plan.get("floor_number"),
                "site_id": plan.get("site_id"),
                "ppm": plan.get("ppm"),
                "width": plan.get("width"),
                "height": plan.get("height"),
            },
            to_redact,
        )
        for plan in (floor_plans.values() if isinstance(floor_plans, Mapping) else ())
        if isinstance(plan, Mapping)
    ]

    redacted_devices = [
        redact_fn(
            {
                "id": dev.get("id"),
                "name": dev.get("name"),
                "model": dev.get("model"),
                "device_type": dev.get("device_type"),
                "placement_state": dev.get("placement_state"),
                "floor_plan_id": dev.get("floor_plan_id"),
                "floor_plan_name": dev.get("floor_plan_name"),
                "site_id": dev.get("site_id"),
                "mac": dev.get("mac"),
                "serial": dev.get("serial"),
                "matched_domain": dev.get("matched_domain"),
                "matched_site_id": dev.get("matched_site_id"),
                "matched_device_id": dev.get("matched_device_id"),
                "matched_protect_type": dev.get("matched_protect_type"),
            },
            to_redact,
        )
        for dev in (devices.values() if isinstance(devices, Mapping) else ())
        if isinstance(dev, Mapping)
    ]

    return {
        "available": available,
        "project": redacted_project,
        "counts": {
            "floor_plans": len(floor_plans) if isinstance(floor_plans, Mapping) else 0,
            "access_points": (
                len(access_points) if isinstance(access_points, Mapping) else 0
            ),
            "switches": len(switches) if isinstance(switches, Mapping) else 0,
            "inventory": len(inventory) if isinstance(inventory, Mapping) else 0,
            "devices": len(devices) if isinstance(devices, Mapping) else 0,
        },
        "floor_plans": redacted_plans,
        "devices": redacted_devices,
        "last_update": snapshot.get("last_update"),
    }
