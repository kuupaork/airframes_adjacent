#!/usr/bin/env python3
# dumbhfdl.py - a frequency-picking harness for dumphfdl.
# copyright 2024 Kuupa Ork <kuupaork+github@ork.rodeo>
# see LICENSE for terms of use (TL;DR: BSD 3-clause)

# system libraries
import asyncio.subprocess
import bisect
import collections
import contextlib
import datetime
import functools
import itertools
import io
import json
import logging
import os
import pathlib
import re
import signal
import sys
import tempfile
import threading
import time
# third party
import click        # apt install python3-click or `pip install click` or https://pypi.org/project/click/
import requests     # apt install python3-requests or `pip install requests` or https://pypi.org/project/requests/

# all frequencies/bandwidths are in kHz
import fallback


logging.basicConfig(level=logging.DEBUG, format='[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s')
logger = logging.getLogger(sys.argv[0].rsplit('/', 1)[-1].rsplit('.', 1)[0] if __name__ == '__main__' else __name__)
dumphfdl_logger = logging.getLogger('dumphfdl')

# The maximum practical kSamples/sec to accept. For my RSPdx, the technical limit is 10000 (10MS/s) but practical
# experimentation shows that using this rate causes occasional data streaming errors, so I back it off a little bit
# Set with `--max-samples` on command line or environment variable `DUMPHFDL_MAX_SAMPLES`
MAXIMUM_SAMPLE_SIZE = 9250

# A naive factor to apply to the Sample Size to account for an aliasing filter the radio may use.
FILTER_FACTOR = 0.9

# The URL to retrieve Ground Station from
GROUND_STATION_URL = 'https://api.airframes.io/hfdl/ground-stations'
# How long to cache ground station updates (seconds)
GS_EXPIRY = 3 * 3600
#
# How often (seconds) to update the frequency list (and probably restart dumphfdl)
# Set with `--watch-interval` on command line or environment variable `DUMPHFDL_WATCH_INTERVAL`)
WATCH_INTERVAL = 600
#
# Cooldown between stopping dumphfdl and starting it again. SDRPlay radios need something like this. (seconds)
# Set with `--sdr-settle` on command line or environment variable `DUMPHFDL_SDR_SETTLE`)
SDR_SETTLE_TIME = 5


# if FILL_OTHER_STATIONS is True, the chooser will add any frequencies from other stations
# that fit in the allowed bandwidth, but the core stations determine where the watch group lies.
# Set this to False if you are concerned about CPU usage.
# DST (no SRC for any of these): NZ: 69, Bahrain: 12, Korea: 2, Guam: 6. All others nil.
# you can set this to False by passing `--skip-fill` on the command line
FILL_OTHER_STATIONS = True


# Try some other picking strategies for different results. Can be invoked by using `--experimental`. Does not effect
# choices made to actually run listener.
EXPERIMENTAL = False

# dumphfdl config
# base path for files to be written (if any)
DUMB_SHARE_PATH = pathlib.Path.home() / '.local/share/dumbhfdl/'
# The system location of the systable.conf list of names. Can be set to None, in which case lookup will not be done.
# Can be set using `--system_table` option on command line or the `DUMPHFDL_SYSTABLE` environment variable
SYSTABLE_LOCATION = '/usr/local/share/dumphfdl/systable.conf'
# The location where an updated systable.conf will be written. If None, no updates will be saved.
# Can be set using `--system_table-save` option on command line 
# or the `DUMPHFDL_SYSTABLE_UPDATES` environment variable
SYSTABLE_UPDATES_PATH = f'{DUMB_SHARE_PATH}/hfdl-systable-new.conf'
# The default location where logs will be written. If None, no packet logs will be kept. 
# Override with `--log-path` option or the `DUMPHFDL_LOG_PATH` environment variable.
LOG_PATH = DUMB_SHARE_PATH / "logs"


def bandwidth_for_interval(interval):
    return interval[-1] - interval[0]


class GroundStation:
    last_updated = 0
    frequencies = []
    dirty = False
    name = None
    gsid = "unknown"

    def __setattr__(self, name, value):
        oldval = getattr(self, name, object())
        super().__setattr__(name, value)
        if name != 'dirty' and value != oldval:
            super().__setattr__('dirty', True)

    def update_from_station(self, data):
        if data.last_updated > self.last_updated:
            self.last_updated = data.last_updated
            self.gsid = data.gsid
            self.name = data.name
            self.frequencies = data.frequencies

    def update_from_airframes(self, data, mark_clean=False):
        if data['last_updated'] > self.last_updated:
            self.last_updated = data['last_updated']
            self.gsid = data['id']
            self.name = data['name']
            self.frequencies = sorted(data['frequencies']['active'])
            logger.debug(f'airframes update for {self}')
            if mark_clean:
                self.mark_clean()

    def update_from_squitter(self, data, last_updated):
        if last_updated > self.last_updated:
            self.last_updated = last_updated
            self.gsid = data['gs']['id']
            self.name = data['gs']['name']
            self.frequencies = sorted(map(int, (sf['freq'] for sf in data['freqs'])))
            logger.debug(f'squitter update for {self}')

    def update_from_hfnpdu(self, data, last_updated):
        if not self.last_updated:
            self.last_updated = last_updated
            self.gsid = data['gs']['id']
            self.name = data['gs']['name']
            self.frequencies = sorted(map(int, (sf['freq'] for sf in data['heard_on_freqs'])))
            logger.debug(f'hfnpdu update for {self}')

    def dict(self):
        return {
            'id': self.gsid,
            'name': self.name,
            'frequencies': {
                'active': self.frequencies,
            },
            'last_updated': self.last_updated,
        }

    def mark_clean(self):
        self.dirty = False

    def is_valid(self):
        now = datetime.datetime.now().timestamp()
        horizon = now - GS_EXPIRY
        return self.last_updated >= horizon and self.frequencies

    def __str__(self):
        return f'#{self.gsid}. {self.name} ({",".join(str(f) for f in self.frequencies)})'


class GroundStationCache:
    path = None

    def __init__(self, path=None):
        self.stations_by_id = collections.defaultdict(GroundStation)
        self.stations_by_name = {}
        self.last = None
        if (path):
            self.path = pathlib.Path(path)
            self.load()

    def load(self):
        if self.path and self.path.exists():
            s = self.path.read_text()
            if s:
                self.last = s
                self.merge_airframes(json.loads(s), mark_clean=True)

    def save(self):
        if self.path:
            current = json.dumps(self.dict(), indent=4)
            if current != self.last:  # very naive
                logger.info('saving station cache')
                self.path.write_text(current)
                self.last = current
                map(lambda that: that.mark_clean(), self.stations)

    def update_lookups(self):
        self.stations_by_name = {gs.name: gs for gs in self.stations if gs.name}

    def prune_expired(self):
        for station in list(self.stations_by_id.values()):
            if not station.is_valid() and station.gsid in self.stations_by_id:
                logger.info(f'pruning {station}')
                del self.stations_by_id[station.gsid]

    def merge_airframes(self, airframes, is_load=False, mark_clean=False):
        for gs in airframes.get('ground_stations', []):
            self[gs['id']].update_from_airframes(gs, mark_clean)
        self.update_lookups()
        self.prune_expired()
        self.save()

    def merge_packet(self, packet):
        hfdl = packet.get('hfdl', {})
        last_updated = hfdl.get('t', {}).get('sec', 0)
        for station in hfdl.get('spdu', {}).get('gs_status', []):
            self[station['gs']['id']].update_from_squitter(station, last_updated)
        for station in hfdl.get('lpdu', {}).get('hfnpdu', {}).get('freq_data', []):
            self[station['gs']['id']].update_from_hfnpdu(station, last_updated)
        self.update_lookups()
        self.prune_expired()
        self.save()

    def merge(self, other):
        for gs in other.stations_by_id.values():
            self.stations_by_id[gs.gsid].update_from_station(gs)
        self.update_lookups()
        self.prune_expired()
        self.save()

    def dict(self):
        return {'ground_stations': list(station.dict() for station in self.stations_by_id.values())}

    def pruned_dict(self):
        self.prune_expired()
        return self.dict()

    def __getitem__(self, key):
        try:
            return self.stations_by_id[int(key)]
        except ValueError:
            return self.stations_by_name[key]

    def __contains__(self, key):
        try:
            return int(key) in self.stations_by_id
        except ValueError:
            pass
        return key in self.stations_by_name

    def frequencies(self, key):
        try:
            return self[key].frequencies
        except KeyError:
            return ()

    @property
    def stations(self):
        return self.stations_by_id.values()


class GroundStationWatcher:
    core_ids = []
    fringe_ids = []
    skip_fill = False
    watch_interval = 600
    _max_sample_size = 20000
    _sample_rates = None

    def __init__(self, ground_station_cache, on_update=None):
        self.ground_station_cache = ground_station_cache
        self.task = None
        self.on_update = on_update
        self.last = []

    def set_ignore_ranges(self, data):
        if data.startswith('['):
            try:
                self.ignore_ranges = json.loads(data)
            except json.JSONDecodeError:
                pass
        elif '-' in data:
            self.ignore_ranges = [(int(s),int(e)) for s, e in (r.split('-') for r in data.split(','))]
        elif not data:
            self.ignore_ranges = []
        else:
            raise ValueError(f'unsupported ignored ranges {data}')

    async def run(self):
        while True:
            logger.info("refreshing")
            self.refresh()
            for i in range(self.watch_interval // 10):
                logger.debug(f'tick {i}')
                await asyncio.sleep(10)

    def remote(self, url):
        data = {}
        if url:
            response = requests.get(url)
            try:
                data = response.json()
            except (json.JSONDecodeError, requests.JSONDecodeError):
                pass
        return data

    def parse_airframes(self, data):
        stations = GroundStationCache()  # in-memory only
        stations.merge_airframes(data)
        return stations

    def refresh(self):
        sources = [
            ('Airframes Ground Station URL', lambda: self.remote(GROUND_STATION_URL)),
            ('Cached Squitter Data', lambda: self.ground_station_cache.pruned_dict()),
            ('Backup Ground Station URL', lambda: self.remote(os.getenv('DUMPHFDL_BACKUP_URL'))),
            ('All Allocated Frequencies', lambda: fallback.ALL_FREQUENCIES),
        ]
        logger.info('refreshing ground station data')
        for name, source in sources:
            ground_station_data = source()
            parsed = self.parse_airframes(ground_station_data)
            if all(parsed.frequencies(core_id) for core_id in self.core_ids):
                logger.info(f'Using {name}')
                self.ground_station_cache.merge(parsed)
                return self.choose_best_frequencies()
            else:
                logger.info(f'Cannot update from {name}')
        else:
            raise ValueError('No frequency sources are valid')

    def reconcile_samples(self):
        max_window = 30000  # we will not need a sample size larger than this!
        if self._sample_rates:
            max_window = self._sample_rates[-1] // 1000
        self.maximum_bandwidth = min(max_window, self.max_sample_size) * FILTER_FACTOR

    @property
    def max_sample_size(self):
        return self._max_sample_size

    @max_sample_size.setter
    def max_sample_size(self, new_value):
        self._max_sample_size = int(new_value)
        self.reconcile_samples()
        logger.debug(f'maximum bandwidth is now {self.maximum_bandwidth}')

    @property
    def sample_rates(self):
        return self._sample_rates

    @sample_rates.setter
    def sample_rates(self, windows):
        self._sample_rates = windows
        self.reconcile_samples()

    def choose_best_frequencies(self):
        best_pool = self.best_pool()
        best_frequencies = list(best_pool)
        if best_frequencies != self.last:
            self.last = best_frequencies
            if callable(self.on_update):
                self.on_update(best_frequencies)
        else:
            logger.info(f"frequencies unchanged {best_frequencies}")
        return best_frequencies

    def create_pool(self, seed=None):
        return FrequencyPool(seed=seed, ignored_ranges=self.ignore_ranges, maximum_bandwidth=self.maximum_bandwidth)

    def best_pool(self):
        """
        Builds a pool of the "best" frequencies fitting within the identified maximum bandwidth.
        1. For each active station in the core station list
            - skip ignored frequency ranges
            - add the frequency if the pool can be expanded to cover a frequency and remain in bandwidth limits.
        2. Step 1 is performed twice, once with ascending frequency lists, and once with descending.
        3. The core pool from (2) with the most frequencies wins (ties resolve in favour of the ascending (low) freqs)
        4. Fringe stations' active frequencies may then be added in a similar manner. (Can be disabled)
        5. Finally, any other stations' active frequencies may then be added. (Can be disabled)

        This mechanism has a few caveats:
        - 21 MHz is somewhat selected against, as its distance from other bands reduces the other bands that may 
          also be covered.
        - If low frequency bands are considered, they may skew against the middle bands (10, 11, 13MHz). At my station,
          there's a lot of noise and not much signal below 6MHz, so I've marked that range as ignored and it works well
        """
        # build core range
        core_stations = [self.ground_station_cache[n] for n in self.core_ids]
        low_pool = self.create_pool()
        low_pool.add_stations(core_stations, pivot=0)
        low = list(low_pool)

        high_pool = self.create_pool()
        high_pool.add_stations(core_stations, pivot=-1)
        high = list(high_pool)
        actual_pool = high_pool if len(high_pool) > len(low_pool) else low_pool
        logger.debug(f"low pool: {low}")
        logger.debug(f"high pool: {high}")

        # Fringe stations don't determine pool range, but fill in frequencies more likely to be heard
        # don't need pivot here.
        actual_pool.add_stations(self.ground_station_cache[n] for n in self.fringe_ids)

        # now fill in the others "just in case"
        if not self.skip_fill:
            # a bit wasteful, but these are all small enough that completely readding everything won't hurt.
            actual_pool.add_stations(self.ground_station_cache.stations)
        return actual_pool

    def experimental_pools(self):
        # experimental pools can be generated with the `--experimental` flag. They currently never are used (though
        # the results may duplicate the high or low pools above.
        def experimental_middle_pool():
            # "middle pool" works a bit differently to the high and low pools above. Instead of ranking the Core
            # stations, it combines all their frequencies into a single group. It takes the middle entry from the
            # list and works outwards from it until the bandwidth is filled.
            core_stations = [self.ground_station_cache.frequencies(n) for n in self.core_ids]
            core_freqs = sorted(itertools.chain(*core_stations))
            middle_pool = self.create_pool()
            middle_pool.extend(core_freqs)
            return middle_pool

        def experimental_iterate_core():
            # "iterate core" is similar to "middle pool", but instead of picking just one frequency to build out from,
            # each "core" frequency is tried in turn, building a pool outward from it. The unique pools generated by
            # this mechanism are yielded.
            core_station_active = [self.ground_station_cache.frequencies(n) for n in self.core_ids]
            core_freqs = sorted(itertools.chain(*core_station_active))
            seen = []
            for ix in range(0, len(core_freqs)):
                pool = self.create_pool()
                pool.extend(core_freqs, pivot=ix)
                freqs = list(pool)
                if freqs not in seen:
                    seen.append(freqs)
                    yield pool

        if EXPERIMENTAL:
            middle_pool = experimental_middle_pool()
            logger.info("[experimental] middle pool: {list(middle_pool)} (unranked)")
            logger.info('[experimental] iterate-core pools:')
            for pool in experimental_iterate_core():
                logger.info(f'    {list(pool)}')
            logger.info('===')


class PacketWatcher:
    task = None
    enabled = False

    def __init__(self, fifo, on_update=None):
        self.fifo = pathlib.Path(fifo)
        self.on_update = on_update or self.default_update

    async def run(self):
        self.enabled = True
        logger.debug(f'watching for squitters and frequency updates')
        await self.watch_fifo(self.fifo)

    def start(self):
        self.task = loop.create_task(self.run())

    def stop(self):
        self.enabled = False
        if self.task:
            self.task.cancel()
            self.task = None

    async def watch_fifo(self, fifo):
        while self.enabled and not fifo.exists():
            logger.info(f'waiting for fifo {fifo}')
            await asyncio.sleep(1)
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        with open(fifo) as pipe:
            try:
                await loop.connect_read_pipe(lambda: protocol, pipe)
                async for data in reader:
                    await asyncio.sleep(0)
                    if not self.enabled:
                        break
                    line = data.decode('utf8')
                    if line:
                        if '"gs_status"' in line:
                            packet = json.loads(line)
                            self.on_update(packet)
                        elif '"Frequency data"' in line:
                            packet = json.loads(line)
                            self.on_update(packet)
            except asyncio.CancelledError:
                logger.info('packet watcher cancelled')
            logger.info('packet watcher completed')

    def default_update(self, update):
        logger.info(update)


def balancing_iter(sources, targets=None, pivot=None):
    if not targets:
        targets = sources
    if pivot is None:
        pivot = len(targets) // 2

    if targets and pivot < len(targets):
        pivot_freq = targets[pivot]
        # source_pivot = bisect.bisect_left(sources, pivot_freq)
    elif pivot == -1:
        # source_pivot = -1
        pivot_freq = sources[-1]
    else:
        # source_pivot = 0
        pivot_freq = sources[0]
    # ordered by distance. This is generally fair; it favours same and near band.
    return sorted(sources, key=lambda x: abs(x - pivot_freq))

    # low = list(reversed(sources[0:source_pivot]))
    # high = sources[source_pivot:]
    # if not targets:
    #     zipper = []
    # elif (pivot % len(targets)) >= len(targets) // 2:
    #     zipper = itertools.zip_longest(high, low)
    # else:
    #     zipper = itertools.zip_longest(low, high)
    # for next_freqs in zipper:
    #     yield from next_freqs


def ordered_by_distance(data, origin):
    return 


class FrequencyPool:
    def __init__(self, seed=None, ignored_ranges=None, maximum_bandwidth=None):
        self.frequencies = list(seed) if seed else []
        self.ignored_ranges = ignored_ranges or []
        self.maximum_bandwidth = maximum_bandwidth or 2000
        self.filters = [
            self.filter_none,
            self.filter_duplicates,
            self.filter_ignored_ranges,
            self.filter_bandwidth,
        ]

    def add(self, frequency):
        for filter in self.filters:
            if not filter(frequency):
                break
        else:
            bisect.insort(self.frequencies, frequency)
        return frequency in self.frequencies

    def extend(self, frequencies, pivot=None):
        for frequency in balancing_iter(frequencies, self.frequencies, pivot):
            self.add(frequency)

    def filter_none(self, frequency):
        return frequency is not None

    def filter_duplicates(self, frequency):
        return frequency not in self.frequencies

    def filter_ignored_ranges(self, frequency):
        for interval_start, interval_end in self.ignored_ranges:
            if interval_start <= frequency <= interval_end:
                return False
        return True

    def filter_bandwidth(self, frequency):
        return self.can_cover_bandwidth(frequency, self.frequencies)

    def __iter__(self):
        yield from self.frequencies

    def __len__(self):
        return len(self.frequencies)

    def add_stations(self, stations, pivot=None):
        for station in stations:
            self.extend(station.frequencies, pivot)

    def can_cover_bandwidth(self, frequency, others):
        return (
                not others
                or others[0] <= frequency <= others[-1]
                or (others[0] > frequency and others[-1] - frequency < self.maximum_bandwidth)
                or (others[-1] < frequency and frequency - others[0] < self.maximum_bandwidth)
            )


def sample_rate_for(sample_size, sample_rates):
    sample_size *= 1000
    if not sample_rates:
        return sample_size
    current = 0
    ix = bisect.bisect_right(sample_rates, sample_size)
    if ix >= len(sample_rates):
        raise ValueError(f'cannot fulfill desired sample rate: {sample_size} from {sample_rates}')
    return sample_rates[ix]


@contextlib.contextmanager
def temp_fifo():
    with tempfile.TemporaryDirectory() as dirname:
        fifo = pathlib.Path(f'{dirname}/dumphfdl.pipe')
        os.mkfifo(fifo)
        yield fifo
        logger.debug('fifo done')
        # polite to unlink, but likely not strictly necessary.
        fifo.unlink()


class HFDLListener:
    statsd_server = None
    quiet = False
    log_path = None
    sdr_settle = 5
    acars_hub = None
    ground_station_cache = None
    sample_rates = None
    killed = False
    dumphfdl_task = None
    process = None
    packet_watcher = None

    def __init__(self, ground_station_cache, sample_rates, **dumphfdl_opts):
        self.ground_station_cache = ground_station_cache
        self.sample_rates = sample_rates
        self.dumphfdl_opts = dumphfdl_opts

    def dumphfdl_commandline(self, frequencies):
        sample_rate = sample_rate_for(int(bandwidth_for_interval(frequencies) / FILTER_FACTOR), self.sample_rates)
        dump_cmd = [
            'dumphfdl',
            '--sample-rate', str(sample_rate),
        ]
        opt_map = [
            ('device_settings', 'device-settings'),
            ('soapysdr', 'soapysdr'),
            ('gain_elements', 'gain-elements'),
            ('gain', 'gain'),
            ('antenna', 'antenna'),
            ('system_table', 'system-table'),
            ('system_table_save', 'system-table-save'),
            ('station_id', 'station-id'),
        ]
        for from_opt, to_opt in opt_map:
            value = self.dumphfdl_opts.get(from_opt, None)
            if value is not None:
                dump_cmd.extend([f'--{to_opt}', str(value)])
        if not self.quiet:
            dump_cmd.extend(['--output', 'decoded:text:file:path=/dev/stdout',])
        if self.statsd_server:
            dump_cmd.extend([
                '--statsd', self.statsd_server,
                '--noise-floor-stats-interval', '30',
            ])
        if self.acars_hub:
            host, port = self.acars_hub.split(':')
            dump_cmd.extend(['--output', f'decoded:json:tcp:address={host},port={port}'])
        elif self.dumphfdl_opts.get('station_id'):
            dump_cmd.extend(['--output', 'decoded:json:tcp:address=feed.airframes.io,port=5556',])
        if self.log_path:
            dump_cmd.extend(['--output', f'decoded:json:file:path={self.log_path}/hfdl.json.log,rotate=daily',])
        # special pipe for ground_station_updater.
        dump_cmd.extend(['--output', f'decoded:json:file:path={self.fifo}'])
        for output in json.loads(os.getenv('DUMPHFDL_OUTPUTS', '[]')):
            dump_cmd.extend(['--output', output])
        dump_cmd += [str(f) for f in frequencies]
        return dump_cmd

    def listen(self, frequencies):
        self.frequencies = frequencies
        if self.dumphfdl_task:
            self.terminate()
        else:
            self.dumphfdl_task = loop.create_task(self.run())

    async def run(self):
        while not self.killed:
            with temp_fifo() as fifo:
                logger.debug(f'with fifo {fifo}')
                self.fifo = fifo
                if self.packet_watcher:
                    logger.debug('cleaning up old packet watcher')
                    self.packet_watcher.stop()
                if self.process:  # dubious, but this is the old process, which should have been nulled out
                    logger.info('giving SDR a chance to settle')
                    await asyncio.sleep(self.sdr_settle)
                logger.info(f'gathering options for {self.frequencies}')
                cmd = self.dumphfdl_commandline(self.frequencies)
                logger.debug(cmd)
                logger.info('starting dumphfdl')
                self.process = await asyncio.create_subprocess_exec(*cmd, stderr=asyncio.subprocess.PIPE)
                logger.debug(f'process started {self.process}')
                logger.info(f'starting packet watcher')
                self.packet_watcher = PacketWatcher(fifo, self.ground_station_cache.merge_packet)
                self.packet_watcher.start()
                logger.info(f'starting error watcher')
                loop.create_task(self.watch_stderr(self.process.stderr))

                await self.process.wait()

                logger.info('dumphfdl process finished')
                self.process = None
                self.packet_watcher.stop()

    def terminate(self):
        if self.process:
            logger.info(f'stopping dumphfdl')
            self.process.terminate()

    def kill(self):
        if self.process:
            logger.warning('killing dumphfdl')
            self.killed = True
            self.process.kill()
            self.process = None

    async def watch_stderr(self, stream):
        errors = ['^Unable to initialize input']  # , '^Sample buffer overrun']
        async for data in stream:
            await asyncio.sleep(0)
            if not self.process:
                break
            line = data.decode('utf8').rstrip()
            dumphfdl_logger.info(line)
            if any(re.search(pattern, line) for pattern in errors):
                logger.warning(f'encountered error: "{line}". Restarting')
                # force restart
                self.kill()
                break
        logger.info(f'finished watching {stream}')


def split_stations(stations):
    raw_ids = []
    if not stations or stations == '.':
        return raw_ids
    if stations.startswith('['):
        # could be JSON
        try:
            raw_ids = json.loads(stations)
        except json.JSONDecodeError:
            pass
    elif '+' in stations or ';' in stations:
        raw_ids = re.split('[+;]', stations)
    raw_ids = stations.split(',')
    ids = []
    for x in raw_ids:
        try:
            ids.append(int(x))
        except ValueError:
            pass
    if len(ids) == 2 and all(not isinstance(x, int) for x in ids):
        # it could still be a station name, not an id.
        return stations
    return ids


def common_params(func):
    @click.option('--core-ids', default=os.getenv('DUMPHFDL_CORE_IDS', []))
    @click.option('--fringe-ids', default=os.getenv('DUMPHFDL_FRINGE_IDS', []))
    @click.option('--skip-fill', is_flag=True)
    @click.option('--max-samples', type=int, default=os.getenv('DUMPHFDL_MAX_SAMPLES', MAXIMUM_SAMPLE_SIZE))
    @click.option('--ignore-ranges', default=os.getenv('DUMPHFDL_IGNORE_RANGES', []))
    @click.option('--gs-cache', help='Airframes Station Data path', default=os.getenv('DUMPHFDL_AIRFRAMES_CACHE'))
    @click.option('--sample-rates', help='the sample sizes supported by your radio', default=os.getenv('DUMPHFDL_SAMPLE_RATES', ''))
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx):
    if not ctx.invoked_subcommand:
        run()


@main.command()
@common_params
@click.option('-s', '--station-id', default=os.getenv('DUMPHFDL_STATION_ID'))
@click.option('--quiet', help='Suppress packet logging to stdout.', is_flag=True)
@click.option('--watch-interval', default=os.getenv('DUMPHFDL_WATCH_INTERVAL', WATCH_INTERVAL))
@click.option('--sdr-settle', default=int(os.getenv('DUMPHFDL_SDR_SETTLE', SDR_SETTLE_TIME)))
@click.option('--log-path', default=os.getenv('DUMPHFDL_LOG_PATH', LOG_PATH))
@click.option('--acars-hub', default=os.getenv('DUMPHFDL_ACARS_HUB'))
@click.option('--statsd', help='(see dumphfdl)', default=os.getenv('DUMPHFDL_STATSD'))
@click.option('--soapysdr', help='(see dumphfdl)', default=os.getenv('DUMPHFDL_SOAPYSDR'))
@click.option('--antenna', help='(see dumphfdl)', default=os.getenv('DUMPHFDL_ANTENNA'))
@click.option('--device-settings', help='(see dumphfdl)', default=os.getenv('DUMPHFDL_DEVICE_SETTINGS'))
@click.option('--system-table', help='(see dumphfdl)', default=os.getenv('DUMPHFDL_SYSTABLE', SYSTABLE_LOCATION))
@click.option(
    '--system-table-save', help='(see dumphfdl)', default=os.getenv('DUMPHFDL_SYSTABLE_UPDATES', SYSTABLE_UPDATES_PATH)
)
@click.option('--gain', help='(see dumphfdl)', default=os.getenv('DUMPHFDL_GAIN'))
@click.option('--gain-elements', help='(see dumphfdl)', default=os.getenv('DUMPHFDL_GAIN_ELEMENTS'))
def run(core_ids, fringe_ids, skip_fill, max_samples, ignore_ranges, gs_cache, sample_rates,
        statsd, quiet, log_path, watch_interval, sdr_settle,acars_hub,
        **dumphfdl_opts
    ):
    ground_station_cache = GroundStationCache(gs_cache)
    sample_rates = sorted(int(x) for x in sample_rates.split(',') if x)

    listener = HFDLListener(ground_station_cache, sample_rates, **dumphfdl_opts)
    listener.statsd_server = statsd
    listener.quiet = quiet
    listener.log_path = log_path
    listener.sdr_settle = sdr_settle
    listener.acars_hub = acars_hub

    def frequencies_updated(new_frequencies):
        logger.info(f'frequencies: {new_frequencies} = {bandwidth_for_interval(new_frequencies)}')
        listener.listen(new_frequencies)

    watcher = GroundStationWatcher(ground_station_cache, frequencies_updated)
    watcher.core_ids = split_stations(core_ids)
    watcher.fringe_ids = split_stations(fringe_ids)
    watcher.skip_fill = skip_fill
    watcher.watch_interval = int(watch_interval)
    watcher.max_sample_size = max_samples
    watcher.sample_rates = sample_rates
    watcher.set_ignore_ranges(ignore_ranges)

    try:
        loop.run_until_complete(watcher.run())
    except asyncio.CancelledError:
        listener.kill()
    except KeyboardInterrupt:
        listener.kill()
    except:
        listener.kill()
        raise


@main.command()
@common_params
@click.option('--core', help='Build pool from Core stations only', is_flag=True)
@click.option('--named', help='Build pool from Core and Fringe stations only', is_flag=True)
@click.option('--experiments', help='show other possible pools based on experimental strategies.', is_flag=True)
def scan(
        core_ids, fringe_ids, skip_fill, max_samples, ignore_ranges, gs_cache, sample_rates,
        core, named, experiments
    ):
    global FRINGE_STATIONS, FILL_OTHER_STATIONS, EXPERIMENTAL
    EXPERIMENTAL = experiments
    if core:
        FRINGE_STATIONS = []
    FILL_OTHER_STATIONS = not (named or core)

    ground_station_cache = GroundStationCache(gs_cache)
    sample_rates = sorted(int(x) for x in sample_rates.split(',') if x)

    watcher = GroundStationWatcher(ground_station_cache)
    watcher.core_ids = split_stations(core_ids)
    watcher.fringe_ids = split_stations(fringe_ids)
    watcher.skip_fill = skip_fill
    watcher.max_sample_size = max_samples
    watcher.sample_rates = sample_rates
    watcher.set_ignore_ranges(ignore_ranges)
    assert watcher.core_ids, 'No core stations identified'
    logger.debug(f'core ids = {core_ids} / {watcher.core_ids}')

    freqs = watcher.refresh()
    bandwidth = int(bandwidth_for_interval(freqs))
    samples = int(bandwidth / FILTER_FACTOR)
    sample_rate = sample_rate_for(samples, watcher.sample_rates)
    watcher.experimental_pools()
    logger.info(f"Best Frequencies: {freqs}")
    logger.info(f"Required bandwidth: {bandwidth}kHz")
    logger.info(f"Required Samples/Second: {sample_rate}")


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    main()
 