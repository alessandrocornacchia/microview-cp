import time
import logging
import requests
import numpy as np
from typing import Dict, List, Tuple, Any, Optional, Callable

from prometheus_client import start_http_server, REGISTRY, Metric
from rdma.rdma_cm_collector import RDMACollectorCm

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

# Constants
PAGE_SIZE = 4096
MAX_GROUP_SIZE = 64 * 1024  # 64KB maximum size for RDMA read groups

def group_memory_pages_contiguous(metrics_layout: Dict[str, Dict], num_connections: int) -> List[List[Dict]]:
    """
    Group memory pages by contiguous page offsets, ensuring pages within a group are adjacent in memory
    
    Args:
        metrics_layout: Dictionary mapping microservice IDs to their page info
        num_connections: Number of RDMA connections to distribute groups across
        
    Returns:
        List of groups, where each group is a list of page dictionaries.
    """
    # Convert the layout into a list of pages with their metadata
    pages = []
    for microservice_id, page_info in metrics_layout.items():
        pages.append({
            "microservice_id": microservice_id,
            "page_offset": page_info.get("page_offset", 0),
            "num_entries": page_info.get("num_entries", 0),
            "max_metrics": page_info.get("max_metrics", 0)
        })
    
    # Sort pages by page_offset to find contiguous pages
    pages.sort(key=lambda x: x["page_offset"])
    
    # Group pages into chunks of contiguous pages that fit within MAX_GROUP_SIZE
    groups = []
    current_group = []
    current_size = 0
    last_page_end_offset = -1  # Track the end of the last page in the current group
    
    for page in pages:
        page_offset = page["page_offset"]
        
        # Check if this page is contiguous with the previous one
        is_contiguous = (page_offset == last_page_end_offset) or (not current_group)
        
        # Start a new group if:
        # 1. The current group would exceed max size with this page, OR
        # 2. This page is not contiguous with the last page in the group
        if ((current_size + PAGE_SIZE > MAX_GROUP_SIZE) or (not is_contiguous)) and current_group:
            groups.append(current_group)
            current_group = []
            current_size = 0
        
        current_group.append(page)
        current_size += PAGE_SIZE
        last_page_end_offset = page_offset + PAGE_SIZE # Update the end offset
        logger.debug(f"Adding page {page['microservice_id']} to current group, size: {current_size}")
    
    # Add the last group if not empty
    if current_group:
        groups.append(current_group)
    
    # Ensure we have at least one group
    if not groups:
        logger.warning("No valid page groups created from metrics layout")
        return []
    
    # If we have fewer groups than connections, some connections will be idle
    num_connections = min(num_connections, len(groups))
    if num_connections <= 0:
        logger.warning("No valid connections specified")
        return []
    
    # Distribute groups evenly across connections
    connection_groups = [[] for _ in range(num_connections)]
    for i, group in enumerate(groups):
        connection_idx = i % num_connections
        connection_groups[connection_idx].extend(group)
    
    logger.info(f"Created {len(groups)} page groups distributed across {num_connections} connections")
    for i, group in enumerate(groups):
        page_offsets = [page["page_offset"] for page in group]
        microservices = [page["microservice_id"] for page in group]
        logger.debug(f"Group {i}: {len(group)} pages, offsets: {page_offsets}, microservices: {microservices}")
        
    return connection_groups

class RDMAManager:
    """Manages RDMA connections and memory region grouping"""
    
    def __init__(self, host_addr: str, port: str = "18515", num_connections: int = 1, 
                 metrics_layout: Dict[str, Dict] = None,
                 grouping_function: Callable = group_memory_pages_contiguous):
        """
        Initialize the RDMA manager
        
        Args:
            host_addr: Address of the host running the RDMA server
            port: Port of the RDMA server
            num_connections: Number of RDMA connections to create
            metrics_layout: Optional pre-loaded metrics layout
            grouping_function: Function to use for grouping memory pages
        """
        self.host_addr = host_addr
        self.port = port
        self.num_connections = max(1, num_connections)  # At least one connection
        self.grouping_function = grouping_function
        self.collectors = []  # List of RDMACollectorCm instances
        self.metrics_page_groups = []  # List of metric groups for each connection
        self.metrics_layout = metrics_layout  # Store the metrics layout
        
        logger.info(f"Initializing RDMA Manager with {self.num_connections} connections to {host_addr}:{port}")
        
        # If metrics layout is provided, group pages right away
        if metrics_layout:
            self.memory_pages_to_rdma_mr(metrics_layout)
            # Group memory pages
            logger.info(f"Grouped metrics into {len(self.metrics_page_groups)} connection groups")
        
    
    # NOTE: having this function allows lazy loading of the metrics layout
    def memory_pages_to_rdma_mr(self, metrics_layout: Dict[str, Dict]) -> List[List[Dict]]:
        """
        Group memory pages into RDMA memory regions using the current grouping function
        
        Args:
            metrics_layout: Dictionary mapping microservice IDs to their metrics info
            
        Returns:
            List of groups, where each group is a list of page dictionaries
        """
        self.metrics_layout = metrics_layout
        self.metrics_page_groups = self.grouping_function(
            metrics_layout, 
            self.num_connections
        )    
        return self.metrics_page_groups
    
    
    def initialize_connections(self):
        """Initialize RDMA connections based on the grouped memory regions"""
        if not self.metrics_page_groups:
            logger.warning("No connection groups defined. Call memory_pages_to_rdma_mr first.")
            return
        
        # Create collectors for each connection
        self.collectors = []
        for i in range(min(self.num_connections, len(self.metrics_page_groups))):
            try:
                collector = RDMACollectorCm(self.host_addr, self.port)
                self.collectors.append(collector)
                logger.info(f"Initialized RDMA connection {i+1}/{self.num_connections}")
            except Exception as e:
                logger.error(f"Failed to initialize RDMA connection {i+1}: {e}")
    
    
    def setup_remote_access(self):
        """Request remote access for each connection and register memory regions"""
        if not self.collectors or not self.connection_groups:
            logger.warning("No connections or groups defined.")
            return
            
        for i, (collector, group) in enumerate(zip(self.collectors, self.connection_groups)):
            try:
                for page_info in group:
                    # Request remote access for this memory page
                    remote_addr, rkey = collector.request_remote_access(
                        service_id=page_info.get("microservice_id"),
                        page_offset=page_info.get("page_offset", 0)
                    )
                    
                    # Register the memory region for RDMA reads
                    collector.register_remote_read_region(
                        remote_addr=remote_addr,
                        rkey=rkey,
                        length=PAGE_SIZE,
                        name=f"{page_info.get('microservice_id')}"
                    )
                    
                logger.info(f"Registered {len(group)} memory regions for connection {i+1}")
            except Exception as e:
                logger.error(f"Failed to setup remote access for connection {i+1}: {e}")
    
    def read_all_metrics(self) -> Dict[str, bytes]:
        """
        Read all metrics from all RDMA connections
        
        Returns:
            Dictionary mapping metric names to their values
        """
        all_results = {}
        
        for i, collector in enumerate(self.collectors):
            try:
                results = collector.read_metrics()
                all_results.update(results)
                logger.debug(f"Read {len(results)} metrics from connection {i+1}")
            except Exception as e:
                logger.error(f"Failed to read metrics from connection {i+1}: {e}")
        
        return all_results
    
    def cleanup(self):
        """Clean up all RDMA connections"""
        for collector in self.collectors:
            try:
                # The collector's destructor will handle cleanup
                pass
            except Exception as e:
                logger.error(f"Error during RDMA collector cleanup: {e}")

class MicroView:
    """MicroView collector for Prometheus that reads metrics using RDMA"""
    
    def __init__(self, control_plane_url: str):
        """
        Initialize the MicroView collector
        
        Args:
            control_plane_url: URL of the MicroView control plane
        """
        self.control_plane_url = control_plane_url
        self.metrics_layout = {}  # Memory layout of metrics from control plane
        self.metrics_config = {}  # Configuration of metrics (types, etc.)
        self.rdma_manager = None
        
        # Parse host and port from control plane URL
        host_parts = control_plane_url.split(':')
        self.host = host_parts[0]
        self.port = host_parts[1] if len(host_parts) > 1 else "5000"
        
        logger.info(f"Initializing MicroView collector with control plane at {control_plane_url}")
        
    def setup(self, num_rdma_connections: int = 1):
        """
        Initialize the collector by fetching metrics layout and setting up RDMA
        
        Args:
            num_rdma_connections: Number of RDMA connections to create
        """
        try:
            # Fetch memory layout from control plane
            metrics_layout = self.get_memory_layout()
            logger.info(f"Fetched memory layout with {len(metrics_layout)} microservices")
            
            # Initialize RDMA manager
            self.rdma_manager = RDMAManager(
                self.host,
                num_connections=num_rdma_connections,
                grouping_function=group_memory_pages_contiguous,
                metrics_layout=metrics_layout
            )
            
            # Initialize RDMA connections
            self.rdma_manager.initialize_connections()
            
            # Set up remote access
            #  TODO self.rdma_manager.setup_remote_access()
            
            logger.info("MicroView collector initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize MicroView collector: {e}")
            raise
    
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
            raw_metrics = self.rdma_manager.read_all_metrics()
            
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
        if self.rdma_manager:
            self.rdma_manager.cleanup()



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
            
            # Set up the collector with 1 RDMA connection (i.e., 1 Queue Pair)
            uview.setup(num_rdma_connections=1)
            
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
            if hasattr(uview, 'rdma_manager') and uview.rdma_manager:
                uview.rdma_manager.cleanup()

    test_function_name = "test_" + args.test.lower()
    run_test(test_function_name)