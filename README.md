# A console client for the BitTorrent client [Transmission](http://www.transmissionbt.com/ "Transmission Homepage").

**Download the latest version from [here](http://github.com/fagga/transmission-remote-cli/raw/master/transmission-remote-cli.py).**

## Screenshot
![Screenshot](http://cloud.github.com/downloads/fagga/transmission-remote-cli/screenshot.png)


## Modules

For Python 2.5 or older, you need [simplejson](http://pypi.python.org/pypi/simplejson/) which should be
packaged in any Linux distribution. The Debian/Ubuntu package is called
`python-simplejson`.

### Optional Modules (you don't need them but they add features):
- GeoIP: Guess which country peers come from.
- adns: Resolve IPs to host names.

Debian/Ubuntu package names are `python-adns` and `python-geoip`.


## Connection information
Authentication and host/port can be set via command line with one
of these patterns:  
`$ transmission-remote-cli.py -c homeserver`  
`$ transmission-remote-cli.py -c homeserver:1234`  
`$ transmission-remote-cli.py -c johndoe:secretbirthday@homeserver`  
`$ transmission-remote-cli.py -c johndoe:secretbirthday@homeserver:1234`  

You can write this (and other) stuff into a configuration file:  
`$ transmission-remote-cli.py -c johndoe:secretbirthday@homeserver:1234 --create-config`  

No configuration file is created automatically, you have to do this
somehow. However, if the file exists, it is re-written when trcli exits to
remember some settings. This means you shouldn't have trcli running when
editing your configuration file.

If you don't like the default configuration file path
~/.config/transmission-remote-cli/settings.cfg, change it:  
`$ transmission-remote-cli.py -f ~/.trclirc --create-config`


## Calling transmission-remote  
transmission-remote-cli forwards all arguments after '--' to
transmission-remote. This is useful if your daemon requires authentication
and/or doesn't listen on the default localhost:9091 for
instructions. transmission-remote-cli reads HOST:PORT and authentication from
the config file and forwards them on to transmission-remote, along with your
arguments.

Some examples:  
`$ transmission-remote-cli.py -- -l`  
`$ transmission-remote-cli.py -- -t 2 -i`  
`$ transmission-remote-cli.py -- -as`


## Add torrents  
If you provide only one command line argument and it doesn't start with '-',
it's treated like a torrent file/URL and submitted to the daemon via
transmission-remote. This is useful because you can instruct Firefox to open
torrent files with transmission-remote-cli.py.

`$ transmission-remote-cli.py http://link/to/file.torrent`  
`$ transmission-remote-cli.py path/to/some/torrent-file`


## Contact
Feel free to request new features or provide bug reports.  
You can find my email address [here](http://github.com/fagga).
