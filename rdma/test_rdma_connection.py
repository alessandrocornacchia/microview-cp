#!/usr/bin/env python3
import pickle
import time
import argparse
from rdma_cm_collector import RDMACollectorCm

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
    
    # Load memory region information from pickle file
    try:
        print(f"Loading memory region info from {args.pickle_file}")
        with open(args.pickle_file, "rb") as f:
            mr_info = pickle.load(f)
            
        print(f"Memory region: addr={hex(mr_info['addr'])}, rkey={mr_info['rkey']}, size={mr_info['size']}")
    except Exception as e:
        print(f"Error loading memory region info: {e}")
        return
    
    # Create RDMACollectorCm instance
    try:
        print(f"Connecting to RDMA server at {args.host}:{args.port}")
        collector = RDMACollectorCm(host_addr=args.host, port=args.port)
        
        # Register the remote memory region
        region_idx = collector.register_remote_read_region(
            remote_addr=mr_info['addr'],
            rkey=mr_info['rkey'],
            length=mr_info['size'],
            name="test_region"
        )
        
        print(f"Registered remote memory region with index {region_idx}")
        
        # Perform RDMA read operations in a loop
        for i in range(3):
            print(f"Performing RDMA read operation #{i+1}")
            
            # Read metrics from all registered regions
            results = collector.read_metrics()
            
            # Display the results
            for name, data in results.items():
                # Show first 64 bytes as hex and as ASCII
                hex_data = data[:64].hex()
                ascii_data = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data[:64])
                
                print(f"Region '{name}':")
                print(f"  First 64 bytes (hex): {hex_data}")
                print(f"  First 64 bytes (ascii): {ascii_data}")
                
                # Check for our test pattern
                if b"RDMA-TEST-DATA" in data[:64]:
                    print("  Found test pattern 'RDMA-TEST-DATA'")
                
                # Look for timestamp (which changes)
                if b"Timestamp:" in data:
                    timestamp_pos = data.find(b"Timestamp:")
                    timestamp_end = data.find(b"\x00", timestamp_pos)
                    if timestamp_end > timestamp_pos:
                        timestamp = data[timestamp_pos:timestamp_end].decode()
                        print(f"  {timestamp}")
            
            # Wait before the next read
            if i < 2:  # Don't sleep after the last read
                print("Waiting for 2 seconds...")
                time.sleep(2)
        
        print("RDMA read test completed successfully")
        
    except Exception as e:
        print(f"Error during RDMA operations: {e}")
    
if __name__ == "__main__":
    main()