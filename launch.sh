#!/bin/bash

if [[ $# -ne 2 ]] ; then
    echo "Usage: $0 <num_pods> <experiment duration>"
    exit 1
fi

NUM_PODS=$1
DURATION=$2

# upon kill signal also terminate all running pods
trap "echo killing pods; pkill pod; exit 0" SIGINT SIGTERM

echo "Launching $NUM_PODS pods"
for ((i=0;i<$NUM_PODS;i++)) ; do
    ./bin/pod "0.0.0.0" &> /dev/null &
done

echo "Running experiment for $DURATION seconds"
sleep $DURATION;  # run experiment for 4 minuts then kill pods

echo "Killing pods and terminating experiment"
pkill pod

