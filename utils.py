import json
from multiprocessing import shared_memory
import multiprocessing
import ctypes
import pickle
from typing import Any, Dict


def get_value_ptr_in_shm(shm, offset):
    """ Maps the value pointer to the process memory space, using offset from the base address of the shared memory
    in the process virtual address space."""
    shm_base_addr = ctypes.addressof(ctypes.c_char.from_buffer(shm.buf))
    # print(f"Shared memory base address: {int(shm_base_addr)}, {hex(shm_base_addr)}")
    # print(f"Shared memory offset: {offset}")
    return shm_base_addr + offset


def open_untracked_shared_memory(name : str) -> shared_memory.SharedMemory:
    """
    Open a shared memory block without auto-cleanup by the resource tracker.
    
    This function allows you to open a shared memory block that was created
    with the resource tracker but does not automatically unregister it,
    allowing for manual management of the shared memory lifecycle.
    """
    shm = shared_memory.SharedMemory(name=name)
    # Unregister from resource_tracker to prevent auto-cleanup
    try:
        multiprocessing.resource_tracker.unregister(shm._name, "shared_memory")
    except:
        pass  # Ignore if already unregistered
    return shm


def load_with_extension(filename: str) -> Dict[str, Any]:
    """Load QP information from a file"""
    # if extension is json
    if filename.endswith('.json'):
        with open(filename, 'r') as f:
            remote_info = json.load(f)
    # if extension is pickle
    elif filename.endswith('.pickle'):
        with open(filename, 'rb') as f:
            remote_info = pickle.load(f)
    else:
        raise ValueError(f"Unsupported file format: {filename}") 
    return remote_info


def qp_info_from_file(filename: str) -> Dict[str, Any]:
    return load_with_extension(filename)


def mr_info_from_file(filename: str):
    mr_info = load_with_extension(filename)
    mr_info = mr_info["default"]
    return mr_info["addr"], mr_info["rkey"], mr_info["size"]