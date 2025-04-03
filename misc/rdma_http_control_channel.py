#!/usr/bin/env python3
import os
import sys
import time
import json
import logging
import argparse
import requests
import numpy as np

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
from pyverbs.cq import CQ, CqInitAttrEx, CompChannel, WC
from pyverbs.addr import GID
from pyverbs.addr import GlobalRoute
from pyverbs.addr import AHAttr
            

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('RDMAQPTest')




def run_rdma_qp_test(host, port=5000):
    """
    Test connecting to a remote QP and performing an RDMA READ
    
    Args:
        host: Host running the MicroviewHostAgent with RDMA server
        port: HTTP API port of the MicroviewHostAgent
    """
    api_base_url = f"http://{host}:{port}"
    
    try:
        # 1. Create RDMA client
        client = RDMAClientQP(buffer_size=4096)
        logger.info("Created RDMA client")
        
        # 2. Get available QPs from server
        response = requests.get(f"{api_base_url}/rdma/qps")
        if not response.ok:
            raise RuntimeError(f"Failed to get QPs: {response.text}")
        
        qps = response.json().get("queue_pairs", [])
        if not qps:
            raise RuntimeError("No QPs available")
        
        logger.info(f"Available QPs: {json.dumps(qps, indent=2)}")
        
        # 3. Get connection info for the first available QP
        qp_index = qps[0]["index"]
        response = requests.get(f"{api_base_url}/rdma/qp/{qp_index}")
        if not response.ok:
            raise RuntimeError(f"Failed to get QP info: {response.text}")
        
        remote_qp_info = response.json().get("queue_pair", {})
        logger.info(f"Remote QP info: {json.dumps(remote_qp_info, indent=2)}")
        
        # 4. Connect to the remote QP
        success = client.connect_to_remote_qp(remote_qp_info)
        if not success:
            raise RuntimeError("Failed to connect to remote QP")
        
        # 5. Get local QP info for connecting from remote side
        local_qp_info = client.post_send_info()
        
        # 6. Tell the server to connect to our QP
        response = requests.post(
            f"{api_base_url}/rdma/qp/{qp_index}/connect",
            json={"remote_info": local_qp_info}
        )
        if not response.ok:
            raise RuntimeError(f"Failed to establish bidirectional connection: {response.text}")
        
        # 7. Get available memory regions from server
        response = requests.get(f"{api_base_url}/rdma/mrs")
        if not response.ok:
            raise RuntimeError(f"Failed to get memory regions: {response.text}")
        
        mrs = response.json().get("memory_regions", [])
        if not mrs:
            raise RuntimeError("No memory regions available")
        
        logger.info(f"Available memory regions: {json.dumps(mrs, indent=2)}")
        
        # 8. Choose the first memory region for RDMA READ
        mr_info = mrs[0]
        remote_addr = mr_info["addr"]
        rkey = mr_info["rkey"]
        size = min(mr_info["size"], client.buffer_size)
        
        # 9. Perform RDMA READ
        logger.info(f"Performing RDMA READ from {hex(remote_addr)} with rkey {rkey}, size {size}")
        data = client.perform_rdma_read(remote_addr, rkey, length=size)
        
        # 10. Display the data read
        if data:
            # Try to decode as string (first 64 bytes)
            try:
                data_str = data[:64].decode('utf-8', errors='replace').strip('\x00')
                logger.info(f"Read data (as string): '{data_str}'")
            except:
                pass
            
            # Show as hex
            data_hex = ' '.join([f'{b:02x}' for b in data[:32]])
            logger.info(f"Read data (hex): {data_hex}...")
            
            # If it's the default memory region, it should contain our test string
            if "RDMA-MR-" in data_str:
                logger.info("✅ RDMA READ test SUCCESSFUL - test string found in data")
            else:
                logger.info("⚠️ Test string not found, but RDMA READ completed")
        
    except Exception as e:
        logger.error(f"RDMA QP test failed: {e}")
    finally:
        if 'client' in locals():
            client.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test RDMA QP connection and READ")
    parser.add_argument("--host", default="localhost", help="Host running MicroviewHostAgent")
    parser.add_argument("--port", type=int, default=5000, help="HTTP API port")
    
    args = parser.parse_args()

    # for test purposes only, can add to the parser if desired (TODO)
    global DEFAULT_REMOTE_QP_INFO_FILE
    global DEFAULT_RDMA_DEVICE
    DEFAULT_REMOTE_QP_INFO_FILE = "rdma_passive_info.json"
    DEFAULT_RDMA_DEVICE = "mlx5_1"  # Default RDMA device name
    
    run_rdma_qp_connection_test()
    #run_rdma_qp_test(args.host, args.port)