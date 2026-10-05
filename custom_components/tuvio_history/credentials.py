"""Copy legacy references into integration-owned settings."""
from .const import (
    CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_REGION, CONF_DEVICE_ID,
    CONF_CLOUD_ENTRY_ID, CONF_VACUUM_ENTRY_ID,
)


def independent_settings(data, get_entry):
    """Resolve references once; never retain another integration's credentials link."""
    result = dict(data)
    cloud_id = result.pop(CONF_CLOUD_ENTRY_ID, None)
    vacuum_id = result.pop(CONF_VACUUM_ENTRY_ID, None)
    if cloud_id and (cloud := get_entry(cloud_id)):
        for key in (CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_REGION):
            if cloud.data.get(key):
                result.setdefault(key, cloud.data[key])
    if vacuum_id and (vacuum := get_entry(vacuum_id)):
        if vacuum.data.get(CONF_DEVICE_ID):
            result.setdefault(CONF_DEVICE_ID, vacuum.data[CONF_DEVICE_ID])
    result.setdefault(CONF_REGION, "eu")
    return result
