import numpy as np
import ctypes
from defaults import default_logger
from utils import get_ptr_to_shmbuf


# 64B metrics envelope
metric_dtype = np.dtype([
    ('name', 'S55'),    # metric name, 55B
    ('type', bool),  # 0 counter, 1 gauge, 1B
    ('value', np.float64),  # metric value, 8B
], align=True)

METRIC_TYPE_COUNTER = 0
METRIC_TYPE_GAUGE = 1

def update_metric_value_from_ptr(ptr_address, new_value):
    """Update a float64 value at the given address"""
    # Create a ctypes double pointer at the address
    value_ptr = ctypes.cast(ptr_address, ctypes.POINTER(ctypes.c_double))
    # Update the value
    value_ptr[0] = new_value


class MetricsPage:
    """Wrapper class for a page of metrics in shared memory"""
    def __init__(self, buffer, size, offset=0):
        
        # buffer is already in shared memory, create numpy array view for the portion of shared memory 
        self.raw = np.ndarray(
            (size,),
            dtype=metric_dtype,
            buffer=buffer,
            offset=offset
        )
        
        self.addr = get_ptr_to_shmbuf(buffer)
        self.max_metrics = size  # Max metrics this page can hold
        self.num_entries = 0  # Current number of metrics
        self.page_offset = 0
        
        # These will be populated when the page is read from raw bytes
        self.names = None
        self.types = None
        self.numpy_values = None
        
        default_logger.info(f"MetricsPage initialized at address: {self.addr + offset}.")

    
    def get_value_field_relative_addr(self, index: int) -> int:
        """
        Get the address to the value of a metric at the given index.
        
        Args:
            index: Index of the metric
        
        Returns:
            Pointer to the value of the metric, relative to the buffer address
        """
        
        record_offset = index * self.raw.itemsize
        field_offset = metric_dtype.fields['value'][1]
        return self.page_offset + record_offset + field_offset
    
    
    def add_metric(self, metric_name: str, metric_type: bool, initial_value: float) -> int:
        # Check if the page is full
        if self.num_entries >= self.max_metrics:
            raise ValueError(f"Maximum metrics ({self.max_metrics}) reached")
        
        # Add the new metric
        new_index = self.num_entries
        # Tuple (name, type, value)
        self.raw[new_index] = (metric_name, metric_type, initial_value)
        
        # Increment the number of entries
        self.num_entries += 1
        
        return self.get_value_field_relative_addr(new_index)


    def get_metrics(self):
        """
        Get all metrics in this page.
        
        Returns:
            List of tuples obtained by deserializing metrics array
        """
        return self.names, self.types, self.numpy_values
    
    
    def is_full(self):
        """
        Check if the page is full.
        
        Returns:
            True if the page is full, False otherwise
        """
        return self.num_entries >= self.max_metrics
    

    @classmethod
    def from_bytes(cls, raw_bytes, num_entries):
        """
        Create a MetricsPage object from raw bytes (e.g., returned by RDMA read)
        
        Args:
            raw_bytes: Raw bytes from RDMA read operation
            num_entries: Active number of entries
        
        Returns:
            A new MetricsPage instance populated with data from raw_bytes
        """

        # Create a new instance
        page = cls.__new__(cls)

        # Directly create the structured array from the raw bytes
        page.raw = np.frombuffer(raw_bytes, dtype=metric_dtype)
        page.max_metrics = len(page.raw)
        page.num_entries = num_entries

        # Create direct views for each field (these are references, not copies)
        page.numpy_values = page.raw['value'][:num_entries]  # Float64 array
        page.names = [n.decode() for n in page.raw['name'][:num_entries]]  # String array
        page.types = page.raw['type'][:num_entries]
        
        return page
    

    def get_metrics_values(self):
        """
        Get all metric values in this page.
        
        Returns:
            List of metric values
        """
        return self.numpy_values