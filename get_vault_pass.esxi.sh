#!/bin/bash
# to store (-l is the label, mb will be different later):
#   security add-generic-password -a all -D "Ansible Vault" -s "ansible_vault" -l "pass-kind" -w 'pass-here!'
# ansible allows to prompt for data (to stdout) and have different passwords here
security find-generic-password -s "ansible_vault" -l "esxi" -w
