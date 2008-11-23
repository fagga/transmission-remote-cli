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

VERSION=0.1


USERNAME = ''
PASSWORD = ''
HOST = 'localhost'
PORT = 9091

from optparse import OptionParser
parser = OptionParser(usage="Usage: %prog [[USERNAME:PASSWORD@]HOST[:PORT]]",
                      version="%%prog %s" % VERSION)
parser.add_option("--debug", action="store_true", dest="DEBUG", default=False,
                  help="Create file debug.log in current directory and put alienese messages in it.")
(options, connection) = parser.parse_args()


# parse connection data
if connection:
    if connection[0].find('@') >= 0:
        auth, connection[0] = connection[0].split('@')
        if auth.find(':') >= 0:
            USERNAME, PASSWORD = auth.split(':')

    if connection[0].find(':') >= 0:
        HOST, PORT = connection[0].split(':')
        PORT = int(PORT)
    else:
        HOST = connection[0]


# error codes
CONNECTION_ERROR = 1
JSON_ERROR       = 2
AUTH_ERROR       = 3


# Handle communication with Transmission server.
import time
import simplejson as json
import urllib2


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
        self.http_request = urllib2.Request(url=self.url, data=json.dumps(request_data))

    def send_request(self):
        """Ask for information from server OR submit command."""

        try:
            self.open_request = urllib2.urlopen(self.http_request)
        except AttributeError:
            return
        except urllib2.HTTPError, msg:
            quit(str(msg), CONNECTION_ERROR)
        except urllib2.URLError, msg:
            if msg.reason[0] == 4:
                return
            else:
                quit("Cannot connect to %s: %s" % (self.http_request.host, msg.reason[1]), CONNECTION_ERROR)

    def get_response(self):
        """Get response to previously sent request."""

        if self.open_request == None:
            return {'result': 'no open request'}

        response = self.open_request.read()
        try:
            data = json.loads(response)
        except ValueError:
            quit("Cannot not parse response: %s" % response, JSON_ERROR)
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

    LIST_FIELDS = [ 'id', 'name', 'status', 'seeders', 'leechers', 'desiredAvailable',
                    'rateDownload', 'rateUpload', 'eta', 'uploadRatio',
                    'sizeWhenDone', 'haveValid', 'haveUnchecked', 'addedDate',
                    'uploadedEver', 'errorString', 'recheckProgress',
                    'swarmSpeed', 'peersConnected' ]

    DETAIL_FIELDS = [ 'files', 'priorities', 'wanted', 'peers', 'trackers', 'webseeds',
                      'activityDate', 'dateCreated', 'startDate', 'doneDate',
                      'totalSize', 'announceURL', 'announceResponse', 'lastAnnounceTime',
                      'nextAnnounceTime', 'lastScrapeTime', 'nextScrapeTime',
                      'scrapeResponse', 'scrapeURL', 'hashString', 'timesCompleted',
                      'pieceCount', 'pieceSize', 'downloadedEver', 'corruptEver',
                      'peersKnown', 'peersSendingToUs', 'peersGettingFromUs' ] + LIST_FIELDS

    def __init__(self, host, port, username, password):
        self.host  = host
        self.port  = port
        self.username = username
        self.password = password

        if username and password:
            url = 'http://%s:%d/transmission/rpc' % (host, port)
            authhandler = urllib2.HTTPDigestAuthHandler()
            authhandler.add_password('Transmission RPC Server', url, username, password)
            opener = urllib2.build_opener(authhandler)
            urllib2.install_opener(opener)

        self.requests = {'torrent-list':
                             TransmissionRequest(host, port, 'torrent-get', 7, {'fields': self.LIST_FIELDS}),
                         'session-stats':
                             TransmissionRequest(host, port, 'session-stats', 21),
                         'session-get':
                             TransmissionRequest(host, port, 'session-get', 22),
                         'torrent-details':
                             TransmissionRequest(host, port)}


        self.torrent_cache = []
        self.status_cache  = dict()
        self.torrent_details_cache = dict()

        # make sure there are no undefined values
        self.wait_for_torrentlist_update()



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
        if response['tag'] == 7 or response['tag'] == 77:
            for t in response['arguments']['torrents']:
                t['uploadRatio'] = round(float(t['uploadRatio']), 1)
                t['percent_done'] = percent(float(t['sizeWhenDone']),
                                            float(t['haveValid'] + t['haveUnchecked']))

            if response['tag'] == 7:
                self.torrent_cache = response['arguments']['torrents']
            elif response['tag'] == 77:
                self.torrent_details_cache = response['arguments']['torrents'][0]

        # response is a reply to session-stats
        elif response['tag'] == 21:
            self.status_cache.update(response['arguments']['session-stats'])

        # response is a reply to session-get
        elif response['tag'] == 22:
            self.status_cache.update(response['arguments'])

        return response['tag']



    def get_global_stats(self):
        return self.status_cache

    def get_torrent_list(self, sort_orders, reverse=False):
        for sort_order in sort_orders:
            self.torrent_cache.sort(cmp=lambda x,y: self.my_cmp(x, y, sort_order), reverse=reverse)
        return self.torrent_cache

    def my_cmp(self, x, y, sort_order):
        if isinstance(x[sort_order], (int, long, float)):
            return cmp(x[sort_order], y[sort_order])
        else:
            return cmp(x[sort_order].lower(), y[sort_order].lower())

    def get_torrent_details(self):
        return self.torrent_details_cache
    def set_torrent_details_id(self, id):
        if id < 0:
            self.requests['torrent-details'] = TransmissionRequest(self.host, self.port)
        else:
            self.requests['torrent-details'].set_request_data('torrent-get', 77,
                                                              {'ids':id, 'fields': self.DETAIL_FIELDS})

    def set_upload_limit(self, new_limit):
        request = TransmissionRequest(self.host, self.port, 'session-set', 1,
                                      { 'speed-limit-up': int(new_limit),
                                        'speed-limit-up-enabled': 1 })
        request.send_request()

    def set_download_limit(self, new_limit):
        request = TransmissionRequest(self.host, self.port, 'session-set', 1,
                                      { 'speed-limit-down': int(new_limit),
                                        'speed-limit-down-enabled': 1 })
        request.send_request()


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


# 91 	   "files-wanted"             | array      indices of file(s) to download
# 92 	   "files-unwanted"           | array      indices of file(s) to not download
# 93 	   "ids"                      | array      torrent list, as described in 3.1
# 94 	   "peer-limit"               | number     maximum number of peers
# 95 	   "priority-high"            | array      indices of high-priority file(s)
# 96 	   "priority-low"             | array      indices of low-priority file(s)
# 97 	   "priority-normal"          | array      indices of normal-priority file(s)
    def increase_file_priority(self, file_num):
        current_priority = self.torrent_details_cache['priorities'][file_num]
        if not self.torrent_details_cache['wanted'][file_num]:
            self.set_priority(self.torrent_details_cache['id'], file_num, 'low')
        elif current_priority == -1:
            self.set_priority(self.torrent_details_cache['id'], file_num, 'normal')
        elif current_priority == 0:
            self.set_priority(self.torrent_details_cache['id'], file_num, 'high')
        else:
            return

    def decrease_file_priority(self, file_num):
        current_priority = self.torrent_details_cache['priorities'][file_num]
        if current_priority >= 1:
            self.set_priority(self.torrent_details_cache['id'], file_num, 'normal')
        elif current_priority == 0:
            self.set_priority(self.torrent_details_cache['id'], file_num, 'low')
        elif current_priority == -1:
            self.set_priority(self.torrent_details_cache['id'], file_num, 'off')
        else:
            return

    def set_priority(self, torrent_id, file_num, priority):
        request_data = {'ids': [torrent_id]}
        if priority == 'off':
            request_data['files-unwanted'] = [file_num]
        else:
            request_data['files-wanted'] = [file_num]
            request_data['priority-' + priority] = [file_num]
        request = TransmissionRequest(self.host, self.port, 'torrent-set', 1, request_data)
        request.send_request()
        self.wait_for_details_update()


    def wait_for_torrentlist_update(self):
        self.wait_for_update(7)
    def wait_for_details_update(self):
        self.wait_for_update(77)
    def wait_for_update(self, update_id):
        start = time.time()
        self.update(0) # send request
        while True:    # wait for response
            if self.update(0, update_id): break
        debug("delay was %.3f seconds\n\n\n" % (time.time() - start))
        

    def get_status(self, torrent):
        if torrent['status'] == Transmission.STATUS_CHECK_WAIT:
            status = 'will verify'
        elif torrent['status'] == Transmission.STATUS_CHECK:
            status = "verifying"
        elif torrent['errorString']:
            status = torrent['errorString']
        elif torrent['status'] == Transmission.STATUS_SEED:
            status = 'seeding'
        elif torrent['status'] == Transmission.STATUS_DOWNLOAD:
            status = ('idle','downloading')[torrent['rateDownload'] > 0]
        elif torrent['status'] == Transmission.STATUS_STOPPED:
            status = 'paused'
        else:
            status = 'unknown state'
        return status

# End of Class Transmission



    

# User Interface
import curses
import os
import signal
import locale
locale.setlocale(locale.LC_ALL, '')

class Interface:
    def __init__(self, server):
        self.server = server

        self.filter_list    = ''
        self.filter_inverse = False

        self.sort_orders  = ['name']
        self.sort_reverse = False

        self.selected = -1  # changes to >-1 when focus >-1 & user hits return
        self.torrents = self.server.get_torrent_list(self.sort_orders, self.sort_reverse)
        self.stats    = self.server.get_global_stats()

        self.focus     = -1  # -1: nothing focused; min: 0 (top of list); max: <# of torrents>-1 (bottom of list)
        self.scrollpos = 0   # start of torrentlist
        self.torrents_per_page  = 0
        self.rateDownload_width = self.rateUpload_width = 0

        self.details_category_focus = 0;

        os.environ['ESCDELAY'] = '0' # make escape usable
        curses.wrapper(self.run)


    def init_screen(self):
        curses.halfdelay(10)      # STDIN timeout
        try: curses.curs_set(0)   # hide cursor
        except curses.error: pass

        curses.init_pair(1, curses.COLOR_BLACK,   curses.COLOR_BLUE)  # download rate
        curses.init_pair(2, curses.COLOR_BLACK,   curses.COLOR_RED)   # upload rate
        curses.init_pair(3, curses.COLOR_BLUE,    curses.COLOR_BLACK) # unfinished progress
        curses.init_pair(4, curses.COLOR_GREEN,   curses.COLOR_BLACK) # finished progress
        curses.init_pair(5, curses.COLOR_BLACK,   curses.COLOR_WHITE) # eta/ratio
        curses.init_pair(6, curses.COLOR_CYAN,    curses.COLOR_BLACK) # idle progress
        curses.init_pair(7, curses.COLOR_MAGENTA, curses.COLOR_BLACK) # verifying

        curses.init_pair(8, curses.COLOR_WHITE, curses.COLOR_BLACK) # button
        curses.init_pair(9, curses.COLOR_BLACK, curses.COLOR_WHITE) # focused button

        signal.signal(signal.SIGWINCH, lambda y,frame: self.get_screen_size())
        self.get_screen_size()


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

        self.focus     = -1
        self.scrollpos = 0
        self.focus_filelist     = -1
        self.scrollpos_filelist = 0
        self.manage_layout()


    def manage_layout(self):
        self.pad_height = max((len(self.torrents)+1)*3, self.height)
        self.pad = curses.newpad(self.pad_height, self.width)
        self.mainview_height = self.height - 2
        self.torrents_per_page  = self.mainview_height/3
        self.files_per_page = self.height - 8


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


    def run(self, screen):
        self.screen = screen
        self.init_screen()

        self.draw_title_bar()
        self.draw_stats()
        self.draw_torrent_list()

        while True:
            # update torrentlist
            self.server.update(1)
            self.torrents = self.server.get_torrent_list(self.sort_orders, self.sort_reverse)
            self.filter_torrent_list()
            self.stats    = self.server.get_global_stats()

            self.manage_layout()

            # display torrentlist
            if self.selected == -1:
                self.draw_torrent_list()

            # display some torrent's details
            else:
                self.draw_details()

            self.draw_title_bar()  # show shortcuts and stuff
            self.draw_stats()      # show global states

            self.screen.move(0,0)
            self.handle_user_input()

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
        elif self.filter_list == 'idle':
            self.torrents = [t for t in self.torrents if t['status'] == Transmission.STATUS_DOWNLOAD \
                                 and t['rateDownload'] == 0 and t['rateUpload'] == 0]
        elif self.filter_list == 'verifying':
            self.torrents = [t for t in self.torrents if t['status'] == Transmission.STATUS_CHECK \
                                 or t['status'] == Transmission.STATUS_CHECK_WAIT]
        # invert list?
        if self.filter_inverse:
            self.torrents = [t for t in unfiltered if t not in self.torrents]


    def handle_user_input(self):
        c = self.screen.getch()
        if c == -1: return

        # reset + redraw
        elif c == 27 or c == curses.KEY_BREAK or c == 12:
            if self.focus_filelist > -1:     # unfocus file
                self.focus_filelist     = -1
                self.scrollpos_filelist = 0
            elif self.selected > -1:         # return from details
                self.selected = -1
            else:                            # unfocus main list
                self.scrollpos = 0
                self.focus     = -1

        # quit on q or ctrl-c
        elif c == ord('q'):
            if self.selected == -1: quit()  # exit
            else:                           # return to list view
                self.server.set_torrent_details_id(-1)
                self.selected = -1
                self.details_category_focus = 0;

        # select torrent for detailed view
        elif c == ord("\n") and self.focus > -1 and self.selected == -1:
            self.screen.clear()
            self.selected = self.focus
            self.server.set_torrent_details_id(self.torrents[self.focus]['id'])
            self.server.wait_for_details_update()

        # show sort order menu
        elif c == ord('s') and self.selected == -1:
            options = [('name','_Name'), ('addedDate','_Age'), ('percent_done','_Progress'),
                       ('seeders','_Seeds'), ('leechers','Lee_ches'), ('sizeWhenDone', 'Si_ze'),
                       ('status','S_tatus'), ('uploadedEver','_Uploaded'),
                       ('rateUpload','Up_load Speed'), ('rateDownload','Do_wnload Speed'),
                       ('swarmSpeed','Swar_m Rate'), ('uploadRatio','Rati_o_'),
                       ('peersConnected','P_eers'), ('reverse','_Reverse')]
            choice = self.dialog_menu('Sort order', options,
                                      map(lambda x: x[0]==self.sort_orders[-1], options).index(True)+1)
            if choice == 'reverse':
                self.sort_reverse = not self.sort_reverse
            else:
                self.sort_orders.append(choice)
                while len(self.sort_orders) > 2:
                    self.sort_orders.pop(0)

        # show state filter menu
        elif c == ord('f') and self.selected == -1:
            options = [('downloading','_Downloading'), ('uploading','_Uploading'),
                       ('paused','_Paused'), ('seeding','_Seeding'), ('verifying','_Verifying'),
                       ('idle','_Idle'), ('invert','I_nvert'), ('','_All')]
            choice = self.dialog_menu('Show only', options,
                                      map(lambda x: x[0]==self.filter_list, options).index(True)+1)
            if choice == 'invert':
                self.filter_inverse = not self.filter_inverse
            else:
                if choice == '': self.filter_inverse = False
                self.filter_list = choice


        # upload/download limits
        elif c == ord('u'):
            limit = self.dialog_input_number("Upload limit in Kilobytes per second", self.stats['speed-limit-up']/1024)
            if limit >= 0: self.server.set_upload_limit(limit)
        elif c == ord('d'):
            limit = self.dialog_input_number("Download limit in Kilobytes per second", self.stats['speed-limit-down']/1024)
            if limit >= 0: self.server.set_download_limit(limit)

        # pause/unpause torrent
        elif c == ord('p'):
            if self.focus < 0: return
            id = self.torrents[self.focus]['id']
            if self.torrents[self.focus]['status'] == Transmission.STATUS_STOPPED:
                self.server.start_torrent(id)
            else:
                self.server.stop_torrent(id)
            
        # verify torrent data
        elif c == ord('v'):
            if self.focus < 0: return
            id = self.torrents[self.focus]['id']
            if self.torrents[self.focus]['status'] != Transmission.STATUS_CHECK:
                self.server.verify_torrent(id)

        # remove torrent
        elif c == ord('r') or c == curses.KEY_DC:
            if self.focus < 0: return
            id = self.torrents[self.focus]['id']
            name = self.torrents[self.focus]['name'][0:self.width - 15]
            if self.dialog_yesno("Remove %s?" % name.encode('utf8')) == True:
                self.server.remove_torrent(id)

        # movement in torrent list
        elif self.selected == -1:
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
        elif self.selected > -1:
            if c == ord("\t"): self.next_details()
            elif c == ord('o'): self.details_category_focus = 0
            elif c == ord('f'): self.details_category_focus = 1
            elif c == ord('e'): self.details_category_focus = 2
            elif c == ord('t'): self.details_category_focus = 3
            elif c == ord('w'): self.details_category_focus = 4

            # file priority OR walk through details
            elif c == curses.KEY_RIGHT:
                if self.focus_filelist > -1:
                    self.server.increase_file_priority(self.focus_filelist)
                else:
                    self.next_details()
            elif c == curses.KEY_LEFT:
                if self.focus_filelist > -1:
                    self.server.decrease_file_priority(self.focus_filelist)
                else:
                    self.prev_details()

            # file list focus
            elif c == curses.KEY_UP:
                self.focus_filelist, self.scrollpos_filelist = \
                    self.move_up(self.focus_filelist, self.scrollpos_filelist, 1)
            elif c == curses.KEY_DOWN:
                self.focus_filelist, self.scrollpos_filelist = \
                    self.move_down(self.focus_filelist, self.scrollpos_filelist, 1,
                                   self.files_per_page, len(self.torrent_details['files']))


        else: return # don't recognize key

        # update view
        if self.selected == -1:
            self.draw_torrent_list()
        else:
            self.draw_details()




    def draw_torrent_list(self):
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
            self.draw_downloadrate(torrent['rateDownload'], y)
        if torrent['status'] == Transmission.STATUS_DOWNLOAD or torrent['status'] == Transmission.STATUS_SEED:
            self.draw_uploadrate(torrent['rateUpload'], y)
        if torrent['percent_done'] < 100 and torrent['status'] == Transmission.STATUS_DOWNLOAD:
            self.draw_eta(torrent, y)

        self.draw_ratio(torrent, y)

        # the line below the title/progress
        self.draw_torrentlist_status(torrent, focused, y)



    def draw_downloadrate(self, rate, ypos):
        self.pad.addstr(ypos, self.width-self.rateDownload_width-self.rateUpload_width-3, "D")
        self.pad.addstr(ypos, self.width-self.rateDownload_width-self.rateUpload_width-2,
                        "%s" % scale_bytes(rate).rjust(self.rateDownload_width),
                        curses.color_pair(1) + curses.A_BOLD + curses.A_REVERSE)

    def draw_uploadrate(self, rate, ypos):
        self.pad.addstr(ypos, self.width-self.rateUpload_width-1, "U")
        self.pad.addstr(ypos, self.width-self.rateUpload_width,
                       "%s" % scale_bytes(rate).rjust(self.rateUpload_width),
                       curses.color_pair(2) + curses.A_BOLD + curses.A_REVERSE)

    def draw_ratio(self, torrent, ypos):
        self.pad.addstr(ypos+1, self.width-self.rateUpload_width-1, "R")
        self.pad.addstr(ypos+1, self.width-self.rateUpload_width,
                       "%s" % num2str(torrent['uploadRatio']).rjust(self.rateUpload_width),
                       curses.color_pair(5) + curses.A_BOLD + curses.A_REVERSE)

    def draw_eta(self, torrent, ypos):
        self.pad.addstr(ypos+1, self.width-self.rateDownload_width-self.rateUpload_width-3, "T")
        self.pad.addstr(ypos+1, self.width-self.rateDownload_width-self.rateUpload_width-2,
                        "%s" % scale_time(torrent['eta']).rjust(self.rateDownload_width),
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
            if torrent['seeders'] <= 0:
                available = torrent['desiredAvailable'] + torrent['haveValid']
                size = "%5s / " % scale_bytes(torrent['desiredAvailable'] + torrent['haveValid']) + size
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

        if not torrent['errorString']:
            if torrent['status'] == Transmission.STATUS_CHECK:
                parts[0] += " (%d%%)" % int(float(torrent['recheckProgress']) * 100)
            elif torrent['status'] == Transmission.STATUS_DOWNLOAD:
                parts[0] += " (%d%%)" % torrent['percent_done']
            parts[0] = parts[0].ljust(18)

            # seeds and leeches will be appended right justified later
            peers  = "%4s seed%s " % (num2str(torrent['seeders']), ('s', ' ')[torrent['seeders']==1])
            peers += "%4s leech%s" % (num2str(torrent['leechers']), ('es', '  ')[torrent['leechers']==1])

            # show additional information if enough room
            if self.torrent_title_width - sum(map(lambda x: len(x), parts)) - len(peers) > 15:
                parts.append("%5s uploaded" % scale_bytes(torrent['uploadedEver']))

            if self.torrent_title_width - sum(map(lambda x: len(x), parts)) - len(peers) > 18:
                parts.append("%5s swarm rate" % scale_bytes(torrent['swarmSpeed']))

            if self.torrent_title_width - sum(map(lambda x: len(x), parts)) - len(peers) > 20:
                parts.append("%4s peers connected" % torrent['peersConnected'])

            
        if focused: tags = curses.A_REVERSE + curses.A_BOLD
        else:       tags = 0

        remaining_space = self.torrent_title_width - sum(map(lambda x: len(x), parts), len(peers))
        delimiter = ' ' * int(remaining_space / (len(parts)))
        line = delimiter.join(parts)

        # make sure the peers element is always right justified
        line += ' ' * int(self.torrent_title_width - len(line) - len(peers)) + peers
        self.pad.addstr(ypos+1, 0, line, tags)
        



    def draw_details(self):
        self.torrent_details = self.server.get_torrent_details()
#        if not torrent: return

        # details could need more space than the torrent list
        self.pad_height = max(50, len(self.torrent_details['files'])+10, (len(self.torrents)+1)*3, self.height)
        self.pad = curses.newpad(self.pad_height, self.width)

        # torrent name + progress bar
        self.draw_torrentlist_item(self.torrent_details, False, 0)

        # divider + menu
        menu_items = ['_Overview', "_Files", 'P_eers', '_Tracker', '_Webseeds' ]
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

        # reset file list focus if not viewing file list
        if self.details_category_focus != 1:
            self.focus_filelist     = -1
            self.scrollpos_filelist = 0
        

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
            self.draw_webseedlist(5)
        self.pad.refresh(0,0, 1,0, self.height-2,self.width)
        self.screen.refresh()



    def draw_details_overview(self, ypos):
        t = self.torrent_details
        info = []
        info.append(['Hash', " %s" % t['hashString']])
        info.append(['ID', " %s" % t['id']])

        info.append(['Size', " %s; " % scale_bytes(t['totalSize'], 'long'),
                     "%s wanted" % (scale_bytes(t['sizeWhenDone'], 'long'),'everything') \
                         [t['totalSize']==t['sizeWhenDone']]])

        info.append(['Files', " %d; " % len(t['files'])])
        complete     = map(lambda x: x['bytesCompleted'] == x['length'], t['files']).count(True)
        not_complete = filter(lambda x: x['bytesCompleted'] != x['length'], t['files'])
        partial      = map(lambda x: x['bytesCompleted'] > 0, not_complete).count(True)
        if complete == len(t['files']):
            info[-1].append("all complete")
        else:
            info[-1].append("%d complete; " % complete)
            info[-1].append("%d commenced" % partial)

        info.append(['Pieces', " %s; " % t['pieceCount'],
                     "%s each" % scale_bytes(t['pieceSize'], 'long')])

        info.append(['Download'])
        info[-1].append(" %s" % scale_bytes(t['downloadedEver'], 'long') + \
                        " (%d%%) received; " % int(percent(t['sizeWhenDone'], t['downloadedEver'])))
        info[-1].append("%s" % scale_bytes(t['haveValid'], 'long') + \
                        " (%d%%) verified; " % int(percent(t['sizeWhenDone'], t['haveValid'])))
        info[-1].append("%s corrupt"  % scale_bytes(t['corruptEver'], 'long'))
        if t['percent_done'] < 100:
            info[-1][-1] += '; '
            if t['rateDownload']:
                info[-1].append("receiving %s per second" % scale_bytes(t['rateDownload'], 'long'))
            else:
                info[-1].append("no reception in progress")

        info.append(['Upload', " %s " % scale_bytes(t['uploadedEver'], 'long') + \
                         "(%.2f copies) distributed; " % (float(t['uploadedEver']) / float(t['sizeWhenDone']))])
        if t['rateUpload']:
            info[-1].append("sending %s per second" % scale_bytes(t['rateUpload'], 'long'))
        else:
            info[-1].append("no transmission in progress")

        info.append(['Peers', " %d reported by tracker; " % t['peersKnown'],
                     "connected to %d; "                  % t['peersConnected'],
                     "downloading from %d; "              % t['peersSendingToUs'],
                     "uploading to %d"                    % t['peersGettingFromUs']])

        ypos, key_width = self.draw_details_list(ypos, info)

        self.pad.addstr(ypos, 1, "Tracker has seen %s clients completing this torrent." \
                            % num2str(t['timesCompleted']))

        self.draw_details_eventdates(ypos+2)
        return ypos+2

    def draw_details_eventdates(self, ypos):
        t = self.torrent_details
        self.draw_hline(ypos, self.width, ' Events ') ; ypos += 1
        # dates (started, finished, etc)
        info = [['Added',    ' ' + timestamp(t['addedDate'])],
                ['Started',  ' ' + timestamp(t['startDate'])],
                ['Activity', ' ' + timestamp(t['activityDate'])]]
        if t['percent_done'] < 100 and t['eta'] > 0:
            info.append(['Finished', ' ' + timestamp(time.time() + t['eta'])])
        elif t['doneDate'] <= 0:
            info.append(['Finished', ' sometime'])
        else:
            info.append(['Finished', ' ' + timestamp(t['doneDate'])])
        return self.draw_details_list(ypos, info)


    def draw_filelist(self, ypos):
        t = self.torrent_details
        # draw column names
        column_names = '  # Progress Size Priority Filename'
        self.pad.addstr(ypos, 0, column_names.ljust(self.width), curses.A_UNDERLINE)

        start = self.scrollpos_filelist
        end   = self.scrollpos_filelist + self.files_per_page

        ypos += 1
        for file in t['files'][start:end]:
            index = t['files'].index(file)

            focused = (index == self.focus_filelist)
            if focused:
                self.pad.attron(curses.A_REVERSE)
                self.pad.addstr(ypos, 0, ' '*self.width, curses.A_REVERSE)

            self.pad.addstr(ypos, 0, str(index+1).rjust(3))
            self.draw_filelist_percent(file, ypos)
            self.draw_filelist_size(file, ypos)
            self.draw_filelist_priority(t, index, ypos)
            self.draw_filelist_filename(file, ypos)

            if focused:
                self.pad.attroff(curses.A_REVERSE)
            ypos += 1

    def draw_filelist_percent(self, file, ypos):
        done = str(int(percent(file['length'], file['bytesCompleted']))) + '%'
        self.pad.addstr(ypos, 4, "%s" % done.rjust(6))

    def draw_filelist_size(self, file, ypos):
        self.pad.addstr(ypos, 12, scale_bytes(file['length']).rjust(5))

    def draw_filelist_priority(self, torrent, index, ypos):
        priority = torrent['priorities'][index]
        if not torrent['wanted'][index]: priority = 'off'
        elif priority <= -1: priority = 'low'
        elif priority == 0:  priority = 'normal'
        elif priority >= 1:  priority = 'high'
        self.pad.addstr(ypos, 18, priority.center(8))

    def draw_filelist_filename(self, file, ypos):
        self.pad.addstr(ypos, 27, "%s" % file['name'][0:self.width-27].encode('utf-8'))



    def draw_peerlist(self, ypos):
        try: self.torrent_details['peers']
        except:
            self.pad.addstr(ypos, 1, "Peer list is not available in versions below 1.4.")
            return

        column_names = '       IP       Flags     Down    Up Progress Client'
        self.pad.addstr(ypos, 0, column_names.ljust(self.width), curses.A_UNDERLINE)
        ypos += 1
        for peer in self.torrent_details['peers']:
            self.draw_peerlist_address(peer, ypos, 0)
            self.draw_peerlist_flags(peer, ypos, 16)
            self.draw_peerlist_download(peer, ypos, 25)
            self.draw_peerlist_upload(peer, ypos, 31)
            self.draw_peerlist_progress(peer, ypos, 40)
            self.draw_peerlist_clientname(peer, ypos, 46)
            ypos += 1
    def draw_peerlist_address(self, peer, ypos, xpos):
        self.pad.addstr(ypos, xpos, "%15s" % peer['address'])
    def draw_peerlist_download(self, peer, ypos, xpos):
        self.pad.addstr(ypos, xpos, "%5s" % scale_bytes(peer['rateToClient']))
    def draw_peerlist_upload(self, peer, ypos, xpos):
        self.pad.addstr(ypos, xpos, "%5s" % scale_bytes(peer['rateToPeer']))
    def draw_peerlist_progress(self, peer, ypos, xpos):
        self.pad.addstr(ypos, xpos, "%d%%" % (float(peer['progress'])*100))
    def draw_peerlist_clientname(self, peer, ypos, xpos):
        self.pad.addstr(ypos, xpos, peer['clientName'].encode('utf-8'))
    def draw_peerlist_flags(self, peer, ypos, xpos):
        self.pad.addstr(ypos, xpos, "%-7s" % peer['flagStr'])


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
        self.pad.addstr(ypos+1, 2, "Latest announce:   %s" % timestamp(t['lastAnnounceTime']))
        self.pad.addstr(ypos+2, 2, "Announce response: %s" % t['announceResponse'])
        self.pad.addstr(ypos+3, 2, "Next announce:     %s" % timestamp(t['nextAnnounceTime']))

        scrape_width   = max(60, len(active['scrape']))
        announce_width = max(60, len(active['announce']))
        if self.width < announce_width + scrape_width + 2:
            xpos = 0
            ypos += 5
        else:
            xpos = announce_width + 2
        self.pad.addstr(ypos,   xpos, active['scrape'])
        self.pad.addstr(ypos+1, xpos+2, "Latest scrape:   %s" % timestamp(t['lastScrapeTime']))
        self.pad.addstr(ypos+2, xpos+2, "Scrape response: %s" % t['scrapeResponse'])
        self.pad.addstr(ypos+3, xpos+2, "Next scrape:     %s" % timestamp(t['nextScrapeTime']))
        ypos += 5
        
        if inactive:
            self.pad.addstr(ypos, 0, "Fallback Tracker%s:" % ('','s')[len(inactive)>1])
            # show inactive trackers
            for tracker in inactive:
                ypos += 1
                self.pad.addstr(ypos, 2, tracker['announce'])

            
    def draw_webseedlist(self, ypos):
        self.pad.addstr(ypos, 1, "Feature not implemented yet.")
        debug("webseeds: " + repr(self.torrent_details['webseeds']) + "\n\n\n")
        




    def draw_hline(self, ypos, width, title):
        self.pad.hline(ypos, 0, curses.ACS_HLINE, width)
        self.pad.addstr(ypos, width-(width-2), title, curses.A_REVERSE)

    def draw_details_list(self, ypos, info):
        key_width = max(map(lambda x: len(x[0]), info))
        for i in info:
            self.pad.addstr(ypos, 1, i[0].rjust(key_width) + ':') # key
            # value part may be wrapped if it gets too long
            for v in i[1:]:
                y, x = self.pad.getyx()
                if x + len(v) > self.width:
                    ypos += 1
                    self.pad.move(ypos, key_width+3)
                self.pad.addstr(v)
            ypos += 1
        return ypos, key_width

    def next_details(self):
        if self.details_category_focus >= 4:
            self.details_category_focus = 0
        else:
            self.details_category_focus += 1
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
                scrollpos -= 3
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
        self.draw_current_filter()
        self.draw_global_rates()

    def draw_torrents_stats(self):
        torrents = "%d Torrents: " % self.stats['torrentCount']
        downloading_torrents = filter(lambda x: x['status']==Transmission.STATUS_DOWNLOAD, self.torrents)
        torrents += "%d downloading; " % len(downloading_torrents)
        seeding_torrents = filter(lambda x: x['status']==Transmission.STATUS_SEED, self.torrents)
        torrents += "%d seeding; " % len(seeding_torrents)
        torrents += "%d paused" % self.stats['pausedTorrentCount']
        self.screen.addstr((self.height-1), 0, torrents, curses.A_REVERSE)

    def draw_current_filter(self):
        if not self.filter_list or self.width < 80: return
        line = self.filter_list
        if self.filter_inverse: line = '!' + line
        if self.width >= 100: line = 'Filter: ' + line
        free_space = self.width - 50 - 15
        xpos = 45 + (free_space / 2)
        self.screen.addstr(self.height-1, xpos, line, curses.A_REVERSE)

    def draw_global_rates(self):
        rates_width = self.rateDownload_width + self.rateUpload_width + 3
        self.screen.move(self.height-1, self.width-rates_width)
        self.screen.addstr('D', curses.A_REVERSE)
        self.screen.addstr(scale_bytes(self.stats['downloadSpeed']).rjust(self.rateDownload_width),
                           curses.A_REVERSE + curses.A_BOLD + curses.color_pair(1))
        self.screen.addstr(' U', curses.A_REVERSE)
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
        help = [('u','Upload Limit'), ('d','Download Limit')]

        if self.selected == -1:
            if self.focus >= 0:
                help = [('enter','View Details'), ('p','Pause/Unpause'), ('r','Remove'), ('v','Verify')]
            else:
                help = [('f','Filter'), ('s','Sort')] + help + [('q','Quit')]
        else:
            help = [('Move with','cursor keys'), ('q','Back to List')]
            if self.focus_filelist > -1:
                help = [('left/right','Decrease/Increase Priority'),
                        ('escape','Unfocus')] + help
                

        # convert help to str
        line = ' | '.join(map(lambda x: "%s %s" % (x[0], x[1]), help))
        line = line[0:self.width]
        self.screen.insstr(0, self.width-len(line), line, curses.A_REVERSE)






    def window(self, height, width, message=''):
        ypos = int(self.height - height)/2
        xpos = int(self.width  - width)/2
        win = curses.newwin(height, width, ypos, xpos)
        win.box()
        win.bkgd(' ', curses.A_REVERSE + curses.A_BOLD)

        ypos = 1
        for msg in message.split("\n"):
            win.addstr(ypos, 2, msg)
            ypos += 1

        return win


    def dialog_message(self, message):
        height = 5 + message.count("\n")
        width  = len(message)+4
        win = self.window(height, width, message)
        win.addstr(height-2, (width/2) - 6, 'Press any key')
        win.notimeout(True)
        win.getch()

    def dialog_yesno(self, message):
        height = 5 + message.count("\n")
        width  = len(message)+4
        win = self.window(height, width, message)
        win.notimeout(True)
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


    def dialog_input_number(self, message, current_value):
        if current_value < 50:
            bigstep   = 10
            smallstep = 1
        else:
            bigstep   = 100
            smallstep = 10


        message += "\nup/down    +/- %3d" % bigstep
        message += "\nleft/right +/- %3d" % smallstep
        height = 4 + message.count("\n")
        width  = max(map(lambda x: len(x), message.split("\n"))) + 4

        win = self.window(height, width, message)
        win.notimeout(True)
        win.keypad(True)

        input = str(current_value)
        while True:
            win.addstr(height-2, 2, input.ljust(width-4), curses.color_pair(5))
            c = win.getch()
            if c == 27 or c == curses.KEY_BREAK:
                return -1
            elif c == ord("\n"):
                if input: return int(input)
                else:     return -1
                
            elif c == curses.KEY_BACKSPACE or c == curses.KEY_DC or c == 127 or c == 8:
                input = input[:-1]
                if input == '': input = '0'
            elif len(input) >= width-4:
                curses.beep()
            elif c >= ord('0') and c <= ord('9'):
                input += chr(c)

            elif c == curses.KEY_LEFT:
                input = str(int(input) - smallstep)
            elif c == curses.KEY_RIGHT:
                input = str(int(input) + smallstep)
            elif c == curses.KEY_DOWN:
                input = str(int(input) - bigstep)
            elif c == curses.KEY_UP:
                input = str(int(input) + bigstep)
            if int(input) < 0: input = '0'


    def dialog_menu(self, title, options, focus=1):
        height = len(options) + 2
        width  = max(max(map(lambda x: len(x[1])+4, options)), len(title)+3)
        win = self.window(height, width)

        win.addstr(0,1, title)
        win.notimeout(True)
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
            win.addstr(''.ljust(width - len(option[1]) - 4), tag)

            keys[title[1][0].lower()] = i-1
            i+=1
        return keys


# End of class Interface



def percent(full, part):
    try: percent = 100/(float(full) / float(part))
    except ZeroDivisionError: percent = 0.0
    return percent


def scale_time(seconds, type='short'):
    if seconds < 0:
        return ('?', 'some time')[type=='long']
    elif seconds < 60:
        if type == 'long':
            if seconds < 5:
                return 'now'
            else:
                return "%s second%s" % (seconds, ('', 's')[seconds>1])
        else:
            return "%ss" % seconds
    elif seconds < 3600:
        minutes = int(seconds / 60)
        if type == 'long':
            return "%d minute%s" % (minutes, ('', 's')[minutes>1])
        else:
            return "%dm" % minutes
    elif seconds < 86400:
        hours = int(seconds / 3600)
        if type == 'long':
            return "%d hour%s" % (hours, ('', 's')[hours>1])
        else:
            return "%dh" % hours
    else:
        days = int(seconds / 86400)
        if type == 'long':
            return "%d day%s" % (days, ('', 's')[days>1])
        else:
            return "%dd" % days

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
        scaled_bytes = round((bytes / 1024.0), 1)
        if scaled_bytes >= 10:
            scaled_bytes = int(scaled_bytes)
        unit = ('K','Kilobyte')[type == 'long']
    else:
        scaled_bytes = bytes
        unit = ('B','Byte')[type == 'long']

    # add s to unit if necessary
    if type == 'long':
        if scaled_bytes == 0 and unit == 'Byte':
            return 'nothing'
        unit = ' ' + unit + ('s', '')[scaled_bytes == 1]

    # convert to integer if .0
    if int(scaled_bytes) == float(scaled_bytes):
        return "%d%s" % (int(scaled_bytes), unit)
    else:
        return "%s%s" % (str(scaled_bytes).rstrip('0'), unit)
    

def num2str(num):
    if int(num) == -1:
        return '?'
    elif int(num) == -2:
        return 'oo'
    else:
        return str(num)


def debug(data):
    if options.DEBUG:
        file = open("debug.log", 'a')
        file.write(data.encode('utf-8'))
        file.close
    

import sys
def quit(msg='', exitcode=0):
    try:
        curses.nocbreak()
        curses.echo()
        curses.noraw()
        curses.endwin()
    except curses.error:
        pass

    print msg
    exit(exitcode)



ui = Interface(Transmission(HOST, PORT, USERNAME, PASSWORD))





