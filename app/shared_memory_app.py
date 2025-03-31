from multiprocessing import shared_memory
import random
import numpy as np
import time

PAGE_SIZE = 4096

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
    # Create a structured NumPy array using the shared memory
    array = np.ndarray((3,), dtype=metric_dtype, buffer=shm.buf)

    # Initialize the array with data
    array[0] =  ("request_number".encode('utf-8'), 0, 1923.5)

    print(f"Shared memory name: {shm.name}")
    print(f"Initial array: {array}")

    while True:
        print(f"Current array: {array}")

        time.sleep(1)
        
        # Update using any method above
        #update_metric_value(value_ptr, random.random())
        
except:
    # Cleanup
    print("Cleaning up shared memory")
    shm.close()
    shm.unlink()
    raise
