#!/usr/bin/python
########################################################################
# This is transmission-remote-cli, a client for the daemon of the      #
# BitTorrent client Transmission.                                      #
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


DEBUG=True

USERNAME = ''
PASSWORD = ''
HOST = 'localhost'
PORT = 9091

from optparse import OptionParser
parser = OptionParser(usage="Usage: %prog [[USERNAME:PASSWORD@]HOST[:PORT]]")
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
    def __init__(self, host, port, method, tag, arguments=None, username=None, password=None):
        url = 'http://%s:%d/transmission/rpc' % (host, port)
        self.open_request  = None
        self.last_update   = 0

        request_data = {'method':method, 'tag':tag}
        if arguments: request_data['arguments'] = arguments
        self.http_request = urllib2.Request(url=url, data=json.dumps(request_data))



    def send_request(self):
        """Ask for information from server OR submit command."""

        try:
            self.open_request = urllib2.urlopen(self.http_request)
        except urllib2.HTTPError, msg:
            quit(str(msg), CONNECTION_ERROR)
            
        except urllib2.URLError, msg:
            if msg.reason[0] == 4:
                return
            else:
                quit("Cannot connect to %s: %s" % (self.http_request.host, msg.reason[1]), CONNECTION_ERROR)

        except:
            quit("unexpected error in send_request() - report how you did this")


    def get_response(self):
        """Get response to previously sent request."""

        if self.open_request == None:
            return {'result': 'no open request'}

        try:
            response = self.open_request.read()
        except:
            quit("unexpected error in get_response() - report how you did this")


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

    DETAIL_FIELDS = [ 'files', 'peers', 'trackers' ] + LIST_FIELDS

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

        self.requests = [TransmissionRequest(host, port, 'torrent-get', 7, {'fields': self.LIST_FIELDS}),
                         TransmissionRequest(host, port, 'session-stats', 21),
                         TransmissionRequest(host, port, 'session-get', 22)]

        self.torrent_cache = []
        self.status_cache  = dict()

        # make sure there are no undefined values
        self.update(0) # send request
        self.update(0) # get response



    def update(self, delay, tag_waiting_for=0):
        """Maintain up-to-date data."""

        tag_waiting_for_occurred = False

        for request in self.requests:
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
        if response['tag'] == 7:
            self.torrent_cache = response['arguments']['torrents']
            for t in self.torrent_cache:
                t['uploadRatio'] = round(float(t['uploadRatio']), 1)
                t['percent_done'] = percent(float(t['sizeWhenDone']), float(t['haveValid']))

        # response is a reply to session-stats
        elif response['tag'] == 21:
            self.status_cache.update(response['arguments']['session-stats'])

        # response is a reply to session-get
        elif response['tag'] == 22:
            self.status_cache.update(response['arguments'])

        return response['tag']





    def get_torrent_list(self, sort_orders, reverse=False):
        for sort_order in sort_orders:
            self.torrent_cache.sort(cmp=lambda x,y: self.my_cmp(x, y, sort_order), reverse=reverse)
        return self.torrent_cache

    def my_cmp(self, x, y, sort_order):
        if isinstance(x[sort_order], (int, long, float)):
            return cmp(x[sort_order], y[sort_order])
        else:
            return cmp(x[sort_order].lower(), y[sort_order].lower())


    def get_torrent_details(self, id):
        request = TransmissionRequest(self.host, self.port, 'torrent-get', 7, {'ids':id, 'fields': self.DETAIL_FIELDS})
        request.send_request()
        response = request.get_response()
        while response['result'] == 'no open request':
            response = request.get_response()
            

        if response['result'] != 'success':
            quit(str(response['result']), CONNECTION_ERROR)

        else:
            t = response['arguments']['torrents'][0]
            t['percent_done'] = percent(float(t['sizeWhenDone']), float(t['haveValid']))
            t['uploadRatio']  = round(float(t['uploadRatio']), 1)
            return t



    def get_global_stats(self):
        return self.status_cache

            


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


    def wait_for_torrentlist_update(self):
        # for some reason we need to wait twice
        while True:
            if self.update(0, 7): break
            time.sleep(0.01)
        while True:
            if self.update(0, 7): break
            time.sleep(0.01)


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

        self.sort_orders  = ['name']
        self.sort_reverse = False
        self.selected = -1  # changes to >-1 when focus >-1 & user hits return
        self.torrents = self.server.get_torrent_list(self.sort_orders, self.sort_reverse)
        self.stats    = self.server.get_global_stats()

        self.focus     = -1  # -1: nothing focused; min: 0 (top of list); max: <# of torrents>-1 (bottom of list)
        self.scrollpos = 0   # start of torrentlist
        self.torrents_per_page  = 0
        self.rateDownload_width = self.rateUpload_width = 0

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
            if self.width < 50:
                self.screen.addstr(0,0, "Terminal too small", curses.A_REVERSE + curses.A_BOLD)
                time.sleep(0.5)
            else:
                break

        self.focus = -1
        self.scrollpos = 0
        self.manage_layout()


    def manage_layout(self):
        self.pad = curses.newpad((len(self.torrents)+1)*3, self.width)
        self.torrentlist_height = self.height - 2
        self.torrents_per_page  = self.torrentlist_height/3

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
        new_width = max(max(map(lambda x: len(scale_time(x['eta'], 'short')), torrents)), new_width)
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
            self.stats    = self.server.get_global_stats()

            self.manage_layout()

            # display torrentlist
            if self.selected == -1:
                self.draw_torrent_list()

            # display some torrent's details
            else:
                self.draw_torrent_details(self.torrents[self.selected]['id'])

            self.draw_title_bar()  # show shortcuts and stuff
            self.draw_stats()      # show global states

            self.screen.move(0,0)
            self.handle_user_input()


    def handle_user_input(self):
        c = self.screen.getch()
        if c == -1: return

#        elif c == curses.KEY_RESIZE:
#            self.get_screen_size()

        # reset + redraw
        elif c == 27 or c == curses.KEY_BREAK or c == 12:
            if self.selected > -1:
                self.selected = -1
            else:
                self.scrollpos = 0
                self.focus     = -1

        # quit on q or ctrl-c
        elif c == ord('q'):
            if self.selected == -1: quit()  # exit
            else: self.selected = -1        # return to list view

        # select torrent for detailed view
        elif c == ord("\n") and self.focus > -1 and self.selected == -1:
            self.screen.clear()
            self.pad.clear()
            self.selected = self.focus

        # show sort order menu
        elif c == ord('s') and self.selected == -1:
            options = [('name','_Name'), ('addedDate','_Age'), ('percent_done','_Progress'),
                       ('seeders','_Seeds'), ('leechers','_Leeches'), ('sizeWhenDone', 'Si_ze'),
                       ('status','S_tatus'), ('uploadedEver','_Uploaded'), ('uploadRatio','Rati_o_'),
                       ('swarmSpeed','S_warm Rate'), ('peersConnected','P_eers'),
                       ('reverse','_Reverse')]
            choice = self.dialog_menu('Sort order', options,
                                      map(lambda x: x[0]==self.sort_orders[-1], options).index(True)+1)
            if choice:
                if choice == 'reverse':
                    self.sort_reverse = not self.sort_reverse
                else:
                    self.sort_orders.append(choice)
                    while len(self.sort_orders) > 2:
                        self.sort_orders.pop(0)


        # upload/download limits
        elif c == ord('u'):
            limit = self.dialog_input_number("Upload limit in K/s", self.stats['speed-limit-up']/1024)
            if limit >= 0: self.server.set_upload_limit(limit)
        elif c == ord('d'):
            limit = self.dialog_input_number("Download limit in K/s", self.stats['speed-limit-down']/1024)
            if limit >= 0: self.server.set_download_limit(limit)

        # pause/unpause torrent
        elif c == ord('p'):
            if self.focus < 0: return
            id = self.torrents[self.focus]['id']
            if self.torrents[self.focus]['status'] == Transmission.STATUS_STOPPED:
                self.server.start_torrent(id)
            else:
                self.server.stop_torrent(id)
#            self.torrents = self.server.get_torrent_list(self.sort_orders, self.sort_reverse)
            
        # verify torrent data
        elif c == ord('v'):
            if self.focus < 0: return
            id = self.torrents[self.focus]['id']
            if self.torrents[self.focus]['status'] != Transmission.STATUS_CHECK:
                self.server.verify_torrent(id)
#            self.torrents = self.server.get_torrent_list(self.sort_orders, self.sort_reverse)

        # remove torrent
        elif c == ord('r') or c == curses.KEY_DC:
            if self.focus < 0: return
            id = self.torrents[self.focus]['id']
            name = self.torrents[self.focus]['name'][0:self.width - 15]
            if self.dialog_yesno("Remove %s?" % name.encode('utf8')) == True:
                self.server.remove_torrent(id)
#            self.torrents = self.server.get_torrent_list(self.sort_orders, self.sort_reverse)


        # movement
        elif self.selected == -1:
            if c == curses.KEY_UP:       self.scroll_up()
            elif c == curses.KEY_DOWN:   self.scroll_down()
            elif c == curses.KEY_PPAGE:  self.scroll_page_up()
            elif c == curses.KEY_NPAGE:  self.scroll_page_down()
            elif c == curses.KEY_HOME:   self.scroll_to_top()
            elif c == curses.KEY_END:    self.scroll_to_end()

        else: return

        if self.selected == -1:
            self.draw_torrent_list()
        else:
            self.draw_torrent_details(self.torrents[self.selected]['id'])




    def draw_torrent_list(self):
        ypos = 0
        for i in range(len(self.torrents)):
            self.draw_torrent_item(self.torrents[i], (i == self.focus), ypos)
            ypos += 3

        self.pad.refresh(self.scrollpos,0, 1,0, self.torrentlist_height,self.width-1)
        self.screen.refresh()


    def draw_torrent_item(self, torrent, focused, y):
        # the torrent name is also a progress bar
        self.draw_torrent_title(torrent, focused, y)

        rates = ''
        if torrent['status'] == Transmission.STATUS_DOWNLOAD:
            self.draw_downloadrate(torrent['rateDownload'], y)
        if torrent['status'] == Transmission.STATUS_DOWNLOAD or torrent['status'] == Transmission.STATUS_SEED:
            self.draw_uploadrate(torrent['rateUpload'], y)
        if torrent['percent_done'] < 1:
            self.draw_eta(torrent, y)

        self.draw_ratio(torrent, y)

        # the line below the title/progress
        self.draw_torrent_status(torrent, focused, y)



    def draw_downloadrate(self, rate, ypos):
        self.pad.addstr(ypos, self.width-self.rateDownload_width-self.rateUpload_width-3, "D")
        self.pad.addstr(ypos, self.width-self.rateDownload_width-self.rateUpload_width-2,
                        "%s" % scale_bytes(rate).rjust(self.rateDownload_width, ' '),
                        curses.color_pair(1) + curses.A_BOLD + curses.A_REVERSE)

    def draw_uploadrate(self, rate, ypos):
        self.pad.addstr(ypos, self.width-self.rateUpload_width-1, "U")
        self.pad.addstr(ypos, self.width-self.rateUpload_width,
                       "%s" % scale_bytes(rate).rjust(self.rateUpload_width, ' '),
                       curses.color_pair(2) + curses.A_BOLD + curses.A_REVERSE)

    def draw_ratio(self, torrent, ypos):
        self.pad.addstr(ypos+1, self.width-self.rateUpload_width-1, "R")
        self.pad.addstr(ypos+1, self.width-self.rateUpload_width,
                       "%s" % num2str(torrent['uploadRatio']).rjust(self.rateUpload_width, ' '),
                       curses.color_pair(5) + curses.A_BOLD + curses.A_REVERSE)

    def draw_eta(self, torrent, ypos):
        self.pad.addstr(ypos+1, self.width-self.rateDownload_width-self.rateUpload_width-3, "T")
        self.pad.addstr(ypos+1, self.width-self.rateDownload_width-self.rateUpload_width-2,
                        "%s" % scale_time(torrent['eta'], 'short').rjust(self.rateDownload_width, ' '),
                        curses.color_pair(5) + curses.A_BOLD + curses.A_REVERSE)


    def draw_torrent_title(self, torrent, focused, ypos):
        if torrent['status'] == Transmission.STATUS_CHECK:
            percent_done = torrent['recheckProgress']
        else:
            percent_done = torrent['percent_done']

        bar_width = int(float(self.torrent_title_width) * float(percent_done))
        title = torrent['name'][0:self.torrent_title_width].ljust(self.torrent_title_width, ' ')

        size = "%5s" % scale_bytes(torrent['sizeWhenDone'])

        if torrent['percent_done'] < 1:
            if torrent['seeders'] <= 0:
                available = torrent['desiredAvailable'] + torrent['haveValid']
                size = "%5s / " % scale_bytes(torrent['desiredAvailable'] + torrent['haveValid']) + size
            size = "%5s / " % scale_bytes(torrent['haveValid']) + size

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
        elif torrent['percent_done'] < 1:
            color = curses.color_pair(3)
        else:
            color = 0

        title = title.encode('utf-8')
        if focused: 
            self.pad.addstr(ypos, 0, title[0:bar_width], curses.A_REVERSE + color + curses.A_BOLD)
            self.pad.addstr(ypos, bar_width, title[bar_width:], curses.A_REVERSE + curses.A_BOLD)
        else:
            self.pad.addstr(ypos, 0, title[0:bar_width], curses.A_REVERSE + color)
            self.pad.addstr(ypos, bar_width, title[bar_width:], curses.A_REVERSE)


    def draw_torrent_status(self, torrent, focused, ypos):
        peers = ''
        parts = ['no status information available']
        if torrent['status'] == Transmission.STATUS_CHECK or \
                torrent['status'] == Transmission.STATUS_CHECK_WAIT:
            if torrent['status'] == Transmission.STATUS_CHECK_WAIT:
                parts[0] = 'will verify'
            else:
                parts[0] = "verifying (%d%%)" % int(float(torrent['recheckProgress']) * 100)

        elif torrent['errorString']:
            parts[0] = ("Error: " + torrent['errorString']).ljust(self.torrent_title_width, ' ')

        else:
            if torrent['status'] == Transmission.STATUS_SEED:
                parts[0] = 'seeding'
            elif torrent['status'] == Transmission.STATUS_DOWNLOAD:
                parts[0] = ('idle','downloading')[torrent['rateDownload'] > 0]
            elif torrent['status'] == Transmission.STATUS_STOPPED:
                parts[0] = 'paused'
            if torrent['percent_done'] < 1:
                parts[0] += " (%d%%)" % int(float(torrent['percent_done']) * 100)
            parts[0] = parts[0].ljust(17, ' ')

            # seeds and leeches will be appended right justified later
            peers  = "%4s seed%s " % (num2str(torrent['seeders']), ('s', ' ')[torrent['seeders']==1])
            peers += "%4s leech%s" % (num2str(torrent['leechers']), ('es', '  ')[torrent['leechers']==1])

            # show additional information if enough room
            if self.torrent_title_width - sum(map(lambda x: len(x), parts)) - len(peers) > 15:
                parts.append("%5s uploaded" % scale_bytes(torrent['uploadedEver']))

            if self.torrent_title_width - sum(map(lambda x: len(x), parts)) - len(peers) > 17:
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
        



    def draw_torrent_details(self, id):
        torrent = self.server.get_torrent_details(id)
#        debug(repr(torrent) + "\n\n")
        self.draw_torrent_item(torrent, False, 0)

        self.pad.hline(2, 0, curses.ACS_HLINE, self.width)

        self.draw_torrent_filelist(torrent, 3)

        self.pad.refresh(0,0, 1,0, self.height-1,self.width)
        self.screen.refresh()



    def draw_torrent_filelist(self, torrent, ypos):
        for file in torrent['files']:
            done = percent(file['length'], file['bytesCompleted'])

            self.pad.move(ypos, 0)
            self.pad.addstr("%3d%% %s" % (done*100, file['name']))
            ypos += 1



    def scroll_up(self):
        if self.focus < 0:
            self.focus = -1
            return
        else:
            self.focus -= 1
            if self.scrollpos/3 - self.focus > 0:
                self.scrollpos -= 3
                self.scrollpos = max(0, self.scrollpos)
            while self.scrollpos % 3:
                self.scrollpos -= 1

    def scroll_down(self):
        if self.focus >= len(self.torrents)-1:
            return
        else:
            self.focus += 1
            if self.focus+1 - self.scrollpos/3 > self.torrents_per_page:
                self.scrollpos += 3

    def scroll_page_up(self):
        for x in range(self.torrents_per_page - 1):
            self.scroll_up()

    def scroll_page_down(self):
        if self.focus < 0: self.focus = 0
        for x in range(self.torrents_per_page - 1):
            self.scroll_down()

    def scroll_to_top(self):
        self.focus     = 0
        self.scrollpos = 0

    def scroll_to_end(self):
        self.focus     = len(self.torrents)-1
        self.scrollpos = max(0, (len(self.torrents) - self.torrents_per_page) * 3)







    def draw_stats(self):
        self.screen.insstr((self.height-1), 0, ' '.center(self.width, ' '), curses.A_REVERSE)
        self.draw_torrent_stats()
        self.draw_global_rates()


    def draw_torrent_stats(self):
        torrents = "%d Torrents: " % self.stats['torrentCount']

        downloading_torrents = filter(lambda x: x['status']==Transmission.STATUS_DOWNLOAD, self.torrents)
        torrents += "%d downloading; " % len(downloading_torrents)

        seeding_torrents = filter(lambda x: x['status']==Transmission.STATUS_SEED, self.torrents)
        torrents += "%d seeding; " % len(seeding_torrents)

        torrents += "%d paused" % self.stats['pausedTorrentCount']

        self.screen.addstr((self.height-1), 0, torrents, curses.A_REVERSE)


    def draw_global_rates(self):
        rates_width = self.rateDownload_width + self.rateUpload_width + 3
        self.screen.move((self.height-1), self.width-rates_width)

        self.screen.addstr('D', curses.A_REVERSE)
        self.screen.addstr(scale_bytes(self.stats['downloadSpeed']).rjust(self.rateDownload_width, ' '),
                           curses.A_REVERSE + curses.A_BOLD + curses.color_pair(1))

        self.screen.addstr(' U', curses.A_REVERSE)
        self.screen.insstr(scale_bytes(self.stats['uploadSpeed']).rjust(self.rateUpload_width, ' '),
                           curses.A_REVERSE + curses.A_BOLD + curses.color_pair(2))





    def draw_title_bar(self, error_msg=''):
        self.screen.insstr(0, 0, ' '.center(self.width, ' '), curses.A_REVERSE)
        if error_msg:
            self.screen.addstr(0, 0, error_msg.encode('utf-8'), curses.A_REVERSE + curses.color_pair(1))
        else:
            self.draw_connection_status()
            self.draw_quick_help()
        
    def draw_connection_status(self):
        status = "Transmission @ %s:%s" % (self.server.host, self.server.port)
        self.screen.addstr(0, 0, status.encode('utf-8'), curses.A_REVERSE)

    def draw_quick_help(self):
        help = [('u','Upload Limit'), ('d','Download Limit')]

        if self.selected == -1:
            help  = [('s','Sort')] + help + [('q','Quit')]
            if self.focus >= 0:
                help = [('p','Pause/Unpause'), ('r','Remove'), ('v','Verify')] + help
        else:
            help += [('q','Back to List')]

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
            win.addstr(height-2, 2, input.ljust(width-4, ' '), curses.color_pair(5))
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

        while True:
            keymap = self.dialog_list_menu_options(win, width, options, focus)
            c = win.getch()
            
            if c > 96 and c < 123 and chr(c) in keymap:
                return options[keymap[chr(c)]][0]
            elif c == 27 or c == curses.KEY_BREAK:
                return None
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
            win.addstr(''.ljust(width - len(option[1]) - 4, ' '), tag)

            keys[title[1][0].lower()] = i-1
            i+=1
        return keys

# End of class Interface



def percent(full, part):
    try: percent = 1/(float(full) / float(part))
    except ZeroDivisionError: percent = 0.0
    return percent


def scale_time(seconds, type):
    if seconds < 0:
        return ('?', 'unknown')[type=='long']
    elif seconds < 60:
        if type == 'long':
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


def scale_bytes(bytes):
    if bytes >= 1073741824:
        scaled_bytes = round((bytes / 1073741824.0), 2)
        unit = "G"
    elif bytes >= 1048576:
        scaled_bytes = round((bytes / 1048576.0), 1)
        if scaled_bytes >= 100:
            scaled_bytes = int(scaled_bytes)
        unit = "M"
    elif bytes >= 1024:
        scaled_bytes = round((bytes / 1024.0), 1)
        if scaled_bytes >= 10:
            scaled_bytes = int(scaled_bytes)
        unit = "K"
    else:
        return "%dB" % bytes

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
    if DEBUG:
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





