#!/bin/bash

# Run the application with parameters from environment variables
python app.py \
  --num-metrics ${NUM_METRICS:-2} \
  ${UPDATE_METRICS:+--update-metrics}

# Keep container running if needed
# exec "$@"