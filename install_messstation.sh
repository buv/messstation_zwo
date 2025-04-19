#!/bin/bash

# if argument is given, use it as the host ip
if [ -n "$1" ]; then
  HOST_ARGS="-i $1,"
else
  HOST_ARGS="--connection=local -i 127.0.0.1,"
fi

./dfld.yml $HOST_ARGS -i inventory.yml 
