"""Read-only Tuya cleaning history; images stay behind HA authentication."""
import asyncio
import base64
from collections import OrderedDict
import hashlib
import hmac
import io
import re
import struct
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from PIL import Image, ImageDraw
import voluptuous as vol
from homeassistant.components import frontend, websocket_api
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.recorder import get_instance, history as recorder_history
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed
from .credentials import independent_settings
from .security import Admission, HistoryBusy, WorkPool, download_map, visible_records
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_CLEAN_MODE_ENTITY_ID,
    CONF_CLOUD_ENTRY_ID,
    CONF_SUCTION_ENTITY_ID,
    CONF_VACUUM_ENTRY_ID,
    CONF_WATER_ENTITY_ID,
    DOMAIN,
    CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_REGION, CONF_DEVICE_ID, REGIONS,
)

CONFIG_SCHEMA = vol.Schema({vol.Optional(DOMAIN): vol.Schema({
    vol.Optional(CONF_CLOUD_ENTRY_ID): str,
    vol.Optional(CONF_VACUUM_ENTRY_ID): str,
    vol.Optional(CONF_CLIENT_ID): str,
    vol.Optional(CONF_CLIENT_SECRET): str,
    vol.Optional(CONF_DEVICE_ID): str,
    vol.Optional(CONF_REGION, default="eu"): vol.In(REGIONS),
    vol.Optional(CONF_CLEAN_MODE_ENTITY_ID): str,
    vol.Optional(CONF_SUCTION_ENTITY_ID): str,
    vol.Optional(CONF_WATER_ENTITY_ID): str,
})}, extra=vol.ALLOW_EXTRA)

CARD_PATH = "/tuvio_history/tuvio-history-card.js"
CARD_URL = f"{CARD_PATH}?v=1.2.0"


def get_setting_history(hass, start, end, entity_ids):
    """Read locally recorded vacuum settings without blocking the event loop."""
    return recorder_history.get_significant_states(
        hass, start, end, entity_ids, None, True, True, False, True, False
    )


def decode_map(data, section_sizes=()):
    """Render a Tuya v1 archived map with its appended cleaning route."""
    if len(data) < 24 or data[0] != 1:
        raise ValueError('Unsupported map format')
    width, height = struct.unpack_from('>HH', data, 4)
    size = int.from_bytes(data[18:22], 'big')
    compressed = int.from_bytes(data[22:24], 'big')
    if not (0 < width <= 2048 and 0 < height <= 2048 and width * height <= size <= 8_000_000):
        raise ValueError('Invalid map dimensions')
    if compressed:
        source = data[24:24 + compressed]
        if len(source) != compressed:
            raise ValueError('Truncated map')
        raw = bytearray()
        i = 0
        while i < len(source):
            token = source[i]; i += 1
            length = token >> 4
            if length == 15:
                while True:
                    n = source[i]; i += 1; length += n
                    if n != 255: break
            if i + length > len(source) or len(raw) + length > size:
                raise ValueError('Invalid literal length')
            raw.extend(source[i:i + length]); i += length
            if i == len(source): break
            offset = source[i] | (source[i + 1] << 8); i += 2
            length = (token & 15) + 4
            if (token & 15) == 15:
                while True:
                    n = source[i]; i += 1; length += n
                    if n != 255: break
            if offset == 0 or offset > len(raw) or len(raw) + length > size:
                raise ValueError('Invalid match')
            for _ in range(length): raw.append(raw[-offset])
        if len(raw) != size: raise ValueError('Invalid decoded size')
    else:
        raw = data[24:24 + size]
        if len(raw) != size: raise ValueError('Truncated map')
    palette = [(151,184,238), (162,213,202), (225,197,151), (184,177,230), (236,178,189), (157,205,224)]
    pixels = []
    for value in raw[:width * height]:
        if value in (243,255): color = (244,247,251)
        elif value >= 240 or value & 3: color = (57,72,94)
        else: color = palette[(value // 4) % len(palette)]
        pixels.append(color)
    image = Image.new('RGB', (width,height)); image.putdata(pixels)
    scale = max(1, min(4, 1100 // max(width,height)))
    image = image.resize((width * scale,height * scale), Image.Resampling.NEAREST)
    if len(section_sizes) >= 2 and section_sizes[0] >= 24 and section_sizes[1] >= 17:
        route_start = section_sizes[0]
        route_end = route_start + section_sizes[1]
        route = data[route_start:route_end]
        if len(route) == section_sizes[1] and (len(route) - 13) % 4 == 0:
            points = []
            for offset in range(13, len(route), 4):
                x, y = struct.unpack_from('>hh', route, offset)
                px = (x + struct.unpack_from('>H', data, 8)[0]) / 10 * scale
                py = (-y + struct.unpack_from('>H', data, 10)[0]) / 10 * scale
                if -scale <= px <= image.width + scale and -scale <= py <= image.height + scale:
                    points.append((px, py))
            if len(points) >= 2:
                draw = ImageDraw.Draw(image)
                draw.line(points, fill=(250, 253, 255), width=max(3, scale + 2), joint='curve')
                draw.line(points, fill=(24, 132, 255), width=max(2, scale), joint='curve')
    output = io.BytesIO(); image.save(output, 'PNG')
    return base64.b64encode(output.getvalue()).decode()


class History:
    def __init__(self, hass, cloud, vacuum, settings):
        self.hass = hass
        self.cloud = cloud
        self.device = vacuum['device_id']
        self.host = 'https://openapi.tuya' + cloud.get('region','eu') + '.com'
        self.token = ''; self.expires = 0
        self.lock = asyncio.Lock(); self.limit = asyncio.Semaphore(3)
        self.log_lock = asyncio.Lock(); self.last_log_request = 0
        self.pages = {}; self.records = {}; self.types = {}; self.images = OrderedDict()
        self.admission = Admission()
        self.work = WorkPool()
        self.setting_entities = {
            'cleaning_mode': settings.get(CONF_CLEAN_MODE_ENTITY_ID),
            'suction': settings.get(CONF_SUCTION_ENTITY_ID),
            'water': settings.get(CONF_WATER_ENTITY_ID),
        }

    async def request(self, path, token=''):
        stamp = str(int(time.time() * 1000))
        message = self.cloud['client_id'] + token + stamp + 'GET\n' + hashlib.sha256(b'').hexdigest() + '\n\n' + path
        headers = {'client_id': self.cloud['client_id'], 'access_token': token, 't': stamp, 'sign_method':'HMAC-SHA256', 'sign':hmac.new(self.cloud['client_secret'].encode(),message.encode(),hashlib.sha256).hexdigest().upper()}
        async with asyncio.timeout(25):
            async with async_get_clientsession(self.hass).get(self.host + path,headers=headers) as response:
                response.raise_for_status(); result = await response.json()
        if not result.get('success'): raise ValueError('Tuya API unavailable')
        return result['result']

    async def api(self, path):
        async with self.lock:
            if time.monotonic() >= self.expires:
                result = await self.request('/v1.0/token?grant_type=1')
                self.token = result['access_token']
                self.expires = time.monotonic() + max(30, int(result.get('expire_time',7200)) - 120)
        return await self.request(path,self.token)

    async def listing(self, page, refresh):
        cached = self.pages.get(page)
        if cached:
            age = time.monotonic() - cached[0]
            if age < (30 if refresh else 300):
                return cached[1]
        return await self.work.run(('page', page), lambda: self._listing(page))

    async def _listing(self, page):
        async with self.limit:
            result = await self.api('/v1.0/users/sweepers/file/' + self.device + '/list?' + urlencode({'file_type':'pic','page_no':page,'page_size':20}))
        records = [{'id':str(item['id']), 'time':int(item['time'])} for item in result.get('datas',[])]
        for record, item in zip(records, result.get('datas', [])):
            match = re.fullmatch(r'\d{5}_\d{8}_\d{6}_(\d{3})_(\d{3})_(\d{5})_(\d{5})_(\d{5})_(\d{5})', item.get('extend', ''))
            if match:
                record['minutes'] = int(match[1]); record['area'] = int(match[2])
                record['section_sizes'] = [int(value) for value in match.groups()[2:]]
        records.sort(key=lambda r:r['time'],reverse=True)
        types = await asyncio.gather(*(self.cleaning_type(record) for record in records))
        for record, cleaning_type in zip(records, types):
            if cleaning_type: record['cleaning_type'] = cleaning_type
        await self.add_recorded_settings(records)
        value = {'records':records,'total':result.get('total_count',len(records)), 'has_more':bool(result.get('has_more')), 'page':page, 'updated':int(time.time())}
        self.records.update((r["id"], r) for r in records)
        self.pages[page] = (time.monotonic(),value)
        while len(self.pages) > 32:
            self.pages.pop(next(iter(self.pages)))
        retained_ids = {record["id"] for _, cached_page in self.pages.values()
                        for record in cached_page["records"]}
        self.records = {record_id: record for record_id, record in self.records.items()
                        if record_id in retained_ids}
        return value

    async def cleaning_type(self, record):
        cached = self.types.get(record['id'])
        if cached and (cached[1] is not None or time.monotonic() - cached[0] < 300):
            return cached[1]
        minutes = record.get('minutes')
        if minutes is None: return None
        start = (record['time'] - (minutes + 10) * 60) * 1000
        end = (record['time'] + 2 * 60) * 1000
        path = '/v2.0/cloud/thing/' + self.device + '/report-logs?' + urlencode(sorted({
            'codes':'mode,status', 'start_time':start, 'end_time':end, 'size':100
        }.items()), safe=',')
        labels = {
            'smart':'Уборка квартиры', 'zone':'Зональная уборка', 'zone_clean':'Зональная уборка',
            'pose':'Точечная уборка', 'goto_pos':'Точечная уборка',
            'part':'Уборка комнат', 'part_clean':'Уборка комнат', 'select_room':'Уборка комнат'
        }
        try:
            async with self.log_lock:
                cached = self.types.get(record['id'])
                if cached and (cached[1] is not None or time.monotonic() - cached[0] < 300):
                    return cached[1]
                delay = .28 - (time.monotonic() - self.last_log_request)
                if delay > 0: await asyncio.sleep(delay)
                self.last_log_request = time.monotonic()
                result = await self.api(path)
                label = next((labels[str(item.get('value'))] for item in result.get('logs', [])
                              if str(item.get('value')) in labels), None)
                self.types[record['id']] = (time.monotonic(), label)
                while len(self.types) > 640:
                    self.types.pop(next(iter(self.types)))
                return label
        except Exception:
            return None
        return None

    async def add_recorded_settings(self, records):
        entity_ids = [entity_id for entity_id in self.setting_entities.values() if entity_id]
        if not records or not entity_ids: return
        starts = [record['time'] - record.get('minutes', 0) * 60 for record in records]
        start = datetime.fromtimestamp(min(starts) - 86400, timezone.utc)
        end = datetime.fromtimestamp(max(record['time'] for record in records) + 1, timezone.utc)
        try:
            states = await get_instance(self.hass).async_add_executor_job(
                get_setting_history, self.hass, start, end, entity_ids
            )
        except Exception:
            return
        for record, record_start in zip(records, starts):
            target = record_start + 120
            for field, entity_id in self.setting_entities.items():
                if not entity_id: continue
                value = None
                for state in states.get(entity_id, []):
                    changed = state.last_updated.timestamp()
                    if changed > target: break
                    if state.state not in ('unknown', 'unavailable'):
                        value = state.state
                if value is not None: record[field] = value

    async def image(self, file_id):
        record = self.records.get(file_id)
        if not record: raise ValueError('Load history first')
        if file_id in self.images:
            self.images.move_to_end(file_id); return self.images[file_id]
        return await self.work.run(('map', file_id), lambda: self._image(file_id, record))

    async def _image(self, file_id, record):
        async with self.limit:
            result = await self.api('/v1.0/users/sweepers/file/' + self.device + '/download?' + urlencode({'id':file_id}))
            data = await download_map(result.get('app_map', ''))
            image = await self.hass.async_add_executor_job(decode_map,data,record.get('section_sizes',()))
            self.images[file_id] = image
            while len(self.images) > 32: self.images.popitem(last=False)
            return image


async def async_setup(hass, config):
    """Register the authenticated API and import legacy YAML configuration."""
    hass.data.setdefault(DOMAIN, {})
    static_dir = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_PATH, str(static_dir / "tuvio-history-card.js"), True)]
    )
    frontend.add_extra_js_url(hass, CARD_URL)
    websocket_api.async_register_command(hass, ws_list)
    websocket_api.async_register_command(hass, ws_map)
    if settings := config.get(DOMAIN):
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "import"},
                data=dict(settings),
            )
        )
    return True


async def async_migrate_entry(hass, entry: ConfigEntry) -> bool:
    """Detach existing configurations from LocalTuya and Tuya Local."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        data = independent_settings(entry.data, hass.config_entries.async_get_entry)
        hass.config_entries.async_update_entry(entry, data=data, version=2)
    return True


async def async_setup_entry(hass, entry: ConfigEntry) -> bool:
    """Set up history using this integration's own credentials and device ID."""
    settings = dict(entry.data)
    if not all(settings.get(key) for key in
               (CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_DEVICE_ID)):
        raise ConfigEntryAuthFailed("Enter Tuya project credentials and device ID")
    hass.data[DOMAIN]["history"] = History(
        hass, settings, {"device_id": settings[CONF_DEVICE_ID]}, settings
    )
    return True


async def async_unload_entry(hass, entry: ConfigEntry) -> bool:
    """Unload the configured history source."""
    if history := hass.data[DOMAIN].pop("history", None):
        await history.work.close()
    return True


@websocket_api.websocket_command({vol.Required('type'):'tuvio_history/list',vol.Optional('page',default=1):vol.All(int,vol.Range(min=1,max=1000)),vol.Optional('refresh',default=False):bool})
@websocket_api.async_response
async def ws_list(hass,connection,msg):
    if connection.user is None:
        connection.send_error(msg['id'], 'unauthorized', 'Authentication required')
        return
    try:
        history = hass.data[DOMAIN]["history"]
        with history.admission.request(connection.user.id):
            result = await history.listing(msg['page'],msg['refresh'])
            connection.send_result(msg['id'], visible_records(
                result, history.setting_entities, connection.user, POLICY_READ))
    except HistoryBusy:
        connection.send_error(msg['id'], 'history_busy', 'Слишком много запросов. Попробуйте позже.')
    except Exception:
        connection.send_error(msg['id'],'history_unavailable','Не удалось загрузить историю из Tuya. Попробуйте обновить позже.')


@websocket_api.websocket_command({vol.Required('type'):'tuvio_history/map',vol.Required('file_id'):str})
@websocket_api.async_response
async def ws_map(hass,connection,msg):
    if connection.user is None:
        connection.send_error(msg['id'], 'unauthorized', 'Authentication required')
        return
    try:
        history = hass.data[DOMAIN]["history"]
        with history.admission.request(connection.user.id):
            image = await history.image(msg['file_id'])
            connection.send_result(msg['id'],{'image':image})
    except HistoryBusy:
        connection.send_error(msg['id'], 'history_busy', 'Слишком много запросов. Попробуйте позже.')
    except Exception:
        connection.send_error(msg['id'],'map_unavailable','Карта этой уборки недоступна или имеет неподдерживаемый формат.')
