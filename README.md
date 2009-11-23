# A console client for the BitTorrent client [Transmission](http://www.transmissionbt.com/ "Transmission Homepage").

**Download the latest version [here](http://github.com/fagga/transmission-remote-cli/raw/master/transmission-remote-cli.py).**

### Screenshot
![Screenshot](http://cloud.github.com/downloads/fagga/transmission-remote-cli/screenshot.png)


### Setup
If your Transmission daemon is listening for clients at localhost:9091 without
authentication, you don't need to configure anything.

Authentication and connection information can be set via command line with one
of these patterns:  
`$ transmission-remote-cli.py homeserver`  
`$ transmission-remote-cli.py homeserver:1234`  
`$ transmission-remote-cli.py johndoe:secretbirthday@homeserver`  
`$ transmission-remote-cli.py johndoe:secretbirthday@homeserver:1234`  

You can write this (and other) stuff into a configuration file:  
`$ transmission-remote-cli.py johndoe:secretbirthday@homeserver:1234 --create-config`  

No configuration file is created unless you create it somehow. However, if the
file exists, it is re-written when trcli exits.

If you don't like the default configuration file path
(~/.config/transmission-remote-cli/settings.cfg), change it:  
`$ transmission-remote-cli.py johndoe:secretbirthday@homeserver:1234 --config ~/.trclirc --create-config`


### Contact
Feel free to request new features or provide bug reports.  
You can find my email address [here](http://github.com/fagga).
