#!/bin/bash

sudo apt-get install ansible
ansible-playbook --connection=local --inventory localhost, dfld.yml 
