**Please don't request or submit new features that would add more than a
  handful (literally!) of lines of code.**

Given the messy implementation, adding functionality either causes new bugs
that nobody wants to fix or it adds cruft that I won't be able to understand
two weeks later because there's already so much cruft that mostly works but
without any regards to the bigger picture. (Also, there is not much of a
bigger picture, it's hacks all the way down (and maybe even up). Imagine a
house that is hold together by the corpses in the drywall.)

**This doesn't mean the project is dead.**  
I'll always accept any bugfixes. (Unless they cause new bugs.)

## About

A console client for the BitTorrent client [Transmission](http://www.transmissionbt.com/ "Transmission Homepage").


## Distributions

- [Arch Linux](https://www.archlinux.org/packages/community/any/transmission-remote-cli/)
- [Debian](http://packages.debian.org/search?keywords=transmission-remote-cli)
- [Fedora](https://admin.fedoraproject.org/pkgdb/package/transmission-remote-cli/)
- [OpenSUSE](http://software.opensuse.org/package/transmission-remote-cli?search_term=transmission-remote-cli)
- [Ubuntu](http://packages.ubuntu.com/search?keywords=transmission-remote-cli)


## Requirements

For Python 2.5 or older, you need [simplejson](http://pypi.python.org/pypi/simplejson/) which should be
packaged in any Linux distribution. The Debian/Ubuntu package is called
`python-simplejson`.

### Optional Modules (you don't need them but they add features):

- GeoIP: Guess which country peers come from.
- adns: Resolve IPs to host names.
- [xerox](https://pypi.python.org/pypi/xerox): Copy magnet links to the system clipboard. You will also need either xclip on linux or pbcopy on OS X for this to work.

Debian/Ubuntu package names are `python-adns` and `python-geoip`.


## Usage

### Connection information

Authentication and host/port can be set via command line with one
of these patterns:  
`$ transmission-remote-cli -c homeserver`  
`$ transmission-remote-cli -c homeserver:1234`  
`$ transmission-remote-cli -c johndoe:secretbirthday@homeserver`  
`$ transmission-remote-cli -c johndoe:secretbirthday@homeserver:1234`  

You can write this (and other) stuff into a configuration file:  
`$ transmission-remote-cli -c johndoe:secretbirthday@homeserver:1234 --create-config`  

No configuration file is created automatically, you have to do this
somehow. However, if the file exists, it is re-written when trcli exits to
remember some settings. This means you shouldn't have trcli running when
editing your configuration file.

If you don't like the default configuration file path
~/.config/transmission-remote-cli/settings.cfg, change it:  
`$ transmission-remote-cli -f ~/.trclirc --create-config`


### Calling transmission-remote

transmission-remote-cli forwards all arguments after '--' to
transmission-remote. This is useful if your daemon requires authentication
and/or doesn't listen on the default localhost:9091 for
instructions. transmission-remote-cli reads HOST:PORT and authentication from
the config file and forwards them on to transmission-remote, along with your
arguments.

Some examples:  
`$ transmission-remote-cli -- -l`  
`$ transmission-remote-cli -- -t 2 -i`  
`$ transmission-remote-cli -- -as`


### Add torrents

If you provide only one command line argument and it doesn't start with '-',
it's treated like a torrent file/URL and submitted to the daemon via
transmission-remote. This is useful because you can instruct Firefox to open
torrent files with transmission-remote-cli.

`$ transmission-remote-cli http://link/to/file.torrent`  
`$ transmission-remote-cli path/to/some/torrent-file`


## Screenshots

![Main window - full, v1.3](https://github.com/fagga/transmission-remote-cli/raw/master/screenshots/screenshot-mainfull-v1.3.png)

![Main window - compact, v1.3](https://github.com/fagga/transmission-remote-cli/raw/master/screenshots/screenshot-maincompact-v1.3.png)

![Info window, v1.3](https://github.com/fagga/transmission-remote-cli/raw/master/screenshots/screenshot-details-v1.3.png)


## Copyright

Released under the GPLv3 license, see [COPYING](COPYING) for details.


## Contact

Feel free to request new features or provide bug reports.  
You can find my email address [here](http://github.com/fagga).
