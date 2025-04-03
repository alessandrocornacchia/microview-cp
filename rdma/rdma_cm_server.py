#!/usr/bin/env python3
import argparse
import pickle
import signal
import sys
import os
import time
from typing import Dict, Any, Optional
import numpy as np

# Import the correct PyVerbs modules
from pyverbs.cmid import CMID, AddrInfo
from pyverbs.qp import QPInitAttr, QPCap
from pyverbs.device import Context
from pyverbs.pd import PD
from pyverbs.mr import MR
from pyverbs.cq import CQ
import pyverbs.cm_enums as ce
import pyverbs.enums

import threading

# Default values
DEFAULT_PORT = "18515"
DEFAULT_BUFFER_SIZE = 4096
PICKLE_FILE = "rdma_mr_info.pickle"

class RDMAPassiveServer:
    """
    A passive RDMA server that listens for incoming connections and exposes
    a memory region that can be accessed via RDMA READ operations.
    """
    def __init__(self, port: str = DEFAULT_PORT, buffer_size: int = DEFAULT_BUFFER_SIZE):
        """
        Initialize the RDMA passive server.
        
        Args:
            port: Port to listen on
            buffer_size: Size of the memory buffer to expose via RDMA
        """
        self.port = port
        self.buffer_size = buffer_size
        self.running = False
        
        # RDMA connection components
        self.listener_id = None
        self.cmid = None
        
        # Memory region
        self.buffer = None
        self.mr = None
        self.pd = None
        
        # MR info for pickle file
        self.mr_info = {}
        
    def start(self):
        """
        Start the RDMA passive server and listen for connections.
        """
        try:
            print(f"Starting RDMA passive server on port {self.port}")
            
            # Create address info for binding
            addr_info = AddrInfo(
                src=None,
                src_service=self.port,
                port_space=ce.RDMA_PS_TCP,
                flags=ce.RAI_PASSIVE
            )
            
            # Create QP capabilities
            cap = QPCap(max_send_wr=10, max_recv_wr=10, max_send_sge=1, max_recv_sge=1)
            qp_init_attr = QPInitAttr(cap=cap, qp_type=pyverbs.enums.IBV_QPT_RC)

            # Create passive CMID for listening
            self.listener_id = CMID(creator=addr_info, qp_init_attr=qp_init_attr)  # Create without event channel
            
            # Listen for connections
            self.listener_id.listen()
            
            print(f"RDMA passive server listening on port {self.port}")
            
            # Start event loop
            self.running = True
            self.event_loop()
            
        except Exception as e:
            print(f"Error starting RDMA passive server: {e}")
            self.cleanup()
            raise
            
    def init_memory_region(self, pd=None):
        """
        Initialize and register a memory region for RDMA operations.
        """
       # Create buffer with numpy array
        self.buffer = np.zeros(self.buffer_size, dtype=np.uint8)
        
        # Optionally fill with some initial data
        initial_data = b"RDMA-TEST"
        self.buffer[:len(initial_data)] = np.frombuffer(initial_data, dtype=np.uint8)
        
        # if pd is None:
        #     print('Creating new protection domain for MR')
        #     # Only create context and pd if not provided
        #     self.ctx = Context(name='mlx5_1')
        #     self.pd = PD(self.ctx)
        # else:
        #     # Use the provided pd
        #     self.pd = pd

        # Register the MR with remote read access (if address is not provideed, it will be allocated)
        buffer_addr = self.buffer.ctypes.data  # Get raw address
        buffer_length = self.buffer_size       # Use explicit size
        access_flags = pyverbs.enums.IBV_ACCESS_LOCAL_WRITE | pyverbs.enums.IBV_ACCESS_REMOTE_WRITE | pyverbs.enums.IBV_ACCESS_REMOTE_READ

        self.mr = MR(pd, buffer_length, access_flags, buffer_addr)
        
        print(f"Memory region created: mr_addr={hex(self.mr.buf)}, buffer_addr={hex(self.buffer.ctypes.data)}, rkey={self.mr.rkey}, size={self.buffer_size}")

        # Save MR info
        self.mr_info = {
            "addr": self.buffer.ctypes.data, #self.mr.buf,
            "rkey": self.mr.rkey,
            "size": self.buffer_size
        }
        
    
    def save_mr_info(self):
        """
        Save memory region information to a pickle file.
        """
        with open(PICKLE_FILE, "wb") as f:
            pickle.dump(self.mr_info, f)
            
        print(f"Memory region info saved to {PICKLE_FILE}")


    def event_loop(self):
        """
        Main event loop for processing CM events.
        """
        
        while self.running:
            try:
                # In non-blocking mode, get_cm_event would raise an exception if no event is available
                # Instead, we use a blocking approach to wait for connection
                print("Waiting for connection requests...")
                cmid = self.listener_id.get_request()
                if cmid:

                    # TODO Start new thread for this connection
                    # thread = threading.Thread(target=connection_handler, args=(new_cmid,))
                    # thread.daemon = True
                    # thread.start()

                    self.handle_connect_request(cmid)
                
            except KeyboardInterrupt:
                print("Interrupted by user")
                break
            except Exception as e:
                print(f"Error in event loop: {e}")
                
    def handle_connect_request(self, cmid):
        """
        Handle a connect request from a client.
        
        Args:
            cmid: The CM ID from the connection request
        """
        try:
            print("Received connection request")
            
            # Create QP for this connection
            # cmid.create_qp(qp_init_attr)
            
            # Accept the connection
            cmid.accept()
            
            # Create buffer and register memory region
            self.init_memory_region(cmid.pd)
            print(f"Memory region: addr={hex(self.mr.buf)}, rkey={self.mr.rkey}, size={self.buffer_size}")
            
            # Save MR info to pickle file
            self.save_mr_info()
            
            
            print("Connection request accepted")
            
            # Update the CMID with the cmid of the connected client (similar to TCP socket programming)
            self.cmid = cmid
            
            while self.running:
                try:
                    # You could poll connection state or try a zero-length receive
                    # This is a simple way to keep the connection alive
                    time.sleep(10)
                    
                    # Optional: Check if connection is still valid
                    # If you want to detect disconnection, you could try:
                    status = cmid.query() 
                    # if status shows disconnected, break
                    if status == pyverbs.enums.IBV_CM_ESTABLISHED:
                        print("Connection still alive")
                    else:
                        break
                except Exception:
                    print("Connection appears to be closed")
                    break
            
            print("Client disconnected")
            
        except Exception as e:
            print(f"Error handling connect request: {e}")

        
    def cleanup(self):
        """
        Clean up resources.
        """
        self.running = False
        
        if self.mr:
            try:
                self.mr.close()
            except:
                pass
        
        if self.cmid:
            try:
                self.cmid.close()
            except:
                pass
            
        if self.listener_id:
            try:
                self.listener_id.close()
            except:
                pass
            
        print("RDMA passive server stopped")
        
    def __del__(self):
        """
        Destructor to clean up resources.
        """
        self.cleanup()
        
        
def signal_handler(sig, frame):
    """
    Handle Ctrl+C to gracefully exit.
    """
    print("Ctrl+C detected, exiting...")
    global server
    if server:
        server.cleanup()
    sys.exit(0)
    
    
# Main entry point
if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="RDMA Passive Server")
    parser.add_argument("--port", type=str, default=DEFAULT_PORT, 
                        help=f"Port to listen on (default: {DEFAULT_PORT})")
    parser.add_argument("--size", type=int, default=DEFAULT_BUFFER_SIZE, 
                        help=f"Size of the memory buffer (default: {DEFAULT_BUFFER_SIZE})")
    
    args = parser.parse_args()
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Create and start server
    server = RDMAPassiveServer(port=args.port, buffer_size=args.size)
    
    try:
        server.start()
    except KeyboardInterrupt:
        print("Interrupted by user")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        server.cleanup()