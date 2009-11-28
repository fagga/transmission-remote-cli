#!/usr/bin/python
########################################################################
# This is transmission-remote-cli, whereas 'cli' stands for 'Curses    #
# Luminous Interface', a client for the daemon of the BitTorrent       #
# client Transmission.                                                 #
#                                                                      #
# This program is free software: you can redistribute it and/or modify #
# it under the terms of the GNU General Public License as published by #
# the Free Software Foundation, either version 3 of the License, or    #
# (at your option) any later version.                                  #
#                                                                      #
# This program is distributed in the hope that it will be useful,      #
# but WITHOUT ANY WARRANTY; without even the implied warranty of       #
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the        #
# GNU General Public License for more details:                         #
# http://www.gnu.org/licenses/gpl-3.0.txt                              #
########################################################################

VERSION='0.4.4'

TRNSM_VERSION_MIN = '1.60'
TRNSM_VERSION_MAX = '1.76'
RPC_VERSION_MIN = 5
RPC_VERSION_MAX = 6

# error codes
CONNECTION_ERROR = 1
JSON_ERROR       = 2
CONFIGFILE_ERROR = 3

import time
import re
import base64
import simplejson as json
import httplib
import urllib2
import socket
socket.setdefaulttimeout(None)
import ConfigParser
from optparse import OptionParser, SUPPRESS_HELP
import sys
import os
import signal
import locale
locale.setlocale(locale.LC_ALL, '')
import curses
from textwrap import wrap
from subprocess import call


# optional features provided by non-standard modules
features = {'dns':False, 'geoip':False}
try:   import adns; features['dns'] = True     # resolve IP to host name
except ImportError: features['dns'] = False

try:   import GeoIP; features['geoip'] = True  # show country peer seems to be in
except ImportError:  features['geoip'] = False


# define config defaults
config = ConfigParser.SafeConfigParser()
config.add_section('Connection')
config.set('Connection', 'password', '')
config.set('Connection', 'username', '')
config.set('Connection', 'port', '9091')
config.set('Connection', 'host', 'localhost')
config.add_section('Sorting')
config.set('Sorting', 'order',   'name')
config.set('Sorting', 'reverse', 'False')
config.add_section('Filtering')
config.set('Filtering', 'filter', '')
config.set('Filtering', 'invert', 'False')




# Handle communication with Transmission server.
class TransmissionRequest:
    def __init__(self, host, port, method=None, tag=None, arguments=None):
        self.url = 'http://%s:%d/transmission/rpc' % (host, port)
        self.open_request  = None
        self.last_update   = 0
        if method and tag:
            self.set_request_data(method, tag, arguments)

    def set_request_data(self, method, tag, arguments=None):
        request_data = {'method':method, 'tag':tag}
        if arguments: request_data['arguments'] = arguments
#        debug(repr(request_data) + "\n")
        self.http_request = urllib2.Request(url=self.url, data=json.dumps(request_data))

    def send_request(self):
        """Ask for information from server OR submit command."""

        try:
            self.open_request = urllib2.urlopen(self.http_request)
        except AttributeError:
            # request data (http_request) isn't specified yet -- data will be available on next call
            pass
        except httplib.BadStatusLine, msg:
            # server sends something httplib doesn't understand.
            # (happens sometimes with high cpu load[?])
            pass  
        except urllib2.HTTPError, msg:
            msg = html2text(str(msg.read()))
            m = re.search('X-Transmission-Session-Id:\s*(\w+)', msg)
            try: # extract session id and send request again
                self.http_request.add_header('X-Transmission-Session-Id', m.group(1))
                self.send_request()
            except AttributeError: # a real error occurred
                quit(str(msg) + "\n", CONNECTION_ERROR)
        except urllib2.URLError, msg:
            try:
                reason = msg.reason[1]
            except IndexError:
                reason = str(msg.reason)
            quit("Cannot connect to %s: %s\n" % (self.http_request.host, reason), CONNECTION_ERROR)

    def get_response(self):
        """Get response to previously sent request."""

        if self.open_request == None:
            return {'result': 'no open request'}
        response = self.open_request.read()
        try:
            data = json.loads(response)
        except ValueError:
            quit("Cannot not parse response: %s\n" % response, JSON_ERROR)
        self.open_request = None
        return data


# End of Class TransmissionRequest


# Higher level of data exchange
class Transmission:
    STATUS_CHECK_WAIT = 1 << 0
    STATUS_CHECK      = 1 << 1
    STATUS_DOWNLOAD   = 1 << 2
    STATUS_SEED       = 1 << 3
    STATUS_STOPPED    = 1 << 4

    TAG_TORRENT_LIST    = 7
    TAG_TORRENT_DETAILS = 77
    TAG_SESSION_STATS   = 21
    TAG_SESSION_GET     = 22

    LIST_FIELDS = [ 'id', 'name', 'status', 'seeders', 'leechers', 'desiredAvailable',
                    'rateDownload', 'rateUpload', 'eta', 'uploadRatio',
                    'sizeWhenDone', 'haveValid', 'haveUnchecked', 'addedDate',
                    'uploadedEver', 'errorString', 'recheckProgress', 'swarmSpeed',
                    'peersKnown', 'peersConnected', 'uploadLimit', 'downloadLimit',
                    'uploadLimited', 'downloadLimited', 'bandwidthPriority']

    DETAIL_FIELDS = [ 'files', 'priorities', 'wanted', 'peers', 'trackers',
                      'activityDate', 'dateCreated', 'startDate', 'doneDate',
                      'totalSize', 'leftUntilDone', 'comment',
                      'announceURL', 'announceResponse', 'lastAnnounceTime',
                      'nextAnnounceTime', 'lastScrapeTime', 'nextScrapeTime',
                      'scrapeResponse', 'scrapeURL',
                      'hashString', 'timesCompleted', 'pieceCount', 'pieceSize', 'pieces',
                      'downloadedEver', 'corruptEver',
                      'peersFrom', 'peersSendingToUs', 'peersGettingFromUs' ] + LIST_FIELDS

    def __init__(self, host, port, username, password):
        self.host = host
        self.port = port

        if username and password:
            password_mgr = urllib2.HTTPPasswordMgrWithDefaultRealm()
            url = 'http://%s:%d/transmission/rpc' % (host, port)
            password_mgr.add_password(None, url, username, password)
            authhandler = urllib2.HTTPBasicAuthHandler(password_mgr)
            opener = urllib2.build_opener(authhandler)
            urllib2.install_opener(opener)

        # check rpc version
        request = TransmissionRequest(host, port, 'session-get', self.TAG_SESSION_GET)
        request.send_request()
        response = request.get_response()

        # rpc version too old?
        version_error = "Unsupported Transmission version: " + str(response['arguments']['version']) + \
            " -- RPC protocol version: " + str(response['arguments']['rpc-version']) + "\n"

        min_msg = "Please install Transmission version " + TRNSM_VERSION_MIN + " or higher.\n"
        try:
            if response['arguments']['rpc-version'] < RPC_VERSION_MIN:
                quit(version_error + min_msg)
        except KeyError:
            quit(version_error + min_msg)

        # rpc version too new?
        if response['arguments']['rpc-version'] > RPC_VERSION_MAX:
            quit(version_error + "Please install Transmission version " + TRNSM_VERSION_MAX + " or lower.\n")


        # set up request list
        self.requests = {'torrent-list':
                             TransmissionRequest(host, port, 'torrent-get', self.TAG_TORRENT_LIST, {'fields': self.LIST_FIELDS}),
                         'session-stats':
                             TransmissionRequest(host, port, 'session-stats', self.TAG_SESSION_STATS, 21),
                         'session-get':
                             TransmissionRequest(host, port, 'session-get', self.TAG_SESSION_GET),
                         'torrent-details':
                             TransmissionRequest(host, port)}

        self.torrent_cache = []
        self.status_cache  = dict()
        self.torrent_details_cache = dict()
        self.peer_progress_cache   = dict()
        self.hosts_cache   = dict()
        self.geo_ips_cache = dict()
        if features['dns']:   self.resolver = adns.init()
        if features['geoip']: self.geo_ip = GeoIP.new(GeoIP.GEOIP_MEMORY_CACHE)

        # make sure there are no undefined values
        self.wait_for_torrentlist_update()

        # this fills self.peer_progress_cache with initial values
        for t in self.torrent_cache:
            self.requests['torrent-details'].set_request_data('torrent-get', self.TAG_TORRENT_DETAILS,
                                                              {'ids':t['id'], 'fields': self.DETAIL_FIELDS})
            self.wait_for_details_update()
        self.requests['torrent-details'] = TransmissionRequest(self.host, self.port)


    def update(self, delay, tag_waiting_for=0):
        """Maintain up-to-date data."""

        tag_waiting_for_occurred = False

        for request in self.requests.values():
            if time.time() - request.last_update >= delay:
                request.last_update = time.time()
                response = request.get_response()

                if response['result'] == 'no open request':
                    request.send_request()

                elif response['result'] == 'success':
                    tag = self.parse_response(response)
                    if tag == tag_waiting_for:
                        tag_waiting_for_occurred = True

        if tag_waiting_for:
            return tag_waiting_for_occurred
        else:
            return None

                    

    def parse_response(self, response):
        # response is a reply to torrent-get
        if response['tag'] == self.TAG_TORRENT_LIST or response['tag'] == self.TAG_TORRENT_DETAILS:
            for t in response['arguments']['torrents']:
                t['uploadRatio'] = round(float(t['uploadRatio']), 2)
                t['percent_done'] = percent(float(t['sizeWhenDone']),
                                            float(t['haveValid'] + t['haveUnchecked']))

            if response['tag'] == self.TAG_TORRENT_LIST:
                self.torrent_cache = response['arguments']['torrents']

            elif response['tag'] == self.TAG_TORRENT_DETAILS:
                self.torrent_details_cache = response['arguments']['torrents'][0]
                self.upgrade_peerlist()

        elif response['tag'] == self.TAG_SESSION_STATS:
            self.status_cache.update(response['arguments'])

        elif response['tag'] == self.TAG_SESSION_GET:
            self.status_cache.update(response['arguments'])

        return response['tag']

    def upgrade_peerlist(self):
        for index,peer in enumerate(self.torrent_details_cache['peers']):
            ip = peer['address']
            peerid = ip + self.torrent_details_cache['hashString']

            # make sure peer cache exists
            if not self.peer_progress_cache.has_key(peerid):
                self.peer_progress_cache[peerid] = {'last_progress':0, 'last_update':0, 'download_speed':0, 'time_left':0}

            # estimate how fast a peer is downloading
            if peer['progress'] < 1:
                progress_diff = peer['progress'] - self.peer_progress_cache[peerid]['last_progress']
                if progress_diff > 0:
                    this_time  = time.time()
                    time_diff  = this_time - self.peer_progress_cache[peerid]['last_update']
                    downloaded = self.torrent_details_cache['totalSize'] * progress_diff
                    avg_speed  = downloaded / time_diff
                    avg_speed  = ((self.peer_progress_cache[peerid]['download_speed']*10) + avg_speed) /11  # make it less jumpy
                    downloaded_total = self.torrent_details_cache['totalSize'] \
                        - (self.torrent_details_cache['totalSize']*peer['progress'])
                    time_left  = downloaded_total / avg_speed

                    self.peer_progress_cache[peerid]['last_update']    = this_time  # remember update time
                    self.peer_progress_cache[peerid]['download_speed'] = avg_speed
                    self.peer_progress_cache[peerid]['time_left']      = time_left

                self.peer_progress_cache[peerid]['last_progress'] = peer['progress']  # remember progress
            self.torrent_details_cache['peers'][index].update(self.peer_progress_cache[peerid])
                
            # resolve and locate peer's ip
            if features['dns'] and not self.hosts_cache.has_key(ip):
                self.hosts_cache[ip] = self.resolver.submit_reverse(ip, adns.rr.PTR)
            if features['geoip'] and not self.geo_ips_cache.has_key(ip):
                self.geo_ips_cache[ip] = self.geo_ip.country_code_by_addr(ip)
                if self.geo_ips_cache[ip] == None:
                    self.geo_ips_cache[ip] = '?'


    def get_global_stats(self):
        return self.status_cache

    def get_torrent_list(self, sort_orders, reverse=False):
        try:
            for sort_order in sort_orders:
                if isinstance(self.torrent_cache[0][sort_order], (str, unicode)):
                    self.torrent_cache.sort(key=lambda x: x[sort_order].lower(), reverse=reverse)
                else:
                    self.torrent_cache.sort(key=lambda x: x[sort_order], reverse=reverse)
        except IndexError:
            return []
        return self.torrent_cache

    def get_torrent_by_id(self, id):
        i = 0
        while self.torrent_cache[i]['id'] != id:  i += 1
        if self.torrent_cache[i]['id'] == id:
            return self.torrent_cache[i]
        else:
            return None


    def get_torrent_details(self):
        return self.torrent_details_cache
    def set_torrent_details_id(self, id):
        if id < 0:
            self.requests['torrent-details'] = TransmissionRequest(self.host, self.port)
        else:
            self.requests['torrent-details'].set_request_data('torrent-get', self.TAG_TORRENT_DETAILS,
                                                              {'ids':id, 'fields': self.DETAIL_FIELDS})

    def get_hosts(self):
        return self.hosts_cache

    def get_geo_ips(self):
        return self.geo_ips_cache


    def set_option(self, option_name, option_value):
        request = TransmissionRequest(self.host, self.port, 'session-set', 1, {option_name: option_value})
        request.send_request()
        self.wait_for_status_update()


    def set_rate_limit(self, direction, new_limit, torrent_id=-1):
        data = dict()
        if new_limit < 0:
            return
        elif new_limit == 0:
            new_limit     = None
            limit_enabled = False
        else:
            limit_enabled = True

        if torrent_id < 0:
            type = 'session-set'
            data['speed-limit-'+direction]            = new_limit
            data['speed-limit-'+direction+'-enabled'] = limit_enabled
        else:
            type = 'torrent-set'
            data['ids'] = [torrent_id]
            data[direction+'loadLimit']   = new_limit
            data[direction+'loadLimited'] = limit_enabled

        request = TransmissionRequest(self.host, self.port, type, 1, data)
        request.send_request()
        self.wait_for_torrentlist_update()


    def increase_bandwidth_priority(self, torrent_id):
        torrent = self.get_torrent_by_id(torrent_id)
        if torrent == None or torrent['bandwidthPriority'] >= 1:
            return False
        else:
            new_priority = torrent['bandwidthPriority'] + 1
            request = TransmissionRequest(self.host, self.port, 'torrent-set', 1,
                                          {'ids': [torrent_id], 'bandwidthPriority':new_priority})
            request.send_request()
            self.wait_for_torrentlist_update()

    def decrease_bandwidth_priority(self, torrent_id):
        torrent = self.get_torrent_by_id(torrent_id)
        if torrent == None or torrent['bandwidthPriority'] <= -1:
            return False
        else:
            new_priority = torrent['bandwidthPriority'] - 1
            request = TransmissionRequest(self.host, self.port, 'torrent-set', 1,
                                          {'ids': [torrent_id], 'bandwidthPriority':new_priority})
            request.send_request()
            self.wait_for_torrentlist_update()
        

    def toggle_turtle_mode(self):
        self.set_option('alt-speed-enabled', not self.status_cache['alt-speed-enabled'])


    def stop_torrent(self, id):
        request = TransmissionRequest(self.host, self.port, 'torrent-stop', 1, {'ids': [id]})
        request.send_request()
        self.wait_for_torrentlist_update()

    def start_torrent(self, id):
        request = TransmissionRequest(self.host, self.port, 'torrent-start', 1, {'ids': [id]})
        request.send_request()
        self.wait_for_torrentlist_update()

    def verify_torrent(self, id):
        request = TransmissionRequest(self.host, self.port, 'torrent-verify', 1, {'ids': [id]})
        request.send_request()
        self.wait_for_torrentlist_update()

    def remove_torrent(self, id):
        request = TransmissionRequest(self.host, self.port, 'torrent-remove', 1, {'ids': [id]})
        request.send_request()
        self.wait_for_torrentlist_update()



    def increase_file_priority(self, file_nums):
        file_nums = list(file_nums)
        ref_num = file_nums[0]
        for num in file_nums:
            if not self.torrent_details_cache['wanted'][num]:
                ref_num = num
                break
            elif self.torrent_details_cache['priorities'][num] < \
                    self.torrent_details_cache['priorities'][ref_num]:
                ref_num = num
        current_priority = self.torrent_details_cache['priorities'][ref_num]
        if not self.torrent_details_cache['wanted'][ref_num]:
            self.set_file_priority(self.torrent_details_cache['id'], file_nums, 'low')
        elif current_priority == -1:
            self.set_file_priority(self.torrent_details_cache['id'], file_nums, 'normal')
        elif current_priority == 0:
            self.set_file_priority(self.torrent_details_cache['id'], file_nums, 'high')

    def decrease_file_priority(self, file_nums):
        file_nums = list(file_nums)
        ref_num = file_nums[0]
        for num in file_nums:
            if self.torrent_details_cache['priorities'][num] > \
                    self.torrent_details_cache['priorities'][ref_num]:
                ref_num = num
        current_priority = self.torrent_details_cache['priorities'][ref_num]
        if current_priority >= 1:
            self.set_file_priority(self.torrent_details_cache['id'], file_nums, 'normal')
        elif current_priority == 0:
            self.set_file_priority(self.torrent_details_cache['id'], file_nums, 'low')
        elif current_priority == -1:
            self.set_file_priority(self.torrent_details_cache['id'], file_nums, 'off')


    def set_file_priority(self, torrent_id, file_nums, priority):
        request_data = {'ids': [torrent_id]}
        if priority == 'off':
            request_data['files-unwanted'] = file_nums
        else:
            request_data['files-wanted'] = file_nums
            request_data['priority-' + priority] = file_nums
        request = TransmissionRequest(self.host, self.port, 'torrent-set', 1, request_data)
        request.send_request()
        self.wait_for_details_update()

    def get_file_priority(self, torrent_id, file_num):
        priority = self.torrent_details_cache['priorities'][file_num]
        if not self.torrent_details_cache['wanted'][file_num]: return 'off'
        elif priority <= -1: return 'low'
        elif priority == 0:  return 'normal'
        elif priority >= 1:  return 'high'
        return '?'


    def wait_for_torrentlist_update(self):
        self.wait_for_update(7)
    def wait_for_details_update(self):
        self.wait_for_update(77)
    def wait_for_status_update(self):
        self.wait_for_update(22)
    def wait_for_update(self, update_id):
        start = time.time()
        self.update(0) # send request
        while True:    # wait for response
            debug("still waiting for %d\n" % update_id)
            if self.update(0, update_id): break
            time.sleep(0.1)
        debug("delay was %dms\n\n" % ((time.time() - start) * 1000))
        

    def get_status(self, torrent):
        if torrent['status'] == Transmission.STATUS_CHECK_WAIT:
            status = 'will verify'
        elif torrent['status'] == Transmission.STATUS_CHECK:
            status = "verifying"
        elif torrent['status'] == Transmission.STATUS_SEED:
            status = 'seeding'
        elif torrent['status'] == Transmission.STATUS_DOWNLOAD:
            status = ('idle','downloading')[torrent['rateDownload'] > 0]
        elif torrent['status'] == Transmission.STATUS_STOPPED:
            status = 'paused'
        else:
            status = 'unknown state'
        return status

    def get_bandwidth_priority(self, torrent):
        if torrent['bandwidthPriority'] == -1:
            return '-'
        elif torrent['bandwidthPriority'] == 0:
            return ' '
        elif torrent['bandwidthPriority'] == 1:
            return '+'
        else:
            return '?'

# End of Class Transmission



    

# User Interface
class Interface:
    def __init__(self, server):
        self.server = server

        self.filter_list    = config.get('Filtering', 'filter')
        self.filter_inverse = config.getboolean('Filtering', 'invert')

        self.sort_orders  = config.get('Sorting', 'order').split(',') #['name']
        self.sort_reverse = config.getboolean('Sorting', 'reverse')

        self.selected_torrent = -1  # changes to >-1 when focus >-1 & user hits return
        self.torrents = self.server.get_torrent_list(self.sort_orders, self.sort_reverse)
        self.stats    = self.server.get_global_stats()

        self.focus     = -1  # -1: nothing focused; 0: top of list; <# of torrents>-1: bottom of list
        self.scrollpos = 0   # start of torrentlist
        self.torrents_per_page  = 0 # will be set by manage_layout()
        self.rateDownload_width = self.rateUpload_width = 2

        self.details_category_focus = 0  # overview/files/peers/tracker in details
        self.focus_detaillist       = -1 # same as focus but for details
        self.selected_files         = [] # marked files in details
        self.scrollpos_detaillist   = 0  # same as scrollpos but for details


        try:
            self.init_screen()
            self.run()
        except:
            self.restore_screen()
            (exc_type, exc_value, exc_traceback) = sys.exc_info()
            raise exc_type, exc_value, exc_traceback
        else:
            self.restore_screen()


    def init_screen(self):
        os.environ['ESCDELAY'] = '0' # make escape usable
        self.screen = curses.initscr()
        curses.noecho() ; curses.cbreak() ; self.screen.keypad(1)
        curses.halfdelay(10) # STDIN timeout
        
        try: curses.curs_set(0)   # hide cursor if possible
        except curses.error: pass # some terminals seem to have problems with that

        # enable colors if available
        try:
            curses.start_color()
            curses.init_pair(1, curses.COLOR_BLACK,   curses.COLOR_BLUE)  # download rate
            curses.init_pair(2, curses.COLOR_BLACK,   curses.COLOR_RED)   # upload rate
            curses.init_pair(3, curses.COLOR_BLUE,    curses.COLOR_BLACK) # unfinished progress
            curses.init_pair(4, curses.COLOR_GREEN,   curses.COLOR_BLACK) # finished progress
            curses.init_pair(5, curses.COLOR_BLACK,   curses.COLOR_WHITE) # eta/ratio
            curses.init_pair(6, curses.COLOR_CYAN,    curses.COLOR_BLACK) # idle progress
            curses.init_pair(7, curses.COLOR_MAGENTA, curses.COLOR_BLACK) # verifying
            curses.init_pair(8, curses.COLOR_WHITE,   curses.COLOR_BLACK) # button
            curses.init_pair(9, curses.COLOR_BLACK,   curses.COLOR_WHITE) # focused button
        except:
            pass

        signal.signal(signal.SIGWINCH, lambda y,frame: self.get_screen_size())
        self.get_screen_size()

    def restore_screen(self):
        curses.endwin()



    def get_screen_size(self):
        time.sleep(0.1) # prevents curses.error on rapid resizing
        while True:
            curses.endwin()
            self.screen.refresh()
            self.height, self.width = self.screen.getmaxyx()
            if self.width < 50 or self.height < 16:
                self.screen.erase()
                self.screen.addstr(0,0, "Terminal too small", curses.A_REVERSE + curses.A_BOLD)
                time.sleep(1)
            else:
                break
        self.manage_layout()


    def manage_layout(self):
        self.pad_height = max((len(self.torrents)+1)*3, self.height)
        self.pad = curses.newpad(self.pad_height, self.width)
        self.mainview_height = self.height - 2
        self.torrents_per_page  = self.mainview_height/3

        self.detaillistitems_per_page = self.height - 8

        if self.torrents:
            visible_torrents = self.torrents[self.scrollpos/3 : self.scrollpos/3 + self.torrents_per_page + 1]
            self.rateDownload_width = self.get_rateDownload_width(visible_torrents)
            self.rateUpload_width   = self.get_rateUpload_width(visible_torrents)

            self.torrent_title_width = self.width - self.rateUpload_width - 2
            # show downloading column only if any downloading torrents are visible
            if filter(lambda x: x['status']==Transmission.STATUS_DOWNLOAD, visible_torrents):
                self.torrent_title_width -= self.rateDownload_width + 2
        else:
            self.torrent_title_width = 80

    def get_rateDownload_width(self, torrents):
        new_width = max(map(lambda x: len(scale_bytes(x['rateDownload'])), torrents))
        new_width = max(max(map(lambda x: len(scale_time(x['eta'])), torrents)), new_width)
        new_width = max(len(scale_bytes(self.stats['downloadSpeed'])), new_width)
        new_width = max(self.rateDownload_width, new_width) # don't shrink
        return new_width

    def get_rateUpload_width(self, torrents):
        new_width = max(map(lambda x: len(scale_bytes(x['rateUpload'])), torrents))
        new_width = max(max(map(lambda x: len(num2str(x['uploadRatio'])), torrents)), new_width)
        new_width = max(len(scale_bytes(self.stats['uploadSpeed'])), new_width)
        new_width = max(self.rateUpload_width, new_width) # don't shrink
        return new_width


    def run(self):
        self.draw_title_bar()
        self.draw_stats()
        self.draw_torrent_list()

        while True:
            self.server.update(1)

            # display torrentlist
            if self.selected_torrent == -1:
                self.draw_torrent_list()

            # display some torrent's details
            else:
                self.draw_details()

            self.stats = self.server.get_global_stats()
            self.draw_title_bar()  # show shortcuts and stuff
            self.draw_stats()      # show global states

            self.screen.move(0,0)  # in case cursor can't be invisible
            self.handle_user_input()

    def handle_user_input(self):
        c = self.screen.getch()
        if c == -1:
            return

        # list all currently available key bindings
        elif c == ord('?') or c == curses.KEY_F1:
            self.list_key_bindings()

        # go back or unfocus
        elif c == 27 or c == curses.KEY_BREAK or c == 12:
            if self.focus_detaillist > -1:   # unfocus and deselect file
                self.focus_detaillist     = -1
                self.scrollpos_detaillist = 0
                self.selected_files       = []
            elif self.selected_torrent > -1: # return from details
                self.details_category_focus = 0
                self.selected_torrent = -1
                self.selected_files   = []
            else:
                if self.focus > -1:
                    self.scrollpos = 0    # unfocus main list
                    self.focus     = -1
                elif self.filter_list:
                    self.filter_list = '' # reset filter

        # leave details
        elif self.selected_torrent > -1 and (c == curses.KEY_BACKSPACE or 
                                             c == curses.KEY_UP and self.focus_detaillist == -1):
            self.server.set_torrent_details_id(-1)
            self.selected_torrent       = -1
            self.details_category_focus = 0
            self.scrollpos_detaillist   = 0
            self.selected_files         = []


        # go back or quit on q
        elif c == ord('q'):
            if self.selected_torrent == -1:
                config.set('Sorting', 'order',   ','.join(self.sort_orders))
                config.set('Sorting', 'reverse', str(self.sort_reverse))
                config.set('Filtering', 'filter', self.filter_list)
                config.set('Filtering', 'invert', str(self.filter_inverse))
                quit()
            else: # return to list view
                self.server.set_torrent_details_id(-1)
                self.selected_torrent       = -1
                self.details_category_focus = 0
                self.focus_detaillist       = -1
                self.scrollpos_detaillist   = 0
                self.selected_files         = []


        # show options window
        elif self.selected_torrent == -1 and c == ord('o'):
            self.draw_options_dialog()


        # select torrent for detailed view
        elif (c == ord("\n") or c == curses.KEY_RIGHT) and self.focus > -1 and self.selected_torrent == -1:
            self.screen.clear()
            self.selected_torrent = self.focus
            self.server.set_torrent_details_id(self.torrents[self.focus]['id'])
            self.server.wait_for_details_update()

        # show sort order menu
        elif c == ord('s') and self.selected_torrent == -1:
            options = [('name','_Name'), ('addedDate','_Age'), ('percent_done','_Progress'),
                       ('seeders','_Seeds'), ('leechers','Lee_ches'), ('sizeWhenDone', 'Si_ze'),
                       ('status','S_tatus'), ('uploadedEver','Up_loaded'),
                       ('rateUpload','_Upload Speed'), ('rateDownload','_Download Speed'),
                       ('swarmSpeed','Swar_m Rate'), ('uploadRatio','_Ratio'),
                       ('peersConnected','P_eers'), ('reverse','Re_verse')]
            choice = self.dialog_menu('Sort order', options,
                                      map(lambda x: x[0]==self.sort_orders[-1], options).index(True)+1)
            if choice == 'reverse':
                self.sort_reverse = not self.sort_reverse
            else:
                self.sort_orders.append(choice)
                while len(self.sort_orders) > 2:
                    self.sort_orders.pop(0)

        # show state filter menu
        elif c == ord('f') and self.selected_torrent == -1:
            options = [('uploading','_Uploading'), ('downloading','_Downloading'),
                       ('active','Ac_tive'), ('paused','_Paused'), ('seeding','_Seeding'),
                       ('incomplete','In_complete'), ('verifying','Verif_ying'),
                       ('invert','In_vert'), ('','_All')]
            choice = self.dialog_menu(('Show only','Filter all')[self.filter_inverse], options,
                                      map(lambda x: x[0]==self.filter_list, options).index(True)+1)
            if choice == 'invert':
                self.filter_inverse = not self.filter_inverse
            else:
                if choice == '': self.filter_inverse = False
                self.filter_list = choice


        # global upload/download limits
        elif c == ord('u'):
            current_limit = (0,self.stats['speed-limit-up'])[self.stats['speed-limit-up-enabled']]
            limit = self.dialog_input_number("Global upload limit in kilobytes per second", current_limit)
            self.server.set_rate_limit('up', limit)
        elif c == ord('d'):
            current_limit = (0,self.stats['speed-limit-down'])[self.stats['speed-limit-down-enabled']]
            limit = self.dialog_input_number("Global download limit in kilobytes per second", current_limit)
            self.server.set_rate_limit('down', limit)

        # per torrent upload/download limits
        elif c == ord('U') and self.focus > -1:
            current_limit = (0,self.torrents[self.focus]['uploadLimit'])[self.torrents[self.focus]['uploadLimited']]
            limit = self.dialog_input_number("Upload limit in kilobytes per second for\n%s" % \
                                                 self.torrents[self.focus]['name'], current_limit)
            self.server.set_rate_limit('up', limit, self.torrents[self.focus]['id'])
        elif c == ord('D') and self.focus > -1:
            current_limit = (0,self.torrents[self.focus]['downloadLimit'])[self.torrents[self.focus]['downloadLimited']]
            limit = self.dialog_input_number("Download limit in Kilobytes per second for\n%s" % \
                                                 self.torrents[self.focus]['name'], current_limit)
            self.server.set_rate_limit('down', limit, self.torrents[self.focus]['id'])

        # toggle turtle mode
        elif c == ord('t') and self.selected_torrent == -1:
            self.server.toggle_turtle_mode()


        # torrent bandwidth priority
        elif c == ord('-') and self.focus > -1:
            self.server.decrease_bandwidth_priority(self.torrents[self.focus]['id'])
        elif c == ord('+') and self.focus > -1:
            self.server.increase_bandwidth_priority(self.torrents[self.focus]['id'])


        # pause/unpause torrent
        elif c == ord('p') and self.focus > -1:
            if self.torrents[self.focus]['status'] == Transmission.STATUS_STOPPED:
                self.server.start_torrent(self.torrents[self.focus]['id'])
            else:
                self.server.stop_torrent(self.torrents[self.focus]['id'])
            
        # verify torrent data
        elif self.focus > -1 and (c == ord('v') or c == ord('y')):
            if self.torrents[self.focus]['status'] != Transmission.STATUS_CHECK:
                self.server.verify_torrent(self.torrents[self.focus]['id'])

        # remove torrent
        elif self.focus > -1 and (c == ord('r') or c == curses.KEY_DC):
            name = self.torrents[self.focus]['name'][0:self.width - 15]
            if self.dialog_yesno("Remove %s?" % name.encode('utf8')) == True:
                if self.selected_torrent > -1:  # leave details
                    self.server.set_torrent_details_id(-1)
                    self.selected_torrent = -1
                    self.details_category_focus = 0
                self.server.remove_torrent(self.torrents[self.focus]['id'])


        # movement in torrent list
        elif self.selected_torrent == -1:
            if   c == curses.KEY_UP:
                self.focus, self.scrollpos = self.move_up(self.focus, self.scrollpos, 3)
            elif c == curses.KEY_DOWN:
                self.focus, self.scrollpos = self.move_down(self.focus, self.scrollpos, 3,
                                                            self.torrents_per_page, len(self.torrents))
            elif c == curses.KEY_PPAGE:
                self.focus, self.scrollpos = self.move_page_up(self.focus, self.scrollpos, 3,
                                                               self.torrents_per_page)
            elif c == curses.KEY_NPAGE:
                self.focus, self.scrollpos = self.move_page_down(self.focus, self.scrollpos, 3,
                                                                 self.torrents_per_page, len(self.torrents))
            elif c == curses.KEY_HOME:
                self.focus, self.scrollpos = self.move_to_top()
            elif c == curses.KEY_END:
                self.focus, self.scrollpos = self.move_to_end(3, self.torrents_per_page, len(self.torrents))


        # torrent details
        elif self.selected_torrent > -1:
            if c == ord("\t"): self.next_details()
            elif c == ord('o'): self.details_category_focus = 0
            elif c == ord('f'): self.details_category_focus = 1
            elif c == ord('e'): self.details_category_focus = 2
            elif c == ord('t'): self.details_category_focus = 3
            elif c == ord('c'): self.details_category_focus = 4

            # file priority OR walk through details
            elif c == curses.KEY_RIGHT:
                if self.details_category_focus == 1 and \
                        (self.selected_files or self.focus_detaillist > -1):
                    if self.selected_files:
                        files = set(self.selected_files)
                        self.server.increase_file_priority(files)
                    elif self.focus_detaillist > -1:
                        self.server.increase_file_priority([self.focus_detaillist])
                else:
                    self.scrollpos_detaillist = 0
                    self.next_details()
            elif c == curses.KEY_LEFT:
                if self.details_category_focus == 1 and \
                        (self.selected_files or self.focus_detaillist > -1):
                    if self.selected_files:
                        files = set(self.selected_files)
                        self.server.decrease_file_priority(files)
                    elif self.focus_detaillist > -1:
                        self.server.decrease_file_priority([self.focus_detaillist])
                else:
                    self.scrollpos_detaillist = 0
                    self.prev_details()

            # file list
            if self.details_category_focus == 1:
                # file selection with space
                if c == ord(' '):
                    try:
                        self.selected_files.pop(self.selected_files.index(self.focus_detaillist))
                    except ValueError:
                        self.selected_files.append(self.focus_detaillist)
                    curses.ungetch(curses.KEY_DOWN) # move down
                # (un)select all files
                elif c == ord('a'):
                    if self.selected_files:
                        self.selected_files = []
                    else:
                        self.selected_files = range(0, len(self.torrent_details['files']))

                # focus/movement
                elif c == curses.KEY_UP:
                    self.focus_detaillist, self.scrollpos_detaillist = \
                        self.move_up(self.focus_detaillist, self.scrollpos_detaillist, 1)
                elif c == curses.KEY_DOWN:
                    self.focus_detaillist, self.scrollpos_detaillist = \
                        self.move_down(self.focus_detaillist, self.scrollpos_detaillist, 1,
                                       self.detaillistitems_per_page, len(self.torrent_details['files']))
                elif c == curses.KEY_PPAGE:
                    self.focus_detaillist, self.scrollpos_detaillist = \
                        self.move_page_up(self.focus_detaillist, self.scrollpos_detaillist, 1,
                                          self.detaillistitems_per_page)
                elif c == curses.KEY_NPAGE:
                    self.focus_detaillist, self.scrollpos_detaillist = \
                        self.move_page_down(self.focus_detaillist, self.scrollpos_detaillist, 1,
                                            self.detaillistitems_per_page, len(self.torrent_details['files']))
                elif c == curses.KEY_HOME:
                    self.focus_detaillist, self.scrollpos_detaillist = self.move_to_top()
                elif c == curses.KEY_END:
                    self.focus_detaillist, self.scrollpos_detaillist = \
                        self.move_to_end(1, self.detaillistitems_per_page, len(self.torrent_details['files']))

            # peer list movement
            elif self.details_category_focus == 2:
                list_len = len(self.torrent_details['peers'])
                if c == curses.KEY_UP:
                    if self.scrollpos_detaillist > 0:
                        self.scrollpos_detaillist -= 1
                elif c == curses.KEY_DOWN:
                    if self.scrollpos_detaillist < list_len - self.detaillistitems_per_page:
                        self.scrollpos_detaillist += 1
                elif c == curses.KEY_HOME:
                    self.scrollpos_detaillist = 0
                elif c == curses.KEY_END:
                    self.scrollpos_detaillist = list_len - self.detaillistitems_per_page

        else:
            return # don't recognize key

        # update view
        if self.selected_torrent == -1:
            self.draw_torrent_list()
        else:
            self.draw_details()



    def filter_torrent_list(self):
        unfiltered = self.torrents
        if self.filter_list == 'downloading':
            self.torrents = [t for t in self.torrents if t['rateDownload'] > 0]
        elif self.filter_list == 'uploading':
            self.torrents = [t for t in self.torrents if t['rateUpload'] > 0]
        elif self.filter_list == 'paused':
            self.torrents = [t for t in self.torrents if t['status'] == Transmission.STATUS_STOPPED]
        elif self.filter_list == 'seeding':
            self.torrents = [t for t in self.torrents if t['status'] == Transmission.STATUS_SEED]
        elif self.filter_list == 'incomplete':
            self.torrents = [t for t in self.torrents if t['percent_done'] < 100]
        elif self.filter_list == 'active':
            self.torrents = [t for t in self.torrents if not t['rateDownload'] == t['rateUpload'] == 0]
        elif self.filter_list == 'verifying':
            self.torrents = [t for t in self.torrents if t['status'] == Transmission.STATUS_CHECK \
                                 or t['status'] == Transmission.STATUS_CHECK_WAIT]
        # invert list?
        if self.filter_inverse:
            self.torrents = [t for t in unfiltered if t not in self.torrents]

    def follow_list_focus(self, id):
        if self.focus == -1:
            return
        elif len(self.torrents) == 0:
            self.focus, self.scrollpos = -1, 0
            return

        self.focus = min(self.focus, len(self.torrents)-1)
        if self.torrents[self.focus]['id'] != id:
            for i,t in enumerate(self.torrents):
                if id == t['id']:
                    new_focus = i
                    break
            try:
                self.focus = new_focus
            except UnboundLocalError:
                self.focus, self.scrollpos = -1, 0
                return

        # make sure the focus is not above the visible area
        while self.focus < (self.scrollpos/3):
            self.scrollpos -= 3
        # make sure the focus is not below the visible area
        while self.focus > (self.scrollpos/3) + self.torrents_per_page-1:
            self.scrollpos += 3
        # keep min and max bounds
        self.scrollpos = min(self.scrollpos, (len(self.torrents) - self.torrents_per_page) * 3)
        self.scrollpos = max(0, self.scrollpos)

    def draw_torrent_list(self):
        try:
            focused_id = self.torrents[self.focus]['id']
        except IndexError:
            focused_id = -1
        self.torrents = self.server.get_torrent_list(self.sort_orders, self.sort_reverse)
        self.filter_torrent_list()
        self.follow_list_focus(focused_id)
        self.manage_layout()

        ypos = 0
        for i in range(len(self.torrents)):
            self.draw_torrentlist_item(self.torrents[i], (i == self.focus), ypos)
            ypos += 3

        self.pad.refresh(self.scrollpos,0, 1,0, self.mainview_height,self.width-1)
        self.screen.refresh()


    def draw_torrentlist_item(self, torrent, focused, y):
        # the torrent name is also a progress bar
        self.draw_torrentlist_title(torrent, focused, self.torrent_title_width, y)

        rates = ''
        if torrent['status'] == Transmission.STATUS_DOWNLOAD:
            self.draw_downloadrate(torrent, y)
        if torrent['status'] == Transmission.STATUS_DOWNLOAD or torrent['status'] == Transmission.STATUS_SEED:
            self.draw_uploadrate(torrent, y)
        if torrent['percent_done'] < 100 and torrent['status'] == Transmission.STATUS_DOWNLOAD:
            self.draw_eta(torrent, y)

        self.draw_ratio(torrent, y)

        # the line below the title/progress
        self.draw_torrentlist_status(torrent, focused, y)



    def draw_downloadrate(self, torrent, ypos):
        self.pad.move(ypos, self.width-self.rateDownload_width-self.rateUpload_width-3)
        self.pad.addch(curses.ACS_DARROW, (0,curses.A_BOLD)[torrent['downloadLimited']])
        rate = ('',scale_bytes(torrent['rateDownload']))[torrent['rateDownload']>0]
        self.pad.addstr(rate.rjust(self.rateDownload_width),
                        curses.color_pair(1) + curses.A_BOLD + curses.A_REVERSE)
    def draw_uploadrate(self, torrent, ypos):
        self.pad.move(ypos, self.width-self.rateUpload_width-1)
        self.pad.addch(curses.ACS_UARROW, (0,curses.A_BOLD)[torrent['uploadLimited']])
        rate = ('',scale_bytes(torrent['rateUpload']))[torrent['rateUpload']>0]
        self.pad.addstr(rate.rjust(self.rateUpload_width),
                        curses.color_pair(2) + curses.A_BOLD + curses.A_REVERSE)
    def draw_ratio(self, torrent, ypos):
        self.pad.addch(ypos+1, self.width-self.rateUpload_width-1, curses.ACS_DIAMOND,
                       (0,curses.A_BOLD)[torrent['uploadRatio'] < 1 and torrent['uploadRatio'] >= 0])
        self.pad.addstr(ypos+1, self.width-self.rateUpload_width,
                        num2str(torrent['uploadRatio']).rjust(self.rateUpload_width),
                        curses.color_pair(5) + curses.A_BOLD + curses.A_REVERSE)
    def draw_eta(self, torrent, ypos):
        self.pad.addch(ypos+1, self.width-self.rateDownload_width-self.rateUpload_width-3, curses.ACS_PLMINUS)
        self.pad.addstr(ypos+1, self.width-self.rateDownload_width-self.rateUpload_width-2,
                        scale_time(torrent['eta']).rjust(self.rateDownload_width),
                        curses.color_pair(5) + curses.A_BOLD + curses.A_REVERSE)


    def draw_torrentlist_title(self, torrent, focused, width, ypos):
        if torrent['status'] == Transmission.STATUS_CHECK:
            percent_done = float(torrent['recheckProgress']) * 100
        else:
            percent_done = torrent['percent_done']

        bar_width = int(float(width) * (float(percent_done)/100))
        title = torrent['name'][0:width].ljust(width)

        size = "%5s" % scale_bytes(torrent['sizeWhenDone'])
        if torrent['percent_done'] < 100:
            if torrent['seeders'] <= 0 and torrent['status'] != Transmission.STATUS_CHECK:
                available = torrent['desiredAvailable'] + torrent['haveValid'] + torrent['haveUnchecked']
                size = "%5s / " % scale_bytes(available) + size
            size = "%5s / " % scale_bytes(torrent['haveValid'] + torrent['haveUnchecked']) + size
        size = '| ' + size
        title = title[:-len(size)] + size

        if torrent['status'] == Transmission.STATUS_SEED:
            color = curses.color_pair(4)
        elif torrent['status'] == Transmission.STATUS_STOPPED:
            color = curses.color_pair(5) + curses.A_UNDERLINE
        elif torrent['status'] == Transmission.STATUS_CHECK or \
                torrent['status'] == Transmission.STATUS_CHECK_WAIT:
            color = curses.color_pair(7)
        elif torrent['rateDownload'] == 0:
            color = curses.color_pair(6)
        elif torrent['percent_done'] < 100:
            color = curses.color_pair(3)
        else:
            color = 0

        tag = curses.A_REVERSE
        tag_done = tag + color
        if focused:
            tag += curses.A_BOLD
            tag_done += curses.A_BOLD

        title = title.encode('utf-8')
        # addstr() dies when you tell it to draw on the last column of the
        # terminal, so we have to catch this exception.
        try:
            self.pad.addstr(ypos, 0, title[0:bar_width], tag_done)
            self.pad.addstr(ypos, bar_width, title[bar_width:], tag)
        except:
            pass


    def draw_torrentlist_status(self, torrent, focused, ypos):
        peers = ''
        parts = [self.server.get_status(torrent)]

        # show tracker error if appropriate
        if torrent['errorString'] and \
                not torrent['status'] == Transmission.STATUS_STOPPED and \
                torrent['peersKnown'] == 0:
            parts[0] = torrent['errorString']

        else:
            if torrent['status'] == Transmission.STATUS_CHECK:
                parts[0] += " (%d%%)" % int(float(torrent['recheckProgress']) * 100)
            elif torrent['status'] == Transmission.STATUS_DOWNLOAD:
                parts[0] += " (%d%%)" % torrent['percent_done']
            parts[0] = parts[0].ljust(20)

            # seeds and leeches will be appended right justified later
            peers  = "%4s seed%s " % (num2str(torrent['seeders']), ('s', ' ')[torrent['seeders']==1])
            peers += "%4s leech%s" % (num2str(torrent['leechers']), ('es', '  ')[torrent['leechers']==1])

            # show additional information if enough room
            if self.torrent_title_width - sum(map(lambda x: len(x), parts)) - len(peers) > 18:
                uploaded = scale_bytes(torrent['uploadedEver'])
                parts.append("%7s uploaded" % ('nothing',uploaded)[uploaded != '0B'])

            if self.torrent_title_width - sum(map(lambda x: len(x), parts)) - len(peers) > 18:
                swarm_rate = scale_bytes(torrent['swarmSpeed'])
                parts.append("%5s swarm rate" % ('no',swarm_rate)[swarm_rate != ''])

            if self.torrent_title_width - sum(map(lambda x: len(x), parts)) - len(peers) > 22:
                parts.append("%4s peer%s connected" % (torrent['peersConnected'],
                                                       ('s',' ')[torrent['peersConnected'] == 1]))

            
        if focused: tags = curses.A_REVERSE + curses.A_BOLD
        else:       tags = 0

        remaining_space = self.torrent_title_width - sum(map(lambda x: len(x), parts), len(peers)) - 2
        delimiter = ' ' * int(remaining_space / (len(parts)))

        line = self.server.get_bandwidth_priority(torrent) + ' ' + delimiter.join(parts)

        # make sure the peers element is always right justified
        line += ' ' * int(self.torrent_title_width - len(line) - len(peers)) + peers
        self.pad.addstr(ypos+1, 0, line, tags)
        



    def draw_details(self):
        self.torrent_details = self.server.get_torrent_details()

        # details could need more space than the torrent list
        self.pad_height = max(50, len(self.torrent_details['files'])+10, (len(self.torrents)+1)*3, self.height)
        self.pad = curses.newpad(self.pad_height, self.width)

        # torrent name + progress bar
        self.draw_torrentlist_item(self.torrent_details, False, 0)

        # divider + menu
        menu_items = ['_Overview', "_Files", 'P_eers', '_Tracker', 'Pie_ces' ]
        xpos = int((self.width - sum(map(lambda x: len(x), menu_items))-len(menu_items)) / 2)
        for item in menu_items:
            self.pad.move(3, xpos)
            tags = curses.A_BOLD
            if menu_items.index(item) == self.details_category_focus:
                tags += curses.A_REVERSE
            title = item.split('_')
            self.pad.addstr(title[0], tags)
            self.pad.addstr(title[1][0], tags + curses.A_UNDERLINE)
            self.pad.addstr(title[1][1:], tags)
            xpos += len(item)+1

        # which details to display
        if self.details_category_focus == 0:
            self.draw_details_overview(5)
        elif self.details_category_focus == 1:
            self.draw_filelist(5)
        elif self.details_category_focus == 2:
            self.draw_peerlist(5)
        elif self.details_category_focus == 3:
            self.draw_trackerlist(5)
        elif self.details_category_focus == 4:
            self.draw_pieces_map(5)

        self.pad.refresh(0,0, 1,0, self.height-2,self.width)
        self.screen.refresh()


    def draw_details_overview(self, ypos):
        t = self.torrent_details
        info = []
        info.append(['Hash: ', "%s" % t['hashString']])
        info.append(['ID: ',   "%s" % t['id']])

        wanted = 0
        for i, file_info in enumerate(t['files']):
            if t['wanted'][i] == True: wanted += t['files'][i]['length']

        info.append(['Size: ', "%s; " % scale_bytes(t['totalSize'], 'long'),
                     "%s wanted; " % (scale_bytes(wanted, 'long'),'everything') [t['totalSize'] == wanted],
                     "%s left" % scale_bytes(t['leftUntilDone'], 'long')])

        info.append(['Files: ', "%d; " % len(t['files'])])
        complete     = map(lambda x: x['bytesCompleted'] == x['length'], t['files']).count(True)
        not_complete = filter(lambda x: x['bytesCompleted'] != x['length'], t['files'])
        partial      = map(lambda x: x['bytesCompleted'] > 0, not_complete).count(True)
        if complete == len(t['files']):
            info[-1].append("all complete")
        else:
            info[-1].append("%d complete; " % complete)
            info[-1].append("%d commenced" % partial)

        info.append(['Pieces: ', "%s; " % t['pieceCount'],
                     "%s each" % scale_bytes(t['pieceSize'], 'long')])

        info.append(['Download: '])
        info[-1].append("%s" % scale_bytes(t['downloadedEver'], 'long') + \
                        " (%d%%) received; " % int(percent(t['sizeWhenDone'], t['downloadedEver'])))
        info[-1].append("%s" % scale_bytes(t['haveValid'], 'long') + \
                        " (%d%%) verified; " % int(percent(t['sizeWhenDone'], t['haveValid'])))
        info[-1].append("%s corrupt"  % scale_bytes(t['corruptEver'], 'long'))
        if t['percent_done'] < 100:
            info[-1][-1] += '; '
            if t['rateDownload']:
                info[-1].append("receiving %s per second" % scale_bytes(t['rateDownload'], 'long'))
                if t['downloadLimited']:
                    info[-1][-1] += " (throttled to %s)" % scale_bytes(t['downloadLimit']*1024, 'long')
            else:
                info[-1].append("no reception in progress")

        try:
            copies_distributed = (float(t['uploadedEver']) / float(t['sizeWhenDone']))
        except ZeroDivisionError:
            copies_distributed = 0
        info.append(['Upload: ', "%s " % scale_bytes(t['uploadedEver'], 'long') + \
                         "(%.2f copies) distributed; " % copies_distributed])
        if t['rateUpload']:
            info[-1].append("sending %s per second" % scale_bytes(t['rateUpload'], 'long'))
            if t['uploadLimited']:
                info[-1][-1] += " (throttled to %s)" % scale_bytes(t['uploadLimit']*1024, 'long')
        else:
            info[-1].append("no transmission in progress")

        info.append(['Peers: ', "%d reported by tracker; " % t['peersKnown'],
                     "connected to %d; "                  % t['peersConnected'],
                     "downloading from %d; "              % t['peersSendingToUs'],
                     "uploading to %d"                    % t['peersGettingFromUs']])

        ypos = self.draw_details_list(ypos, info)

        self.pad.addstr(ypos, 1, "Tracker has seen %s clients completing this torrent." \
                            % num2str(t['timesCompleted']))

        self.draw_details_eventdates(ypos+2)
        return ypos+2

    def draw_details_eventdates(self, ypos):
        t = self.torrent_details

        self.pad.addstr(ypos,   1, ' Created: ' + timestamp(t['dateCreated']))
        self.pad.addstr(ypos+1, 1, '   Added: ' + timestamp(t['addedDate']))
        self.pad.addstr(ypos+2, 1, ' Started: ' + timestamp(t['startDate']))
        self.pad.addstr(ypos+3, 1, 'Activity: ' + timestamp(t['activityDate']))

        if t['percent_done'] < 100 and t['eta'] > 0:
            self.pad.addstr(ypos+4, 1, 'Finished: ' + timestamp(time.time() + t['eta']))
        elif t['doneDate'] <= 0:
            self.pad.addstr(ypos+4, 1, 'Finished: sometime')
        else:
            self.pad.addstr(ypos+4, 1, 'Finished: ' + timestamp(t['doneDate']))

        if self.width >= 75 and t['comment']:
            width = self.width - 50
            comment = wrap('Comment: ' + t['comment'], width)
            for i, line in enumerate(comment):
                self.pad.addstr(ypos+i, 50, line)


    def draw_filelist(self, ypos):
        column_names = '  #  Progress  Size  Priority  Filename'
        self.pad.addstr(ypos, 0, column_names.ljust(self.width), curses.A_UNDERLINE)
        ypos += 1

        for line in self.create_filelist():
            curses_tags = 0
            while line.startswith('_'):
                if line[1] == 'S':
                    curses_tags  = curses.A_BOLD
                    line = line[2:]
                if line[1] == 'F':
                    curses_tags += curses.A_REVERSE
                    line = line[2:]
                self.pad.addstr(ypos, 0, ' '*self.width, curses_tags)
            self.pad.addstr(ypos, 0, line, curses_tags)
            ypos += 1

    def create_filelist(self):
        filelist = []
        start = self.scrollpos_detaillist
        end   = self.scrollpos_detaillist + self.detaillistitems_per_page
        for index in range(start, end):
            filelist.append(self.create_filelist_line(index))
        return filelist

    def create_filelist_line(self, index):
        try:
            file = self.torrent_details['files'][index]
        except IndexError:
            return ''
        line = str(index+1).rjust(3) + \
            "  %6.1f%%" % percent(file['length'], file['bytesCompleted']) + \
            '  '+scale_bytes(file['length']).rjust(5) + \
            '  '+self.server.get_file_priority(self.torrent_details['id'], index).center(8) + \
            "  %s" % file['name'][0:self.width-31].encode('utf-8')
        if index == self.focus_detaillist:
            line = '_F' + line
        if index in self.selected_files:
            line = '_S' + line
        return line


    def draw_peerlist(self, ypos):
        start = self.scrollpos_detaillist
        end   = self.scrollpos_detaillist + self.detaillistitems_per_page
        peers = self.torrent_details['peers'][start:end]

        clientname_width = 0
        for peer in peers:
            if len(peer['clientName']) > clientname_width:
                clientname_width = len(peer['clientName'])
        
        column_names = "Flags %3d Down %3d Up   Progress     ETA   " % \
            (self.torrent_details['peersSendingToUs'], self.torrent_details['peersGettingFromUs'])
        column_names += 'Client'.ljust(clientname_width) + "          Address"
        if features['geoip']: column_names += "  Country"
        if features['dns']: column_names += "  Host"

        self.pad.addstr(ypos, 0, column_names.ljust(self.width), curses.A_UNDERLINE)
        ypos += 1

        hosts = self.server.get_hosts()
        geo_ips = self.server.get_geo_ips()
        for index, peer in enumerate(peers):
            if features['dns']:
                try:
                    host = hosts[peer['address']].check()
                    try:
                        host_name = host[3][0]
                    except IndexError:
                        host_name = "<not resolvable>"
                except adns.NotReady:
                    host_name = "<resolving>"
                except adns.Error, msg:
                    host_name = msg

            clientname = peer['clientName']
            if len(clientname) > clientname_width:
                clientname = middlecut(peer['clientName'], clientname_width)

            upload_tag = download_tag = line_tag = 0
            if peer['rateToPeer']:   upload_tag   = curses.A_BOLD
            if peer['rateToClient']: download_tag = curses.A_BOLD

            self.pad.move(ypos, 0)
            self.pad.addstr("%-6s   " % peer['flagStr'])
            self.pad.addstr("%5s  " % scale_bytes(peer['rateToClient']), download_tag)
            self.pad.addstr("%5s   " % scale_bytes(peer['rateToPeer']), upload_tag)

            self.pad.addstr("%3d%%" % (float(peer['progress'])*100), curses.A_BOLD)
            if peer['progress'] < 1 and peer['download_speed'] > 1024:
                self.pad.addstr(" @ ")
                self.pad.addch(curses.ACS_PLMINUS)
                self.pad.addstr("%-4s" % scale_bytes(peer['download_speed']))
                self.pad.addstr(" ")
                self.pad.addch(curses.ACS_PLMINUS)
                self.pad.addstr("%-3s" % scale_time(peer['time_left']))
                self.pad.addstr("  ")
            else:
                self.pad.addstr("               ")



            self.pad.addstr(clientname.ljust(clientname_width).encode('utf-8'))
            self.pad.addstr("  %15s  " % peer['address'])
            if features['geoip']:
                self.pad.addstr("  %2s     " % geo_ips[peer['address']])
            if features['dns']:
                self.pad.addstr(host_name.encode('utf-8'), curses.A_DIM)
            ypos += 1


    def draw_trackerlist(self, ypos):
        t = self.torrent_details
        # find active tracker
        active   = ''
        inactive = []
        for tracker in t['trackers']:
            if tracker['announce'] == t['announceURL']:
                active = tracker
            else:
                inactive.append(tracker)

        # show active tracker
        self.pad.addstr(ypos, 0, active['announce'])
        self.pad.addstr(ypos+1, 2, "  Latest announce: %s" % timestamp(t['lastAnnounceTime']))
        self.pad.addstr(ypos+2, 2, "Announce response: %s" % t['announceResponse'])
        self.pad.addstr(ypos+3, 2, "    Next announce: %s" % timestamp(t['nextAnnounceTime']))
        if t['errorString']:
            self.pad.addstr(ypos+4, 2, "Error: %s" % t['errorString'])

        if not active['scrape']:
            active['scrape'] = "No scrape URL announced"

        scrape_width   = max(60, len(active['scrape']))
        announce_width = max(60, len(active['announce']))
        if self.width < announce_width + scrape_width + 2:
            xpos = 0
            ypos += 6
        else:
            xpos = announce_width + 2
        self.pad.addstr(ypos,   xpos, active['scrape'])
        self.pad.addstr(ypos+1, xpos+2, "  Latest scrape: %s" % timestamp(t['lastScrapeTime']))
        self.pad.addstr(ypos+2, xpos+2, "Scrape response: %s" % t['scrapeResponse'])
        self.pad.addstr(ypos+3, xpos+2, "    Next scrape: %s" % timestamp(t['nextScrapeTime']))
        ypos += 5
        if self.width >= announce_width + scrape_width + 2:
            ypos += 1
        
        # show a list of inactive trackers
        if inactive:
            self.pad.addstr(ypos, 0, "%d Fallback Tracker%s:" % (len(inactive), ('','s')[len(inactive)>1]) )

            # find longest tracker url to make multiple columns if necessary
            max_url_length = max(map(lambda x: len(x['announce']), inactive))

            ypos_start = ypos + 1
            xpos = 0
            for tracker in inactive:
                ypos += 1
                if ypos >= self.height:
                    # start new column
                    xpos += max_url_length + 2
                    ypos = ypos_start
                if xpos+max_url_length > self.width:
                    # all possible columns full
                    break
                self.pad.addstr(ypos, xpos, tracker['announce'])


    def draw_pieces_map(self, ypos):
        pieces = ''
        for p in base64.decodestring(self.torrent_details['pieces']):
            pieces += int2bin(ord(p))
        pieces = pieces[:self.torrent_details['pieceCount']] # strip off non-existent pieces

        map_width = int(str(self.width-7)[0:-1] + '0')
        for x in range(10, map_width, 10):
            self.pad.addstr(ypos, x+5, str(x), curses.A_BOLD)
        ypos += 1

        xpos = 6 ; counter = 1
        self.pad.addstr(ypos, 1, "%4d" % 0, curses.A_BOLD)
        for piece in pieces:
            if int(piece): self.pad.addch(ypos, xpos, ' ', curses.A_REVERSE)
            else:          self.pad.addch(ypos, xpos, '_')
            if counter % map_width == 0:
                ypos += 1 ; xpos = 6
                self.pad.addstr(ypos, 1, "%4d" % counter, curses.A_BOLD)
            else:
                xpos += 1

            # end map if terminal is too small
            if ypos >= self.height-3:
                line = ('[' + str(len(pieces)-counter) + ' more pieces not listed]').center(self.width)
                self.pad.addstr(ypos, 1, line, curses.A_BOLD)
                break
            else:
                counter += 1


    def draw_details_list(self, ypos, info):
        key_width = max(map(lambda x: len(x[0]), info))
        for i in info:
            self.pad.addstr(ypos, 1, i[0].rjust(key_width)) # key
            # value part may be wrapped if it gets too long
            for v in i[1:]:
                y, x = self.pad.getyx()
                if x + len(v) >= self.width:
                    ypos += 1
                    self.pad.move(ypos, key_width+1)
                self.pad.addstr(v)
            ypos += 1
        return ypos

    def next_details(self):
        if self.details_category_focus >= 4:
            self.details_category_focus = 0
        else:
            self.details_category_focus += 1
        self.focus_detaillist     = -1
        self.scrollpos_detaillist = 0
        self.pad.erase()

    def prev_details(self):
        if self.details_category_focus <= 0:
            self.details_category_focus = 4
        else:
            self.details_category_focus -= 1
        self.pad.erase()
        



    def move_up(self, focus, scrollpos, step_size):
        if focus < 0: focus = -1
        else:
            focus -= 1
            if scrollpos/step_size - focus > 0:
                scrollpos -= step_size
                scrollpos = max(0, scrollpos)
            while scrollpos % step_size:
                scrollpos -= 1
        return focus, scrollpos

    def move_down(self, focus, scrollpos, step_size, elements_per_page, list_height):
        if focus < list_height - 1:
            focus += 1
            if focus+1 - scrollpos/step_size > elements_per_page:
                scrollpos += step_size
        return focus, scrollpos

    def move_page_up(self, focus, scrollpos, step_size, elements_per_page):
        for x in range(elements_per_page - 1):
            focus, scrollpos = self.move_up(focus, scrollpos, step_size)
        if focus < 0: focus = 0
        return focus, scrollpos

    def move_page_down(self, focus, scrollpos, step_size, elements_per_page, list_height):
        if focus < 0: focus = 0
        for x in range(elements_per_page - 1):
            focus, scrollpos = self.move_down(focus, scrollpos, step_size, elements_per_page, list_height)
        return focus, scrollpos

    def move_to_top(self):
        return 0, 0

    def move_to_end(self, step_size, elements_per_page, list_height):
        focus     = list_height - 1
        scrollpos = max(0, (list_height - elements_per_page) * step_size)
        return focus, scrollpos





    def draw_stats(self):
        self.screen.insstr(self.height-1, 0, ' '.center(self.width), curses.A_REVERSE)
        self.draw_torrents_stats()
        self.draw_global_rates()

    def draw_torrents_stats(self):
        if self.selected_torrent > -1 and self.details_category_focus == 2:
            self.screen.insstr((self.height-1), 0, 
                               "%d peer%s connected:" % (self.torrent_details['peersConnected'],
                                                         ('s','')[self.torrent_details['peersConnected'] == 1]) + \
                                   " Tracker: %-3d" % self.torrent_details['peersFrom']['fromTracker'] + \
                                   " PEX: %-3d" % self.torrent_details['peersFrom']['fromPex'] + \
                                   " Incoming: %-3d" % self.torrent_details['peersFrom']['fromIncoming'] + \
                                   " Cache: %-3d" % self.torrent_details['peersFrom']['fromCache'],
                               curses.A_REVERSE)
        else:
            self.screen.addstr((self.height-1), 0, 
                               "%d torrent%s" % (len(self.torrents), ('s','')[len(self.torrents) == 1]),
                               curses.A_REVERSE)
            if self.filter_list:
                self.screen.addstr(" ", curses.A_REVERSE)
                self.screen.addstr("%s%s" % (('','not ')[self.filter_inverse], self.filter_list),
                                   curses.A_REVERSE + curses.A_BOLD)

            self.screen.addstr(": %d downloading; " % len(filter(lambda x: x['status']==Transmission.STATUS_DOWNLOAD,
                                                                 self.torrents)) + \
                                   "%d seeding; " % len(filter(lambda x: x['status']==Transmission.STATUS_SEED,
                                                               self.torrents)) + \
                                   "%d paused" % self.stats['pausedTorrentCount'],
                               curses.A_REVERSE)


    def draw_global_rates(self):
        rates_width = self.rateDownload_width + self.rateUpload_width + 3
        if self.stats['alt-speed-enabled']:
            self.screen.move(self.height-1, self.width-rates_width - len('Turtle mode '))
            self.screen.addstr('Turtle mode', curses.A_REVERSE + curses.A_BOLD)
            self.screen.addch(' ', curses.A_REVERSE)
        
        self.screen.move(self.height-1, self.width-rates_width)
        self.screen.addch(curses.ACS_DARROW, curses.A_REVERSE)
        self.screen.addstr(scale_bytes(self.stats['downloadSpeed']).rjust(self.rateDownload_width),
                           curses.A_REVERSE + curses.A_BOLD + curses.color_pair(1))
        self.screen.addch(' ', curses.A_REVERSE)
        self.screen.addch(curses.ACS_UARROW, curses.A_REVERSE)
        self.screen.insstr(scale_bytes(self.stats['uploadSpeed']).rjust(self.rateUpload_width),
                           curses.A_REVERSE + curses.A_BOLD + curses.color_pair(2))


    def draw_title_bar(self):
        self.screen.insstr(0, 0, ' '.center(self.width), curses.A_REVERSE)
        self.draw_connection_status()
        self.draw_quick_help()
    def draw_connection_status(self):
        status = "Transmission @ %s:%s" % (self.server.host, self.server.port)
        self.screen.addstr(0, 0, status.encode('utf-8'), curses.A_REVERSE)

    def draw_quick_help(self):
        help = [('?','Show Keybindings')]

        if self.selected_torrent == -1:
            if self.focus >= 0:
                help = [('enter','View Details'), ('p','Pause/Unpause'), ('r','Remove'), ('v','Verify')]
            else:
                help = [('f','Filter'), ('s','Sort')] + help + [('o','Options'), ('q','Quit')]
        else:
            help = [('Move with','cursor keys'), ('q','Back to List')]
            if self.details_category_focus == 1 and self.focus_detaillist > -1:
                help = [('space','(De)Select File'),
                        ('left/right','De-/Increase Priority'),
                        ('escape','Unfocus/-select')] + help
            elif self.details_category_focus == 2:
                help = [('F1/?','Explain flags')] + help

        line = ' | '.join(map(lambda x: "%s %s" % (x[0], x[1]), help))
        line = line[0:self.width]
        self.screen.insstr(0, self.width-len(line), line, curses.A_REVERSE)


    def list_key_bindings(self):
        message = "          F1/?  Show this help\n" + \
            "             p  Pause/Unpause focused torrent\n" + \
            "             v  Verify focused torrent\n" + \
            "         DEL/r  Remove focused torrent (and keep its content)\n" + \
            "           u/d  Adjust maximum global upload/download rate\n" + \
            "           U/D  Adjust maximum upload/download rate for focused torrent\n" + \
            "           +/-  Adjust bandwidth priority for focused torrent\n"
        if self.selected_torrent == -1:
            message += "             f  Filter torrent list\n" + \
                "             s  Sort torrent list\n" \
                "   Enter/right  View focused torrent's details\n" + \
                "           ESC  Unfocus\n" + \
                "             o  Configuration options\n" + \
                "             t  Toggle turtle mode\n" + \
                "             q  Quit\n\n"
        else:
            if self.details_category_focus == 2:
                message = "Flags:\n" + \
                    "  O  Optimistic unchoke\n" + \
                    "  D  Downloading from this peer\n" + \
                    "  d  We would download from this peer if they'd let us\n" + \
                    "  U  Uploading to peer\n" + \
                    "  u  We would upload to this peer if they'd ask\n" + \
                    "  K  Peer has unchoked us, but we're not interested\n" + \
                    "  ?  We unchoked this peer, but they're not interested\n" + \
                    "  E  Encrypted Connection\n" + \
                    "  X  Peer was discovered through Peer Exchange (PEX)\n" + \
                    "  I  Peer is an incoming connection \n\n"
            else:
                message += "             o  Jump to overview\n" + \
                    "             f  Jump to file list\n" + \
                    "             e  Jump to peer list\n" + \
                    "             t  Jump to tracker information\n"
                if self.details_category_focus == 1:
                    if self.focus_detaillist > -1:
                        message += "           TAB  Jump to next view\n"
                        message += "    left/right  Decrease/Increase file priority\n"
                    message += "       up/down  Select file\n"
                    message += "         SPACE  Select/Deselect focused file\n"
                    message += "             a  Select/Deselect all files\n"
                    message += "           ESC  Unfocus\n"
                else:
                    message += "left/right/TAB  Jump to next/previous view\n"
                message += "   q/backspace  Back to list\n\n"

        width  = max(map(lambda x: len(x), message.split("\n"))) + 4
        width  = min(self.width, width)
        message += "Hit any key to close".center(width-4)
        height = min(self.height, message.count("\n")+3)
        win = self.window(height, width, message=message)
        while True:
            if win.getch() >= 0: return



    def window(self, height, width, message=''):
        height = min(self.height, height)
        width  = min(self.width, width)
        ypos = (self.height - height)/2
        xpos = (self.width  - width)/2
        win = curses.newwin(height, width, ypos, xpos)
        win.box()
        win.bkgd(' ', curses.A_REVERSE + curses.A_BOLD)

        ypos = 1
        for msg in message.split("\n"):
            msg = msg[0:self.width-4]
            win.addstr(ypos, 2, msg)
            ypos += 1

        return win


    def dialog_yesno(self, message):
        height = 5 + message.count("\n")
        width  = len(message)+4
        win = self.window(height, width, message=message)
        win.keypad(True)

        focus_tags   = curses.color_pair(9)
        unfocus_tags = 0

        input = False
        while True:
            win.move(height-2, (width/2)-6)
            if input:
                win.addstr('Y',  focus_tags + curses.A_UNDERLINE)
                win.addstr('es', focus_tags)
                win.addstr('    ')
                win.addstr('N',  curses.A_UNDERLINE)
                win.addstr('o')
            else:
                win.addstr('Y', curses.A_UNDERLINE)
                win.addstr('es')
                win.addstr('    ')
                win.addstr('N',  focus_tags + curses.A_UNDERLINE)
                win.addstr('o', focus_tags)

            c = win.getch()
            if c == ord('y'):
                return True
            elif c == ord('n'):
                return False
            elif c == ord("\t"):
                input = not input
            elif c == curses.KEY_LEFT:
                input = True
            elif c == curses.KEY_RIGHT:
                input = False
            elif c == ord("\n") or c == ord(' '):
                return input
            elif c == 27 or c == curses.KEY_BREAK:
                return -1


    def dialog_input_number(self, message, current_value, cursorkeys=True, floating_point=False):
        width  = max(max(map(lambda x: len(x), message.split("\n"))), 40) + 4
        width  = min(self.width, width)
        height = message.count("\n") + (4,6)[cursorkeys]

        win = self.window(height, width, message=message)
        win.keypad(True)
        input = str(current_value)
        if cursorkeys:
            if floating_point:
                bigstep   = 1
                smallstep = 0.1
            else:
                bigstep   = 100
                smallstep = 10
            win.addstr(height-4, 2, ("   up/down +/- %-3s" % bigstep).rjust(width-4))
            win.addstr(height-3, 2, ("left/right +/- %3s" % smallstep).rjust(width-4))
            win.addstr(height-3, 2, "0 means unlimited")

        while True:
            win.addstr(height-2, 2, input.ljust(width-4), curses.color_pair(5))
            win.addch(height-2, len(input)+2, ' ')
            c = win.getch()
            if c == 27 or c == ord('q') or c == curses.KEY_BREAK:
                return -1
            elif c == ord("\n"):
                try:
                    if floating_point: return float(input)
                    else:              return int(input)
                except ValueError:
                    return -1

            elif c == curses.KEY_BACKSPACE or c == curses.KEY_DC or c == 127 or c == 8:
                input = input[:-1]
            elif len(input) >= width-5:
                curses.beep()
            elif c >= ord('0') and c <= ord('9'):
                input += chr(c)
            elif c == ord('.') and floating_point:
                input += chr(c)

            elif cursorkeys and c != -1:
                try:
                    if floating_point: number = float(input)
                    else:              number = int(input)
                    if number <= 0: number = 0
                    if c == curses.KEY_LEFT:    number -= smallstep
                    elif c == curses.KEY_RIGHT: number += smallstep
                    elif c == curses.KEY_DOWN:  number -= bigstep
                    elif c == curses.KEY_UP:    number += bigstep
                    if number <= 0: number = 0
                    input = str(number)
                except ValueError:
                    pass


    def dialog_menu(self, title, options, focus=1):
        height = len(options) + 2
        width  = max(max(map(lambda x: len(x[1])+3, options)), len(title)+3)
        win = self.window(height, width)

        win.addstr(0,1, title)
        win.keypad(True)

        old_focus = focus
        while True:
            keymap = self.dialog_list_menu_options(win, width, options, focus)
            c = win.getch()
            
            if c > 96 and c < 123 and chr(c) in keymap:
                return options[keymap[chr(c)]][0]
            elif c == 27 or c == ord('q'):
                return options[old_focus-1][0]
            elif c == ord("\n"):
                return options[focus-1][0]
            elif c == curses.KEY_DOWN:
                focus += 1
                if focus > len(options): focus = 1
            elif c == curses.KEY_UP:
                focus -= 1
                if focus < 1: focus = len(options)
            elif c == curses.KEY_HOME:
                focus = 1
            elif c == curses.KEY_END:
                focus = len(options)

    def dialog_list_menu_options(self, win, width, options, focus):
        keys = dict()
        i = 1
        for option in options:
            title = option[1].split('_')
            if i == focus: tag = curses.color_pair(5)
            else:          tag = 0
            win.addstr(i,2, title[0], tag)
            win.addstr(title[1][0], tag + curses.A_UNDERLINE)
            win.addstr(title[1][1:], tag)
            win.addstr(''.ljust(width - len(option[1]) - 3), tag)

            keys[title[1][0].lower()] = i-1
            i+=1
        return keys


    def draw_options_dialog(self):
        enc_options = [('required','_required'), ('preferred','_preferred'), ('tolerated','_tolerated')]

        while True:
            options = [('Peer _Port', "%d" % self.stats['peer-port']),
                       ('UP_nP/NAT-PMP', ('disabled','enabled ')[self.stats['port-forwarding-enabled']]),
                       ('Peer E_xchange', ('disabled','enabled ')[self.stats['pex-enabled']]),
                       ('Global Peer _Limit', "%d" % self.stats['peer-limit-global']),
                       ('Peer Limit per _Torrent', "%d" % self.stats['peer-limit-per-torrent']),
                       ('Protocol En_cryption', "%s" % self.stats['encryption']),
                       ('_Seed Ratio Limit', "%s" % ('unlimited',self.stats['seedRatioLimit'])[self.stats['seedRatioLimited']])]
            max_len = max([sum([len(re.sub('_', '', x)) for x in y[0]]) for y in options])
            win = self.window(len(options)+4, max_len+15)
            win.addstr(0, 2, 'Global Options')

            line_num = 1
            for option in options:
                parts = re.split('_', option[0])
                parts_len = sum([len(x) for x in parts])

                win.addstr(line_num, max_len-parts_len+2, parts.pop(0))
                for part in parts:
                    win.addstr(part[0], curses.A_UNDERLINE)
                    win.addstr(part[1:] + ': ' + option[1])
                line_num += 1
                
            win.addstr(line_num+1, int((max_len+15)/2) - 10, "Hit escape to close")

            c = win.getch()
            if c == 27 or c == ord('q') or c == ord("\n"):
                return

            elif c == ord('p'):
                port = self.dialog_input_number("Port for incoming connections",
                                                self.stats['peer-port'], cursorkeys=False)
                if port >= 0: self.server.set_option('peer-port', port)
            elif c == ord('n'):
                self.server.set_option('port-forwarding-enabled',
                                       (1,0)[self.stats['port-forwarding-enabled']])
            elif c == ord('x'):
                self.server.set_option('pex-enabled', (1,0)[self.stats['pex-enabled']])
            elif c == ord('l'):
                limit = self.dialog_input_number("Maximum number of connected peers",
                                                 self.stats['peer-limit-global'])
                if limit >= 0: self.server.set_option('peer-limit-global', limit)
            elif c == ord('t'):
                limit = self.dialog_input_number("Maximum number of connected peers per torrent",
                                                 self.stats['peer-limit-per-torrent'])
                if limit >= 0: self.server.set_option('peer-limit-per-torrent', limit)
            elif c == ord('s'):
                limit = self.dialog_input_number('Stop seeding with upload/download ratio',
                                                 (0,self.stats['seedRatioLimit'])[self.stats['seedRatioLimited']],
                                                 floating_point=True)
                if limit > 0:
                    self.server.set_option('seedRatioLimit', limit)
                    self.server.set_option('seedRatioLimited', True)
                elif limit == 0:
                    self.server.set_option('seedRatioLimited', False)
            elif c == ord('c'):
                choice = self.dialog_menu('Encryption', enc_options,
                                          map(lambda x: x[0]==self.stats['encryption'], enc_options).index(True)+1)
                self.server.set_option('encryption', choice)

            self.draw_torrent_list()

# End of class Interface



def percent(full, part):
    try: percent = 100/(float(full) / float(part))
    except ZeroDivisionError: percent = 0.0
    return percent


def scale_time(seconds, type='short'):
    minute_in_sec = float(60)
    hour_in_sec   = float(3600)
    day_in_sec    = float(86400)
    month_in_sec  = 27.321661 * day_in_sec # from wikipedia
    year_in_sec   = 365.25    * day_in_sec # from wikipedia

    if seconds < 0:
        return ('?', 'some time')[type=='long']

    elif seconds < minute_in_sec:
        if type == 'long':
            if seconds < 5:
                return 'now'
            else:
                return "%s second%s" % (seconds, ('', 's')[seconds>1])
        else:
            return "%ss" % seconds

    elif seconds < hour_in_sec:
        minutes = round(seconds / minute_in_sec, 0)
        if type == 'long':
            return "%d minute%s" % (minutes, ('', 's')[minutes>1])
        else:
            return "%dm" % minutes

    elif seconds < day_in_sec:
        hours = round(seconds / hour_in_sec, 0)
        if type == 'long':
            return "%d hour%s" % (hours, ('', 's')[hours>1])
        else:
            return "%dh" % hours

    elif seconds < month_in_sec:
        days = round(seconds / day_in_sec, 0)

        if type == 'long':
            return "%d day%s" % (days, ('', 's')[days>1])
        else:
            return "%dd" % days

    elif seconds < year_in_sec:
        months = round(seconds / month_in_sec, 0)
        if type == 'long':
            return "%d month%s" % (months, ('', 's')[months>1])
        else:
            return "%dM" % months

    else:
        years = round(seconds / year_in_sec, 0)
        if type == 'long':
            return "%d year%s" % (years, ('', 's')[years>1])
        else:
            return "%dy" % years


def timestamp(timestamp):
    if timestamp <= 1:
        return 'never'

    date_format = "%x %X"
    absolute = time.strftime(date_format, time.localtime(timestamp))
    if timestamp > time.time():
        relative = 'in ' + scale_time(int(timestamp - time.time()), 'long')
    else:
        relative = scale_time(int(time.time() - timestamp), 'long') + ' ago'

    if relative.startswith('now') or relative.endswith('now'):
        relative = 'now'
    return "%s (%s)" % (absolute, relative)


def scale_bytes(bytes, type='short'):
    if bytes >= 1073741824:
        scaled_bytes = round((bytes / 1073741824.0), 2)
        unit = ('G','Gigabyte')[type == 'long']
    elif bytes >= 1048576:
        scaled_bytes = round((bytes / 1048576.0), 1)
        if scaled_bytes >= 100:
            scaled_bytes = int(scaled_bytes)
        unit = ('M','Megabyte')[type == 'long']
    elif bytes >= 1024:
        scaled_bytes = int(bytes / 1024)
        unit = ('K','Kilobyte')[type == 'long']
    else:
        scaled_bytes = round((bytes / 1024.0), 1)
        unit = ('K','Kilobyte')[type == 'long']

    # add plural s to unit if necessary
    if type == 'long':
        unit = ' ' + unit + ('s', '')[scaled_bytes == 1]

    # handle 0 bytes special
    if bytes == 0 and type == 'long':
        return 'nothing'

    # convert to integer if .0
    if int(scaled_bytes) == float(scaled_bytes):
        scaled_bytes = str(int(scaled_bytes))
    else:
        scaled_bytes = str(scaled_bytes).rstrip('0')
    
    if type == 'blank': return scaled_bytes
    else:               return scaled_bytes + unit


def html2text(str):
    str = re.sub(r'</h\d+>', "\n", str)
    str = re.sub(r'</p>', ' ', str)
    str = re.sub(r'<[^>]*?>', '', str)
    return str

def num2str(num):
    if int(num) == -1:
        return '?'
    elif int(num) == -2:
        return 'oo'
    else:
        return str(num)

def int2bin(n):
    """Returns the binary of integer n"""
    return "".join([str((n >> y) & 1) for y in range(7, -1, -1)])

def middlecut(string, width):
    return string[0:(width/2)-2] + '..' + string[len(string) - (width/2) :]

def debug(data):
    if cmd_args.DEBUG:
        file = open("debug.log", 'a')
        file.write(data.encode('utf-8'))
        file.close
    
def quit(msg='', exitcode=0):
    try:
        curses.endwin()
    except curses.error:
        pass

    # if this is a graceful exit and config file is present
    if not msg and not exitcode and os.path.isfile(cmd_args.configfile):
        try:
            config.write(open(cmd_args.configfile, 'w'))
            os.chmod(cmd_args.configfile, 0600)
        except IOError, msg:
            print >> sys.stderr, "Cannot write config file %s:\n%s" % (cmd_args.configfile, msg)
    else:
        print >> sys.stderr, msg,
    os._exit(exitcode)


def explode_connection_string(connection):
    host, port = config.get('Connection', 'host'), config.getint('Connection', 'port')
    username, password = config.get('Connection', 'username'), config.get('Connection', 'password')
    try:
        if connection.count('@') == 1:
            auth, connection = connection.split('@')
            if auth.count(':') == 1:
                username, password = auth.split(':')
        if connection.count(':') == 1:
            host, port = connection.split(':')
            port = int(port)
        else:
            host = connection
    except ValueError:
        quit("Wrong connection pattern: %s\n" % connection)
    return host, port, username, password


# create initial config file
def create_config(option, opt_str, value, parser):
    configfile = parser.values.configfile
    config.read(configfile)
    if parser.values.connection:
        host, port, username, password = explode_connection_string(parser.values.connection)
        config.set('Connection', 'host', host)
        config.set('Connection', 'port', str(port))
        config.set('Connection', 'username', username)
        config.set('Connection', 'password', password)

    # create directory
    dir = os.path.dirname(configfile)
    if dir != '' and not os.path.isdir(dir):
        try:
            os.makedirs(dir)
        except OSError, msg:
            print msg
            exit(CONFIGFILE_ERROR)
        
    # create config file
    try:
        config.write(open(configfile, 'w'))
        os.chmod(configfile, 0600)
    except IOError, msg:
        print msg
        exit(CONFIGFILE_ERROR)

    print "Wrote config file %s" % configfile
    exit(0)


# command line parameters
default_config_path = os.environ['HOME'] + '/.config/transmission-remote-cli/settings.cfg'
parser = OptionParser(usage="%prog [options] [-- transmission-remote options]",
                      version="%%prog %s" % VERSION,
                      description="%%prog %s" % VERSION)
parser.add_option("--debug", action="store_true", dest="DEBUG", default=False, help=SUPPRESS_HELP)
parser.add_option("-c", "--connect", action="store", dest="connection", default="",
                  help="Point to the server using pattern [username:password@]host[:port]")
parser.add_option("-f", "--config", action="store", dest="configfile", default=default_config_path,
                  help="Path to configuration file.")
parser.add_option("--create-config", action="callback", callback=create_config,
                  help="Create configuration file CONFIGFILE with default values.")
(cmd_args, transmissionremote_args) = parser.parse_args()


# read config from config file
config.read(cmd_args.configfile)

# command line connection data can override config file
if cmd_args.connection:
    host, port, username, password = explode_connection_string(cmd_args.connection)
    config.set('Connection', 'host', host)
    config.set('Connection', 'port', str(port))
    config.set('Connection', 'username', username)
    config.set('Connection', 'password', password)


# forward arguments after '--' to transmission-remote
if transmissionremote_args:
    cmd = ['transmission-remote', '%s:%s' %
           (config.get('Connection', 'host'), config.get('Connection', 'port'))]
    if config.get('Connection', 'username') and config.get('Connection', 'password'):
        cmd.extend(['--auth', '%s:%s' % (config.get('Connection', 'username'), config.get('Connection', 'password'))])

    # one argument and it doesn't start with '-' --> treat it like it's a torrent link/url
    if len(transmissionremote_args) == 1 and not transmissionremote_args[0].startswith('-'):
        cmd.extend(['-a', transmissionremote_args[0]])
    else:
        cmd.extend(transmissionremote_args)
    print "EXECUTING:\n%s\nRESPONSE:" % ' '.join(cmd)

    try:
        retcode = call(cmd)
    except OSError, msg:
        quit("Could not execute the above command: %s\n" % msg, 128)
    quit('', retcode)


# run interface
ui = Interface(Transmission(config.get('Connection', 'host'),
                            config.getint('Connection', 'port'),
                            config.get('Connection', 'username'),
                            config.get('Connection', 'password')))


