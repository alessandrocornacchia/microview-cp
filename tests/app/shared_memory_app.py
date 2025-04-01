from multiprocessing import shared_memory
import random
import numpy as np
import time

PAGE_SIZE = 4096

import ctypes

def update_metric_value(ptr_address, new_value):
    """Update a float64 value at the given address"""
    # Create a ctypes double pointer at the address
    value_ptr = ctypes.cast(ptr_address, ctypes.POINTER(ctypes.c_double))
    # Update the value
    value_ptr[0] = new_value

def get_value_ptr_from_ndarray_base(array, index):
    # Correct - calculate proper memory address:
    base_address = array.ctypes.data  # Base address of the array
    record_offset = index * array.itemsize  # Offset to the first record (index 0)
    field_offset = metric_dtype.fields['value'][1]  # Offset to 'value' field
    return base_address + record_offset + field_offset

# Define a structured dtype for our array
# TODO this could include metric labels defined in the app
metric_dtype = np.dtype([
    ('name', 'S64'),    # metric name
    ('type', np.bool),  # 0 counter, 1 gauge
    ('value', np.float64),
    # ('padding', 'S7')  # To ensure 8-byte alignment
], align=True)

        
# Calculate the size of our structured array
array_size = PAGE_SIZE // np.dtype(metric_dtype).itemsize
print("Page size {}".format(PAGE_SIZE))
print("Item size {}".format(np.dtype(metric_dtype).itemsize))
print("Array size {}".format(array_size))
print("Alignment: {} bytes".format(np.dtype(metric_dtype).alignment))

# Create a shared memory block
shm = shared_memory.SharedMemory(create=True, size=PAGE_SIZE)

try:
    # Create a structured NumPy array using the shared memory at offset 10
    array = np.ndarray((3,), dtype=metric_dtype, buffer=shm.buf, offset=10)

    # Initialize the array with data
    array[0] =  ("request_number".encode('utf-8'), 0, 1923.5)
    value_ptr = get_value_ptr_from_ndarray_base(array, 0)

    print(f"Shared memory name: {shm.name}")
    print(f"Initial array: {array}")

    while True:
        print(f"Current array: {array}")

        time.sleep(1)
        
        # Update using any method above
        update_metric_value(value_ptr, random.random())

        page_offset = 10
        shm_base_addr = ctypes.addressof(ctypes.c_char.from_buffer(shm.buf))
        print(f"Shared memory base address: {hex(shm_base_addr)}")
        actual_address = shm_base_addr + page_offset
        print(f"Actual address: {hex(actual_address)}")
        print("Array ctype.data: {}".format(hex(array.ctypes.data)))
        
except:
    # Cleanup
    print("Cleaning up shared memory")
    shm.close()
    shm.unlink()
    raise
