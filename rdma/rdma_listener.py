import signal
import threading
from typing import Dict
import struct

from pyverbs.cmid import CMID, AddrInfo, CMEvent
from pyverbs.qp import QPInitAttr, QPCap
import pyverbs.cm_enums as ce
import pyverbs.enums as e

from rdma_cm_collector import RDMACollectorCm

# Global settings
MAX_CONNECTIONS = 16

# Global state
active_collectors: Dict[int, RDMACollectorCm] = {}
lock = threading.Lock()
terminate = False


class RDMAListener:
    """
    RDMA listener that accepts incoming connection requests and creates 
    collector objects for each connection.
    """
    
    def __init__(self, port: str, block_size: int = 4096):
        """
        Initialize the RDMA listener.
        
        Args:
            port: Port to listen on
            block_size: Size of memory blocks to read
        """
        self.port = port
        self.block_size = block_size
        self.listener_id = None
        self.ec = None
        self.collectors = {}
        self.running = False
    
    def start(self):
        """Start listening for incoming RDMA connections"""
        try:
            print(f"Starting RDMA listener on port {self.port}")
            
            # Create event channel
            self.ec = CMID.create_event_channel()
            
            # Create listener ID
            self.listener_id = CMID(creator=None, event_channel=self.ec)
            
            # Create address info for binding
            addr_info = AddrInfo(
                src=None,
                src_service=self.port,
                port_space=ce.RDMA_PS_TCP,
                flags=0
            )
            
            # Bind to address
            self.listener_id.bind_addr(addr_info)
            
            # Start listening with backlog of 10
            self.listener_id.listen(10)
            
            print(f"Listening on port {self.port}")
            self.running = True
            
            # Process events in the main loop
            self._process_events()
            
        except Exception as e:
            print(f"Error initializing RDMA listener: {e}")
            self._cleanup()
    
    def _process_events(self):
        """Process incoming CM events"""
        while not terminate:
            try:
                event = self.ec.get_cm_event()
                
                if event.event_type == ce.RDMA_CM_EVENT_CONNECT_REQUEST:
                    self._handle_connect_request(event)
                elif event.event_type == ce.RDMA_CM_EVENT_ESTABLISHED:
                    self._handle_established(event)
                elif event.event_type == ce.RDMA_CM_EVENT_DISCONNECTED:
                    self._handle_disconnect(event)
                else:
                    print(f"Unhandled event: {event.event_type}")
                
                event.ack_cm_event()
                
            except Exception as e:
                if not terminate:
                    print(f"Error processing event: {e}")
    
    def _handle_connect_request(self, event):
        """Handle a connection request from a client"""
        try:
            print("Received connection request")
            
            # Create queue pair capabilities
            cap = QPCap(max_send_wr=10, max_recv_wr=10, max_send_sge=1, max_recv_sge=1)
            qp_init_attr = QPInitAttr(cap=cap, qp_type=e.IBV_QPT_RC)
            
            # Create QP for this connection
            cmid = event.id
            cmid.create_qp(qp_init_attr)
            
            # Create collector for this connection
            conn_id = len(self.collectors)
            collector = RDMACollectorCm.from_cmid(cmid, self.block_size, conn_id)
            
            # Store the collector
            with lock:
                self.collectors[id(cmid)] = collector
                active_collectors[conn_id] = collector
            
            # Accept the connection
            cmid.accept(None)  # No private data
            
            print(f"Accepted connection request (ID: {conn_id})")
            
        except Exception as e:
            print(f"Error handling connect request: {e}")
            raise
    
    def _handle_established(self, event):
        """Handle a connection established event"""
        cmid = event.id
        collector_id = id(cmid)
        
        if collector_id in self.collectors:
            print(f"Connection established")
            # Now the collector needs to prepare to receive MR info
            self.collectors[collector_id].setup_for_mr_exchange()
        else:
            print(f"Connection established for unknown collector ID: {collector_id}")
    
    def _handle_disconnect(self, event):
        """Handle a disconnect event"""
        cmid = event.id
        collector_id = id(cmid)
        
        if collector_id in self.collectors:
            print(f"Client disconnected")
            conn_id = self.collectors[collector_id].conn_id
            
            # Clean up
            with lock:
                if conn_id in active_collectors:
                    del active_collectors[conn_id]
                del self.collectors[collector_id]
        else:
            print(f"Disconnect for unknown collector ID: {collector_id}")
    
    def _cleanup(self):
        """Clean up RDMA resources"""
        self.running = False
        
        # Clean up collectors
        with lock:
            for collector in self.collectors.values():
                collector._cleanup()
            self.collectors.clear()
            active_collectors.clear()
        
        # Clean up listener
        if self.listener_id:
            self.listener_id.close()
        
        if self.ec:
            self.ec.destroy()


def signal_handler(sig, frame):
    """Handle Ctrl+C"""
    print("Ctrl+C detected, exiting...")
    global terminate
    terminate = True
    exit(0)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="RDMA Listener")
    parser.add_argument("port", help="Port to listen on")
    parser.add_argument("block_size", type=int, help="Size of memory blocks to read")
    
    args = parser.parse_args()
    
    # Register signal handler for Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)
    
    # Create and start listener
    listener = RDMAListener(args.port, args.block_size)
    listener.start()