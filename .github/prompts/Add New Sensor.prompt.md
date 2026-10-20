---
agent: "agent"
tools: ["search/codebase", "edit", "search"]
description: "Add a new sensor entity to the UniFi Insights integration"
---

# Add New Sensor

Your goal is to add a new sensor entity to the UniFi Insights integration (`sensor.py`, in the section for its data source).

If not provided, ask for:

- What the sensor measures (CPU temp, bandwidth, PoE power, SFP optical power, ISP latency, ONU RX power, etc.)
- Data source (Network, Protect, Access, InnerSpace, Site Manager, Mobility, or Carrier Fabric coordinator)
- Unit of measurement (`native_unit_of_measurement` and optional `suggested_unit_of_measurement`)
- `SensorDeviceClass`, `SensorStateClass`, `suggested_display_precision`, and `EntityCategory`
- Whether it should be enabled by default (`entity_registry_enabled_default`)

## Implementation Steps

### 1. Add entity description

Add to the description tuple for the data source's section in `custom_components/unifi_insights/sensor.py`:

```python
UnifiInsightsSensorEntityDescription(
    key="sensor_key",
    translation_key="sensor_key",
    native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
    device_class=SensorDeviceClass.DATA_RATE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=2,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=True,
    value_fn=lambda data: get_field(data, "fieldName", "field_name"),
)
```

### 2. Preserve falsy zero values

Always use `get_field()` or `first_not_none()` instead of `data.get("a") or data.get("b")` so `0` or `0.0` measurements do not evaluate as `None`.

### 3. Add translations and icons

- **Files:** `custom_components/unifi_insights/strings.json` and `custom_components/unifi_insights/translations/en.json`:
  ```json
  "entity": {
    "sensor": {
      "sensor_key": {
        "name": "Sensor display name"
      }
    }
  }
  ```
- **File:** `custom_components/unifi_insights/icons.json` (supports `default`, `state`, and numeric `range` keys in HA 2026.9+):
  ```json
  "entity": {
    "sensor": {
      "sensor_key": {
        "default": "mdi:icon-name"
      }
    }
  }
  ```

### 4. Add tests and validate

- Add unit tests in `tests/test_sensor.py` (testing value extraction, `0` value handling, and unavailability)
- Run `./script/lint`, `./script/mypy`, and `pytest tests/test_sensor.py`
