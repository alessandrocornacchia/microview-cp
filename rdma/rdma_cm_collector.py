from pyverbs.cmid import CMID, AddrInfo
from pyverbs.qp import QPInitAttr, QPCap
import pyverbs.cm_enums as ce
import pyverbs.enums as e


class RDMACollectorCm:
    """
    Collector that uses RDMA CM to read metrics from host memory.
    Uses a single Queue Pair in Reliable Connection (RC) mode.
    """
    def __init__(self, host_addr: str, rkey: int, remote_addr: int, 
                 length: int, port: str = "18515"):
        """
        Initialize an RDMA collector to read metrics from remote memory.
        
        Args:
            host_addr: IP address of the remote host
            rkey: Remote key for the memory region
            remote_addr: Remote memory region address
            length: Length of the memory region to read
            port: Port number for RDMA connection
        """
        self.host_addr = host_addr
        self.rkey = rkey
        self.remote_addr = remote_addr
        self.length = length
        self.port = port
        
        # RDMA connection components
        self.cmid = None
        self.buffer = bytearray(length)
        self.connected = False
        
        # Initialize RDMA connection
        self._init_rdma_connection()
    
    def _init_rdma_connection(self):
        """Initialize RDMA connection with remote host using CM"""
        try:
            # Create QP capabilities and init attributes
            cap = QPCap(max_send_wr=10, max_recv_wr=10, max_send_sge=1, max_recv_sge=1)
            qp_init_attr = QPInitAttr(cap=cap, qp_type=e.IBV_QPT_RC)
            
            # Create address info for the connection
            addr_info = AddrInfo(
                src=None,  # Let CM choose source address
                dst=self.host_addr,
                dst_service=self.port,
                port_space=ce.RDMA_PS_TCP
            )
            
            # Create CM ID and establish connection
            self.cmid = CMID(creator=addr_info, qp_init_attr=qp_init_attr)
            
            # Connect to the remote host
            self.cmid.connect()
            
            # Register memory for RDMA operations
            self.mr = self.cmid.reg_msgs(self.buffer)
            
            print(f"RDMA CM connection established to {self.host_addr}:{self.port}")
            self.connected = True
            
        except Exception as e:
            print(f"Error initializing RDMA connection: {e}")
            self._cleanup()
            raise
    
    def read_metrics(self) -> bytearray:
        """
        Read metrics from remote memory using RDMA READ.
        
        Returns:
            bytearray: Buffer containing the read data
        """
        if not self.connected:
            raise RuntimeError("RDMA connection not established")
        
        try:
            # Post RDMA READ work request
            self.cmid.post_read(self.buffer, len(self.buffer), self.mr.lkey, 
                               remote_addr=self.remote_addr, rkey=self.rkey)
            
            # Wait for completion
            wc = self.cmid.get_send_comp()
            if wc.status != e.IBV_WC_SUCCESS:
                raise RuntimeError(f"RDMA READ failed with status: {wc.status}")
            
            return self.buffer
            
        except Exception as e:
            print(f"Error reading metrics via RDMA: {e}")
            raise
    
    def _cleanup(self):
        """Clean up RDMA resources"""
        if self.cmid:
            self.cmid.disconnect()
            self.cmid.close()
    
    def __del__(self):
        """Destructor to clean up resources"""
        self._cleanup()




# Example usage
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    # Example parameters - replace with actual values
    host_addr = "192.168.1.100"  # Replace with target host IP
    rkey = 123                   # Replace with actual remote key
    remote_addr = 0x1000         # Replace with actual remote address
    length = 1024                # Replace with actual length to read
    
    collector = RDMACollectorCm(host_addr, rkey, remote_addr, length)
    try:
        data = collector.read_metrics()
        print(f"Read {len(data)} bytes: {data.hex()}")
    except Exception as e:
        print(f"Failed to read metrics: {e}")
