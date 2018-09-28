# -*- mode: python; python-indent: 4 -*-
import datetime
import queue
import random
import re
import threading
import time

import ncs
from ncs.application import Service
from ncs.dp import Action

global_q = queue.PriorityQueue()

def is_ha_master_or_no_ha():
    with ncs.maapi.single_read_trans("", "system", db=ncs.OPERATIONAL) as trans:
        if trans.exists("/tfnm:ncs-state/tfnm:ha"):
            mode = str(ncs.maagic.get_node(trans,
                                           '/tfnm:ncs-state/tfnm:ha/tfnm:mode'))
            return (mode == 'master')
        else:
            return True


def convert_yang_date_and_time_to_datetime(yang_date_and_time):
    """Convert a timestamp stored in a leaf of type yang:date-and-time to python datetime.datetime"""
    if not yang_date_and_time:
        return None
    # the timestamps are read as 2018-02-20T13:15:14+00:00
    # strip the extra +00:00 from the end
    yang_date_and_time = re.sub(r'[+-]\d{2}:\d{2}$', '', yang_date_and_time)
    if re.search(r'\.\d+$', yang_date_and_time):
        fmt = '%Y-%m-%dT%H:%M:%S.%f'
    else:
        fmt = '%Y-%m-%dT%H:%M:%S'
    return datetime.datetime.strptime(yang_date_and_time, fmt)


def map_cache_populate(log, key_xpath, value_xpath, device):
    key_table_xpath = key_xpath.rsplit('/', 1)[:-1]
    value_table_xpath = value_xpath.rsplit('/', 1)[:-1]
    # double check that both key and value are in the same table/list
    if key_table_xpath != value_table_xpath:
        raise ValueError("key and value do not appear to be in the same table")

    full_key_xpath = "/ncs:devices/ncs:device[name='{}']/live-status/{}".format(device, key_xpath)
    value_name = value_xpath.rsplit('/', 1)[-1]
    res = {}

    start_time = datetime.datetime.utcnow()

    def result_callback(kp, key_value):
        nonlocal res
        # xpath to the table, i.e. remove the key leaf name at the end
        table_xpath = str(kp).rsplit('/', 1)[0]
        # the absolute xpath to the value
        absolute_value_xpath = "{}/{}".format(table_xpath, value_name)
        value_value = t_read.get_elem(absolute_value_xpath)
        res[str(key_value)] = str(value_value)

    with ncs.maapi.single_read_trans('map-cache-populate-read', 'system', db=ncs.OPERATIONAL) as t_read:
        t_read.xpath_eval(full_key_xpath, result_callback, None, '')

    end_time = datetime.datetime.utcnow()

    with ncs.maapi.single_write_trans('map-cache-populate-write', 'system', db=ncs.OPERATIONAL) as t_write:
        root = ncs.maagic.get_root(t_write)
        map_dev = root.map_cache.map.create(key_xpath, value_xpath).device.create(device)

        for k,v in res.items():
            map_entry = map_dev.map.create(k)
            map_entry.v = v

        # figure out old entries to remove and remove them
        to_remove = []
        for entry in map_dev.map:
            if entry.k not in res:
                to_remove.append(entry.k)

        for k in to_remove:
            del(map_dev.map[k])

        # write last-poll-stats
        map_dev.last_poll_stats.start_timestamp = datetime.datetime.strftime(start_time, "%Y-%m-%dT%H:%M:%SZ")
        map_dev.last_poll_stats.end_timestamp = datetime.datetime.strftime(end_time, "%Y-%m-%dT%H:%M:%SZ")
        map_dev.last_poll_stats.duration = int((end_time-start_time).total_seconds() * 100)
        map_dev.last_poll_stats.entries_polled = len(res)

        t_write.apply()


class MapCacheWorkerThread(threading.Thread):
    def __init__(self, app, q, log):
        self.log = log
        super().__init__()
        self.log.info('{}: init'.format(self))
        self._app = app
        self._q = q
        self._exit_flag = threading.Event()
        self._maapi = ncs.maapi.Maapi()
        self._maapi.start_user_session('map-cache-worker', 'system')
        self.workers = []

    def run(self):
        self.log.info('{}: run'.format(self))
        self._app.add_running_thread(self.__class__.__name__ + ' (map-cache worker)')
        timeout = 1
        while not self._exit_flag.wait(timeout=timeout):
            # only run if we are master or non-HA
            if not is_ha_master_or_no_ha():
                self.log.info("We are not master or not in non-HA mode, sleeping for a bit then checking again")
                time.sleep(60)
                continue

            job = None
            try:
                job = self._q.get(block=False)
            except queue.Empty:
                continue

            # high-level job definition for a PriorityQueue is a tuple (priority, data)
            priority, data = job
            key_xpath, value_xpath, device = data
            self.log.info("{}: Got a priority {} job for polling map {} -> {} on {}".format(self, priority, key_xpath, value_xpath, device))
            try:
                map_cache_populate(self.log, key_xpath, value_xpath, device)
                self._q.task_done()
                self.log.info("{}: done polling map {} -> {} on {}".format(self, key_xpath, value_xpath, device))
            except Exception as exc:
                self._q.task_done()
                self.log.error("{}: Failed to populate map-cache: {}".format(self, exc))

    def stop(self):
        self._exit_flag.set()
        self.join()
        self._app.del_running_thread(self.__class__.__name__ + ' (worker)')
        self._maapi.end_user_session()
        self._maapi.close()
        self.log.debug('Closed all resources')


class PeriodicJobThread(threading.Thread):
    def __init__(self, app, q, log):
        self.log = log
        super().__init__()
        self.log.info('{}: init'.format(self))
        self._app = app
        self._q = q
        self._exit_flag = threading.Event()
        self._maapi = ncs.maapi.Maapi()
        self._maapi.start_user_session('python-state-poller-periodic-job', 'system')

    def run(self):
        self.log.info('{}: run'.format(self))
        self._app.add_running_thread(self.__class__.__name__ + ' (PeriodicJob)')
        timeout = 30
        start_timer = None
        num_jobs = 0

        while not self._exit_flag.wait(timeout=timeout):
            # only run if we are master or non-HA
            if not is_ha_master_or_no_ha():
                self.log.info("We are not master or not in non-HA mode, sleeping for a bit then checking again.")
                time.sleep(60)
                continue

            # should we run?
            with self._maapi.start_read_trans() as t:
                root = ncs.maagic.get_root(t)
                enabled = root.map_cache.enabled
                if enabled is not True:
                    self.log.info("Map-cache not enabled, sleeping for a bit")
                    continue

            qs = self._q.qsize()
            if qs > 0:
                self.log.info("There are currently {} queued jobs".format(qs))

            elif qs == 0:
                self.log.info("Job queue is empty - adding periodic map-cache jobs")
                try:
                    with self._maapi.start_read_trans() as t_read:
                        root = ncs.maagic.get_root(t_read)
                        for m in root.map_cache.map:
                            for dev in m.device:
                                # if we don't have a previous run, we should immediately populate
                                start_timestamp = dev.last_poll_stats.start_timestamp
                                if start_timestamp is None:
                                    self._q.put((1, (m.key_xpath, m.value_xpath, dev.name)))
                                    continue

                                # check if it's time to update mapping based on last run and update-interval
                                start_ts = convert_yang_date_and_time_to_datetime(dev.last_poll_stats.start_timestamp)
                                next_run = start_ts + datetime.timedelta(seconds=dev.update_interval)
                                if datetime.datetime.utcnow() > next_run:
                                    self._q.put((1, (m.key_xpath, m.value_xpath, dev.name)))

                except Exception as exc:
                    self.log.error("Failed to add jobs: ", exc)

                end_timer = datetime.datetime.utcnow()

                num_jobs = self._q.qsize()
                start_timer = datetime.datetime.utcnow()
                self.log.info('Queued up {} jobs starting at {}'.format(num_jobs, start_timer))


    def stop(self):
        self._exit_flag.set()
        self.join()
        self._app.del_running_thread(self.__class__.__name__ + ' (PeriodicJob)')
        self._maapi.end_user_session()
        self._maapi.close()
        self.log.debug('Closed all resources')

class WorkerApp(ncs.application.Application):
    def setup(self):
        self.log.info('{} WorkerApp setup()'.format(__name__))
        self.pjt = PeriodicJobThread(app=self,
                                     q=global_q,
                                     log=self.log)
        self.pjt.start()

        self.workers = []

        num_worker_threads = 1
        try:
            with ncs.maapi.single_read_trans('map-cache', 'system', db=ncs.OPERATIONAL) as t:
                root = ncs.maagic.get_root(t)
                num_worker_threads = root.map_cache.worker_threads
        except Exception as exc:
            self.log.error("Unable to read number of map-cache workers", exc)

        self.log.info("Using {} map-cache worker threads".format(num_worker_threads))

        for _ in range(num_worker_threads):
            worker = MapCacheWorkerThread(app=self, q=global_q, log=self.log)
            worker.start()
            self.workers.append(worker)

    def teardown(self):
        self.log.info('{} WorkerApp teardown()'.format(__name__))
        self.pjt.stop()
        for worker in self.workers:
            worker.stop()


class MapCachePopulate(Action):
    """ Action handler for populating the map-cache
    """

    @Action.action
    def cb_action(self, uinfo, name, kp, action_input, action_output):
        self.log.info('Populating the map-cache for {}->{} on {}'.format(action_input.key_xpath, action_input.value_xpath, action_input.device))

        msg = 'Populating the map-cache for {} -> {} on {}'.format(action_input.key_xpath, action_input.value_xpath, action_input.device)
        map_cache_populate(self.log, action_input.key_xpath, action_input.value_xpath, action_input.device)
        try:
            map_cache_populate(self.log, action_input.key_xpath, action_input.value_xpath, action_input.device)
        except Exception as exc:
            action_output.success = False
            action_output.message = exc

        action_output.success = True
        action_output.message = ""


class MapCacheApp(ncs.application.Application):
    """ App for registering action callback for map-cache populate
    """
    def setup(self):
        self.log.info('{} map-cache setup()'.format(__name__))
        self.register_action('map-cache-populate', MapCachePopulate)
