import numpy as np
import ctypes

# Import metric_dtype definition from shared_memory_app.py
metric_dtype = np.dtype([
    ('name', 'S64'),    # metric name
    ('type', np.bool),  # 0 counter, 1 gauge
    ('value', np.float64),
], align=True)


def update_metric_value_from_ptr(ptr_address, new_value):
    """Update a float64 value at the given address"""
    # Create a ctypes double pointer at the address
    value_ptr = ctypes.cast(ptr_address, ctypes.POINTER(ctypes.c_double))
    # Update the value
    value_ptr[0] = new_value


class MetricsPage:
    """Wrapper class for a page of metrics in shared memory"""
    def __init__(self, array, max_metrics, page_offset):
        self.buckets = array  # Numpy array for this page
        self.max_metrics = max_metrics  # Max metrics this page can hold
        self.num_entries = 0  # Current number of metrics
        self.page_offset = page_offset  # Offset in shared memory
        # self.metrics = []  # List of metric metadata (name, etc.)
        print(f"MetricsPage initialized with page_offset: {self.page_offset}, max_metrics: {self.max_metrics}")

    def get_value_offset_from_shm_base_addr(self, index: int) -> int:
        """
        Get the pointer to the value of a metric at the given index.
        
        Args:
            index: Index of the metric
        
        Returns:
            Pointer to the value of the metric
        """
        
        record_offset = index * self.buckets.itemsize
        field_offset = metric_dtype.fields['value'][1]
        return self.page_offset + record_offset + field_offset
    
    
    def add_metric(self, metric_name: str, metric_type: bool, initial_value: float) -> int:
        # Check if the page is full
        if self.num_entries >= self.max_metrics:
            raise ValueError(f"Maximum metrics ({self.max_metrics}) reached")
        
        # Add the new metric
        new_index = self.num_entries
        self.buckets[new_index] = (metric_name.encode('utf-8'), metric_type, initial_value)
        
        # Increment the number of entries
        self.num_entries += 1
        
        return self.get_value_offset_from_shm_base_addr(new_index)
