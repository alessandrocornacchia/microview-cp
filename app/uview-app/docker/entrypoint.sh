#!/bin/bash

# Activate conda environment
source /opt/conda/etc/profile.d/conda.sh
conda activate uview

# Run the application with parameters from environment variables
python app.py \
  --host ${MICROVIEW_HOST:-localhost} \
  --port ${MICROVIEW_PORT:-5000} \
  --num-metrics ${NUM_METRICS:-2} \
  --update-metrics \
  ${DEBUG_MODE:+--debug}

# Keep container running if needed
# exec "$@"