#!/bin/bash

_transmission-remote-cli.py () {
  local cur prev opts

  _get_comp_words_by_ref cur prev

  opts="--version -h --help -c --connect= -s --ssl -f --config= --create-config -n --netrc --debug"

  if [[ ${cur} == -* ]] ; then
    COMPREPLY=( $(compgen -W "${opts}" -- ${cur}) )
  else
    _filedir torrent
  fi
}

complete -F _transmission-remote-cli.py transmission-remote-cli.py
