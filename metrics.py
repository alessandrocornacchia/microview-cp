import numpy as np

# Import metric_dtype definition from shared_memory_app.py
metric_dtype = np.dtype([
    ('name', 'S64'),    # metric name
    ('type', np.bool),  # 0 counter, 1 gauge
    ('value', np.float64),
], align=True)