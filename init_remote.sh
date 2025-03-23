#!/bin/bash

ansible-galaxy role install -r requirements.yml -p ./roles
./dfld.yml -i inventory.yml
