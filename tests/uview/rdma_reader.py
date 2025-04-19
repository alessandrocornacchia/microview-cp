
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


# Add path to project root folder
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from defaults import *
from rdma.helpers import QueuePairPool, MemoryRegionPool


# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RDMAReader")


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



# --------- Main entry point for  testing ------------------

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
        mr_manager = MemoryRegionPool(qp_pool.pd, default_buffer_size=args.buffer_size)
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


def test_one_sided_reader(args, qp_pool):
    """"
    Test a READ() operation using RDMA, with a memory region created by a passive side.
    MR information is saved to a file for the client to use.
    """
    RDMA_MR_INFO_FILE = "mr_info.json"
    
    # Server creates default memory region
    if not args.client:
        mr_manager = MemoryRegionPool(qp_pool.pd, default_buffer_size=args.buffer_size)
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
        # Perform RDMA READ with one-sided reader
        remote_mr = MRMetadata(addr, rkey, size, None)
        one_sided_reader = OneSidedReader(qp_pool.pd, qp_pool.get_qp_object(0), [remote_mr])
        one_sided_reader.start()
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
    parser.add_argument("--buffer-size", type=int, default=DEFAULT_PAGE_SIZE,
                      help=f"Default size of memory buffers (default: {DEFAULT_PAGE_SIZE})")
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
        
        # test_mr_read(args, qp_pool)
        # test_qp_connect(args, qp_pool) #TODO make this selection possible from commandline
        test_one_sided_reader(args, qp_pool)
                
            
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