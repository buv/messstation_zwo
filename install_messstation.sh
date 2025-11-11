#!/bin/bash

# if argument is given, use it as the host ip
if [ -n "$1" ]; then
  HOST_ARGS="-i $1,"
else
  HOST_ARGS="--connection=local -i 127.0.0.1,"
fi

# install required roles
ansible-galaxy role install -r requirements.yml -p ./roles

# use second argument to distinguish between different installations:
# if value starts with "gateway", use gateway installation
# else use messstation installation
if [[ "$2" == zero ]]; then
  ansible-playbook ./zero.yml $HOST_ARGS -i inventory.yml
else
  ansible-playbook ./dfld.yml $HOST_ARGS -i inventory.yml
fi
