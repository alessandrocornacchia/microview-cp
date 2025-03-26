#!/bin/bash

PIDS=$(ps -ef | grep "pod" | grep -v grep | awk '{print $2}')
kill $PODS