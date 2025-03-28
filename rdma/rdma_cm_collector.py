from pyverbs.cmid import CMID, AddrInfo
from pyverbs.qp import QPInitAttr, QPCap
import inspect
import pyverbs.cm_enums as ce
import pyverbs.enums

PAGE_SIZE = 4096


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




class RDMACollectorCm:
    """
    Collector that uses RDMA CM to read metrics from remote host memory.
    Uses a single Queue Pair in Reliable Connection (RC) mode.
    Memory regions (MR) to read from, and corresponding Rkeys, are provided by the external control plane.
    For performacnce optimizations, the MR size by default is set to page size (4096 bytes).
    TODO: Control plane here should be responsible of providing the remote memory regions to read from, possibly being continguous in memory in the remote host.
    """
    def __init__(self, host_addr: str, port: str = "18515"):
        """
        Initialize an RDMA collector to read metrics from remote memory.
        
        Args:
            host_addr: IP address of the remote host
            port: Port number for RDMA connection
        """
        self.host_addr = host_addr
        self.port = port
        
        # RDMA connection components
        self.cmid = None
        self.connected = False
        
        # Storage for remote memory regions
        self.remote_regions: list[MRMetadata] = []
        
        # Initialize RDMA connection
        self._init_rdma_connection()
    
    

    def register_remote_read_region(self, remote_addr: int, rkey: int, length: int = PAGE_SIZE, name: str = None):
        """
        Register a remote memory region for RDMA reads.
        This method allows an external control plane to configure remote memory targets.
        
        Args:
            remote_addr: Remote memory address to read from
            rkey: Remote key for the memory region
            length: Length of the memory region to read (default: PAGE_SIZE)
            name: Optional name identifier for this memory region
            
        Returns:
            int: Index of the registered memory region
        """
        if not self.connected:
            raise RuntimeError("Cannot register memory region: RDMA connection not established")
        
        # here we register a local MR to store the data read from the remote region
        mr = self.cmid.reg_msgs(length)
        # mr.write('a' * length)

        region = MRMetadata(
            remote_addr=remote_addr,
            rkey=rkey,
            length=length,
            mr=mr,
            name=name
        )
        
        self.remote_regions.append(region)
        return len(self.remote_regions) - 1  # Return index of the registered region


    def _init_rdma_connection(self):
        """Initialize RDMA connection with remote host using CM"""

        print(f"RDMA connection initializing with queue pair type {pyverbs.enums.IBV_QPT_RC}")

        try:
            # Create QP capabilities and init attributes
            cap = QPCap(max_send_wr=10, max_recv_wr=10, max_send_sge=1, max_recv_sge=1)
            qp_init_attr = QPInitAttr(cap=cap, qp_type=pyverbs.enums.IBV_QPT_RC)
            
            print(pyverbs.enums.IBV_QPT_RC)
            # Create address info for the connection
            addr_info = AddrInfo(
                src=None,  # Let CM choose source address
                dst=self.host_addr,
                dst_service=self.port,
                port_space=ce.RDMA_PS_TCP
            )
            
            # Create CM ID and establish connection
            self.cmid = CMID(creator=addr_info, qp_init_attr=qp_init_attr)
            
            print(f"RDMA CM connection created to {self.host_addr}:{self.port} ")
            # Connect to the remote host
            self.cmid.connect()
            
            print(f"RDMA CM connection established to {self.host_addr}:{self.port}")
            self.connected = True
            
        except Exception as e:
            # print(f"Error initializing RDMA connection: {e}")
            self._cleanup()
            raise e
        
    
    def read_metrics(self):
        """
        Read metrics from all registered remote memory regions using RDMA READ.
        
        Returns:
            dict: Dictionary mapping region indices to data buffers
        """
        if not self.connected:
            raise RuntimeError("RDMA connection not established")
        
        if not self.remote_regions:
            raise RuntimeError("No remote memory regions registered")
        
        results = {}
        
        try:
            # Post RDMA READ work requests for all registered regions
            for idx, region in enumerate(self.remote_regions):
                print(f"Reading region {idx} of size {region.mr.length} from remote address {hex(region.remote_addr)} and rkey {region.rkey}")
                self.cmid.post_read(
                    region.mr, 
                    region.length,
                    int(region.remote_addr), 
                    int(region.rkey)
                )
                
                # Wait for completion
                wc = self.cmid.get_send_comp()
                if wc.status != pyverbs.enums.IBV_WC_SUCCESS:
                    raise RuntimeError(f"RDMA READ failed for region {idx} with status: {wc.status}")
                
                # Store the result
                key = region.name if region.name is not None else idx
                results[key] = region.mr.read(region.length, 0) # read the buffer, length and offset
            
            return results
            
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
    
    try:
        # Create collector and establish connection
        collector = RDMACollectorCm(host_addr)
        
        # Register remote memory regions
        collector.register_remote_read_region(
            remote_addr=0x1000,  # Replace with actual remote address
            rkey=123,            # Replace with actual remote key
            length=1024,         # Replace with actual length to read
            name="metrics_region_1"
        )
        
        collector.register_remote_read_region(
            remote_addr=0x2000,  # Different memory region
            rkey=456,
            length=512,
            name="metrics_region_2"
        )
        
        # Read all registered regions
        data_dict = collector.read_metrics()
        
        # Process the results
        for region_name, data in data_dict.items():
            print(f"Region '{region_name}': Read {len(data)} bytes: {data[:20].hex()}...")
            
    except Exception as e:
        print(f"Failed to read metrics: {e}")
