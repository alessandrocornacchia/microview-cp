"""
    It provides some helper classes to deal with RDMA operations, such as a MR manager, to create or register exisitng 
    memory within a RDMA protection domain, and a QP pool manager, to create and manage a pool of RDMA queue pairs.

    At the bottom, two tests show how these helper classes can be used to connect two queue pairs and perform RDMA 
    reads from a remote memory region.
"""


#!/usr/bin/env python3
import json
import pickle
import signal
import sys
import os
import time
import threading
from typing import Dict, List, Any, Optional, Tuple
import numpy as np
import logging

# Import PyVerbs modules
import pyverbs.device as d
import pyverbs.pd as pd
import pyverbs.cq as cq
import pyverbs.qp as qp
import pyverbs.mr as mr
import pyverbs.addr as addr
import pyverbs.enums
from pyverbs.pd import PD
from pyverbs.mr import MR
from pyverbs.qp import QP, QPInitAttr, QPAttr, QPCap
from pyverbs.cq import CQ
from pyverbs.addr import GID
from pyverbs.addr import GlobalRoute
from pyverbs.addr import AHAttr
from pyverbs.wr import SGE, RecvWR, SendWR
from defaults import *


class MemoryRegionPool:
    """
    Manages memory regions that can be accessed via RDMA
    """
    def __init__(self, pd, default_buffer_size=DEFAULT_PAGE_SIZE):
        """
        Initialize the memory region manager
        
        Args:
            pd: Protection Domain to use for memory registration
            default_buffer_size: Default size for memory regions
        """
        self.pd = pd
        self.default_buffer_size = default_buffer_size
        self.memory_regions = {}
        
    
    def register_memory_region(self, name: str, addr: int, size: int) -> Dict[str, Any]:
        """
        Register an existing buffer with RDMA
        
        Args:
            name: Name identifier for this memory region
            addr: Address of the pre-allocated memory region
            size: Size of the memory region
            
        Returns:
            Dict containing memory region information
        """
        # Register with remote access flags
        access_flags = pyverbs.enums.IBV_ACCESS_LOCAL_WRITE | pyverbs.enums.IBV_ACCESS_REMOTE_WRITE | pyverbs.enums.IBV_ACCESS_REMOTE_READ
        
        # Register the memory region
        memory_region = MR(self.pd, size, access_flags, addr)
        
        # Store memory region info (without buffer since it's managed externally)
        mr_info = {
            "name": name,
            "addr": addr,
            "rkey": memory_region.rkey,
            "lkey": memory_region.lkey,
            "size": size,
            "buffer": None,  # Physical buffer is managed externally depending if local or remote use
            "mr": memory_region
        }
        
        self.memory_regions[name] = mr_info
        default_logger.info(f"Registered memory region '{name}': addr={hex(addr)}, rkey={memory_region.rkey}, size={size}")
        
        return self.get_memory_region_info(name)

    def create_memory_region(self, name: str, size: int = None) -> Dict[str, Any]:
        """
        Create and register a new memory region, of given name and size
        
        Args:
            name: Name identifier for this memory region
            size: Size of memory region to create (defaults to default_buffer_size)
            
        Returns:
            Dict containing memory region information
        """
        if size is None:
            size = self.default_buffer_size
        
        # Create buffer with numpy array
        buffer = np.zeros(size, dtype=np.uint8)
        
        # Optionally fill with some initial data
        initial_data = f"RDMA-MR-{name}".encode()
        buffer[:len(initial_data)] = np.frombuffer(initial_data, dtype=np.uint8)
        
        # Get buffer address and register with remote access flags
        buffer_addr = buffer.ctypes.data
        
        # Register the memory region
        self.register_memory_region(name, buffer_addr, size)

        # Store the buffer reference in our memory_regions dict to keep it alive
        self.memory_regions[name]["buffer"] = buffer

        return self.get_memory_region_info(name)

    
    def save_memory_region_info(self, filename: str):
        """ Save memory region information to a file for debugging and sharing with clients."""
        serializable_regions = {
            name: self.get_memory_region_info(name) for name, _ in self.memory_regions.items()
        }
        # Save to json file in json format
        with open(filename + '.json' if not filename.endswith('.json') else filename, 'w') as f:
            json.dump(serializable_regions, f, indent=2)
            default_logger.info(f"Saved RDMA info to JSON file: {filename}")
        # Save to pickle file
        with open(filename + '.pickle' if not filename.endswith('.pickle') else filename, 'wb') as f:
            pickle.dump(serializable_regions, f)
            default_logger.info(f"Saved RDMA info to pickle file: {filename}")
        
    
    def get_memory_region(self, name: str) -> Optional[MR]:
        """Get the MR object for a registered memory region"""
        if name not in self.memory_regions:
            return None
        
        mr_info = self.memory_regions[name]
        return mr_info["mr"]
    

    def get_memory_region_info(self, name: str) -> Optional[Dict[str, Any]]:
        """Get information about a registered memory region"""
        if name not in self.memory_regions:
            return None
        
        mr_info = self.memory_regions[name]
        return {
            "name": mr_info["name"],
            "addr": mr_info["addr"],
            "rkey": mr_info["rkey"],
            "lkey": mr_info["lkey"],
            "size": mr_info["size"]
        }
    
    def list_memory_regions(self) -> List[Dict[str, Any]]:
        """List all registered memory regions"""
        return [self.get_memory_region_info(name) for name in self.memory_regions]
    
    
    def cleanup(self):
        """Close and clean up all memory regions"""
        for name, mr_info in list(self.memory_regions.items()):
            try:
                default_logger.debug(f"Cleaning up RDMA memory region '{name}'")
                mr_info["mr"].close()
                del self.memory_regions[name]
            except Exception as e:
                default_logger.error(f"Error cleaning up memory region '{name}': {e}")


class QueuePairPool:
    """
    Manages a pool of pre-initialized Queue Pairs for RDMA operations
    """
    def __init__(self, 
                 rdma_device: str, 
                 gid_index : int = DEFAULT_GID, 
                 ib_port: int = DEFAULT_IB_PORT,
                 pool_size: int = DEFAULT_QP_POOL_SIZE):
        """
        Initialize the Queue Pair pool
        
        Args:
            pool_size: Number of Queue Pairs to create in the pool
        """
        self.pool_size = pool_size
        
        # RDMA components
        self.ctx = None
        self.pd = None
        self.cq = None
        self.gid = None
        self.gid_index = gid_index
        self.rdma_device = rdma_device
        self.ib_port = ib_port
        
        # Queue Pair pool
        self.qps = []
        
        # Initialize RDMA context and protection domain
        self._init_rdma_context()
        
        # Create QP pool
        self._create_qp_pool()

        
    def _init_rdma_context(self):
        """Initialize RDMA context and protection domain"""
        try:
            
            # Open device context using first device
            self.ctx = d.Context(name=self.rdma_device)
            default_logger.info(f"Opened RDMA device: {self.rdma_device}")
            
            # Create Protection Domain
            self.pd = PD(self.ctx)
            
            # Create Completion Queue (shared by all QPs)
            self.cq = CQ(self.ctx, 100)  # 100 is the CQ size
            
            # Find a valid GID index for RoCEv2
            # Default port number can check with command ibv_devinfo
            self.gid = self.ctx.query_gid(self.ib_port, self.gid_index)
            
        except Exception as e:
            default_logger.error(f"Error initializing RDMA context: {e}")
            raise
    
    
    def _create_qp_pool(self):
        """Create a pool of Queue Pairs"""
        for i in range(self.pool_size):
            try:
                # Create QP Init Attributes
                qp_init_attr = QPInitAttr(
                    qp_type=pyverbs.enums.IBV_QPT_RC,  # Reliable Connection
                    sq_sig_all=1,  # Generate completion for all work requests
                    cap=QPCap(
                        max_send_wr=10,
                        max_recv_wr=10,
                        max_send_sge=1,
                        max_recv_sge=1
                    ),
                    scq=self.cq,
                    rcq=self.cq
                )
                
                default_logger.debug(f"Created Queue Pair #{i} attributes")

                # Create Queue Pair
                queue_pair = QP(self.pd, qp_init_attr)
                
                # Transition QP to INIT state
                attr = QPAttr()
                attr.qp_state = pyverbs.enums.IBV_QPS_INIT
                attr.pkey_index = 0
                attr.port_num = 1
                attr.qp_access_flags = pyverbs.enums.IBV_ACCESS_REMOTE_READ
                
                queue_pair.modify(attr, 
                    pyverbs.enums.IBV_QP_STATE | pyverbs.enums.IBV_QP_PKEY_INDEX | 
                    pyverbs.enums.IBV_QP_PORT | pyverbs.enums.IBV_QP_ACCESS_FLAGS
                )
                
                default_logger.debug(f"Queue Pair #{i} transitioned to INIT state")

                # Store QP information
                qp_info = {
                    "qp": queue_pair,
                    "qp_num": queue_pair.qp_num,
                    "in_use": False,
                    "remote_info": None
                }
                
                self.qps.append(qp_info)
                default_logger.info(f"Created Queue Pair #{i}: GID={self.gid}, qp_num={qp_info['qp_num']}")
                
            except Exception as e:
                default_logger.error(f"Error creating Queue Pair #{i}: {e}")
                raise
        
        default_logger.info(f"Created Queue Pair pool with {self.pool_size} QPs")
    
    
    def get_qp_object(self, index: int) -> Tuple[QP,CQ]:
        """
        Get the QP object at the specified index
        
        Args:
            index: Index of the QP in the pool
            
        Returns:
            QP object if found, None otherwise
            CQ object
        """
        if index < 0 or index >= len(self.qps):
            raise ValueError(f"Queue Pair index {index} out of range (0-{len(self.qps)-1})")
        
        return self.qps[index]["qp"], self.cq
    

    def get_qp_local_info(self, index: int) -> Dict[str, Any]:
        """
        Get information about a specific queue pair or find an available one
        
        Args:
            index: Specific QP index to get, or None to find any available QP
            
        Returns:
            Dict with queue pair information for exchange
        """
        
        if index < 0 or index >= len(self.qps) or index is None:
            raise ValueError(f"Queue Pair index {index} out of range (0-{len(self.qps)-1})")
        return {
                "qp_num": self.qps[index]["qp_num"],
                "gid": str(self.gid),
                "in_use": self.qps[index]["in_use"],
            }

    def save_queue_pair_info(self, filename: str):
        """Save memory region information and queue information to file. 
        This is useful for debugging and sharing with clients."""
        
        qp_info = {
            f"qp_{i}": self.get_qp_local_info(i) for i in range(len(self.qps))
        }
        # Save to json file in json format
        with open(filename + '.json', 'w') as f:
            json.dump(qp_info, f, indent=2)
            default_logger.info(f"Saved RDMA info to JSON file: {filename}")
        # Save to pickle file
        with open(filename + '.pickle', 'wb') as f:
            pickle.dump(qp_info, f)
            default_logger.info(f"Saved RDMA info to pickle file: {filename}")
            

    def connect_queue_pair(self, index: int, remote_info: Dict[str, Any]) -> bool:
        """
        Connect a queue pair to a remote QP
        
        Args:
            index: Index of the QP in the pool
            remote_info: Dictionary containing remote QP information
            
        Returns:
            True if connection successful
        """
        if index < 0 or index >= len(self.qps):
            raise ValueError(f"Queue Pair index {index} out of range (0-{len(self.qps)-1})")
        
        # Destination GID
        remote_gid = GID(remote_info["gid"])
        remote_qp_num = remote_info["qp_num"]
        
        # Transition to RTS (Ready to Send) state
        try:    
            
            # Get the QP object
            local_qp = self.qps[index]
            
            # check if Queue Pair is already in use, if yes reset state
            if local_qp["in_use"]:
                default_logger.warning(f"⚠️ Queue Pair #{index} is already in use")
                return False

            qp = local_qp["qp"] 

            default_logger.info(f"Connecting to remote QP: {remote_qp_num}, GID: {remote_gid}")

            # Create Global Route object
            gr = GlobalRoute(dgid=remote_gid, sgid_index=self.gid_index)

            # Create Address Handle 
            ah_attr = AHAttr(
                gr = gr,
                is_global=1,                    # Using GID (RoCE)
                port_num=self.ib_port,          # Port number
            )
            qa = QPAttr()
            qa.ah_attr = ah_attr
            qa.dest_qp_num = remote_qp_num
            # qa.path_mtu = args['mtu']
            qa.max_rd_atomic = 1
            qa.max_dest_rd_atomic = 1
            qa.qp_access_flags = pyverbs.enums.IBV_ACCESS_REMOTE_WRITE | pyverbs.enums.IBV_ACCESS_REMOTE_READ | pyverbs.enums.IBV_ACCESS_LOCAL_WRITE
            

            # Change QP state to RTS
            qp.to_rts(qa)
            
            # Cache the QP info of the remote QP is trying to connect to here
            local_qp["remote_info"] = remote_info
            local_qp["in_use"] = True
        
            default_logger.info(f"✅ Queue Pair #{index} (qp_num={local_qp['qp_num']}) connected to remote QP {remote_info['qp_num']}")
            return True
            
        except Exception as e:
            default_logger.error(f"❌ Error connecting Queue Pair #{index}: {e}")
            return False
    
    def list_queue_pairs(self) -> List[Dict[str, Any]]:
        """List all queue pairs in the pool with their status"""
        return [self.get_qp_local_info(i) for i in range(len(self.qps))]
    
    def cleanup(self):
        """Clean up all queue pairs and RDMA resources"""
        # First clean up memory regions
        if hasattr(self, 'mr_manager'):
            self.mr_manager.cleanup()
        
        # Clean up queue pairs
        for qp_info in self.qps:
            try:
                qp_info["qp"].close()
            except Exception as e:
                default_logger.error(f"Error closing QP {qp_info['qp_num']}: {e}")
        
        # Clean up other resources
        if hasattr(self, 'cq') and self.cq:
            self.cq.close()
        
        if hasattr(self, 'pd') and self.pd:
            self.pd.close()
        
        if hasattr(self, 'ctx') and self.ctx:
            self.ctx.close()
            
        default_logger.info("RDMA resources cleaned up")


# TODO use this instead of dictionary above
class MRMetadata:
    """
    Represents a remote memory region that can be accessed via RDMA READ.
    Contains all necessary information for performing RDMA operations.
    """
    def __init__(self, remote_addr: int, rkey: int, length: int, mr, name: str = None):
        """
        Initialize a remote memory region.
        
        Args:
            remote_addr: Remote memory address to read from
            rkey: Remote key for the memory region
            length: Length of the memory region to read
            buffer: Local buffer to store read data
            mr: Memory registration for the local buffer
            name: Optional name identifier for this region
        """
        self.remote_addr = remote_addr
        self.rkey = rkey
        self.length = length
        self.mr = mr
        self.name = name


class OneSidedReader:
    """
    A simple class to perform one-sided RDMA READ operations. Assumes the QPs are already connected
    and in RTS (Ready to Send state)
    """

    def __init__(self, pd : PD, queues: Tuple[QP,CQ], remote_mrs : List[MRMetadata], id : int = None, parent: str = "MicroviewNIC"):
        """
        Initialize the one-sided reader
        
        Args:
            pd: Protection Domain to use for memory registration
            queues: Queue Pair object to use for RDMA operations, and Completion Queue
            remote_mr: Remote memory region metadata
        """
        self.pd = pd
        if not queues[0]:
            raise ValueError("Provide a Queue Pair")
        if not queues[1]:
            raise ValueError("Provide a Completion Queue")
        self.qp = queues[0]
        self.cq = queues[1]
        self.remote_mrs = remote_mrs
        self.n_mr = len(remote_mrs)
        self.mrm = None 
        if not id:
            id = hash(time.time()) % 10000
        self.logger = logging.getLogger(f'{parent}.RDMA.{id}')
        
        logging.info(f"OneSidedReader initialized with {self.n_mr} remote memory regions: {self.remote_mrs}")
        
        # create local RDMA buffers
        self._init_local_mr()
    

    def _init_local_mr(self):
        """
        # Create desired number of local memory region for RDMA operations. Each local Mr is a buffer for a
        # remote MR operation. 
        # TODO probably I can send SGE at once to amortize cost of syscalls on a single QP, and also poll for completion
        """
        self.mrm = MemoryRegionPool(self.pd)
        
        for i in range(self.n_mr):
            self.mrm.create_memory_region(f"local_mr_{i}", self.remote_mrs[i].length)

    def execute(self):
        """
        One-off execution
        """
        # issue RDMA reads
        for i in range(self.n_mr):
            self.rdma_read(i)

        # wait for completions
        ncomp = 0
        results = []
        for i in range(self.n_mr):
            res = self.poll_completion(i)
            if res:
                ncomp += 1
                results.append(res)
                # logger.info(f"RDMA READ result for MR {i}: {res}")
        self.logger.debug(f"Polled {ncomp} completions")
        return results


    def cleanup(self):
        """Clean up local memory regions, no Queue Pairs as those are managed externally"""
        if self.mrm:
            self.mrm.cleanup()
        

    def rdma_read(self, index : int):
        """
        Perform an RDMA READ operation
        
        Args:
            index: Index of the remote memory region to read from
            
        Returns:
            The data read from remote memory
        """
        
        try:
            remote_addr = self.remote_mrs[index].remote_addr
            rkey = self.remote_mrs[index].rkey
            length = self.remote_mrs[index].length
            mr = self.mrm.get_memory_region(f"local_mr_{index}")

            self.logger.info(f"Creating RDMA READ work request, remote_addr={hex(remote_addr)}, rkey={rkey}, length={length}")
            # Create RDMA READ work request
            wr = SendWR(
                opcode=pyverbs.enums.IBV_WR_RDMA_READ,
                num_sge=1,
                sg=[SGE(mr.buf, mr.length, mr.lkey)],            
            )
            wr.set_wr_rdma(rkey, remote_addr)

            # Post to QP
            # qp = qp_pool.get_qp_object(0)
            self.qp.post_send(wr)

            self.logger.info(f"Posted RDMA READ request: remote_addr={hex(remote_addr)}, rkey={rkey}, length={length}")
            
        except Exception as e:
            self.logger.error(f"Error performing RDMA READ: {e}")
            raise

    
    # def poll_completion(self, index : int):
    #     """
    #     Poll for completion of RDMA READ operation
    #     """
    #     res = None
        
    #     try:
    #         # Poll for completion
    #         wc_num, wcs = self.cq.poll()
        
    #         # if there is some content return, else None
    #         if wc_num:
    #             if wcs[0].status != pyverbs.enums.IBV_WC_SUCCESS:
    #                 raise RuntimeError(f"❌ RDMA READ failed with status: {wcs[0].status}")
                
    #             mr = self.mrm.get_memory_region(f"local_mr_{index}")
    #             res = mr.read(mr.length, 0)
    #             logger.debug(f"✅ RDMA READ completed successfully: {res}")
    #     except Exception as e:
    #         logger.error(f"❌ Error polling CQ: {e}")
    #         raise
            
    #     return res

    def poll_completion(self, index: int, timeout_seconds: float = 5.0):
        """
        Poll for completion of RDMA READ operation with timeout
        
        Args:
            index: Index of the memory region
            timeout_seconds: Maximum time to wait for completion in seconds
            
        Returns:
            The data read from the memory region, or None if timeout
        """
        res = None
        start_time = time.time()
        
        try:
            while time.time() - start_time < timeout_seconds:
                # Poll for completion
                wc_num, wcs = self.cq.poll()
                
                if wc_num:
                    if wcs[0].status != pyverbs.enums.IBV_WC_SUCCESS:
                        raise RuntimeError(f"❌ RDMA READ failed with status: {wcs[0].status}")
                    
                    mr = self.mrm.get_memory_region(f"local_mr_{index}")
                    res = mr.read(mr.length, 0)
                    self.logger.debug(f"✅ RDMA READ completed successfully for MR {index}")
                    return res
                
                # No completion yet, wait a bit before polling again
                # time.sleep(0.01)  # 10ms sleep to avoid busy-waiting
            
            # If we reached here, we timed out
            self.logger.warning(f"⚠️ Timeout waiting for RDMA READ completion for MR {index}")
            return None
            
        except Exception as e:
            self.logger.error(f"❌ Error polling CQ for MR {index}: {e}")
            raise

