#!/bin/bash

# check whether user had supplied -h or --help . If yes display usage
if [[ ( $@ == "--help") ||  $@ == "-h" || $# -eq 0 ]]
then 
	echo "Usage: $0 remote-host"
	exit 0
fi 

ansible-playbook -i $1, -e "ansible_user=dfld" -k dfld.yml
