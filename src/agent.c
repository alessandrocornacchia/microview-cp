#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <errno.h>
#include <mqueue.h>
#include <arpa/inet.h>
#include <pthread.h>
#include <sys/ipc.h>
#include <sys/shm.h>
#include <fcntl.h>
#include <sys/shm.h>
#include <sys/mman.h>
#include <signal.h>

#include "rdma-common.h"
#include "rdma-agent.h"

#define Q_NAME    "shm"
#define MAX_SIZE  1024
#define MAX_LEN   256
#define M_EXIT    "done"

static char peer_ip[MAX_LEN];
static char peer_port[MAX_LEN];
static int serverSocket;

extern int block_size;
extern int num_mr;

void * poll_pids(void* args);
void  INThandler(int sig);

/* used by host agent */
pthread_mutex_t cp_mutex;
struct control_plane {
    int pod_pids[RDMA_MAX_CONNECTIONS];
    struct rdma_cm_id* conn[RDMA_MAX_CONNECTIONS];
    int num_pods;
};
struct control_plane cp;


int start_rdma_session(int shm_fd, int podID) {
    struct addrinfo *addr;
    struct rdma_cm_event *event = NULL;
    struct rdma_cm_id *conn= NULL;
    struct rdma_event_channel *ec = NULL;

    //set_mode(M_READ);
    //set_role(R_CLIENT);

    /* memory map the shared memory object, and pass it as context to the connection */
    void *shm_ptr = mmap(0, block_size, PROT_WRITE, MAP_SHARED, shm_fd, 0);
    
    TEST_NZ(getaddrinfo(peer_ip, peer_port, NULL, &addr));

    TEST_Z(ec = rdma_create_event_channel());
    TEST_NZ(rdma_create_id(ec, &conn, shm_ptr, RDMA_PS_TCP));   // we pass here the pointer to shared memory
    TEST_NZ(rdma_resolve_addr(conn, NULL, addr->ai_addr, TIMEOUT_IN_MS));

    freeaddrinfo(addr);

    /* register new pod in control plane (watcher thread will track running pods) */
    pthread_mutex_lock(&cp_mutex);
    cp.pod_pids[cp.num_pods] = podID;
    cp.conn[cp.num_pods] = conn;
    cp.num_pods++;
    pthread_mutex_unlock(&cp_mutex);

    while (rdma_get_cm_event(ec, &event) == 0)
    {
        struct rdma_cm_event event_copy;

        memcpy(&event_copy, event, sizeof(*event));
        rdma_ack_cm_event(event);

        if (on_event(&event_copy)) 
        {
            break;
        }
    }

    rdma_destroy_event_channel(ec);

    return 0;
}

// function to handle termination of RDMA connections for dead pods
void * poll_pids(void* args) {
    while (1) {
        
        sleep(2);   // every two seconds check which pods died
        
        pthread_mutex_lock(&cp_mutex);
        for (int i=0; i < cp.num_pods; i++)
        {
            if (cp.pod_pids[i] != -1) {
                // check process id of the pod and terminate if it's no more active
                if (kill(cp.pod_pids[i], 0) == -1) {
                    
                    printf("Pod %d is not active anymore, closing RDMA connection %d\n", cp.pod_pids[i], i);
                    
                    rdma_disconnect(cp.conn[i]);
                    
                    cp.pod_pids[i] = -1;
                }
            } 
        }
        pthread_mutex_unlock(&cp_mutex);
    }
}

// Function to handle client requests
void *handleNewPod(void *clientSocketPtr) {
    int clientSocket = *((int *)clientSocketPtr);
    uint32_t podID;
    int shm_fd;

    /* Receive the podID from the client: podID is the process id in the OS */
    if (recv(clientSocket, &podID, sizeof(podID), 0) <= 0) {
        perror("Error receiving data from client");
        exit(EXIT_FAILURE);
    }
    podID = ntohl(podID);
    printf("\n** New pod with pid %d registered **\n", podID);

    /* create the shared memory object */
    char shm_name[MAX_LEN];
    memset(shm_name, 0, MAX_LEN);
    sprintf(shm_name, "%s-%u", Q_NAME, podID);

    shm_fd = shm_open(shm_name, O_CREAT | O_RDWR, 0666);
    if (shm_fd == -1) {
        perror("Error creating shared memory object");
        exit(EXIT_FAILURE);
    }
    printf("MicroView agent created memory region %s\n", shm_name);
    /* configure the size of the shared memory object */
    ftruncate(shm_fd, block_size);
    
    // Write the name back to the opened socket
    send(clientSocket, &shm_name, MAX_LEN, 0);

    // Close the tcp socket
    close(clientSocket);
    free(clientSocketPtr);

    printf("Starting RDMA session thread\n");
    start_rdma_session(shm_fd, podID);

    // when we arrive at this point means the watcher thread has disconnected
    // rdma session, we can unlink shared memory segment and exit the thread
    // who served this connection. 
    // TODO don't know why this unlink fails..
    
    //TEST_Z(shm_unlink(shm_name));
    pthread_exit(NULL);
}

int run() {
    /* opens TCP server and listens for incoming conenction
       new pods will ask for shared memory region pointer on
       this connection. */
    int clientSocket;
    struct sockaddr_in serverAddr, clientAddr;
    socklen_t clientAddrLen = sizeof(clientAddr);
    pthread_t tid;

    // Create a socket
    serverSocket = socket(AF_INET, SOCK_STREAM, 0);
    if (serverSocket == -1) {
        perror("Error creating socket");
        exit(EXIT_FAILURE);
    }

    // Configure server address
    memset(&serverAddr, 0, sizeof(serverAddr));
    serverAddr.sin_family = AF_INET;
    serverAddr.sin_port = htons(12345);
    serverAddr.sin_addr.s_addr = INADDR_ANY;

    // Bind the socket
    if (bind(serverSocket, (struct sockaddr *)&serverAddr, sizeof(serverAddr)) == -1) {
        perror("Error binding socket");
        close(serverSocket);
        exit(EXIT_FAILURE);
    }

    // Listen for incoming connections
    if (listen(serverSocket, 5) == -1) {
        perror("Error listening for connections");
        close(serverSocket);
        exit(EXIT_FAILURE);
    }

    printf("Server is listening on port 12345...\n");

    // watch active pods and terminate rdma connections for those non-active
    pthread_mutex_init(&cp_mutex, NULL);
    cp.num_pods = 0;
    pthread_t pptid;
    TEST_NZ(pthread_create(&pptid, NULL, poll_pids, NULL));
    pthread_detach(pptid);

    while (1) {
        // Accept a new connection
        clientSocket = accept(serverSocket, (struct sockaddr *)&clientAddr, &clientAddrLen);
        if (clientSocket == -1) {
            perror("Error accepting connection");
            continue;
        }

        // Allocate memory for the client socket descriptor to pass to the thread
        int *clientSockPtr = malloc(sizeof(int));
        if (clientSockPtr == NULL) {
            perror("Error allocating memory");
            close(clientSocket);
            continue;
        }
        *clientSockPtr = clientSocket;

        // Create a new thread to handle the client request
        if (pthread_create(&tid, NULL, handleNewPod, clientSockPtr) != 0) {
            perror("Error creating thread");
            free(clientSockPtr);
            close(clientSocket);
            continue;
        }
        pthread_detach(tid);
    }

    return 0;
}


int main(int argc, char *argv[])
{
    if (argc != 5) {
        usage(argv[0]);
        exit(EXIT_FAILURE);
    }
    
    sprintf(peer_ip, "%s", argv[1]);
    sprintf(peer_port, "%s", argv[2]);
    
    block_size = atoi(argv[3]);
    num_mr = atoi(argv[4]);

    printf("Agent connects to peer %s on port %s, mode = %s\n", peer_ip, peer_port, "read");
    
    signal(SIGINT, INThandler);
    run();
}

void usage(const char *argv0)
{
  fprintf(stderr, "usage: %s <DPU-address> <DPU-port> <block size> <MR per pod>\n", argv0);
  exit(1);
}


/* handle Ctrl+C termination */
void  INThandler(int sig)
{
    // close sockets and exit
    printf("Terminating agent\n");
    TEST_Z(close(serverSocket));
    exit(0);
    // TODO should also release other resources...

}