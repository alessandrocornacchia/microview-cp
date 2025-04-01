import multiprocessing
import ctypes


def get_value_ptr_in_shm(shm, offset):
    """ Maps the value pointer to the process memory space, using offset from the base address of the shared memory
    in the process virtual address space."""
    shm_base_addr = ctypes.addressof(ctypes.c_char.from_buffer(shm.buf))
    # print(f"Shared memory base address: {int(shm_base_addr)}, {hex(shm_base_addr)}")
    # print(f"Shared memory offset: {offset}")
    return shm_base_addr + offset


def open_untracked_shared_memory(name : str) -> multiprocessing.shared_memory.SharedMemory:
    """
    Open a shared memory block without auto-cleanup by the resource tracker.
    
    This function allows you to open a shared memory block that was created
    with the resource tracker but does not automatically unregister it,
    allowing for manual management of the shared memory lifecycle.
    """
    shm = multiprocessing.shared_memory.SharedMemory(name=name)
    # Unregister from resource_tracker to prevent auto-cleanup
    try:
        multiprocessing.resource_tracker.unregister(shm._name, "shared_memory")
    except:
        pass  # Ignore if already unregistered
    return shm