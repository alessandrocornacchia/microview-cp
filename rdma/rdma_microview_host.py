#!/usr/bin/env python3
import json
import pickle
import signal
import sys
import os
import time
import threading
from typing import Dict, List, Any, Optional
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


# Default values TODO move in config file .env
DEFAULT_BUFFER_SIZE = 4096
DEFAULT_QP_POOL_SIZE = 1
DEFAULT_RDMA_DEVICE = "mlx5_1"
DEFAULT_GID = 3
DEFAULT_IB_PORT = 1

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('RDMAPassiveServer')


class MemoryRegionManager:
    """
    Manages memory regions that can be accessed via RDMA
    """
    def __init__(self, pd, default_buffer_size=DEFAULT_BUFFER_SIZE):
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
        logger.info(f"Registered external memory region '{name}': addr={hex(addr)}, rkey={memory_region.rkey}, size={size}")
        
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
        mr_info = self.register_memory_region(name, buffer_addr, size)

        # Store the buffer reference in our memory_regions dict to keep it alive
        self.memory_regions[name]["buffer"] = buffer

        return mr_info
    
    def save_memory_region_info(self, filename: str):
        """ Save memory region information to a file for debugging and sharing with clients."""
        serializable_regions = {
            name: self.get_memory_region_info(name) for name, _ in self.memory_regions.items()
        }
        # Save to json file in json format
        with open(filename + '.json' if not filename.endswith('.json') else filename, 'w') as f:
            json.dump(serializable_regions, f, indent=2)
            logger.info(f"Saved RDMA info to JSON file: {filename}")
        # Save to pickle file
        with open(filename + '.pickle' if not filename.endswith('.pickle') else filename, 'wb') as f:
            pickle.dump(serializable_regions, f)
            logger.info(f"Saved RDMA info to pickle file: {filename}")
        
    
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
                mr_info["mr"].close()
                del self.memory_regions[name]
            except Exception as e:
                logger.error(f"Error cleaning up memory region '{name}': {e}")


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
            logger.info(f"Opened RDMA device: {self.rdma_device}")
            
            # Create Protection Domain
            self.pd = PD(self.ctx)
            
            # Create Completion Queue (shared by all QPs)
            self.cq = CQ(self.ctx, 100)  # 100 is the CQ size
            
            # Find a valid GID index for RoCEv2
            # Default port number can check with command ibv_devinfo
            self.gid = self.ctx.query_gid(self.ib_port, self.gid_index)
            
        except Exception as e:
            logger.error(f"Error initializing RDMA context: {e}")
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
                
                logger.debug(f"Created Queue Pair #{i} attributes")

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
                
                logger.debug(f"Queue Pair #{i} transitioned to INIT state")

                # Store QP information
                qp_info = {
                    "qp": queue_pair,
                    "qp_num": queue_pair.qp_num,
                    "in_use": False,
                    "remote_info": None
                }
                
                self.qps.append(qp_info)
                logger.info(f"Created Queue Pair #{i}: GID={self.gid}, qp_num={qp_info['qp_num']}")
                
            except Exception as e:
                logger.error(f"Error creating Queue Pair #{i}: {e}")
                raise
        
        logger.info(f"Created Queue Pair pool with {self.pool_size} QPs")
    
    
    def get_qp_object(self, index: int) -> Optional[QP]:
        """
        Get the QP object at the specified index
        
        Args:
            index: Index of the QP in the pool
            
        Returns:
            QP object if found, None otherwise
        """
        if index < 0 or index >= len(self.qps):
            raise ValueError(f"Queue Pair index {index} out of range (0-{len(self.qps)-1})")
        
        return self.qps[index]["qp"]
    

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
        # TODO common to rdma client
        return {
                "qp_num": self.qps[index]["qp_num"],
                "gid": str(self.gid),
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
            logger.info(f"Saved RDMA info to JSON file: {filename}")
        # Save to pickle file
        with open(filename + '.pickle', 'wb') as f:
            pickle.dump(qp_info, f)
            logger.info(f"Saved RDMA info to pickle file: {filename}")
            

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
            logger.info(f"Connecting to remote QP: {remote_qp_num}, GID: {remote_gid}")
            
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
            local_qp = self.qps[index]
            qp = local_qp["qp"] #   Get the QP object
            qp.to_rts(qa)
            
            # Cache the QP info of the remote QP is trying to connect to here
            local_qp["remote_info"] = remote_info
        
            logger.info(f"✅ Queue Pair #{index} (qp_num={local_qp['qp_num']}) connected to remote QP {remote_info['qp_num']}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error connecting Queue Pair #{index}: {e}")
            return False
    
    def list_queue_pairs(self) -> List[Dict[str, Any]]:
        """List all queue pairs in the pool with their status"""
        return [{
            "index": i,
            "qp_num": qp_info["qp_num"],
            "in_use": qp_info["in_use"]
        } for i, qp_info in enumerate(self.qps)]
    
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
                logger.error(f"Error closing QP {qp_info['qp_num']}: {e}")
        
        # Clean up other resources
        if hasattr(self, 'cq') and self.cq:
            self.cq.close()
        
        if hasattr(self, 'pd') and self.pd:
            self.pd.close()
        
        if hasattr(self, 'ctx') and self.ctx:
            self.ctx.close()
            
        logger.info("RDMA resources cleaned up")


# Handler for graceful termination
def signal_handler(sig, frame):
    logger.info("Received signal to terminate")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)




def perform_rdma_read(qp_pool, remote_addr, rkey, length):
    """
    Perform an RDMA READ operation
    
    Args:
        remote_addr: Remote memory address to read from
        rkey: Remote key for the memory region
        length: Length of data to read (defaults to buffer_size)
        
    Returns:
        The data read from remote memory
    """
    from pyverbs.wr import SGE, RecvWR, SendWR

    # Create local buffer for RDMA operations
    buffer = np.zeros(length, dtype=np.uint8)

    # Register the memory region
    buffer_addr = buffer.ctypes.data
    access_flags = pyverbs.enums.IBV_ACCESS_LOCAL_WRITE | pyverbs.enums.IBV_ACCESS_REMOTE_WRITE | pyverbs.enums.IBV_ACCESS_REMOTE_READ

    mr = MR(qp_pool.pd, length, access_flags, buffer_addr)
    logger.info(f"Registered local MR: addr={hex(buffer_addr)}, lkey={mr.lkey}")

    
    # Clear local buffer before read
    buffer.fill(0)
    
    try:
        
        logger.info(f"Creating RDMA READ work request, remote_addr={hex(remote_addr)}, rkey={rkey}, length={length}")
        # Create RDMA READ work request
        wr = SendWR(
            opcode=pyverbs.enums.IBV_WR_RDMA_READ,
            num_sge=1,
            sg=[SGE(mr.buf, mr.length, mr.lkey)],            
        )
        wr.set_wr_rdma(rkey, remote_addr)

        # Post to QP
        qp = qp_pool.get_qp_object(0)
        qp.post_send(wr)

        logger.info(f"Posted RDMA READ request: remote_addr={hex(remote_addr)}, rkey={rkey}, length={length}")
        
        # Poll for completion
        while True:
            wc_num, wcs = qp_pool.cq.poll()
            
            if wc_num:
                if wcs[0].status != pyverbs.enums.IBV_WC_SUCCESS:
                    raise RuntimeError(f"❌ RDMA READ failed with status: {wcs[0].status}")
                
                content = mr.read(mr.length, 0).decode()
                logger.info(f"✅ RDMA READ completed successfully: {content}")
                
                # cleanup memory region
                mr.close()

                break
            else:
                logger.debug("Waiting for RDMA READ completion...")
                time.sleep(0.1)

        
        return content
        
    except Exception as e:
        logger.error(f"Error performing RDMA READ: {e}")
        raise



# --------- Main entry point for standalone testing

def test_qp_connect(args, qp_pool):
    """"
    Test the connection of queue pairs
    """
    # Save memory region info to pickle file
    qp_pool.save_queue_pair_info(args.local_to)
    logger.info(f"RDMA started, check {args.local_to}.json and {args.local_to}.pickle for details")
    
    
    fn = input("Enter filename with remote RDMA information, or Press Enter for default: ")
    if not fn and args.client:
        fn = "rdma_passive_info.json"
    if not fn and not args.client:
        fn = "rdma_active_info.json"

    # Load remote QP information from file
    remote_info = qp_info_from_file(fn)
    
    # Connect to remote QP
    for i in range(len(qp_pool.qps)):
        try:
            qp_pool.connect_queue_pair(i, remote_info[f"qp_{i}"])
        except Exception as e:
            logger.error(f"Error connecting to remote QP: {e}")


def test_mr_read(args, qp_pool):
    """"
    Test a READ() operation using RDMA, with a memory region created by a passive side.
    MR information is saved to a file for the client to use.
    """
    RDMA_MR_INFO_FILE = "mr_info.json"
    
    # Server creates default memory region
    if not args.client:
        mr_manager = MemoryRegionManager(qp_pool.pd, default_buffer_size=args.buffer_size)
        mr_manager.create_memory_region("default", args.buffer_size)
        mr_manager.save_memory_region_info(RDMA_MR_INFO_FILE)
    
    # both connect queue pairs
    test_qp_connect(args, qp_pool)

    # client will retrieve memory region information and issue read requests
    if args.client:
        # running as client for RDMA READS
        fn = input("Enter MR filename or Press Enter to start RDMA reads...")
        if not fn:
            fn = RDMA_MR_INFO_FILE
        addr, rkey, size = mr_info_from_file(fn)
        logger.info(f"Loaded memory region info from {fn}, addr={hex(addr)}, rkey={rkey}, size={size}")
        # Perform RDMA READ
        data = perform_rdma_read(qp_pool, addr, rkey, size)
        logger.info(f"Data read from remote memory: {data}")
    else:
        # Keep main thread alive for testing
        try:
            print("Waiting for RDMA reads(). Press Ctrl+C to exit.")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Exiting RDMA server")
            pass
        finally:
            mr_manager.cleanup()


if __name__ == "__main__":
    import argparse
    # Add parent directory to path for imports
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from utils import qp_info_from_file, mr_info_from_file

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="RDMA Passive Server with QP Pool")
    parser.add_argument("-c", "--client", action="store_true", help="Run as RDMA client")
    parser.add_argument("--qp", type=int, default=DEFAULT_QP_POOL_SIZE,
                      help=f"Size of Queue Pair pool (default: {DEFAULT_QP_POOL_SIZE})")
    parser.add_argument("--buffer-size", type=int, default=DEFAULT_BUFFER_SIZE,
                      help=f"Default size of memory buffers (default: {DEFAULT_BUFFER_SIZE})")
    parser.add_argument("--rdma-device", type=str, default=DEFAULT_RDMA_DEVICE,
                        help=f"RDMA device to use (default: {DEFAULT_RDMA_DEVICE})")
    parser.add_argument("--local-to", type=str, default="rdma_passive_info",
                        help="File to save local RDMA information for other connections")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    
    
    args = parser.parse_args()
    
    # Set logging level
    if args.debug:
        logger.setLevel(logging.DEBUG)
    
    logger.info(f"Starting RDMA with QP pool size {args.qp}")
    qp_pool = None
    
    try:
        # Initialize QP pool
        qp_pool = QueuePairPool(args.rdma_device, pool_size=args.qp)
        
        test_mr_read(args, qp_pool)
        # test_qp_connect(args, qp_pool)
                
            
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Error: {e}")
         # Clean up resources
        if qp_pool:
            qp_pool.cleanup()
        raise
    finally:
        # Clean up resources
        if qp_pool:
            qp_pool.cleanup()