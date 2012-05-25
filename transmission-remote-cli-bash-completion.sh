#!/bin/bash

_transmission-remote-cli () {
  local cur prev opts

  _get_comp_words_by_ref cur prev

  opts="-h --help -v --version -c --connect -s --ssl -f --config --create-config -n --netrc --debug"

  if [[ ${cur} == -* ]] ; then
    COMPREPLY=( $(compgen -W "${opts}" -- ${cur}) )
  else
    case "${prev}" in
      -c|--connect)
        # no completion, wait for user input
        ;;
      -f|--config)
        # dirs and files
        _filedir
        ;;
      *)
        # dirs and torrents
        _filedir torrent
        ;;
    esac
  fi
}

complete -F _transmission-remote-cli transmission-remote-cli
