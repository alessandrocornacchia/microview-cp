import sys
import os


nmetrics = [1, 100, 100, 1000]
num_pods = 8
METRIC_SIZE_BYTES = 64

for n in nmetrics:

    os.environ["EXPERIMENT_LABEL"] = f"metric_{n}"
    os.environ["NUM_METRICS"] = str(n)
    
    page_size = n * METRIC_SIZE_BYTES
    
    # this way each pod has one page
    os.environ["DEFAULT_PAGE_SIZE"] = str(page_size)
    
    # each pod gets one MR -> different LMAPs will read different MRs
    os.environ["DEFAULT_RDMA_MR_SIZE"] = str(1 *page_size)  
    
    os.environ["UVIEW_SCRAPING_INTERVAL"] = "0.1"

    # os.environ["EXPERIMENT_DURATION"] = "60"
    os.environ["DEBUG"] = "false"
    os.environ["NUM_PODS"] = str(num_pods)

    # number of LMAPs is always at most the number of cores of the IPU
    os.environ["NUM_LMAPS"] = str(min(8,num_pods))


    print("*"*50)
    print(f"EXPERIMENT_LABEL: {os.environ['EXPERIMENT_LABEL']}")
    print(f"NUM_METRICS: {os.environ['NUM_METRICS']}")
    print(f"DEFAULT_PAGE_SIZE: {os.environ['DEFAULT_PAGE_SIZE']}")
    print(f"DEFAULT_RDMA_MR_SIZE: {os.environ['DEFAULT_RDMA_MR_SIZE']}")
    print("*"*50)
        
    # run sh script
    os.system("./run.sh")