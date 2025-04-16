#!/bin/bash

# MicroView Distributed Deployment Script
# Runs host and clients on local machine, collector on remote machine

set -e  # Exit on error

# Configuration (can be overridden by environment variables)
LOCAL_HOST=${LOCAL_HOST:-"0.0.0.0"}
LOCAL_PORT=${LOCAL_PORT:-5000}
LOCAL_PUBLIC_IP=${LOCAL_PUBLIC_IP:-$(hostname -I | awk '{print $1}')}  # Automatically get local IP
REMOTE_HOST=${REMOTE_HOST:-"user@remote-machine"}  # Change this to your remote machine
REMOTE_PORT=${REMOTE_PORT:-8000}
NUM_METRICS=${NUM_METRICS:-10}
NUM_CLIENTS=${NUM_CLIENTS:-2}
NUM_LMAPS=${NUM_LMAPS:-2}
DEBUG=${DEBUG:-true}
LOGS_DIR=${LOGS_DIR:-"./logs"}
RDMA_DEVICE=${RDMA_DEVICE:-"mlx5_1"}
WAIT_TIME=${WAIT_TIME:-5}

# Create logs directory if it doesn't exist
mkdir -p $LOGS_DIR

# ---------- functions ----------
# Log function with timestamp
log() {
  echo "[$(date +%T)] $1" | tee -a "${LOGS_DIR}/main.log"
}

# Clean up function
cleanup() {
  echo "Forwarding signal to all processes..."
  
  # Send SIGTERM first to allow graceful cleanup
  [[ -n $HOST_PID ]] && kill -TERM $HOST_PID 2>/dev/null || true
  for pid in "${CLIENT_PIDS[@]}"; do
    kill -TERM $pid 2>/dev/null || true
  done
  [[ -n $REMOTE_PID ]] && ssh $REMOTE_HOST "kill -TERM $REMOTE_PID" 2>/dev/null || true
  
  # Wait a moment for processes to clean up
  sleep 2
  
  # Force kill any remaining processes
  [[ -n $HOST_PID ]] && kill -9 $HOST_PID 2>/dev/null || true
  for pid in "${CLIENT_PIDS[@]}"; do
    kill -9 $pid 2>/dev/null || true
  done
  [[ -n $REMOTE_PID ]] && ssh $REMOTE_HOST "kill -9 $REMOTE_PID" 2>/dev/null || true
  
  echo "All processes terminated"
}
# ---------- functions ----------



# Set up trap
trap cleanup EXIT INT TERM

log "Starting MicroView distributed system:"
log "- Local host: $LOCAL_PUBLIC_IP:$LOCAL_PORT"
log "- Remote collector: $REMOTE_HOST:$REMOTE_PORT"
log "- Metrics clients: $NUM_CLIENTS with $NUM_METRICS metrics each"
log "- LMAP collectors: $NUM_LMAPS"
log "- RDMA device: $RDMA_DEVICE"
log "- Logs directory: $LOGS_DIR"



# 1. Start host agent on local machine
log "Starting MicroView host agent..."
python microview-host.py --rdma --host $LOCAL_HOST --port $LOCAL_PORT $([ "$DEBUG" = true ] && echo "--debug") > "${LOGS_DIR}/host.log" 2>&1 &
HOST_PID=$!
log "Host agent started with PID $HOST_PID"



# Wait for host agent to initialize
log "Waiting $WAIT_TIME seconds for host agent to initialize..."
sleep $WAIT_TIME



# 2. Start metrics clients on local machine
log "Starting $NUM_CLIENTS metrics clients with $NUM_METRICS metrics each..."
CLIENT_PIDS=()
for i in $(seq 1 $NUM_CLIENTS); do
  python libmicroview.py --num-metrics $NUM_METRICS --update-metrics $([ "$DEBUG" = true ] && echo "--debug") > "${LOGS_DIR}/client_${i}.log" 2>&1 &
  CLIENT_PID=$!
  CLIENT_PIDS+=($CLIENT_PID)
  log "Started client $i with PID $CLIENT_PID"
  sleep 1  # Small delay between client starts
done



# Wait for metrics to be registered
log "Waiting $WAIT_TIME seconds for metrics to register..."
sleep $WAIT_TIME


# 3. Start collector on remote machine via SSH
log "Starting MicroView NIC collector on remote machine..."
# Copy a small script to run the collector and get its PID
ssh $REMOTE_HOST "cd ~/microview-cp && python microview-nic.py \
  --control-plane $LOCAL_PUBLIC_IP:$LOCAL_PORT \
  --port $REMOTE_PORT \
  --lmaps $NUM_LMAPS \
  --test with_prometheus_multithread \
  $([ "$DEBUG" = true ] && echo "--debug") > ~/microview_collector.log 2>&1 & echo \$!" > "${LOGS_DIR}/remote_pid.txt"

REMOTE_PID=$(cat "${LOGS_DIR}/remote_pid.txt")
log "Remote NIC collector started with PID $REMOTE_PID"


log "All components started:"
log "- Host agent: logs in ${LOGS_DIR}/host.log"
log "- Clients: logs in ${LOGS_DIR}/client_*.log"
log "- Collector: logs on remote machine at ~/microview_collector.log"
log "- Prometheus metrics available at http://$REMOTE_HOST:$REMOTE_PORT"
log "Press Ctrl+C to stop all processes"


# Keep script running until user interrupts
log "Monitoring system logs. Press Ctrl+C to stop."
tail -f "${LOGS_DIR}/host.log" "${LOGS_DIR}/client_"*.log