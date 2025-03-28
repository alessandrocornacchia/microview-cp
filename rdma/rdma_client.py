#!/usr/bin/env python3
import pickle
import time
import argparse
from rdma_cm_collector import RDMACollectorCm
from pyverbs.cmid import CMID, AddrInfo
from pyverbs.qp import QPInitAttr, QPCap
import inspect
import pyverbs.cm_enums as ce
import pyverbs.enums

def main():
    """
    Test the RDMA connection by reading from a passive RDMA server.
    
    This script:
    1. Loads the memory region information from the pickle file
    2. Creates an RDMACollectorCm instance
    3. Registers the remote memory region
    4. Reads data from the memory region
    """
    parser = argparse.ArgumentParser(description="RDMA Connection Test")
    parser.add_argument("--host", type=str, default="localhost", 
                        help="Host address of the RDMA passive server (default: localhost)")
    parser.add_argument("--port", type=str, default="18515", 
                        help="Port of the RDMA passive server (default: 18515)")
    parser.add_argument("--pickle-file", type=str, default="rdma_mr_info.pickle", 
                        help="Path to the pickle file with MR information")
    
    args = parser.parse_args()
    
    # Create RDMACollectorCm instance
    try:
        print(f"Connecting to RDMA server at {args.host}:{args.port}")
        try:
            # Create QP capabilities and init attributes
            cap = QPCap(max_send_wr=10, max_recv_wr=10, max_send_sge=1, max_recv_sge=1)
            qp_init_attr = QPInitAttr(cap=cap, qp_type=pyverbs.enums.IBV_QPT_RC)
            
            print(pyverbs.enums.IBV_QPT_RC)
            # Create address info for the connection
            addr_info = AddrInfo(
                src=None,  # Let CM choose source address
                dst=args.host,
                dst_service=args.port,
                port_space=ce.RDMA_PS_TCP
            )
            
            # Create CM ID and establish connection
            cmid = CMID(creator=addr_info, qp_init_attr=qp_init_attr)
            
            # Connect to the remote host
            cmid.connect()
            
            print(f"RDMA CM connection established")
        
        except Exception as e:
            # print(f"Error initializing RDMA connection: {e}")
            raise e
            
        # ask input from user for addres and rkey
        remote_addr = input("Enter remote address: ")
        rkey = input("Enter rkey: ")

        # perform RDMA read operation
        mr = cmid.reg_msgs(1000)
        print(f"Registered remote memory region with index")

        print(f"Performing RDMA read operation")
        cmid.post_read(mr, 1000, int(remote_addr), int(rkey))
        
        
        # Wait for completion
        wc = cmid.get_send_comp()
        if wc.status != pyverbs.enums.IBV_WC_SUCCESS:
            raise RuntimeError(f"RDMA READ failed with status: {wc.status}")
        else:
            print("RDMA read test completed successfully")
        
    except Exception as e:
        print(f"Error during RDMA operations: {e}")
    
if __name__ == "__main__":
    main()