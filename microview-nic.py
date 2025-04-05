import abc
from typing import Dict, List, Tuple, Any, Optional, Callable
import time
import logging
import requests
import numpy as np

from prometheus_client import start_http_server, REGISTRY, Metric
from defaults import *
#from rdma.cm_collector import RDMACollectorCm
from rdma.helpers import MRMetadata, OneSidedReader, QueuePairPool
#from readers.rdma_connections import group_memory_pages_contiguous

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
    
    def __init__(self, control_plane_url: str):
        """
        Initialize the MicroView collector base
        
        Args:
            control_plane_url: URL of the MicroView control plane
        """
        self.control_plane_url = control_plane_url
        self.metrics_layout = {}  # Memory layout of metrics from control plane
        self.metrics_config = {}  # Configuration of metrics (types, etc.)
        self.rdma = None
        
        # Parse host and port from control plane URL
        host_parts = control_plane_url.split(':')
        self.host = host_parts[0]
        self.port = host_parts[1] if len(host_parts) > 1 else "5000"
        
        logger.info(f"Initializing MicroView collector with control plane at {control_plane_url}")
    
    @abc.abstractmethod
    def setup(self, num_rdma_connections: int = 1):
        """
        Initialize the collector by fetching metrics layout and setting up RDMA
        
        Args:
            num_rdma_connections: Number of RDMA connections to create
        """
        pass
    
    def get_memory_layout(self) -> Dict[str, Dict]:
        """
        Fetch memory layout from the control plane
        
        Returns:
            Dictionary mapping microservice IDs to their metrics info
        """
        try:
            response = requests.get(f"http://{self.control_plane_url}/metrics")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch memory layout: {e}")
            # Return empty dict or raise exception based on your error handling strategy
            return {}

    def configure_collector(self, service_id: str, *args, **kwargs):
        """Configure collector for a specific service (placeholder for future functionality)"""
        logger.info(f"Configuring collector for service {service_id}")
        # Implementation will depend on specific requirements
    
    def configure_lmap(self, service_id: str, metrics_config: Dict, sketch_params: Dict):
        """Configure local mapping for metrics (placeholder for future functionality)"""
        logger.info(f"Configuring local mapping for service {service_id}")
        self.metrics_config[service_id] = {
            "metrics": metrics_config,
            "sketch_params": sketch_params
        }
    
    def collect(self):
        """
        Collect metrics for Prometheus
        
        Returns:
            List of Prometheus metrics
        """
        prom_metrics = []
        
        try:
            # Read all metrics using RDMA
            raw_metrics = self.rdma.read_all_metrics()
            
            # Process raw metrics into Prometheus format
            # This is a simplified implementation - in reality you would need to parse
            # the raw memory according to the metric_dtype structure
            for microservice_id, raw_data in raw_metrics.items():
                try:
                    # In a real implementation, you would parse the memory page to extract
                    # each metric by using the metric_dtype structure. This is just a placeholder.
                    metric = Metric(f"microview_{microservice_id}", 
                                  f"MicroView metric for {microservice_id}", 
                                  'gauge')
                    
                    metric.add_sample(
                        f"microview_{microservice_id}_collected",
                        value=1.0,  # Placeholder - you would extract real values
                        labels={"service": microservice_id}
                    )
                    
                    prom_metrics.append(metric)
                    
                except Exception as e:
                    logger.error(f"Failed to process metrics for {microservice_id}: {e}")
            
        except Exception as e:
            logger.error(f"Error collecting metrics: {e}")
        
        return prom_metrics
    
    def __del__(self):
        """Clean up resources"""
        if self.rdma:
            self.rdma.cleanup()



class MicroView(MicroViewBase):
    """Standard implementation of MicroView collector"""
    
    def __init__(self, control_plane_url):
        super().__init__(control_plane_url)
        self.qp_pool = None


    def connect_with_microview_host(self):
        """
        Creates Queue Pair pool, exchange queue pair information with the host and connects the queue pairs
        """
        logger.info("Connecting with host")
        try:
            # 1. Initialize QP pool
            self.qp_pool = QueuePairPool(DEFAULT_RDMA_DEVICE, pool_size=DEFAULT_QP_POOL_SIZE)

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
            logger.info("🔗 Connected with MicroView host")

            # 8. Create RDMA client
            remote_memory_regions = [MRMetadata(
                mr["addr"], 
                mr["rkey"], 
                mr["size"], 
                None) for mr in response.json().get("memory_regions", [])]
            self.rdma = OneSidedReader(self.qp_pool.pd, self.qp_pool.get_qp_object(0), remote_memory_regions)
            logger.info("💾 RDMA active reader initialized")
            
        except Exception as e:
            logger.info(f"❌ Failed to connect with host: {e}")
            self.cleanup()
            raise e
            

    def setup(self, num_rdma_connections: int = 1):
        """
        Initialize the collector by fetching metrics layout and setting up RDMA.
        Assumes that the host is already initialized and all memory regions are there.
        No agreement on MRs. Static configuration is used.
        
        Args:
            num_rdma_connections: Number of RDMA connections to create
        """
        try:
            # TODO decide Fetch memory layout from control plane
            metrics_layout = self.get_memory_layout()
            logger.info(f"Fetched memory layout with {len(metrics_layout)} microservices")
            
            # set queue pairs
            self.connect_with_microview_host()

            
            logger.info("✅ MicroView collector initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize MicroView collector: {e}")
            raise


    def cleanup(self):
        """Clean up resources"""
        super().cleanup()
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


def run_test(name):
    if hasattr(__import__(__name__), name):
        test = getattr(__import__(__name__), name)
        logger.info(f"Running test function: {test.__name__}")
        test()
    else:
        logger.error(f"Test function {name} not found")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="MicroView NIC Collector")
    parser.add_argument("--control-plane", required=True, help="Control plane URL")
    parser.add_argument("--port", type=int, default=8000, help="Prometheus HTTP server port")
    parser.add_argument("--connections", type=int, default=1, help="Number of RDMA connections")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--test", type=str, help="Run test function")

    
    args = parser.parse_args()
    
    if args.debug:
        logger.setLevel(logging.DEBUG)

    
    ## --------- quick tests -----------
    def test_microview_setup():
        """Test function to verify MicroView setup"""
        try:
            # Create a MicroView collector instance
            uview = MicroView("localhost:5000")

            # sleep for some time to let services start and register thei metrics with microview agent 
            time.sleep(1)
            
            uview.connect_with_microview_host()

            # Set up the collector with 1 RDMA connection (i.e., 1 Queue Pair)
            #uview.setup(num_rdma_connections=1)
            
            logger.info("MicroView setup test passed")
            
        except Exception as e:
            logger.error(f"MicroView setup test failed: {e}")

    
    # -----------
    def test_with_prometheus(): 
        # Create the MicroView collector instance
        uview = MicroView(args.control_plane)
        
        try:
            # Initialize collector with specified number of connections
            uview.setup(args.connections)
            
            # Register with Prometheus
            REGISTRY.register(uview)
            
            # Start the Prometheus HTTP server
            start_http_server(args.port)
            logger.info(f"Prometheus metrics server started on port {args.port}")
            
            # Keep the main thread alive
            while True:
                time.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        except Exception as e:
            logger.error(f"Error: {e}")
        finally:
            # Cleanup
            if hasattr(uview, 'rdma_manager') and uview.rdma:
                uview.rdma.cleanup()

    test_function_name = "test_" + args.test.lower()
    run_test(test_function_name)