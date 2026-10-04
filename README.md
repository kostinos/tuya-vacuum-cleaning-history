# Tuya Vacuum Cleaning History

Home Assistant custom integration for viewing archived Tuya robot-vacuum
cleanings as an authenticated dashboard card.

> [!IMPORTANT]
> Compatibility has been tested **only with Tuvio TR06HLCB**. Other Tuya
> vacuums can use different archive layouts, route encodings, DP values, or
> cloud APIs and are not currently verified.

## Features

- archived cleaning list, duration, and area;
- room map with the robot's saved route overlaid in blue;
- cleaning scope: apartment, rooms, zone, or spot;
- historical dry/wet cleaning mode, suction power, and water level;
- responsive Lovelace card loaded by the integration;
- maps remain behind Home Assistant authentication and are never written to
  public `/local` storage;
- read-only: the integration sends no commands to the vacuum.

The scope comes from Tuya Cloud report logs near the archive timestamp. Mode,
suction, and water level come from Home Assistant Recorder at the start of that
specific cleaning. Current settings are never substituted for missing history.

## Requirements

- Home Assistant 2026.9 or newer;
- HACS;
- an existing **LocalTuya** config entry containing Tuya Cloud `client_id` and
  `client_secret`;
- an existing **Tuya Local** vacuum entry containing the robot `device_id`;
- Recorder history for the optional mode, suction, and water entities.

The cloud entry may remain disabled in Home Assistant, but it must not be
deleted because this integration reads its stored credentials. No credentials
or signed Tuya URLs are exposed to the browser.

## Installation with HACS

1. Open HACS → Integrations → Custom repositories.
2. Add `https://github.com/kostinos/tuya-vacuum-cleaning-history` as an
   **Integration** repository.
3. Install **Tuya Vacuum Cleaning History** and restart Home Assistant.
4. Open Settings → Devices & services → Add integration.
5. Search for **Tuya Vacuum Cleaning History** and select the existing cloud
   entry, vacuum entry, and optional setting entities.
6. Add a Manual card to a dashboard:

   ```yaml
   type: custom:tuvio-history-card
   ```

The card JavaScript is bundled inside the integration and registered
automatically; no separate Lovelace resource is needed.

## Legacy YAML import

Existing installations can keep this block for one restart. It is imported
into a UI config entry without copying secrets:

```yaml
tuvio_history:
  cloud_entry_id: YOUR_LOCALTUYA_CONFIG_ENTRY_ID
  vacuum_entry_id: YOUR_TUYA_LOCAL_CONFIG_ENTRY_ID
  clean_mode_entity_id: select.robot_cleaning_mode
  suction_entity_id: select.robot_suction
  water_entity_id: select.robot_water_level
```

After the entry appears under Devices & services, remove the YAML block and
restart Home Assistant again. If the card was installed manually before v1.1.0,
also remove the old `/local/tuvio-history-card.js` dashboard resource; the
integration now loads its bundled copy automatically.

## Data retention

Tuya Cloud log retention determines how far back the cleaning scope can be
reconstructed. Home Assistant Recorder retention determines how far back
mode, suction, and water settings are available. Older maps still remain
usable, but missing historical metadata is left blank.

## Support

When reporting a problem, include the vacuum model, Home Assistant version,
integration logs, and whether the archived map itself loads. Do not publish
Tuya client secrets or signed map download URLs.

- [Issues](https://github.com/kostinos/tuya-vacuum-cleaning-history/issues)
- [Tuya vacuum map API documentation](https://developer.tuya.com/en/docs/app-development/sweeper-oss?id=Kf6jwv0u0fl1d)

## License

MIT
