#!/bin/bash

sudo apt-get install -y ansible
ansible-playbook --connection=local --inventory localhost, dfld.yml 
