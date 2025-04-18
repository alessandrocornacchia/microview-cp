import abc
from typing import Dict, List, Tuple, Any, Optional, Callable
import time
import logging
import requests
from LMAP import LMAP
from prometheus_client import start_http_server
from prometheus_client.core import REGISTRY
from defaults import *
from rdma.helpers import MRMetadata, OneSidedReader, QueuePairPool
from classifiers.enums import Models

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('microview_nic.log')
    ]
)
logger = logging.getLogger('MicroviewNIC')


class MicroViewBase(abc.ABC):
    """Abstract base class for MicroView collectors that read metrics using RDMA"""
    
    def __init__(self, control_plane_url: str, scrape_interval: int = 1):
        """
        Initialize the MicroView collector base
        
        Args:
            control_plane_url: URL of the MicroView control plane
        """
        self.control_plane_url = control_plane_url
        self.metrics_layout = {}  # Memory layout of metrics from control plane
        self.metrics_config = {}  # Configuration of metrics (types, etc.)
        
        self.scrape_interval = scrape_interval

        # Parse host and port from control plane URL
        host_parts = control_plane_url.split(':')
        self.host = host_parts[0]
        self.port = host_parts[1] if len(host_parts) > 1 else "5000"
        
        logger.info(f"Initializing MicroView collector with control plane at {control_plane_url}")
    
    @abc.abstractmethod
    def setup(self):
        """
        Initialize the collector by fetching metrics layout and setting up RDMA
        
        """
        pass
    
    def configure_collector(self, service_id: str, *args, **kwargs):
        """Configure collector for a specific service (placeholder for future functionality)"""
        logger.info(f"Configuring collector for service {service_id}")
        # Implementation will depend on specific requirements
    
    def configure_lmaps(self):
        """Configure local mapping for metrics (placeholder for future functionality)"""
        pass
    
    def cleanup(self):
        """Clean up resources"""
        pass    
    
    def __del__(self):
        """Clean up resources"""
        self.cleanup()


class MicroView(MicroViewBase):
    """Standard implementation of MicroView collector"""
    
    def __init__(self, 
                 control_plane_url: str, 
                 rdma_device: str,
                 ib_port: int,
                 gid: int,
                 scrape_interval: int = DEFAULT_POLL_INTERVAL, 
                 num_collectors: int = DEFAULT_LMAP):
        
        super().__init__(control_plane_url, scrape_interval)
        self.qp_pool = None     # Queue Pair pool, each RDMA reader will have its own QP
        self.remote_memory_regions = None   # List of remote memory regions
        self.control_info : List[List[Dict]] = None  # Control info from the control plane
        self.num_collectors = num_collectors
        self.lmaps = []
        self.rdma_device = rdma_device
        self.ib_port = ib_port
        self.gid = gid

    def connect_with_microview_host(self):
        """
        Creates Queue Pair pool, exchange queue pair information with the host and connects the queue pairs
        """
        logger.info(f"⏳ Connecting with MicroView host {self.control_plane_url}, RDMA={self.rdma_device}, port={self.ib_port}, gid={self.gid}")
        try:
            # 1. Initialize QP pool
            self.qp_pool = QueuePairPool(
                self.rdma_device, 
                pool_size=self.num_collectors,
                ib_port=self.ib_port,
                gid_index=self.gid)

            # 2. Obtain local QP info
            local_qp_info = self.qp_pool.list_queue_pairs()

            # 3. Send local QP info to control plane
            response = requests.get(
                f"http://{self.control_plane_url}/rdma/qps",
            )
            response.raise_for_status()

            # 4. Read remote host QP info in response
            remote_qp_info = response.json().get("queue_pairs", [])
            if not remote_qp_info:
                raise RuntimeError("❌ No remote QP info received from control plane")
            logger.debug(f"Received remote QP info: {remote_qp_info}")

            # 5. Connect local QP to remote QP in pairs
            for i,qp_info in enumerate(remote_qp_info):
                if qp_info['in_use']:
                    logger.warning(f"⚠️ Trying to connect to QP {i} already in use")
                self.qp_pool.connect_queue_pair(i, qp_info)

            # 6. Ask remote control plane to connect all
            response = requests.post(
                f"http://{self.control_plane_url}/rdma/qps/connect", 
                json={"queue_pairs": local_qp_info},
                timeout=10,
            )
            response.raise_for_status()
            
            # 7. Now ask for remote memory regions
            response = requests.get(
                f"http://{self.control_plane_url}/rdma/mrs",
            )
            response.raise_for_status()
            logger.info("🔗 Connected")

            # 8. Create local buffers, one for each remote MR
            self.remote_memory_regions = [MRMetadata(
                mr["addr"], 
                mr["rkey"], 
                mr["size"], 
                None) for mr in response.json().get("memory_regions", [])]
            
        except Exception as e:
            logger.info(f"❌ Failed to connect with host: {e}")
            self.cleanup()
            raise e
            
    def configure_lmaps(self, start_local_scrape: bool = False):
        """
        Fetch configuration from remote side for existing metrics, then assigns to 
        RDMA one-sided readers. 
        """
        
        try:
            response = requests.get(f"http://{self.control_plane_url}/metrics")
            response.raise_for_status()
            self.control_info = response.json()
            logger.debug(f"Control info: {self.control_info}")
        except Exception as e:
            logger.error(f"Failed to fetch memory layout: {e}")
        
        if len(self.control_info) != len(self.remote_memory_regions):
            raise RuntimeError("Mismatch between control info and remote memory regions")
        
        # filter out empty memory regions
        active_mrs_idx = []
        for i,mr in enumerate(self.control_info):
            if len(mr) != 0:
                active_mrs_idx.append(i)

        # TODO here is where we would need to decide how many readers to create
        # and how to group memory pages. Now we just evenly distribute Memory Regions
        # among the LMAPs. If the MR contains few memory pages, it will be assigned to
        # one LMAP with a dedicated RDMA reader, which might be inefficient in terms of
        # RDMA goodput
        
        if not active_mrs_idx:
            logger.warning("⚠️ No active memory regions found")
            return
        
        # Distribute memory regions among collectors
        nlmap = min(self.num_collectors, len(active_mrs_idx))

        for i in range(nlmap):
            # Calculate which memory regions this LMAP should handle
            start_idx = i * len(active_mrs_idx) // nlmap
            if i == nlmap - 1:
                end_idx = len(active_mrs_idx)
            else:
                end_idx = (i + 1) * len(active_mrs_idx) // nlmap
            mr_indices = active_mrs_idx[start_idx:end_idx]
            
            # Get the specific memory regions and control info for this collector
            MRs = [self.remote_memory_regions[j] for j in mr_indices]
            CRs = [self.control_info[j] for j in mr_indices]

            # Create a dedicated RDMA reader for this collector targeting selected MRs
            rdma = OneSidedReader(
                self.qp_pool.pd,
                self.qp_pool.get_qp_object(i % DEFAULT_QP_POOL_SIZE),
                MRs
            )
            
            # Create LMAP collector with this RDMA reader, and set the classifier
            lmap = LMAP(
                collector_id=f"LMAP_{i}",
                control_info=CRs,
                rdma=rdma,
            )
            
            lmap.set_classifier(model=Models.FREQUENT_DIRECTION, bootstrap=True)
            
            # Add to list of collectors
            self.lmaps.append(lmap)
            
            # Register with Prometheus
            REGISTRY.register(lmap)
            
            logger.info(f"🔗 LMAP collector {i} registered with {len(mr_indices)} memory regions")

            # Start the collection process
            if start_local_scrape:
                lmap.start_local_scrape_loop()



    def setup(self):
        """
        Initialize the collector by fetching metrics layout and setting up RDMA.
        Assumes that the host is already initialized and all memory regions are there.
        No agreement on MRs. Static configuration is used.
        
        """
        try:
            
            # setup data plane connection
            self.connect_with_microview_host()

            logger.info("✅ MicroView connected to host")
            
        except Exception as e:
            logger.error(f"Failed to initialize MicroView collector: {e}")
            raise
        
    

    def cleanup(self):
        """Clean up resources"""
        super().cleanup()
        for lmap in self.lmaps:
            lmap.cleanup()
        if self.qp_pool:
            self.qp_pool.cleanup()
        logger.info("MicroView collector cleaned up")


# class MicroViewConn(MicroViewBase):
#     """Standard implementation of MicroView collector"""
    
#     def setup(self, num_rdma_connections: int = 1):
#         """
#         Initialize the collector by fetching metrics layout and setting up RDMA
        
#         Args:
#             num_rdma_connections: Number of RDMA connections to create
#         """
#         try:
#             # Fetch memory layout from control plane
#             metrics_layout = self.get_memory_layout()
#             logger.info(f"Fetched memory layout with {len(metrics_layout)} microservices")
            
#             # Initialize RDMA connection based manager
#             self.rdma = RDMACollectorCm(
#                 self.host,
#                 num_connections=num_rdma_connections,
#                 grouping_function=group_memory_pages_contiguous,
#                 metrics_layout=metrics_layout
#             )
            
#             # Initialize RDMA connections
#             self.rdma.initialize_connections()
            
#             # Set up remote access
#             #  TODO self.rdma_manager.setup_remote_access()
            
#             logger.info("MicroView collector initialized successfully")
            
#         except Exception as e:
#             logger.error(f"Failed to initialize MicroView collector: {e}")
#             raise


def run_test(name, args):
    if hasattr(__import__(__name__), name):
        test = getattr(__import__(__name__), name)
        logger.info(f"Running test function: {test.__name__}")
        test(args)
    else:
        logger.error(f"Test function {name} not found")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="MicroView NIC Collector")
    parser.add_argument("--control-plane", "-c", required=True, help="Control plane URL")
    parser.add_argument("--prometheus-port", type=int, default=8000, help="Prometheus HTTP server port")
    parser.add_argument("--scrape-interval", "-i", type=float, default=1., help="Local scrape interval in seconds")
    parser.add_argument("--lmaps", "-l", type=int, default=1, help="Number of LMAP collectors")
    parser.add_argument("--dev", "-d", type=str, default=DEFAULT_RDMA_DEVICE, help="RDMA device name")
    parser.add_argument("--ib-port", type=int, default=DEFAULT_IB_PORT, help="RDMA IB port")
    parser.add_argument("--gid", type=int, default=DEFAULT_GID, help="RDMA GID index")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--test", type=str, help="Run test function")

    
    args = parser.parse_args()
    
    # Set up logging
    if args.debug:
        logger.setLevel(logging.DEBUG)

    
    ## --------- quick tests -----------

    def keep_alive():
        """Keep the main thread alive"""
        logger.info("Press Ctrl+C to exit")
        while True:
            time.sleep(120)


    def test_setup(args):
        """Test function to verify MicroView setup"""
        try:
            # Create a MicroView collector instance
            uview = MicroView(
                control_plane_url=args.control_plane,
                rdma_device=args.dev,
                ib_port=args.ib_port,
                gid=args.gid,
                num_collectors=args.lmaps,
                scrape_interval=args.scrape_interval
            )
            
            # Set up the collection process
            uview.setup()

            logger.info("MicroView setup test passed")
            
            return uview
        except Exception as e:
            logger.error(f"MicroView setup test failed: {e}")
            raise e

    # -----------
    def test_microview_read(args):
        """Test function to verify MicroView read operation"""
        try:
            
            uview = test_setup(args)
            
            input("Waiting for metrics control region. Press Enter to continue...")
            
            # Configure collectors (this call will also register the LMAPs with Prometheus)
            uview.configure_lmaps(start_local_scrape=True)

            keep_alive()    

        except KeyboardInterrupt:
            logger.info("Shutting down...")
        except Exception as e:
            logger.error(f"❌ MicroView read test failed: {e}")
            raise e
        finally:
            uview.cleanup()
    
    # -----------
    def test_with_prometheus(args): 
        # Create the MicroView collector instance
        uview = test_microview_read(args)
         # Start the Prometheus HTTP server
        start_http_server(args.prometheus_port)
        logger.info(f"Prometheus metrics server started on port {args.prometheus_port}")

        try:
            keep_alive()        
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        except Exception as e:
            logger.error(f"Error: {e}")
        finally:
            uview.cleanup()


    # -----------
    def test_with_prometheus_multithread(args):
        """Test function to verify MicroView with Prometheus"""
        try:
            # Create a MicroView collector instance with multiple collectors
            uview = test_setup(args)
            
            input("Waiting for metrics control region. Press Enter to continue...")
            
            # Configure collectors (this call will also register the LMAPs with Prometheus)
            uview.configure_lmaps()
            
            # Start the Prometheus HTTP server
            start_http_server(args.prometheus_port)
            logger.info(f"Prometheus metrics server started on port {args.prometheus_port}")
            
            # Keep the main thread alive
            try:
                keep_alive()
            except KeyboardInterrupt:
                logger.info("Shutting down...")
                
        except Exception as e:
            logger.error(f"Test failed: {e}")
            raise
        finally:
            if 'uview' in locals():
                uview.cleanup()

    
    # entry point for these tests
    test_function_name = "test_" + args.test.lower()
    run_test(test_function_name, args)