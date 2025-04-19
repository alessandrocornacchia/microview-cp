#!/bin/bash

# MicroView Distributed Deployment Script
# Runs host and clients on local machine, collector on remote machine

set -e  # Exit on error

# Configuration (can be overridden by environment variables)
LOCAL_HOST=${LOCAL_HOST:-"0.0.0.0"}
LOCAL_PORT=${LOCAL_PORT:-5000}
LOCAL_PUBLIC_IP=${LOCAL_PUBLIC_IP:-$(hostname -I | awk '{print $1}')}  # Automatically get local IP
REMOTE_HOST=${REMOTE_HOST:-"ubuntu@192.168.100.2"}  # Change this to your remote machine
REMOTE_PORT=${REMOTE_PORT:-8000}


NUM_METRICS=${NUM_METRICS:-64}  # Number of metrics per pod (default they fill 1 metric page)
NUM_PODS=${NUM_PODS:-1}         # Number of pods 
NUM_LMAPS=${NUM_LMAPS:-1}       # Number of LMAP collectors
UVIEW_SCRAPING_INTERVAL=${UVIEW_SCRAPING_INTERVAL:-1}  # Interval for scraping metrics

DEBUG=${DEBUG:-true}
LOGS_DIR=${LOGS_DIR:-"./logs"}
IPU_RDMA_DEVICE=${IPU_RDMA_DEVICE:-"mlx5_3"}
IPU_RDMA_IB_PORT=${IPU_RDMA_IB_PORT:-1}
IPU_RDMA_GID=${IPU_RDMA_GID:-1}
WAIT_TIME=${WAIT_TIME:-5} # time to wait before starting apps
MYUSER=${MYUSER:-$(whoami)}
CONDA_ENV_NAME=${CONDA_ENV_NAME:-"uview"}  # Conda environment name
 
# Experiment mode: either "prometheus", "read_loop" or "setup"
EXPERIMENT_MODE=${EXPERIMENT_MODE:-"read_loop"}
# This will be used to organize files (logs, results, etc. ) in the remote machine if non empty
EXPERIMENT_LABEL=${EXPERIMENT_LABEL:-""}
# Duration of the experiment in seconds (0 for infinite until user stops)
EXPERIMENT_DURATION=${EXPERIMENT_DURATION:-0}

# Create logs directory if it doesn't exist
mkdir -p $LOGS_DIR

# ---------- functions ----------

# Rename statistics files on remote machine if EXPERIMENT_LABEL is non empty
handle_results() {
  if [ -n "$EXPERIMENT_LABEL" ]; then

    log "Renaming statistics files for experiment: $EXPERIMENT_LABEL"

    ssh $REMOTE_HOST "cd $MYUSER/microview-cp/ \
    && mkdir -p ./results/$EXPERIMENT_LABEL \
    && mv ./stats_*.csv ./results/$EXPERIMENT_LABEL/ \
    && mv ./logs/microview_collector.log ./results/$EXPERIMENT_LABEL/ \
    && echo \"Statistics files moved to ./results/$EXPERIMENT_LABEL/\" 
    "
  fi
}

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
  
  # Wait a moment for processes to clean up before force killing
  # here they might be dumpting data to the disk
  sleep 5
  
  # Force kill any remaining processes
  [[ -n $HOST_PID ]] && kill -9 $HOST_PID 2>/dev/null || true
  for pid in "${CLIENT_PIDS[@]}"; do
    kill -9 $pid 2>/dev/null || true
  done
  [[ -n $REMOTE_PID ]] && ssh $REMOTE_HOST "kill -9 $REMOTE_PID" 2>/dev/null || true
  
  handle_results

  echo "All processes terminated"

}

# shortcut to activate conda env
_conda() {
  if [ -z "$CONDA_DEFAULT_ENV" ]; then
    echo "Activating conda environment: $CONDA_ENV_NAME"
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate $CONDA_ENV_NAME
  else
    echo "Conda environment already activated: $CONDA_DEFAULT_ENV"
  fi
}

# ---------- functions ----------



# Set up trap
trap cleanup INT TERM

log "Starting MicroView distributed system:"
log "- Local host: $LOCAL_PUBLIC_IP:$LOCAL_PORT"
log "- Remote collector: $REMOTE_HOST:$REMOTE_PORT"
log "- Metrics clients: $NUM_PODS with $NUM_METRICS metrics each"
log "- LMAP collectors: $NUM_LMAPS"
log "- Scraping interval: $UVIEW_SCRAPING_INTERVAL seconds"
log "- RDMA device: $IPU_RDMA_DEVICE"
log "- Logs directory: $LOGS_DIR"


# Step 0: Activate conda environment
_conda

# 1. Start host agent on local machine
log "Starting MicroView host agent..."

python microview-host.py --rdma-queues $NUM_LMAPS --host $LOCAL_HOST --port $LOCAL_PORT $([ "$DEBUG" = true ] && echo "--debug") > "${LOGS_DIR}/host.log" 2>&1 &
HOST_PID=$!
log "Host agent started with PID $HOST_PID"



# Wait for host agent to initialize
log "Waiting $WAIT_TIME seconds for host agent to initialize..."
sleep $WAIT_TIME



# 2. Start metrics clients on local machine
log "Starting $NUM_PODS metrics clients with $NUM_METRICS metrics each..."
CLIENT_PIDS=()
for i in $(seq 1 $NUM_PODS); do
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

# Create empty file first
> "/tmp/remote_script.sh"

# Create the remote script
echo "cd $MYUSER/microview-cp" >> "/tmp/remote_script.sh"
echo "git pull" >> "/tmp/remote_script.sh"
echo "if [ ! -d "logs" ]; then mkdir logs ; fi" >> "/tmp/remote_script.sh"
echo "conda activate $CONDA_ENV_NAME" >> "/tmp/remote_script.sh"
echo "python microview-nic.py \
-c $LOCAL_PUBLIC_IP:$LOCAL_PORT \
-l $NUM_LMAPS \
-d $IPU_RDMA_DEVICE \
--gid $IPU_RDMA_GID \
--ib-port $IPU_RDMA_IB_PORT \
-s $UVIEW_SCRAPING_INTERVAL \
--test $EXPERIMENT_MODE \
$([ "$DEBUG" = true ] && echo "--debug") &> ./logs/microview_collector.log & echo \$!" >> "/tmp/remote_script.sh"


# Execute the script on the remote machine, and store the PID to kill later
ssh $REMOTE_HOST bash -ls < /tmp/remote_script.sh > "${LOGS_DIR}/.remote_pid.txt"

REMOTE_PID=$(tail -n 1 "${LOGS_DIR}/.remote_pid.txt")
log "Remote NIC collector started with PID $REMOTE_PID"


log "All components started:"
log "- Host agent: logs in ${LOGS_DIR}/host.log"
log "- Clients: logs in ${LOGS_DIR}/client_*.log"
log "- Collector: logs on remote machine at ./logs/microview_collector.log"
log "- Prometheus metrics available at http://$REMOTE_HOST:$REMOTE_PORT"


# Keep script running until user interrupts, or until the specified duration is reached
if [ $EXPERIMENT_DURATION -gt 0 ]; then
  log "Running for $EXPERIMENT_DURATION seconds..."
  tail -f "${LOGS_DIR}/host.log" & #"${LOGS_DIR}/client_"*.log
  sleep $EXPERIMENT_DURATION
  cleanup
else
  log "Press Ctrl+C to stop"
  tail -f "${LOGS_DIR}/host.log" #"${LOGS_DIR}/client_"*.log
fi


handle_results
  