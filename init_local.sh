#!/bin/bash

ansible-galaxy role install -r requirements.yml -p ./roles
./dfld.yml --inventory inventory.local.yml
