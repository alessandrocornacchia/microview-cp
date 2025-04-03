"""
Implements a RDMA manager that handles connections and memory regions for RDMA reads.

0. Assumes a remote RDMA CM server is running and accessible. 
1. Downloads the memory layout from the control plane.
2. Groups memory pages into contiguous regions for RDMA reads.
3. Initializes RDMA connections and registers memory regions.
4. Reads metrics from the RDMA connections.
"""

import time
import logging
import requests
import numpy as np
from typing import Dict, List, Tuple, Any, Optional, Callable

from prometheus_client import start_http_server, REGISTRY, Metric
from rdma.cm_collector import RDMACollectorCm
from defaults import *


def group_memory_pages_contiguous(metrics_layout: Dict[str, Dict], num_connections: int, 
                                  page_size : int = PAGE_SIZE, max_group_size : int = MAX_GROUP_SIZE) -> List[List[Dict]]:
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
    
    # Group pages into chunks of contiguous pages that fit within max_group_size
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
        if ((current_size + page_size > max_group_size) or (not is_contiguous)) and current_group:
            groups.append(current_group)
            current_group = []
            current_size = 0
        
        current_group.append(page)
        current_size += page_size
        last_page_end_offset = page_offset + page_size # Update the end offset
        logging.debug(f"Adding page {page['microservice_id']} to current group, size: {current_size}")
    
    # Add the last group if not empty
    if current_group:
        groups.append(current_group)
    
    # Ensure we have at least one group
    if not groups:
        logging.warning("No valid page groups created from metrics layout")
        return []
    
    # If we have fewer groups than connections, some connections will be idle
    num_connections = min(num_connections, len(groups))
    if num_connections <= 0:
        logging.warning("No valid connections specified")
        return []
    
    # Distribute groups evenly across connections
    connection_groups = [[] for _ in range(num_connections)]
    for i, group in enumerate(groups):
        connection_idx = i % num_connections
        connection_groups[connection_idx].extend(group)
    
    logging.info(f"Created {len(groups)} page groups distributed across {num_connections} connections")
    for i, group in enumerate(groups):
        page_offsets = [page["page_offset"] for page in group]
        microservices = [page["microservice_id"] for page in group]
        logging.debug(f"Group {i}: {len(group)} pages, offsets: {page_offsets}, microservices: {microservices}")
        
    return connection_groups

class RDMAManager:
    """Manages RDMA connections and memory region grouping"""
    
    def __init__(self, host_addr: str, port: str = "18515", num_connections: int = 1, 
                 metrics_layout: Dict[str, Dict] = None,
                 grouping_function: Callable = group_memory_pages_contiguous,
                 page_size: int = PAGE_SIZE):
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
        self.page_size = page_size  # Size of each memory page


        logging.info(f"Initializing RDMA Manager with {self.num_connections} connections to {host_addr}:{port}")
        
        # If metrics layout is provided, group pages right away
        if metrics_layout:
            self.memory_pages_to_rdma_mr(metrics_layout)
            # Group memory pages
            logging.info(f"Grouped metrics into {len(self.metrics_page_groups)} connection groups")
        
    
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
            logging.warning("No connection groups defined. Call memory_pages_to_rdma_mr first.")
            return
        
        # Create collectors for each connection
        self.collectors = []
        for i in range(min(self.num_connections, len(self.metrics_page_groups))):
            try:
                collector = RDMACollectorCm(self.host_addr, self.port)
                self.collectors.append(collector)
                logging.info(f"Initialized RDMA connection {i+1}/{self.num_connections}")
            except Exception as e:
                logging.error(f"Failed to initialize RDMA connection {i+1}: {e}")
    
    
    def setup_remote_access(self):
        """Request remote access for each connection and register memory regions"""
        if not self.collectors or not self.connection_groups:
            logging.warning("No connections or groups defined.")
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
                        length=self.page_size,
                        name=f"{page_info.get('microservice_id')}"
                    )
                    
                logging.info(f"Registered {len(group)} memory regions for connection {i+1}")
            except Exception as e:
                logging.error(f"Failed to setup remote access for connection {i+1}: {e}")
    
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
                logging.debug(f"Read {len(results)} metrics from connection {i+1}")
            except Exception as e:
                logging.error(f"Failed to read metrics from connection {i+1}: {e}")
        
        return all_results
    
    def cleanup(self):
        """Clean up all RDMA connections"""
        for collector in self.collectors:
            try:
                # The collector's destructor will handle cleanup
                pass
            except Exception as e:
                logging.error(f"Error during RDMA collector cleanup: {e}")